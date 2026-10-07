"""红绿灯决策纯逻辑（不依赖 ROS，可离线单元测试）。

设计要点
--------
1. **状态来自时序，不靠猜**：世界插件 TrafficLightSystem 的时序是
   `绿(15s) -> 黄(5s) -> 红(10s)`、`phase = fmod(simTime, 30)`。
   ★ 2026-10 仿真组重建场景时把黄灯从 3s 改成了 5s（官方文档写的是 3s，
   SDF 里实际是 5s），本文件必须与 SDF 同步 —— 否则自动标注会在黄灯那一段
   把真值标成红灯/绿灯，训练出来的模型颜色判断是错的。
   自动标注阶段可以直接用仿真时间算出真值状态；推理阶段则由 YOLO 分类。
   两个函数都放在这里，保证「标注用的定义」与「训练/评测用的定义」一致。

2. **分类而非回归**：模型直接输出 traffic_light_red / _yellow / _green 三个类，
   控制端不需要再做颜色分析，鲁棒性更好。

3. **决策与控制解耦**：`decide()` 只吃「检测结果的抽象」，
   返回一个纯粹的动作枚举 + 缩放系数，方便离线用例覆盖各种边界。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Sequence

# ---------------------------------------------------------------- 类别定义

CLASS_NAMES: list[str] = [
    "traffic_light_red",
    "traffic_light_yellow",
    "traffic_light_green",
    "person_community",
    "person_noncommunity",
    "license_plate",
]

CLS_TL_RED = 0
CLS_TL_YELLOW = 1
CLS_TL_GREEN = 2
# ★ 2026-10 把原来的单一 person 类拆成社区 / 非社区两类。
#   依据是世界 SDF 里 18 个人偶立牌的贴图：
#       person_a1~a5 / person_b1~b5 / person_s1~s6 -> person_community_NN.png   （16 个）
#       person_f1 / person_f2                        -> person_noncommunity_NN.png（ 2 个）
#   赛题要求「非社区人员辨别」，所以必须让模型直接分出这两类，
#   而不是靠颜色后处理猜。
CLS_PERSON_COMMUNITY = 3
CLS_PERSON_NONCOMMUNITY = 4
CLS_PLATE = 5

# 两类人偶的集合（做「前方是否有人」判断时两类都要算）
PERSON_CLASSES = {CLS_PERSON_COMMUNITY, CLS_PERSON_NONCOMMUNITY}
# 兼容旧代码里用的单一人偶类名
CLS_PERSON = CLS_PERSON_COMMUNITY

CLASS_TO_ID = {name: i for i, name in enumerate(CLASS_NAMES)}
TL_CLASSES = {CLS_TL_RED, CLS_TL_YELLOW, CLS_TL_GREEN}

STATE_RED = "red"
STATE_YELLOW = "yellow"
STATE_GREEN = "green"

TL_CLASS_TO_STATE = {
    CLS_TL_RED: STATE_RED,
    CLS_TL_YELLOW: STATE_YELLOW,
    CLS_TL_GREEN: STATE_GREEN,
}

# 反向映射：自动标注时由仿真时间算出状态，再转成类别 id
STATE_TO_TL_CLASS = {v: k for k, v in TL_CLASS_TO_STATE.items()}

# 各目标的「最大边长」(米)，用于 bbox 长边 -> 距离的相似三角形估算。
# ★ 数值直接来自重建后世界 SDF（smart_community.sdf）里各 visual 的 <size>：
#   traffic_light_1（竖排）housing [0.14, 0.05, 0.59]  -> 长边 0.59
#   traffic_light_2（横排）housing [0.59, 0.05, 0.14]  -> 长边 0.59
#     两者都是 0.59，所以横竖两种排布共用同一尺度参数。
#     【不含灯腿】—— 灯腿又细又长（0.025×0.025×0.3/0.48），
#     混进包围盒会把框拉成细长条，破坏距离估计。
#   person（立牌）    [0.05, 0.005, 0.15]              -> 长边 0.15
#   license_plate     [0.095, 0.002, 0.03]             -> 长边 0.095
#   （官方规格：人偶立牌 高15cm 宽5cm 厚5mm；车牌 3×9.5cm —— 与 SDF 一致）
OBJECT_MAX_EXTENT_M: dict[int, float] = {
    CLS_TL_RED: 0.59,
    CLS_TL_YELLOW: 0.59,
    CLS_TL_GREEN: 0.59,
    CLS_PERSON_COMMUNITY: 0.15,
    CLS_PERSON_NONCOMMUNITY: 0.15,
    CLS_PLATE: 0.095,
}


def traffic_light_state_from_sim_time(
    sim_time: float,
    green_time: float = 15.0,
    yellow_time: float = 5.0,
    red_time: float = 10.0,
) -> str:
    """复刻 TrafficLightSystem.cc 的时序，返回 'green' / 'yellow' / 'red'。

    必须与插件保持逐字一致，否则自动标注的类别会错。
    """
    cycle = green_time + yellow_time + red_time
    if cycle <= 0:
        return STATE_GREEN
    phase = sim_time % cycle
    if phase < 0:
        phase += cycle
    if phase < green_time:
        return STATE_GREEN
    if phase < green_time + yellow_time:
        return STATE_YELLOW
    return STATE_RED


# ---------------------------------------------------------------- 决策


class Action(Enum):
    """纵向动作。"""

    GO = "go"          # 按期望速度行驶
    SLOW = "slow"      # 减速接近（黄灯 / 远处红灯）
    STOP = "stop"      # 制动停车


@dataclass(frozen=True)
class Detection:
    """一帧里的一条检测（已从 ROS 消息解出）。"""

    cls_id: int
    score: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width_px(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height_px(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def longest_px(self) -> float:
        return max(self.width_px, self.height_px)

    @property
    def center_x_px(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def bottom_y_px(self) -> float:
        return max(self.y1, self.y2)

    @property
    def class_name(self) -> str:
        if 0 <= self.cls_id < len(CLASS_NAMES):
            return CLASS_NAMES[self.cls_id]
        return f"unknown_{self.cls_id}"


@dataclass
class TrafficDecision:
    action: Action
    speed_scale: float
    reason: str
    state: Optional[str] = None
    distance_m: Optional[float] = None

    @property
    def is_stop(self) -> bool:
        return self.action is Action.STOP


def estimate_distance_m(det: Detection, fy: float) -> Optional[float]:
    """由检测框长边估算距离（米）。"""
    extent = OBJECT_MAX_EXTENT_M.get(det.cls_id)
    if extent is None or fy <= 0:
        return None
    longest = det.longest_px
    if longest <= 1.0:
        return None
    return fy * extent / longest


def _pick_relevant_traffic_light(
    detections: Sequence[Detection],
    image_width: int,
    roi_x_ratio: float,
    min_score: float,
) -> Optional[tuple[Detection, str]]:
    """挑出「与本车相关」的红绿灯。

    策略：只看图像中央的水平 ROI（车辆前进方向），取面积最大的那个。
    面积最大 ≈ 最近/最醒目，能自然滤掉远处另一组灯。
    """
    x_lo = image_width * (0.5 - roi_x_ratio / 2.0)
    x_hi = image_width * (0.5 + roi_x_ratio / 2.0)

    best: Optional[tuple[Detection, str]] = None
    best_area = -1.0
    for det in detections:
        state = TL_CLASS_TO_STATE.get(det.cls_id)
        if state is None or det.score < min_score:
            continue
        if not (x_lo <= det.center_x_px <= x_hi):
            continue
        area = det.width_px * det.height_px
        if area > best_area:
            best_area = area
            best = (det, state)
    return best


def decide(
    detections: Sequence[Detection],
    *,
    fy: float,
    image_width: int,
    stop_distance_m: float = 5.0,
    slow_distance_m: float = 9.0,
    roi_x_ratio: float = 0.6,
    min_score: float = 0.35,
    yellow_policy: str = "stop_if_far",
    cruise_speed: float = 0.6,
    approach_speed: float = 0.25,
) -> TrafficDecision:
    """根据检测结果给出纵向动作。

    规则（与用户要求一致：红灯停、绿灯行）：

    * **红灯**：距离 < `stop_distance_m` -> STOP；否则 SLOW 提前减速。
    * **黄灯**：`yellow_policy='stop_if_far'` 时，能安全停就停（距离够则 STOP，
      已经很近则按 GO 通过，避免急刹）；`'always_stop'` 则一律停。
    * **绿灯**：GO。
    * 没有相关红绿灯：GO（保持当前行驶，不因为看不到灯就停）。
    """
    picked = _pick_relevant_traffic_light(detections, image_width, roi_x_ratio, min_score)
    if picked is None:
        return TrafficDecision(Action.GO, 1.0, "未检测到相关红绿灯", None, None)

    det, state = picked
    dist = estimate_distance_m(det, fy)

    if state == STATE_GREEN:
        return TrafficDecision(Action.GO, 1.0, "绿灯通行", state, dist)

    if state == STATE_RED:
        if dist is None:
            # 距离估不出来（框太小/异常），按「已接近路口」保守处理
            return TrafficDecision(Action.SLOW, approach_speed / max(cruise_speed, 1e-6),
                                   "红灯但距离未知，减速戒备", state, None)
        if dist <= stop_distance_m:
            return TrafficDecision(Action.STOP, 0.0, f"红灯且距离 {dist:.2f}m，停车", state, dist)
        return TrafficDecision(Action.SLOW, approach_speed / max(cruise_speed, 1e-6),
                               f"红灯，距离 {dist:.2f}m，减速接近", state, dist)

    # 黄灯
    if yellow_policy == "always_stop":
        return TrafficDecision(Action.STOP, 0.0, "黄灯，停车", state, dist)
    if dist is None or dist <= stop_distance_m * 0.6:
        return TrafficDecision(Action.GO, 1.0, "黄灯且已过停车线，继续通过", state, dist)
    return TrafficDecision(Action.STOP, 0.0, f"黄灯，距离 {dist:.2f}m，安全停车", state, dist)


def any_person_ahead(
    detections: Sequence[Detection],
    *,
    image_width: int,
    fy: float,
    danger_distance_m: float = 3.0,
    roi_x_ratio: float = 0.4,
    min_score: float = 0.4,
) -> Optional[float]:
    """前方危险距离内是否有人偶。返回最近距离或 None。

    默认不在控制主逻辑里启用（人偶都在封闭街区内，正常不挡路），
    但留作可选的安全兜底。
    """
    x_lo = image_width * (0.5 - roi_x_ratio / 2.0)
    x_hi = image_width * (0.5 + roi_x_ratio / 2.0)
    nearest: Optional[float] = None
    for det in detections:
        if det.cls_id not in PERSON_CLASSES or det.score < min_score:
            continue
        if not (x_lo <= det.center_x_px <= x_hi):
            continue
        dist = estimate_distance_m(det, fy)
        if dist is None or dist > danger_distance_m:
            continue
        if nearest is None or dist < nearest:
            nearest = dist
    return nearest


def scale_twist(vx: float, wz: float, speed_scale: float) -> tuple[float, float]:
    """把上层（遥控/导航）给的速度按决策缩放。

    缩放只作用在线速度上：红灯停车时角速度一并归零，
    避免出现「原地打转但不动」的怪状态。
    """
    if speed_scale <= 0.0:
        return 0.0, 0.0
    return vx * speed_scale, wz
