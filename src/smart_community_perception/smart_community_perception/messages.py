"""检测结果的 JSON 编解码。

为什么不自定义 .msg
-------------------
自定义消息需要额外的 rosidl 接口包（ament_cmake + 生成代码），
会让「装好就能跑」变得麻烦。这里用 ``std_msgs/String`` + JSON，
零额外依赖，同时保留类别名、分数和归一化/像素两套坐标，
调试时 ``ros2 topic echo`` 直接可读。

如果确实需要标准消息，``yolo_detector_node`` 会在 ``vision_msgs``
可用时额外发布一份 ``Detection2DArray``（可选，不影响主链路）。
"""
from __future__ import annotations

import json
from typing import Iterable, Sequence

from .traffic_rules import (
    CLASS_NAMES,
    CLS_PERSON_COMMUNITY,
    CLS_PERSON_NONCOMMUNITY,
    CLS_PLATE,
    Detection,
    STATE_GREEN,
    STATE_RED,
    STATE_YELLOW,
    TL_CLASS_TO_STATE,
)

SCHEMA = "whc.detections/1"

#: 巡检结果（/detection/result）的 schema，与检测流量的 SCHEMA 分开，
#: 避免两个话题的消息被当成同一种东西解析。
RESULT_SCHEMA = "whc.result/1"


def detections_to_json(
    detections: Iterable[Detection],
    *,
    image_width: int,
    image_height: int,
    stamp: float,
    inference_ms: float | None = None,
) -> str:
    payload = {
        "schema": SCHEMA,
        "stamp": round(float(stamp), 6),
        "image_width": int(image_width),
        "image_height": int(image_height),
        "inference_ms": None if inference_ms is None else round(float(inference_ms), 3),
        "class_names": CLASS_NAMES,
        "detections": [
            {
                "cls_id": int(d.cls_id),
                "class_name": d.class_name,
                "score": round(float(d.score), 5),
                "bbox": [
                    round(float(d.x1), 2),
                    round(float(d.y1), 2),
                    round(float(d.x2), 2),
                    round(float(d.y2), 2),
                ],
            }
            for d in detections
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def empty_detections_json(
    *, image_width: int, image_height: int, stamp: float, note: str = ""
) -> str:
    payload = {
        "schema": SCHEMA,
        "stamp": round(float(stamp), 6),
        "image_width": int(image_width),
        "image_height": int(image_height),
        "inference_ms": None,
        "class_names": CLASS_NAMES,
        "detections": [],
        "note": note,
    }
    return json.dumps(payload, ensure_ascii=False)


def detections_from_json(data: str) -> tuple[list[Detection], dict]:
    """返回 (detections, meta)；解析失败时抛出 ValueError。"""
    payload = json.loads(data)
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError(f"未知的检测消息 schema: {payload.get('schema')!r}")

    dets: list[Detection] = []
    for item in payload.get("detections", []):
        bbox = item.get("bbox") or [0, 0, 0, 0]
        if len(bbox) != 4:
            continue
        dets.append(
            Detection(
                cls_id=int(item.get("cls_id", -1)),
                score=float(item.get("score", 0.0)),
                x1=float(bbox[0]),
                y1=float(bbox[1]),
                x2=float(bbox[2]),
                y2=float(bbox[3]),
            )
        )
    return dets, payload


def summarize(detections: Sequence[Detection]) -> str:
    """一小段人类可读的摘要，便于日志。"""
    if not detections:
        return "(无检测)"
    counts: dict[str, int] = {}
    for d in detections:
        counts[d.class_name] = counts.get(d.class_name, 0) + 1
    return ", ".join(f"{k}x{v}" for k, v in sorted(counts.items()))


def dominant_traffic_state(detections: Sequence[Detection]) -> str | None:
    """返回画面中面积最大的红绿灯状态，供可视化/日志用。"""
    best_state, best_area = None, -1.0
    for d in detections:
        state = TL_CLASS_TO_STATE.get(d.cls_id)
        if state is None:
            continue
        area = d.width_px * d.height_px
        if area > best_area:
            best_area, best_state = area, state
    return best_state


# ================================================================ 巡检接口
# 导航组巡检节点（community_patrol）定义的契约：
#   * 订阅 /traffic_light/state : std_msgs/String，**只认大写 'GREEN'**
#   * 发布 /patrol/capture      : JSON {"waypoint_id","task","zone","slot","target","stamp"}
#   * 等待 /detection/result    : 收到**任意一条** String 就算本次 capture 完成
# 下面这一段全是**纯逻辑**（不依赖 ROS）：节点只负责搬数据、裁剪图像、
# 构造一次 PlateOCR，而「结果 JSON 长什么样」在这里被完全定型，
# 于是可以用离线用例逐字段钉死（见 test/test_result_payload.py）。
# ================================================================

TASK_OUTSIDER_DETECT = "outsider_detect"
TASK_CROWD_COUNT = "crowd_count"
TASK_PLATE_OCR = "plate_ocr"
PATROL_TASKS = (TASK_OUTSIDER_DETECT, TASK_CROWD_COUNT, TASK_PLATE_OCR)

#: 画面里没有红绿灯时发的状态。巡检只认 'GREEN'，其余一律继续等，
#: 但日志里 'UNKNOWN' 比空串好排查得多。
UNKNOWN_LIGHT_STATE = "UNKNOWN"

#: 两类人偶（做人数统计时都要算）。顺序固定，便于结果 JSON 可预测。
PERSON_CLASS_IDS = (CLS_PERSON_COMMUNITY, CLS_PERSON_NONCOMMUNITY)

#: 巡检 target 名里带这个标记的是「非社区人员」（世界 SDF: person_f1 / person_f2）
NONCOMMUNITY_TARGET_MARK = "_f"


def traffic_light_state_text(detections: Sequence[Detection] | None) -> str:
    """画面里红绿灯状态的大写文本；没有红绿灯 -> ``'UNKNOWN'``。

    巡检节点做的是 ``msg.data.strip().upper() != 'GREEN'`` 判断，
    所以这里**必须大写**；也绝不能返回 ``''``/``None`` ——
    空串会让日志变成「当前状态=」，排查时看不出是没灯还是话题没通。
    """
    state = dominant_traffic_state(detections or [])
    return state.upper() if state else UNKNOWN_LIGHT_STATE


def target_to_class(target: object) -> str | None:
    """巡检 target 名 -> 本项目类别名（两类人偶之一）。

    ``person_f1`` / ``person_f2`` 是画了「非社区」贴图的立牌（名字含 ``_f``），
    其余（``person_a1``、``person_b3``、``person_s6``…）都算社区人员。
    空 target 返回 ``None``（该站点没说要看谁），不瞎猜。
    """
    name = str(target or "").strip().lower()
    if not name:
        return None
    if NONCOMMUNITY_TARGET_MARK in name:
        return CLASS_NAMES[CLS_PERSON_NONCOMMUNITY]
    return CLASS_NAMES[CLS_PERSON_COMMUNITY]


def count_class(
    detections: Iterable[Detection] | None, cls_id: int, min_score: float = 0.0
) -> int:
    """按类别计数，只算 ``score >= min_score`` 的（低分误检不能进结论）。"""
    return sum(
        1 for d in (detections or []) if d.cls_id == cls_id and d.score >= min_score
    )


def count_persons(
    detections: Iterable[Detection] | None, min_score: float = 0.0
) -> dict[str, int]:
    """两类人偶的数量 -> ``{'person_community': n, 'person_noncommunity': m}``。"""
    dets = list(detections or [])   # 先落成列表：count_class 会各扫一遍
    return {
        CLASS_NAMES[cid]: count_class(dets, cid, min_score) for cid in PERSON_CLASS_IDS
    }


def best_plate_detection(
    detections: Iterable[Detection] | None, min_score: float = 0.0
) -> Detection | None:
    """置信度最高的车牌框；没有则 ``None``。

    巡检的 plate_ocr 任务要拿它去裁图，所以「选哪个框」也必须由这里说了算，
    否则节点裁的框和结果 JSON 里写的不一致。
    """
    best: Detection | None = None
    for d in detections or []:
        if d.cls_id != CLS_PLATE or d.score < min_score:
            continue
        if best is None or d.score > best.score:
            best = d
    return best


def normalize_slot(slot: object) -> object:
    """巡检的 ``slot`` 可能是 int / float / str（YAML 里写 ``2`` 或 ``"2"`` 都合法）。

    约定：数字/字符串**原样**回传，``None`` 归一成空串，其它类型转字符串。
    只保证 JSON 可序列化，不改变「原样回传」的语义。
    """
    if slot is None:
        return ""
    if isinstance(slot, bool) or not isinstance(slot, (int, float, str)):
        return str(slot)
    return slot


def _plate_fields(plate_result: object) -> dict:
    """把 PlateResult（或同形 dict）摊平成 JSON 字段。

    鸭子类型而不是 ``isinstance``：本模块不 import ``plate_ocr``
    （那会连带引入 PIL），离线用例也能塞一个三条属性的替身进来。
    """
    if plate_result is None:
        return {"plate": "", "plate_chars": [], "plate_score": 0.0, "plate_mode": "none"}
    if isinstance(plate_result, dict):
        text = str(plate_result.get("text") or "")
        chars = list(plate_result.get("chars") or [])
        score = float(plate_result.get("score") or 0.0)
        mode = str(plate_result.get("mode") or "none")
    else:
        text = str(getattr(plate_result, "text", "") or "")
        chars = list(getattr(plate_result, "chars", None) or [])
        score = float(getattr(plate_result, "score", 0.0) or 0.0)
        mode = str(getattr(plate_result, "mode", "none") or "none")
    # float() 是必需的：plate_ocr 内部用 numpy 算分，np.float32 直接丢给
    # json.dumps 会抛 TypeError。
    return {
        "plate": text,
        "plate_chars": [str(c) for c in chars],
        "plate_score": round(max(0.0, score), 5),
        "plate_mode": mode,
    }


def build_result_payload(
    detections: Sequence[Detection] | None,
    *,
    task: str = "",
    waypoint_id: object = "",
    zone: object = "",
    slot: object = "",
    target: object = "",
    stamp: float = 0.0,
    min_score: float = 0.35,
    plate_result: object = None,
    frame_stamp: float | None = None,
    reason: str = "",
) -> dict:
    """构造 ``/detection/result`` 的 payload（纯函数，不碰 ROS、不碰图像）。

    参数
    ----
    detections
        ``None`` = **还没有可用的检测结果**（节点刚起来、相机还没出图）。
        这种情况下必须回一条 ``ok=false`` + ``reason``：巡检节点等的是
        「任意一条消息」，吞掉不回应会让它每个 capture 站点都空等到超时。
        传 ``[]`` 则是「有检测管道、但这帧没东西」，属于正常结果（``ok=true``）。
    plate_result
        ``plate_ocr.PlateResult``（或同形 dict / ``None``）。裁图与字符识别
        依赖图像和 PIL，放在节点侧做；这里只负责拼 JSON。

    关于 ``ok``
    -----------
    ``ok=true`` 表示「这次任务算出来了」，不代表「找到了目标」：
    人偶数量为 0 时 ``target_found=false`` 但 ``ok`` 仍是 ``true``。
    """
    dets = list(detections) if detections is not None else None
    task_name = str(task or "").strip()

    payload: dict = {
        "schema": RESULT_SCHEMA,
        "waypoint_id": str(waypoint_id or ""),
        "task": task_name,
        "zone": str(zone or ""),
        "slot": normalize_slot(slot),
        "target": str(target or ""),
        "stamp": round(float(stamp), 6),
        "frame_stamp": None if frame_stamp is None else round(float(frame_stamp), 6),
        "min_score": round(float(min_score), 4),
        "ok": False,
        "reason": "",
    }

    # ---- 0. 还没有图像/检测：立刻回一条 ok=false，绝不能沉默 ----
    if dets is None:
        payload["reason"] = reason or "尚未收到图像或检测结果"
        return payload

    # ---- 1. 非社区人员辨别 ----
    if task_name == TASK_OUTSIDER_DETECT:
        counts = count_persons(dets, min_score)
        n_community = counts[CLASS_NAMES[CLS_PERSON_COMMUNITY]]
        n_noncommunity = counts[CLASS_NAMES[CLS_PERSON_NONCOMMUNITY]]
        target_hint = target_to_class(target)
        payload.update(
            {
                "ok": True,
                "person_community": n_community,
                "person_noncommunity": n_noncommunity,
                "person_total": n_community + n_noncommunity,
                "target_hint": target_hint,
                "target_found": bool(
                    target_hint is not None and counts.get(target_hint, 0) > 0
                ),
            }
        )
        return payload

    # ---- 2. 人群计数 ----
    if task_name == TASK_CROWD_COUNT:
        counts = count_persons(dets, min_score)
        n_community = counts[CLASS_NAMES[CLS_PERSON_COMMUNITY]]
        n_noncommunity = counts[CLASS_NAMES[CLS_PERSON_NONCOMMUNITY]]
        payload.update(
            {
                "ok": True,
                "person_community": n_community,
                "person_noncommunity": n_noncommunity,
                "person_total": n_community + n_noncommunity,
            }
        )
        return payload

    # ---- 3. 车牌识别 ----
    if task_name == TASK_PLATE_OCR:
        box = best_plate_detection(dets, min_score)
        if box is None:
            payload["reason"] = reason or (
                f"未检测到车牌（{CLASS_NAMES[CLS_PLATE]}, score >= {float(min_score):g}）"
            )
            return payload
        payload["plate_bbox"] = [
            round(float(box.x1), 2),
            round(float(box.y1), 2),
            round(float(box.x2), 2),
            round(float(box.y2), 2),
        ]
        # plate_detect_score = YOLO 检测框的置信度；plate_score = OCR 的得分
        payload["plate_detect_score"] = round(float(box.score), 5)
        fields = _plate_fields(plate_result)
        payload.update(fields)
        if plate_result is None:
            payload["reason"] = reason or "车牌识别模块不可用"
            return payload
        if not fields["plate"]:
            payload["reason"] = reason or "车牌字符识别失败"
            return payload
        payload["ok"] = True
        return payload

    # ---- 4. 未知 task（站点表写错/漏填）：同样要回一条 ----
    payload["reason"] = reason or (
        f"未知的 task: {task_name!r}（支持 {', '.join(PATROL_TASKS)}）"
    )
    return payload


__all__ = [
    "SCHEMA",
    "RESULT_SCHEMA",
    "detections_to_json",
    "empty_detections_json",
    "detections_from_json",
    "summarize",
    "dominant_traffic_state",
    "traffic_light_state_text",
    "target_to_class",
    "count_class",
    "count_persons",
    "best_plate_detection",
    "normalize_slot",
    "build_result_payload",
    "TASK_OUTSIDER_DETECT",
    "TASK_CROWD_COUNT",
    "TASK_PLATE_OCR",
    "PATROL_TASKS",
    "UNKNOWN_LIGHT_STATE",
    "STATE_RED",
    "STATE_YELLOW",
    "STATE_GREEN",
]
