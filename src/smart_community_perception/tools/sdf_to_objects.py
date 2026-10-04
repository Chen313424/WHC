#!/usr/bin/env python3
"""从 world SDF 生成自动标注所需的 3D 真值框清单。

思路
----
Gazebo 的相机是标准针孔模型，世界里的物体位姿又是已知的（静态世界 + SDF），
所以只要知道每个目标的 3D 包围盒，就能把它的 8 个角点投影到图像上得到
精确的 YOLO 标注框 —— 完全不需要人工标注。

本脚本扫描 SDF，提取三类目标，输出 `config/world_objects.yaml`：

  * traffic_light : 红绿灯的「灯箱 + 三盏灯」包围盒（**不含灯杆**，
                    因为灯杆又细又高，混进来会把框拉得很长，不利于训练）
  * person        : 人偶立牌（立板 + 头 + 标签 + 底座）的整体包围盒
  * license_plate : 车牌。车牌不是独立 model，而是车身里的 visual
                    （plate_front / plate_rear），所以要单独抽取。

输出坐标约定
------------
* `model_pose` : [x, y, z, roll, pitch, yaw]，模型在世界系下的位姿
* `center`     : 包围盒中心，**模型局部系**
* `size`       : 包围盒尺寸（全边长），**模型局部系**

运行时的世界系角点 = model_pose 变换 ∘ (center ± size/2)。

用法
----
    python3 tools/sdf_to_objects.py \
        --sdf ../smart_community_sim/worlds/smart_community.sdf \
        --out config/world_objects.yaml
"""
from __future__ import annotations

import argparse
import math
import os
import xml.etree.ElementTree as ET
from typing import Iterable, Sequence

# ---------------------------------------------------------------- 基础数学

Vec3 = Sequence[float]


def _f(text: str | None, default: float = 0.0) -> float:
    if text is None:
        return default
    return float(text.strip())


def parse_pose(elem: ET.Element, default: Sequence[float] = (0, 0, 0, 0, 0, 0)) -> list[float]:
    """读取元素的直接子节点 <pose>，格式 `x y z roll pitch yaw`。"""
    node = elem.find("pose")
    if node is None or not (node.text or "").strip():
        return list(default)
    parts = node.text.split()
    if len(parts) < 6:
        parts = parts + ["0"] * (6 - len(parts))
    return [float(p) for p in parts[:6]]


def rpy_to_matrix(roll: float, pitch: float, yaw: float):
    """返回 3x3 旋转矩阵，采用 SDF 约定 R = Rz(yaw) * Ry(pitch) * Rx(roll)。"""
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ]


def transform_point(pose6: Sequence[float], p: Vec3) -> list[float]:
    """把局部点 p 用 pose6 变换到父坐标系。"""
    r = rpy_to_matrix(pose6[3], pose6[4], pose6[5])
    return [
        r[0][0] * p[0] + r[0][1] * p[1] + r[0][2] * p[2] + pose6[0],
        r[1][0] * p[0] + r[1][1] * p[1] + r[1][2] * p[2] + pose6[1],
        r[2][0] * p[0] + r[2][1] * p[1] + r[2][2] * p[2] + pose6[2],
    ]


def transform_aabb_corners(pose6: Sequence[float], corners: Iterable[Vec3]) -> list[list[float]]:
    return [transform_point(pose6, c) for c in corners]


def corners_of_aabb(center: Vec3, size: Vec3) -> list[list[float]]:
    hx, hy, hz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    out = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                out.append([center[0] + sx * hx, center[1] + sy * hy, center[2] + sz * hz])
    return out


def aabb_from_points(points: Iterable[Vec3]) -> tuple[list[float], list[float]]:
    pts = list(points)
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    center = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    size = [hi[i] - lo[i] for i in range(3)]
    return center, size


# ---------------------------------------------------------------- 几何解析


def geometry_aabb(geom: ET.Element) -> tuple[list[float], list[float]] | None:
    """返回 visual 局部系下的 (center, size)。不支持的几何返回 None。"""
    box = geom.find("box")
    if box is not None:
        size_node = box.find("size")
        if size_node is None or not (size_node.text or "").strip():
            return None
        s = [float(v) for v in size_node.text.split()[:3]]
        return [0.0, 0.0, 0.0], s

    cyl = geom.find("cylinder")
    if cyl is not None:
        r = _f(cyl.findtext("radius"), 0.0)
        length = _f(cyl.findtext("length"), 0.0)
        # SDF 圆柱默认沿 Z 轴
        return [0.0, 0.0, 0.0], [2 * r, 2 * r, length]

    sph = geom.find("sphere")
    if sph is not None:
        r = _f(sph.findtext("radius"), 0.0)
        return [0.0, 0.0, 0.0], [2 * r, 2 * r, 2 * r]

    # plane / mesh / heightmap 等：跳过
    return None


def collect_visual_aabbs(link: ET.Element, link_pose: Sequence[float]):
    """产出 (visual_name, center, size)，均已变换到 link 的父坐标系。"""
    for visual in link.findall("visual"):
        geom = visual.find("geometry")
        if geom is None:
            continue
        got = geometry_aabb(geom)
        if got is None:
            continue
        center, size = got
        # visual 自身位姿（相对 link）
        vpose = parse_pose(visual)
        corners = transform_aabb_corners(vpose, corners_of_aabb(center, size))
        # link 位姿（相对 model）
        corners = transform_aabb_corners(link_pose, corners)
        c, s = aabb_from_points(corners)
        yield visual.findtext("name") or visual.get("name") or "", c, s


def collect_model_visuals(model: ET.Element):
    """把一个 model 的所有 visual 汇总成 model 局部系的 (name, center, size) 列表。"""
    out = []
    mpose = [0.0] * 6  # 默认
    for link in model.findall("link"):
        lpose = parse_pose(link)
        for name, c, s in collect_visual_aabbs(link, lpose):
            out.append((name, c, s))
    # 嵌套 model（本项目用不到，但保持健壮）
    for sub in model.findall("model"):
        spose = parse_pose(sub)
        for name, c, s in collect_model_visuals(sub):
            corners = transform_aabb_corners(spose, corners_of_aabb(c, s))
            cc, ss = aabb_from_points(corners)
            out.append((f"{sub.get('name')}/{name}", cc, ss))
    _ = mpose
    return out


# ---------------------------------------------------------------- 分类规则

TRAFFIC_LIGHT_PREFIX = "traffic_light"
PERSON_PREFIX = "person_"
# 灯杆又细又高，混进包围盒会把标注框拉成一条长条，训练时反而干扰，所以排除。
TRAFFIC_LIGHT_SKIP = {"pole", "pole_col", "pole_c"}

CLASS_TRAFFIC_LIGHT = "traffic_light"
CLASS_PERSON = "person"
CLASS_PLATE = "license_plate"


def build_objects(sdf_path: str):
    tree = ET.parse(sdf_path)
    root = tree.getroot()
    world = root.find("world")
    if world is None:
        raise SystemExit(f"在 {sdf_path} 里找不到 <world>")

    objects: list[dict] = []

    for model in world.findall("model"):
        name = model.get("name") or ""
        pose = parse_pose(model)
        visuals = collect_model_visuals(model)

        # --- 车牌：从车身里抽取 plate_* 的 visual ---
        plate_visuals = [v for v in visuals if v[0].startswith("plate")]
        if plate_visuals:
            for vname, c, s in plate_visuals:
                objects.append(
                    {
                        "name": f"{name}/{vname}",
                        "class": CLASS_PLATE,
                        "model_pose": pose,
                        "center": c,
                        "size": s,
                    }
                )

        # --- 红绿灯：灯箱 + 三盏灯 ---
        if name.startswith(TRAFFIC_LIGHT_PREFIX):
            keep = [
                v for v in visuals if v[0] not in TRAFFIC_LIGHT_SKIP and not v[0].startswith("plate")
            ]
            if keep:
                c, s = aabb_from_points(
                    corners_of_aabb(v[1], v[2])[i] for v in keep for i in range(8)
                )
                objects.append(
                    {
                        "name": name,
                        "class": CLASS_TRAFFIC_LIGHT,
                        "model_pose": pose,
                        "center": c,
                        "size": s,
                    }
                )
            continue

        # --- 人偶 ---
        if name.startswith(PERSON_PREFIX):
            keep = [v for v in visuals if not v[0].startswith("plate")]
            if keep:
                c, s = aabb_from_points(
                    corners_of_aabb(v[1], v[2])[i] for v in keep for i in range(8)
                )
                objects.append(
                    {
                        "name": name,
                        "class": CLASS_PERSON,
                        "model_pose": pose,
                        "center": c,
                        "size": s,
                    }
                )
            continue

    return objects


# ---------------------------------------------------------------- YAML 输出


def fmt(v: float) -> str:
    return f"{round(float(v), 6):g}"


def dump_yaml(objects: list[dict]) -> str:
    lines = [
        "# 本文件由 tools/sdf_to_objects.py 自动生成，请勿手工编辑。",
        "# 重新生成： python3 tools/sdf_to_objects.py --sdf <world.sdf> --out config/world_objects.yaml",
        "#",
        "# model_pose: [x, y, z, roll, pitch, yaw]  目标模型在世界系下的位姿",
        "# center/size: 包围盒中心与尺寸，位于【模型局部系】",
        "# 世界系角点 = model_pose 变换 (center ± size/2)",
        "objects:",
    ]
    for o in objects:
        lines.append(f"  - name: {o['name']}")
        lines.append(f"    class: {o['class']}")
        lines.append("    model_pose: [" + ", ".join(fmt(v) for v in o["model_pose"]) + "]")
        lines.append("    center: [" + ", ".join(fmt(v) for v in o["center"]) + "]")
        lines.append("    size: [" + ", ".join(fmt(v) for v in o["size"]) + "]")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    default_sdf = os.path.normpath(
        os.path.join(here, "..", "..", "smart_community_sim", "worlds", "smart_community.sdf")
    )
    default_out = os.path.normpath(os.path.join(here, "..", "config", "world_objects.yaml"))

    ap = argparse.ArgumentParser(description="SDF -> 3D 真值框清单")
    ap.add_argument("--sdf", default=default_sdf)
    ap.add_argument("--out", default=default_out)
    args = ap.parse_args()

    objects = build_objects(args.sdf)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(dump_yaml(objects))

    counts: dict[str, int] = {}
    for o in objects:
        counts[o["class"]] = counts.get(o["class"], 0) + 1
    print(f"已写入 {args.out}")
    print(f"共 {len(objects)} 个目标: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
