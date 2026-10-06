"""纯几何工具：位姿变换、针孔投影、框尺寸->距离估计。

这里刻意不依赖 tf2 / ROS 运行时，方便离线单元测试。
所有旋转遵循 SDF/URDF 约定：R = Rz(yaw) * Ry(pitch) * Rx(roll)。

坐标系链条（与 robot.xacro / bridge.yaml 一致）：

    world ──(spawn 位姿)──> odom ──(OdometryPublisher)──> base_footprint
          ──(+wheel_radius)──> base_link ──(+相机外参)──> camera_link
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np

Vec3 = Sequence[float]
Pose6 = Sequence[float]  # x y z roll pitch yaw


# ---------------------------------------------------------------- 旋转


def rpy_to_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ],
        dtype=float,
    )


def pose_to_matrix(pose6: Pose6) -> np.ndarray:
    """pose6 -> 4x4 齐次变换矩阵。"""
    t = np.eye(4, dtype=float)
    t[:3, :3] = rpy_to_matrix(pose6[3], pose6[4], pose6[5])
    t[:3, 3] = [pose6[0], pose6[1], pose6[2]]
    return t


def matrix_to_pose(t: np.ndarray) -> list[float]:
    """4x4 -> [x, y, z, roll, pitch, yaw]。"""
    roll = math.atan2(t[2, 1], t[2, 2])
    pitch = math.asin(max(-1.0, min(1.0, -t[2, 0])))
    yaw = math.atan2(t[1, 0], t[0, 0])
    return [float(t[0, 3]), float(t[1, 3]), float(t[2, 3]), roll, pitch, yaw]


def invert(t: np.ndarray) -> np.ndarray:
    out = np.eye(4, dtype=float)
    out[:3, :3] = t[:3, :3].T
    out[:3, 3] = -t[:3, :3].T @ t[:3, 3]
    return out


def quaternion_to_rpy(x: float, y: float, z: float, w: float) -> list[float]:
    """四元数 -> [roll, pitch, yaw]（与 rpy_to_matrix 同一约定）。"""
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm < 1e-12:
        return [0.0, 0.0, 0.0]
    x, y, z, w = x / norm, y / norm, z / norm, w / norm

    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    sinp = 2.0 * (w * y - z * x)
    if abs(sinp) >= 1.0:
        pitch = math.copysign(math.pi / 2.0, sinp)
    else:
        pitch = math.asin(sinp)

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return [roll, pitch, yaw]


def odom_pose_to_pose6(msg_pose) -> list[float]:
    """nav_msgs/Odometry.pose.pose -> [x, y, z, roll, pitch, yaw]。"""
    p = msg_pose.position
    q = msg_pose.orientation
    roll, pitch, yaw = quaternion_to_rpy(q.x, q.y, q.z, q.w)
    return [p.x, p.y, p.z, roll, pitch, yaw]


def aabb_corners(center: Vec3, size: Vec3) -> np.ndarray:
    """(center, size) -> 8x3 角点（局部系）。"""
    h = np.array(size, dtype=float) / 2.0
    c = np.array(center, dtype=float)
    signs = np.array(
        [[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=float
    )
    return c + signs * h


# ---------------------------------------------------------------- 针孔模型


def intrinsics_from_hfov(width: int, height: int, hfov_rad: float) -> tuple[float, float, float, float]:
    """由水平视场角推内参。Gazebo 默认正方形像素，故 fy = fx。

    robot.xacro: width=640, height=480, horizontal_fov=1.745329 (100deg)
        fx = 320 / tan(50deg) ~= 268.5
    """
    fx = (width / 2.0) / math.tan(hfov_rad / 2.0)
    fy = fx
    return fx, fy, width / 2.0, height / 2.0


def project_points(points_cam: np.ndarray, K: Sequence[float]):
    """把相机系下的点投影到像素。

    返回 (uv, depth)：
      uv    : Nx2 像素坐标
      depth : N 个点的相机 Z（正值 = 在相机前方）
    相机约定：+X 右、+Y 下、+Z 前（ROS 光学坐标系）。
    """
    fx, fy, cx, cy = K
    pts = np.asarray(points_cam, dtype=float)
    z = pts[:, 2]
    safe_z = np.where(np.abs(z) < 1e-6, 1e-6, z)
    u = fx * pts[:, 0] / safe_z + cx
    v = fy * pts[:, 1] / safe_z + cy
    return np.stack([u, v], axis=1), z


def bbox_from_uv(uv: np.ndarray, width: int, height: int, min_visible: float = 0.25):
    """投影点 -> 图像内 AABB，并做可见性过滤。

    返回 (x1, y1, x2, y2) 或 None。
    过滤条件：
      * 至少 1 个角点在相机前方（否则物体在身后）
      * 框与图像有足够重叠（默认至少保留 25% 面积，避免只露一角就被标注）
    """
    x1 = float(np.min(uv[:, 0]))
    y1 = float(np.min(uv[:, 1]))
    x2 = float(np.max(uv[:, 0]))
    y2 = float(np.max(uv[:, 1]))

    cx1 = max(0.0, min(x1, width - 1.0))
    cy1 = max(0.0, min(y1, height - 1.0))
    cx2 = max(0.0, min(x2, width - 1.0))
    cy2 = max(0.0, min(y2, height - 1.0))
    if cx2 <= cx1 or cy2 <= cy1:
        return None

    full_area = (x2 - x1) * (y2 - y1)
    if full_area <= 0:
        return None
    clipped_area = (cx2 - cx1) * (cy2 - cy1)
    if clipped_area / full_area < min_visible:
        return None

    return cx1, cy1, cx2, cy2


def distance_from_bbox(fy: float, real_extent_m: float, pixel_extent: float) -> float | None:
    """相似三角形估算距离： Z = fy * H_real / h_pixel。

    `real_extent_m` 取目标「最大边长」，`pixel_extent` 取检测框长边，
    这样竖直灯箱(竖排)和水平灯箱(横排)可以用同一个尺度，避免两套参数。
    """
    if pixel_extent <= 1e-6:
        return None
    return float(fy) * float(real_extent_m) / float(pixel_extent)


# ---------------------------------------------------------------- 坐标链


def body_to_optical() -> np.ndarray:
    """camera_link（车体惯例: +X 前 / +Y 左 / +Z 上）-> 光学系（+Z 前 / +X 右 / +Y 下）。

    这是 REP-105 的标准约定，等价于 URDF 里的 rpy = (-pi/2, 0, -pi/2)。
    少了这一步，投影会把「上下左右」全弄反 —— 图像坐标系必须用光学系。
    """
    return pose_to_matrix([0.0, 0.0, 0.0, -math.pi / 2.0, 0.0, -math.pi / 2.0])


def world_to_camera(
    world_to_odom: Pose6,
    odom_to_base_footprint: Pose6,
    base_footprint_to_base_link_z: float,
    base_link_to_camera: Pose6,
) -> np.ndarray:
    """返回 4x4 的 **world -> 相机光学系** 变换矩阵。

    两点容易踩坑，都已在测试里锁定：
      1. 逐级合成得到的是「子 -> 父」（camera -> world），投影需要反向，必须取逆。
      2. `camera_link` 是车体坐标系，投影前还要再转到光学系。
    """
    t = pose_to_matrix(world_to_odom)
    t = t @ pose_to_matrix(odom_to_base_footprint)
    t = t @ pose_to_matrix([0.0, 0.0, base_footprint_to_base_link_z, 0.0, 0.0, 0.0])
    t = t @ pose_to_matrix(base_link_to_camera)
    t = t @ body_to_optical()
    return invert(t)


def aabb_to_bbox(
    world_to_cam: np.ndarray,
    model_pose: Pose6,
    center: Vec3,
    size: Vec3,
    K: Sequence[float],
    width: int,
    height: int,
    min_visible: float = 0.25,
):
    """完整版：世界系 AABB -> 像素框 + 距离（米）。"""
    corners_local = aabb_corners(center, size)
    model_t = pose_to_matrix(model_pose)
    hom = np.hstack([corners_local, np.ones((8, 1))])
    corners_world = (model_t @ hom.T).T[:, :3]
    hom_w = np.hstack([corners_world, np.ones((8, 1))])
    corners_cam = (world_to_cam @ hom_w.T).T[:, :3]

    if np.any(corners_cam[:, 2] <= 1e-3):
        return None, None

    uv, _ = project_points(corners_cam, K)
    bbox = bbox_from_uv(uv, width, height, min_visible=min_visible)
    if bbox is None:
        return None, None

    depth = float(np.min(corners_cam[:, 2]))
    return bbox, depth


def camera_center_in_world(world_to_cam: np.ndarray) -> np.ndarray:
    """相机光心在世界系下的位置（用于粗筛可见性）。"""
    cam_to_world = invert(world_to_cam)
    return cam_to_world[:3, 3]


def yolo_line(cls_id: int, bbox: Iterable[float], width: int, height: int) -> str:
    """生成 YOLO 格式标注行： `cls cx cy w h`，均为归一化值，保留 6 位小数。"""
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0 / width
    cy = (y1 + y2) / 2.0 / height
    w = (x2 - x1) / width
    h = (y2 - y1) / height
    return f"{int(cls_id)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
