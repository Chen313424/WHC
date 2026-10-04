#!/usr/bin/env python3
"""离线验证：不启动 ROS / Gazebo 也能跑的单元测试。

覆盖三件最容易出错的事：
  1. 红绿灯时序真值是否与 TrafficLightSystem.cc 逐字一致（错了标注类别就全错）
  2. 针孔投影 + 世界->相机坐标链是否正确（错了标注框就偏）
  3. 决策逻辑的边界（红灯停/绿灯行/黄灯策略/ROI 过滤）

直接运行（只需 numpy）：
    python3 test/test_offline.py
也兼容 pytest：
    pytest test/test_offline.py
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(HERE)
if PKG_ROOT not in sys.path:
    sys.path.insert(0, PKG_ROOT)

from smart_community_perception.geometry import (  # noqa: E402
    aabb_to_bbox,
    camera_center_in_world,
    intrinsics_from_hfov,
    invert,
    matrix_to_pose,
    pose_to_matrix,
    quaternion_to_rpy,
    world_to_camera,
)
from smart_community_perception.traffic_rules import (  # noqa: E402
    Action,
    CLS_PERSON,
    CLS_TL_GREEN,
    CLS_TL_RED,
    CLS_TL_YELLOW,
    Detection,
    decide,
    estimate_distance_m,
    scale_twist,
    traffic_light_state_from_sim_time,
)

OBJECTS_YAML = os.path.join(PKG_ROOT, "config", "world_objects.yaml")
HFOV = 1.0471975


# ---------------------------------------------------------------- 极简 YAML 读取
# 本机可能没装 PyYAML，这里只解析本项目自己生成的固定格式，保证测试零依赖。
def load_objects(path: str) -> list[dict]:
    objects: list[dict] = []
    cur: dict | None = None
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("- name:"):
                cur = {"name": s.split(":", 1)[1].strip()}
                objects.append(cur)
            elif cur is not None and ":" in s:
                key, val = s.split(":", 1)
                key, val = key.strip(), val.strip()
                if val.startswith("[") and val.endswith("]"):
                    cur[key] = [float(x) for x in val[1:-1].split(",") if x.strip()]
                else:
                    cur[key] = val
    return objects


def find_object(objects: list[dict], name: str) -> dict:
    for o in objects:
        if o["name"] == name:
            return o
    raise AssertionError(f"world_objects.yaml 里找不到 {name}")


# ---------------------------------------------------------------- 1. 时序真值


def test_traffic_light_state_matches_plugin():
    """必须与 TrafficLightSystem.cc 的 绿15/黄3/红10 完全一致。"""
    cases = [
        (0.0, "green"),
        (7.5, "green"),
        (14.999, "green"),
        (15.0, "yellow"),
        (17.999, "yellow"),
        (18.0, "red"),
        (27.999, "red"),
        (28.0, "green"),   # 一个周期
        (43.0, "yellow"),  # 28 + 15
        (46.0, "red"),     # 28 + 18
        (56.0, "green"),   # 两个周期
    ]
    for t, expected in cases:
        got = traffic_light_state_from_sim_time(t)
        assert got == expected, f"t={t}: 期望 {expected}, 实际 {got}"


def test_traffic_light_state_custom_durations():
    # 参数可覆盖，且与插件读取 SDF 参数的行为一致
    assert traffic_light_state_from_sim_time(9.0, 10.0, 2.0, 5.0) == "green"
    assert traffic_light_state_from_sim_time(10.5, 10.0, 2.0, 5.0) == "yellow"
    assert traffic_light_state_from_sim_time(12.5, 10.0, 2.0, 5.0) == "red"


# ---------------------------------------------------------------- 2. 几何/投影


def test_intrinsics_from_hfov():
    fx, fy, cx, cy = intrinsics_from_hfov(640, 480, HFOV)
    # fx = 320 / tan(30deg) = 554.2563
    assert abs(fx - 554.2563) < 0.01, fx
    assert abs(fy - fx) < 1e-9, "Gazebo 默认方形像素，fy 应等于 fx"
    assert (cx, cy) == (320.0, 240.0)


def test_quaternion_roundtrip():
    for yaw in (0.0, math.pi / 2, math.pi, -math.pi / 2):
        qz, qw = math.sin(yaw / 2), math.cos(yaw / 2)
        roll, pitch, got_yaw = quaternion_to_rpy(0.0, 0.0, qz, qw)
        assert abs(roll) < 1e-9 and abs(pitch) < 1e-9
        assert abs(got_yaw - yaw) < 1e-9, (yaw, got_yaw)


def test_projection_of_box_directly_ahead():
    """相机在原点朝 +Z，正前方 5m 的 0.76m 立方体应投影到画面正中。"""
    K = intrinsics_from_hfov(640, 480, HFOV)
    bbox, depth = aabb_to_bbox(
        np.eye(4), [0, 0, 0, 0, 0, 0], [0.0, 0.0, 5.0], [0.76, 0.76, 0.76],
        K, 640, 480, min_visible=0.0,
    )
    assert bbox is not None
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    assert abs(cx - 320.0) < 0.5, cx
    assert abs(cy - 240.0) < 0.5, cy
    # 宽度 = 2 * fx * 0.38 / 4.62 = 91.18 px
    width = bbox[2] - bbox[0]
    assert abs(width - 91.18) < 0.5, width
    # 最近角点深度 = 5 - 0.38
    assert abs(depth - 4.62) < 1e-6, depth


def test_box_behind_camera_is_rejected():
    K = intrinsics_from_hfov(640, 480, HFOV)
    bbox, depth = aabb_to_bbox(
        np.eye(4), [0, 0, 0, 0, 0, 0], [0.0, 0.0, -5.0], [0.5, 0.5, 0.5],
        K, 640, 480, min_visible=0.0,
    )
    assert bbox is None and depth is None


def test_box_far_off_screen_is_rejected():
    """只露一角的框应被 min_visible 过滤掉，否则会污染数据集。"""
    K = intrinsics_from_hfov(640, 480, HFOV)
    bbox, _ = aabb_to_bbox(
        np.eye(4), [0, 0, 0, 0, 0, 0], [30.0, 0.0, 5.0], [0.5, 0.5, 0.5],
        K, 640, 480, min_visible=0.3,
    )
    assert bbox is None


def test_optical_frame_convention():
    """锁定光学系约定：+Z 前 / +X 右 / +Y 下。

    这是最容易悄悄弄反的地方（把左右上下颠倒），单独立一个方向性用例。
    """
    K = intrinsics_from_hfov(640, 480, HFOV)
    T = world_to_camera([0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0], 0.0, [0, 0, 0, 0, 0, 0])

    # 车体 +X（前方）的物体 -> 光学 +Z，距离应为 5 - 0.2
    ahead, depth = aabb_to_bbox(
        T, [0, 0, 0, 0, 0, 0], [5.0, 0.0, 0.0], [0.4, 0.4, 0.4], K, 640, 480, min_visible=0.0
    )
    assert ahead is not None and abs(depth - 4.8) < 1e-6, (ahead, depth)

    # 车体 -Y（右侧）的物体 -> 光学 +X -> 画面右半
    right, _ = aabb_to_bbox(
        T, [0, 0, 0, 0, 0, 0], [5.0, -1.0, 0.0], [0.4, 0.4, 0.4], K, 640, 480, min_visible=0.0
    )
    assert right is not None and (right[0] + right[2]) / 2.0 > 320.0, right

    # 车体 +Y（左侧）的物体 -> 画面左半
    left, _ = aabb_to_bbox(
        T, [0, 0, 0, 0, 0, 0], [5.0, 1.0, 0.0], [0.4, 0.4, 0.4], K, 640, 480, min_visible=0.0
    )
    assert left is not None and (left[0] + left[2]) / 2.0 < 320.0, left

    # 车体 +Z（上方）的物体 -> 光学 -Y -> 画面上半（v < cy）
    above, _ = aabb_to_bbox(
        T, [0, 0, 0, 0, 0, 0], [5.0, 0.0, 1.0], [0.4, 0.4, 0.4], K, 640, 480, min_visible=0.0
    )
    assert above is not None and (above[1] + above[3]) / 2.0 < 240.0, above


def test_world_camera_chain_and_traffic_light_projection():
    """端到端：spawn 偏移 + /odom + 相机外参 + 真值框投影。

    机器人放到中央路 (0, 12) 朝北(+Y)，traffic_light_1 在 (0, 19.5)，
    灯箱中心高 2.0m。相机最终应落在 (0, 12.15, 0.20)。
    """
    spawn = [32.0, 14.0, 0.05, 0.0, 0.0, 1.5708]          # 与 launch 一致
    world_T_robot = pose_to_matrix([0.0, 12.0, 0.05, 0.0, 0.0, math.pi / 2])
    odom_T_robot = invert(pose_to_matrix(spawn)) @ world_T_robot
    odom_pose6 = matrix_to_pose(odom_T_robot)

    T = world_to_camera(spawn, odom_pose6, 0.05, [0.15, 0.0, 0.10, 0.0, 0.0, 0.0])
    cam_pos = camera_center_in_world(T)
    assert abs(cam_pos[0] - 0.0) < 1e-6, cam_pos
    assert abs(cam_pos[1] - 12.15) < 1e-6, cam_pos
    assert abs(cam_pos[2] - 0.20) < 1e-6, cam_pos

    objects = load_objects(OBJECTS_YAML)
    tl1 = find_object(objects, "traffic_light_1")
    K = intrinsics_from_hfov(640, 480, HFOV)

    bbox, depth = aabb_to_bbox(
        T, tl1["model_pose"], tl1["center"], tl1["size"], K, 640, 480, min_visible=0.3
    )
    assert bbox is not None, "红绿灯在正前方 7.35m 却没被投影出来，坐标链有问题"
    x1, y1, x2, y2 = bbox
    # 水平方向在画面正中（灯就在正前方）
    assert abs((x1 + x2) / 2.0 - 320.0) < 2.0, bbox
    # 竖直方向在画面上半部（灯高 2.0m > 相机 0.2m，故 v < cy）
    assert 60.0 < y1 < 95.0, bbox
    assert 118.0 < y2 < 145.0, bbox
    # 灯箱宽 0.24m @ ~7.3m -> 约 18px
    assert 12.0 < (x2 - x1) < 26.0, bbox
    # 最近角点深度 ~ 7.35 - 0.09
    assert 7.0 < depth < 7.5, depth

    # 距离估计应接近真实距离
    det = Detection(CLS_TL_RED, 0.9, x1, y1, x2, y2)
    dist = estimate_distance_m(det, K[1])
    assert dist is not None and 5.5 < dist < 9.5, dist


def test_generated_world_objects():
    """校验 sdf_to_objects.py 的产物与 SDF 手算一致。"""
    objects = load_objects(OBJECTS_YAML)
    classes = {}
    for o in objects:
        classes[o["class"]] = classes.get(o["class"], 0) + 1
    assert classes.get("traffic_light") == 2, classes
    assert classes.get("person") == 10, classes
    assert classes.get("license_plate") == 6, classes

    tl1 = find_object(objects, "traffic_light_1")
    # 红灯 z=2.3+0.09, 绿灯 z=1.7-0.09 -> 跨度 0.78, 中心 2.0
    assert abs(tl1["size"][2] - 0.78) < 1e-6, tl1
    assert abs(tl1["center"][2] - 2.0) < 1e-6, tl1

    tl2 = find_object(objects, "traffic_light_2")
    # 横排：灯在 y=±0.28, 半径 0.09 -> 跨度 0.74
    assert abs(tl2["size"][1] - 0.74) < 1e-6, tl2

    person = find_object(objects, "person_a1")
    assert abs(person["size"][2] - 1.83) < 1e-6, person

    plate = find_object(objects, "car_a/plate_front")
    assert abs(plate["size"][0] - 0.5) < 1e-6 and abs(plate["center"][1] - 2.0) < 1e-6


# ---------------------------------------------------------------- 3. 决策逻辑

FY = intrinsics_from_hfov(640, 480, HFOV)[1]


def det(cls_id, x1, y1, x2, y2, score=0.9):
    return Detection(cls_id, score, x1, y1, x2, y2)


def test_distance_estimation():
    # 长边 100px, 真实 0.76m -> 554.256*0.76/100 = 4.21m
    d = det(CLS_TL_RED, 310, 190, 330, 290)
    assert abs(estimate_distance_m(d, FY) - 4.2123) < 0.01


def test_red_light_stop():
    near = det(CLS_TL_RED, 310, 190, 330, 290)   # 100px -> 4.21m < 5m
    res = decide([near], fy=FY, image_width=640)
    assert res.action is Action.STOP, res
    assert res.state == "red"
    assert res.distance_m is not None and res.distance_m < 5.0


def test_red_light_slow_when_far():
    far = det(CLS_TL_RED, 310, 210, 330, 250)    # 40px -> 10.53m > 5m
    res = decide([far], fy=FY, image_width=640)
    assert res.action is Action.SLOW, res
    assert 0.0 < res.speed_scale < 1.0


def test_green_light_go():
    res = decide([det(CLS_TL_GREEN, 310, 190, 330, 290)], fy=FY, image_width=640)
    assert res.action is Action.GO, res
    assert res.state == "green"
    assert res.speed_scale == 1.0


def test_no_detection_go():
    res = decide([], fy=FY, image_width=640)
    assert res.action is Action.GO
    assert res.state is None and res.distance_m is None


def test_traffic_light_outside_roi_ignored():
    """画面最左边缘的红灯与本车无关，不应触发停车。"""
    left = det(CLS_TL_RED, 30, 190, 50, 290)     # 中心 x=40，ROI=0.6 -> [128,512]
    res = decide([left], fy=FY, image_width=640, roi_x_ratio=0.6)
    assert res.action is Action.GO, res


def test_low_score_ignored():
    weak = det(CLS_TL_RED, 310, 190, 330, 290, score=0.1)
    res = decide([weak], fy=FY, image_width=640, min_score=0.35)
    assert res.action is Action.GO, res


def test_nearest_light_wins_by_area():
    """画面里同时有远近两个红灯时，应取面积更大的（更近的）那个。"""
    small = det(CLS_TL_RED, 300, 220, 340, 245)   # 面积 40*25=1000
    big = det(CLS_TL_RED, 250, 150, 390, 330)     # 面积 140*180=25200
    res = decide([small, big], fy=FY, image_width=640)
    assert res.action is Action.STOP, res
    assert res.distance_m is not None and res.distance_m < 5.0


def test_yellow_policies():
    far = det(CLS_TL_YELLOW, 310, 210, 330, 250)     # 10.53m
    near = det(CLS_TL_YELLOW, 300, 150, 340, 300)    # 150px -> 2.81m

    assert decide([far], fy=FY, image_width=640, yellow_policy="stop_if_far").action is Action.STOP
    assert decide([near], fy=FY, image_width=640, yellow_policy="stop_if_far").action is Action.GO
    assert decide([far], fy=FY, image_width=640, yellow_policy="always_stop").action is Action.STOP


def test_person_does_not_affect_traffic_rule():
    """人偶不参与红绿灯决策（除非显式开启 avoid_person）。"""
    res = decide([det(CLS_PERSON, 300, 150, 340, 330)], fy=FY, image_width=640)
    assert res.action is Action.GO, res


def test_scale_twist():
    assert scale_twist(0.5, 1.0, 0.0) == (0.0, 0.0), "停车时角速度必须一起归零"
    vx, wz = scale_twist(1.0, 0.5, 0.25)
    assert abs(vx - 0.25) < 1e-9 and abs(wz - 0.5) < 1e-9


# ---------------------------------------------------------------- 4. 消息契约


def test_message_roundtrip():
    """推理节点与控制节点之间靠 JSON 通信，这条契约必须稳。"""
    from smart_community_perception.messages import (
        detections_from_json,
        detections_to_json,
        dominant_traffic_state,
        empty_detections_json,
        summarize,
    )

    dets = [
        det(CLS_TL_RED, 310.0, 190.0, 330.0, 290.0, score=0.87),
        det(CLS_PERSON, 100.0, 100.0, 150.0, 300.0, score=0.61),
    ]
    payload = detections_to_json(
        dets, image_width=640, image_height=480, stamp=12.5, inference_ms=33.3
    )
    back, meta = detections_from_json(payload)

    assert len(back) == 2, back
    assert back[0].cls_id == CLS_TL_RED
    assert abs(back[0].x2 - 330.0) < 1e-9
    assert back[0].class_name == "traffic_light_red"
    assert abs(back[0].score - 0.87) < 1e-6
    assert meta["image_width"] == 640 and meta["image_height"] == 480
    assert abs(meta["stamp"] - 12.5) < 1e-9

    assert dominant_traffic_state(back) == "red"
    summary = summarize(back)
    assert "traffic_light_red" in summary and "person" in summary


def test_empty_message_is_valid():
    from smart_community_perception.messages import (
        detections_from_json,
        empty_detections_json,
    )

    payload = empty_detections_json(image_width=640, image_height=480, stamp=0.0)
    dets, meta = detections_from_json(payload)
    assert dets == []
    assert meta["class_names"][0] == "traffic_light_red"


def test_unknown_schema_rejected():
    from smart_community_perception.messages import detections_from_json

    try:
        detections_from_json('{"schema": "something.else/9", "detections": []}')
    except ValueError:
        return
    raise AssertionError("未知 schema 应该抛 ValueError，避免静默错配")


# ---------------------------------------------------------------- runner


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed: list[tuple[str, str]] = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"  FAIL  {name}\n        {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
