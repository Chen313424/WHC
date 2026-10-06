#!/usr/bin/env python3
"""巡检结果 JSON 的离线自检（不需要 ROS / Gazebo / 相机）。

跑法::

    cd src/smart_community_perception
    python test/test_result_payload.py

被测的是 ``messages.build_result_payload`` —— 一个纯函数（吃 detections +
task/target/zone/slot + 可选的 PlateResult，吐 dict），所以「巡检到底会收到
什么 JSON」可以在这里逐字段钉死。覆盖：

  * 三条任务（outsider_detect / crowd_count / plate_ocr）的正常分支
  * 每条任务的缺数据分支（还没出图 / 没检测到车牌 / 字符识别失败 / 模板库为空）
  * 巡检契约里几个必须成立的细节：schema/waypoint_id/task/stamp/ok 一定在，
    slot 数字与字符串都原样回传，``ok=false`` 一定带非空 reason
  * 结果必须能 ``json.dumps``（中文车牌、numpy 浮点分数不能泄漏进 JSON）
  * 红绿灯状态文本必须是大写（巡检只认 'GREEN'），没灯时是 'UNKNOWN'

只依赖 numpy（不需要 Pillow / ultralytics），可以离线跑。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from smart_community_perception.messages import (  # noqa: E402
    RESULT_SCHEMA,
    TASK_CROWD_COUNT,
    TASK_OUTSIDER_DETECT,
    TASK_PLATE_OCR,
    UNKNOWN_LIGHT_STATE,
    best_plate_detection,
    build_result_payload,
    count_class,
    normalize_slot,
    target_to_class,
    traffic_light_state_text,
)
from smart_community_perception.traffic_rules import (  # noqa: E402
    CLASS_NAMES,
    CLS_PERSON_COMMUNITY,
    CLS_PERSON_NONCOMMUNITY,
    CLS_PLATE,
    CLS_TL_GREEN,
    CLS_TL_RED,
    CLS_TL_YELLOW,
    Detection,
)

_RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _RESULTS.append((bool(cond), name, detail))


def det(cls_id: int, score: float, box=(10.0, 10.0, 50.0, 90.0)) -> Detection:
    x1, y1, x2, y2 = box
    return Detection(cls_id, score, x1, y1, x2, y2)


class FakePlate:
    """PlateResult 替身：节点传的是真 PlateResult，这里只要同形就够了。

    （真类在 plate_ocr 里，构造它需要贴图，离线用例不该依赖那个。）
    """

    def __init__(self, text: str, score: float, mode: str = "glyph", chars=None) -> None:
        self.text = text
        self.score = score
        self.mode = mode
        self.chars = list(text) if chars is None else list(chars)


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def main() -> int:
    # ---------------- 1. 公共契约字段 ----------------
    base = build_result_payload(
        [det(CLS_TL_GREEN, 0.9)],
        task=TASK_OUTSIDER_DETECT,
        waypoint_id="wp_3",
        zone="A",
        slot="2",
        target="person_f1",
        stamp=12.34,
    )
    check(
        "必含字段 schema/waypoint_id/task/stamp/ok",
        all(k in base for k in ("schema", "waypoint_id", "task", "stamp", "ok")),
        f"keys={sorted(base)}",
    )
    check(
        "schema 固定为 whc.result/1",
        base["schema"] == RESULT_SCHEMA == "whc.result/1",
        f"{base['schema']!r}",
    )
    check(
        "waypoint_id / task / stamp 原样回传",
        base["waypoint_id"] == "wp_3"
        and base["task"] == TASK_OUTSIDER_DETECT
        and abs(base["stamp"] - 12.34) < 1e-9,
        f"{base['waypoint_id']!r} {base['task']!r} {base['stamp']!r}",
    )
    check("zone 原样回传", base["zone"] == "A", f"{base['zone']!r}")

    slot_int = build_result_payload([], task=TASK_CROWD_COUNT, slot=2)
    slot_str = build_result_payload([], task=TASK_CROWD_COUNT, slot="3")
    check(
        "slot 数字原样回传（2 而不是 '2'）",
        slot_int["slot"] == 2 and isinstance(slot_int["slot"], int),
        repr(slot_int["slot"]),
    )
    check("slot 字符串原样回传", slot_str["slot"] == "3", repr(slot_str["slot"]))
    check(
        "slot=None 归一成空串（JSON 里不能是 null）",
        normalize_slot(None) == "",
        repr(normalize_slot(None)),
    )
    check(
        "空 detections 列表 = 有数据（ok=True），不等于没有数据",
        build_result_payload([], task=TASK_CROWD_COUNT)["ok"] is True,
        dumps(build_result_payload([], task=TASK_CROWD_COUNT)),
    )

    # ---------------- 2. outsider_detect ----------------
    dets = [
        det(CLS_PERSON_COMMUNITY, 0.90),
        det(CLS_PERSON_COMMUNITY, 0.55),
        det(CLS_PERSON_NONCOMMUNITY, 0.80),
        det(CLS_TL_GREEN, 0.99),            # 红绿灯不能被人偶计数带进去
        det(CLS_PERSON_COMMUNITY, 0.10),    # 低于阈值，不计
    ]
    p = build_result_payload(
        dets, task=TASK_OUTSIDER_DETECT, target="person_f1", slot="2", min_score=0.35
    )
    check(
        "outsider_detect: 社区人数（含低分过滤 + 类别过滤）",
        p["person_community"] == 2,
        f"得到 {p['person_community']}，期望 2",
    )
    check(
        "outsider_detect: 非社区人数",
        p["person_noncommunity"] == 1,
        f"得到 {p['person_noncommunity']}，期望 1",
    )
    check(
        "outsider_detect: target=person_f1 -> person_noncommunity",
        p["target_hint"] == "person_noncommunity",
        f"{p['target_hint']!r}",
    )
    check("outsider_detect: target_found=True", p["target_found"] is True, dumps(p))
    check(
        "outsider_detect: 有数据就算 ok=True",
        p["ok"] is True,
        dumps(p),
    )
    check("outsider_detect: 结果可 JSON 序列化", isinstance(dumps(p), str), dumps(p))

    p2 = build_result_payload(dets, task=TASK_OUTSIDER_DETECT, target="person_a1")
    check(
        "outsider_detect: 不含 _f 的 target -> person_community",
        p2["target_hint"] == "person_community" and p2["target_found"] is True,
        dumps(p2),
    )

    p3 = build_result_payload(
        [det(CLS_PERSON_COMMUNITY, 0.9)], task=TASK_OUTSIDER_DETECT, target="person_f2"
    )
    check(
        "outsider_detect: 目标没出现 -> target_found=False 但 ok 仍为 True",
        p3["target_found"] is False and p3["ok"] is True,
        dumps(p3),
    )

    p4 = build_result_payload(
        [det(CLS_PERSON_COMMUNITY, 0.9)], task=TASK_OUTSIDER_DETECT, target=""
    )
    check(
        "outsider_detect: target 为空 -> target_hint=None / target_found=False",
        p4["target_hint"] is None and p4["target_found"] is False,
        dumps(p4),
    )

    p5 = build_result_payload(
        None, task=TASK_OUTSIDER_DETECT, waypoint_id="wp_1", target="person_f1", stamp=7.0
    )
    check(
        "outsider_detect: 没有图像/检测 -> ok=False + reason",
        p5["ok"] is False and bool(p5["reason"]),
        dumps(p5),
    )
    check(
        "outsider_detect: 没数据时契约字段一个不少",
        p5["schema"] == RESULT_SCHEMA
        and p5["task"] == TASK_OUTSIDER_DETECT
        and p5["waypoint_id"] == "wp_1"
        and abs(p5["stamp"] - 7.0) < 1e-9
        and "ok" in p5,
        dumps(p5),
    )

    check(
        "target_to_class: person_f1 -> 非社区",
        target_to_class("person_f1") == "person_noncommunity",
        f"{target_to_class('person_f1')!r}",
    )
    check(
        "target_to_class: person_s6 / person_b3 -> 社区",
        target_to_class("person_s6") == "person_community"
        and target_to_class("person_b3") == "person_community",
        f"{target_to_class('person_s6')!r}",
    )
    check(
        "target_to_class: 空/None -> None（不瞎猜）",
        target_to_class(None) is None and target_to_class("") is None,
        f"{target_to_class(None)!r}",
    )
    check(
        "两类人偶的名字来自 traffic_rules，不是硬编码",
        CLASS_NAMES[CLS_PERSON_COMMUNITY] == "person_community"
        and CLASS_NAMES[CLS_PERSON_NONCOMMUNITY] == "person_noncommunity",
        f"{CLASS_NAMES}",
    )
    check(
        "count_class: 只数指定类别且尊重阈值",
        count_class(dets, CLS_PERSON_COMMUNITY) == 3
        and count_class(dets, CLS_PERSON_COMMUNITY, 0.35) == 2
        and count_class(dets, CLS_TL_GREEN) == 1
        and count_class(None, CLS_PERSON_COMMUNITY) == 0,
        f"{count_class(dets, CLS_PERSON_COMMUNITY)}/{count_class(dets, CLS_PERSON_COMMUNITY, 0.35)}",
    )

    # ---------------- 3. crowd_count ----------------
    c = build_result_payload(dets, task=TASK_CROWD_COUNT, slot=3, min_score=0.35)
    check(
        "crowd_count: 两类数量",
        (c["person_community"], c["person_noncommunity"]) == (2, 1),
        f"{c['person_community']}/{c['person_noncommunity']}",
    )
    check("crowd_count: person_total = 两类之和", c["person_total"] == 3, dumps(c))
    check("crowd_count: ok=True", c["ok"] is True, dumps(c))

    c0 = build_result_payload([], task=TASK_CROWD_COUNT)
    check(
        "crowd_count: 画面里没有人 -> 全 0 且 ok=True",
        c0["person_total"] == 0 and c0["ok"] is True,
        dumps(c0),
    )

    c_low = build_result_payload(dets, task=TASK_CROWD_COUNT, min_score=0.95)
    check(
        "crowd_count: 提高阈值后低于阈值的都不计数",
        c_low["person_total"] == 0,
        dumps(c_low),
    )

    c_none = build_result_payload(None, task=TASK_CROWD_COUNT)
    check(
        "crowd_count: 没有图像/检测 -> ok=False + reason",
        c_none["ok"] is False and bool(c_none["reason"]),
        dumps(c_none),
    )

    # ---------------- 4. plate_ocr ----------------
    plate_dets = [
        det(CLS_PLATE, 0.60, (10.0, 20.0, 110.0, 60.0)),
        det(CLS_PLATE, 0.88, (200.0, 200.0, 300.0, 240.0)),
        det(CLS_PERSON_COMMUNITY, 0.99),
    ]
    best = best_plate_detection(plate_dets, 0.35)
    check(
        "plate_ocr: 选置信度最高的车牌框",
        best is not None and abs(best.x1 - 200.0) < 1e-9 and abs(best.score - 0.88) < 1e-9,
        f"{best}",
    )
    check(
        "plate_ocr: 阈值之上没有车牌框 -> None",
        best_plate_detection(plate_dets, 0.9) is None,
        f"{best_plate_detection(plate_dets, 0.9)}",
    )

    pr = FakePlate("苏A·B8Q62", 0.91, "glyph")
    pl = build_result_payload(
        plate_dets,
        task=TASK_PLATE_OCR,
        zone="B",
        slot=2,
        min_score=0.35,
        plate_result=pr,
        frame_stamp=99.5,
    )
    check("plate_ocr: ok=True", pl["ok"] is True, dumps(pl))
    check("plate_ocr: plate 字符串", pl["plate"] == "苏A·B8Q62", f"{pl['plate']!r}")
    check(
        "plate_ocr: plate_chars 逐字列表",
        pl["plate_chars"] == ["苏", "A", "·", "B", "8", "Q", "6", "2"],
        f"{pl['plate_chars']!r}",
    )
    check("plate_ocr: plate_score 为 OCR 得分", abs(pl["plate_score"] - 0.91) < 1e-9, f"{pl['plate_score']!r}")
    check("plate_ocr: slot 原样回传", pl["slot"] == 2, repr(pl["slot"]))
    check(
        "plate_ocr: 用最高分那个框（200,200,300,240）",
        pl["plate_bbox"] == [200.0, 200.0, 300.0, 240.0]
        and abs(pl["plate_detect_score"] - 0.88) < 1e-9,
        f"{pl.get('plate_bbox')} {pl.get('plate_detect_score')}",
    )
    check("plate_ocr: 帧时间戳带上（便于对回是哪一帧）", abs(pl["frame_stamp"] - 99.5) < 1e-9, f"{pl['frame_stamp']!r}")
    check(
        "plate_ocr: 中文车牌能 json.dumps 且不转义",
        "苏A·B8Q62" in dumps(pl),
        dumps(pl),
    )

    no_plate = build_result_payload(
        [det(CLS_PERSON_COMMUNITY, 0.9)], task=TASK_PLATE_OCR, slot=3, plate_result=pr
    )
    check(
        "plate_ocr: 没有检测到车牌 -> ok=False + reason",
        no_plate["ok"] is False and "车牌" in no_plate["reason"],
        dumps(no_plate),
    )

    ocr_fail = build_result_payload(
        plate_dets, task=TASK_PLATE_OCR, slot=3, plate_result=FakePlate("", 0.0, "none")
    )
    check(
        "plate_ocr: 字符识别失败 -> ok=False + reason，字段仍在",
        ocr_fail["ok"] is False
        and bool(ocr_fail["reason"])
        and ocr_fail["plate"] == ""
        and ocr_fail["plate_chars"] == []
        and ocr_fail["plate_mode"] == "none",
        dumps(ocr_fail),
    )

    no_mod = build_result_payload(
        plate_dets,
        task=TASK_PLATE_OCR,
        slot=3,
        plate_result=None,
        reason="车牌模板库为空（贴图目录 /nowhere）",
    )
    check(
        "plate_ocr: OCR 模块不可用 -> ok=False 且透传具体原因",
        no_mod["ok"] is False and no_mod["reason"].startswith("车牌模板库为空"),
        dumps(no_mod),
    )

    no_data = build_result_payload(None, task=TASK_PLATE_OCR, slot=3)
    check(
        "plate_ocr: 没有图像/检测 -> ok=False + reason",
        no_data["ok"] is False and bool(no_data["reason"]),
        dumps(no_data),
    )

    class NumpyPlate:
        """模拟真 PlateResult：score 是 numpy 标量（直接进 json 会 TypeError）。"""

        text = "苏A·B8Q62"
        chars = ["苏", "A", "·", "B", "8", "Q", "6", "2"]
        mode = "glyph"
        score = np.float32(0.87)

    try:
        np_payload = build_result_payload(
            plate_dets, task=TASK_PLATE_OCR, slot="3", plate_result=NumpyPlate()
        )
        dumps(np_payload)
        np_ok, np_detail = True, f"plate_score={np_payload['plate_score']!r}"
    except TypeError as exc:
        np_ok, np_detail = False, str(exc)
    check("plate_ocr: numpy 分数不泄漏进 JSON", np_ok, np_detail)

    # ---------------- 5. 未知 task / 红绿灯状态 ----------------
    unk = build_result_payload([det(CLS_PERSON_COMMUNITY, 0.9)], task="walk_dog")
    check(
        "未知 task -> ok=False + reason（站点表写错也要有回应）",
        unk["ok"] is False and "walk_dog" in unk["reason"],
        dumps(unk),
    )
    check(
        "task 缺失 -> ok=False + reason",
        build_result_payload([])["ok"] is False and bool(build_result_payload([])["reason"]),
        dumps(build_result_payload([])),
    )

    check(
        "灯态: GREEN 映射成大写（巡检只认 'GREEN'）",
        traffic_light_state_text([det(CLS_TL_GREEN, 0.9)]) == "GREEN",
        traffic_light_state_text([det(CLS_TL_GREEN, 0.9)]),
    )
    check(
        "灯态: RED / YELLOW",
        traffic_light_state_text([det(CLS_TL_RED, 0.9)]) == "RED"
        and traffic_light_state_text([det(CLS_TL_YELLOW, 0.9)]) == "YELLOW",
        f"{traffic_light_state_text([det(CLS_TL_RED, 0.9)])}/"
        f"{traffic_light_state_text([det(CLS_TL_YELLOW, 0.9)])}",
    )
    check(
        "灯态: 没有红绿灯 -> UNKNOWN（不能是空串/None）",
        traffic_light_state_text([]) == UNKNOWN_LIGHT_STATE == "UNKNOWN"
        and traffic_light_state_text(None) == "UNKNOWN"
        and traffic_light_state_text([det(CLS_PERSON_COMMUNITY, 0.9)]) == "UNKNOWN",
        f"{traffic_light_state_text([])!r}",
    )
    check(
        "灯态: 画面里两组灯时取面积最大的那个",
        traffic_light_state_text(
            [
                det(CLS_TL_RED, 0.9, (10.0, 10.0, 60.0, 200.0)),      # 面积 50*190
                det(CLS_TL_GREEN, 0.9, (100.0, 100.0, 110.0, 120.0)),  # 面积 10*20
            ]
        )
        == "RED",
        traffic_light_state_text(
            [det(CLS_TL_RED, 0.9, (10.0, 10.0, 60.0, 200.0)), det(CLS_TL_GREEN, 0.9)]
        ),
    )

    return _report()


def _report() -> int:
    ok = sum(1 for r in _RESULTS if r[0])
    print()
    for passed, name, detail in _RESULTS:
        mark = "PASS" if passed else "FAIL"
        line = f"  {mark}  {name}"
        print(line + (f"\n        {detail}" if detail and not passed else ""))
    print(f"\n{ok}/{len(_RESULTS)} 通过")
    return 0 if ok == len(_RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
