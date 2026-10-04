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
    Detection,
    STATE_GREEN,
    STATE_RED,
    STATE_YELLOW,
    TL_CLASS_TO_STATE,
)

SCHEMA = "whc.detections/1"


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


__all__ = [
    "SCHEMA",
    "detections_to_json",
    "empty_detections_json",
    "detections_from_json",
    "summarize",
    "dominant_traffic_state",
    "STATE_RED",
    "STATE_YELLOW",
    "STATE_GREEN",
]
