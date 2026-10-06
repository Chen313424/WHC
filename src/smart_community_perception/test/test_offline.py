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
    CLS_PERSON_COMMUNITY,
    CLS_PERSON_NONCOMMUNITY,
    CLS_PLATE,
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
    """必须与 TrafficLightSystem.cc 的 绿15/黄5/红10（周期 30s）完全一致。"""
    cases = [
        (0.0, "green"),
        (7.5, "green"),
        (14.999, "green"),
        (15.0, "yellow"),
        (18.0, "yellow"),    # 15 <= t < 20 都是黄灯
        (19.999, "yellow"),
        (20.0, "red"),
        (29.999, "red"),
        (30.0, "green"),     # 一个周期
        (45.0, "yellow"),    # 30 + 15
        (49.999, "yellow"),
        (50.0, "red"),       # 30 + 20
        (60.0, "green"),     # 两个周期
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

    spawn 是 world->odom（与 launch 的 x/y/z/Y 一致；重建后场地为 4.2m 见方）。
    机器人放到场地右下角 (0.6, -1.3) 朝北(+Y)——回字形环线最右侧竖直道路的
    主行驶方向；相机外参 (前 0.15、上 0.05 轮半径 + 0.10) 合成后，
    相机光心应落在 world (0.6, -1.15, 0.20)。

    traffic_light_1 的箱体中心在 world (-0.6, 1.573, 0.42)：
    相机左前方（西 1.2m、北 2.72m、高 0.22m），水平偏角 23.8°，
    投影中心 (u, v) ≈ (75.4, 194.4)，最近角点深度 2.675m。
    """
    spawn = [1.3, 1.3, 0.05, 0.0, 0.0, -1.5708]           # 与 launch 一致
    world_T_robot = pose_to_matrix([0.6, -1.3, 0.05, 0.0, 0.0, math.pi / 2])
    odom_T_robot = invert(pose_to_matrix(spawn)) @ world_T_robot
    odom_pose6 = matrix_to_pose(odom_T_robot)

    # 链条中段：/odom 读数是机器人相对 spawn 的位姿。
    # 场地右下角 (0.6,-1.3) 相对 spawn (1.3,1.3) = 前进 2.6m、右移 0.7m、掉头 180°。
    assert abs(odom_pose6[0] - 2.6) < 1e-4, odom_pose6
    assert abs(odom_pose6[1] + 0.7) < 1e-4, odom_pose6
    assert abs(abs(odom_pose6[5]) - math.pi) < 1e-4, odom_pose6

    T = world_to_camera(spawn, odom_pose6, 0.05, [0.15, 0.0, 0.10, 0.0, 0.0, 0.0])
    cam_pos = camera_center_in_world(T)
    assert abs(cam_pos[0] - 0.6) < 1e-6, cam_pos
    assert abs(cam_pos[1] + 1.15) < 1e-6, cam_pos
    assert abs(cam_pos[2] - 0.20) < 1e-6, cam_pos

    objects = load_objects(OBJECTS_YAML)
    tl1 = find_object(objects, "traffic_light_1")
    K = intrinsics_from_hfov(640, 480, HFOV)

    bbox, depth = aabb_to_bbox(
        T, tl1["model_pose"], tl1["center"], tl1["size"], K, 640, 480, min_visible=0.3
    )
    assert bbox is not None, "红绿灯在前方 2.7m 却没被投影出来，坐标链有问题"
    x1, y1, x2, y2 = bbox
    # 整个灯箱必须完整落在画面内
    assert 0.0 < x1 < x2 < 640.0 and 0.0 < y1 < y2 < 480.0, bbox
    # 水平方向：灯在车体左前方（西 1.2m / 北 2.72m），故框心在画面左侧 1/4 处
    assert abs((x1 + x2) / 2.0 - 75.4) < 2.0, bbox
    # 竖直方向：箱体顶 0.715m、底 0.125m，相机高 0.20m -> v 跨 133.3 ~ 255.5
    assert abs(y1 - 133.3) < 2.5, bbox
    assert abs(y2 - 255.5) < 2.5, bbox
    # 箱体 0.14 宽 / 0.096 深，2.7m 处斜视 -> 约 37px（只按正视算会是 28.9px）
    assert 34.0 < (x2 - x1) < 40.0, bbox
    # 最近角点深度 = 2.675m（相机到箱体近侧面）
    assert abs(depth - 2.675) < 0.01, depth

    # 距离估计：长边 122px 正好是 0.59m 的灯箱高，估回来还是同一个 2.675m
    det = Detection(CLS_TL_RED, 0.9, x1, y1, x2, y2)
    dist = estimate_distance_m(det, K[1])
    assert dist is not None and abs(dist - 2.675) < 0.05, dist


def world_aabb(obj: dict) -> tuple[list[float], list[float]]:
    """按 YAML 语义「世界角点 = model_pose 变换 (center ± size/2)」算出世界系 AABB。

    返回 (min_xyz, max_xyz)，用来把生成的真值框拉回 SDF 手算的世界坐标核对。
    """
    t = pose_to_matrix(obj["model_pose"])
    c = np.array(obj["center"], dtype=float)
    h = np.array(obj["size"], dtype=float) / 2.0
    pts = [
        (t @ np.append(c + np.array([sx, sy, sz]) * h, 1.0))[:3]
        for sx in (-1.0, 1.0)
        for sy in (-1.0, 1.0)
        for sz in (-1.0, 1.0)
    ]
    arr = np.array(pts)
    return arr.min(axis=0).tolist(), arr.max(axis=0).tolist()


def test_generated_world_objects():
    """校验 sdf_to_objects.py 的产物与重建后的 SDF 手算一致。

    重建场地（4.2m×4.2m）真值目标共 23 个：
      2 组红绿灯 + 16 社区人立牌 + 2 非社区人立牌 + 3 车牌。
    数量、类别、模型位姿、局部包围盒都要能逐项对回
    smart_community.sdf 里的 <pose> 与 <size>，否则自动标注会把框画歪。
    """
    objects = load_objects(OBJECTS_YAML)
    classes = {}
    for o in objects:
        classes[o["class"]] = classes.get(o["class"], 0) + 1
    assert classes.get("traffic_light") == 2, classes
    assert classes.get("person_community") == 16, classes
    assert classes.get("person_noncommunity") == 2, classes
    assert classes.get("license_plate") == 3, classes
    assert len(objects) == 23, len(objects)

    # 社区/非社区的划分必须与贴图一致（赛题要辨非社区人员，分错类整条链路都错）
    community = {o["name"] for o in objects if o["class"] == "person_community"}
    assert community == (
        {f"person_a{i}" for i in range(1, 6)}
        | {f"person_b{i}" for i in range(1, 6)}
        | {f"person_s{i}" for i in range(1, 7)}
    ), community
    noncommunity = {o["name"] for o in objects if o["class"] == "person_noncommunity"}
    assert noncommunity == {"person_f1", "person_f2"}, noncommunity

    # ---- traffic_light_1（竖排）：SDF pose "-0.6 1.55 0 0 0 0" ----
    # housing box(0.14,0.05,0.59) @ z=0.42 + 三颗 r=0.045 灯球 @ y=0.026
    # -> 局部包围盒 center y=0.023 / z=0.42，size (0.14, 0.096, 0.59)（不含灯腿）
    tl1 = find_object(objects, "traffic_light_1")
    assert all(
        abs(a - b) < 1e-6 for a, b in zip(tl1["model_pose"], [-0.6, 1.55, 0.0, 0.0, 0.0, 0.0])
    ), tl1
    assert all(abs(a - b) < 1e-6 for a, b in zip(tl1["size"], [0.14, 0.096, 0.59])), tl1
    assert abs(tl1["center"][1] - 0.023) < 1e-6, tl1
    assert abs(tl1["center"][2] - 0.42) < 1e-6, tl1
    lo, hi = world_aabb(tl1)
    # 世界系：x=-0.6±0.07, y=1.55+0.023±0.048, z=0.42±0.295
    assert all(abs(a - b) < 1e-6 for a, b in zip(lo, [-0.67, 1.525, 0.125])), (lo, hi)
    assert all(abs(a - b) < 1e-6 for a, b in zip(hi, [-0.53, 1.621, 0.715])), (lo, hi)

    # ---- traffic_light_2（横排）：SDF pose "-0.4 -1.55 0 0 0 3.14159" ----
    # housing box(0.59,0.05,0.14) @ z=0.41 -> size (0.59, 0.096, 0.14)
    tl2 = find_object(objects, "traffic_light_2")
    assert all(
        abs(a - b) < 1e-5
        for a, b in zip(tl2["model_pose"], [-0.4, -1.55, 0.0, 0.0, 0.0, 3.14159])
    ), tl2
    assert all(abs(a - b) < 1e-6 for a, b in zip(tl2["size"], [0.59, 0.096, 0.14])), tl2
    assert abs(tl2["center"][2] - 0.41) < 1e-6, tl2
    lo, hi = world_aabb(tl2)
    # 绕 Z 转 180°：局部 +y（灯球朝向）反到世界 -y
    assert all(abs(a - b) < 1e-5 for a, b in zip(lo, [-0.695, -1.621, 0.34])), (lo, hi)
    assert all(abs(a - b) < 1e-5 for a, b in zip(hi, [-0.105, -1.525, 0.48])), (lo, hi)

    # ---- 人偶立牌：官方规格 高15cm × 宽5cm × 厚0.5cm，贴地摆放 ----
    person = find_object(objects, "person_a1")
    assert all(abs(a - b) < 1e-6 for a, b in zip(person["size"], [0.05, 0.005, 0.15])), person
    assert abs(person["model_pose"][2] - 0.075) < 1e-6, person       # 立牌中心离地 7.5cm
    lo, hi = world_aabb(person)
    assert abs(lo[2] - 0.0) < 1e-6 and abs(hi[2] - 0.15) < 1e-6, (lo, hi)
    # A 区中线 y=0.625，立牌厚 0.5cm
    assert abs(lo[1] - 0.6225) < 1e-6 and abs(hi[1] - 0.6275) < 1e-6, (lo, hi)
    assert abs(lo[0] + 1.105) < 1e-6 and abs(hi[0] + 1.055) < 1e-6, (lo, hi)

    # ---- 车牌：官方规格 9.5×3cm，贴在车背景立牌正面（局部 -0.08, 0.0035, -0.09）----
    for name, py in (("car_1/plate", -1.75), ("car_2/plate", -1.15), ("car_3/plate", -0.55)):
        plate = find_object(objects, name)
        assert all(abs(a - b) < 1e-6 for a, b in zip(plate["size"], [0.095, 0.002, 0.03])), plate
        assert abs(plate["model_pose"][0] - 1.7) < 1e-6, plate
        assert abs(plate["model_pose"][1] - py) < 1e-6, plate
        assert abs(plate["model_pose"][2] - 0.125) < 1e-6, plate     # 车背景立牌中心高
        assert all(
            abs(a - b) < 1e-6 for a, b in zip(plate["center"], [-0.08, 0.0035, -0.09])
        ), plate
        lo, hi = world_aabb(plate)
        # 车牌世界系中心 = (1.62, py+0.0035, 0.035)
        assert abs((lo[0] + hi[0]) / 2.0 - 1.62) < 1e-6, (lo, hi)
        assert abs((lo[1] + hi[1]) / 2.0 - (py + 0.0035)) < 1e-6, (lo, hi)
        assert abs((lo[2] + hi[2]) / 2.0 - 0.035) < 1e-6, (lo, hi)


# ---------------------------------------------------------------- 3. 决策逻辑

FY = intrinsics_from_hfov(640, 480, HFOV)[1]


def det(cls_id, x1, y1, x2, y2, score=0.9):
    return Detection(cls_id, score, x1, y1, x2, y2)


def test_distance_estimation():
    # 同一像素长边(100px)下，估计距离与目标真实长边成正比：Z = fy * extent / 100
    #   灯箱 0.59m  -> 554.2563*0.59/100 = 3.2701m
    #   人偶 0.15m  -> 0.8314m
    #   车牌 0.095m -> 0.5265m
    # 类别不同、框一样大，距离必须拉开差距（extent 表错了这里立刻就会暴露）
    same_box = (310, 190, 330, 290)           # 100px 长边
    assert abs(estimate_distance_m(det(CLS_TL_RED, *same_box), FY) - 3.2701) < 0.01
    assert abs(estimate_distance_m(det(CLS_TL_YELLOW, *same_box), FY) - 3.2701) < 0.01
    assert abs(estimate_distance_m(det(CLS_TL_GREEN, *same_box), FY) - 3.2701) < 0.01
    assert abs(estimate_distance_m(det(CLS_PERSON_COMMUNITY, *same_box), FY) - 0.8314) < 0.01
    assert abs(estimate_distance_m(det(CLS_PERSON_NONCOMMUNITY, *same_box), FY) - 0.8314) < 0.01
    assert abs(estimate_distance_m(det(CLS_PLATE, *same_box), FY) - 0.5265) < 0.01


def test_red_light_stop():
    near = det(CLS_TL_RED, 310, 190, 330, 290)   # 100px -> 3.27m < 5m
    res = decide([near], fy=FY, image_width=640)
    assert res.action is Action.STOP, res
    assert res.state == "red"
    assert res.distance_m is not None and res.distance_m < 5.0


def test_red_light_slow_when_far():
    far = det(CLS_TL_RED, 310, 210, 330, 250)    # 40px -> 8.18m > 5m
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
    far = det(CLS_TL_YELLOW, 310, 210, 330, 250)     # 40px -> 8.18m
    near = det(CLS_TL_YELLOW, 300, 150, 340, 300)    # 150px -> 2.18m

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
