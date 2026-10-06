#!/usr/bin/env python3
"""离线合成数据集生成器（不需要跑 Gazebo）。

什么时候用
----------
* 想先验证「训练 -> 推理 -> 控制」整条链路是否通
* 仿真环境还没配好，但想先有一个能用的模型
* 采集到的真实帧不够，需要补充**稀有类别**（黄灯只有 5s，很难拍到）

★ 2026-10 重写：原来的版本是按【旧的 88x44m 城市场景】画的
（人偶 0.6x1.83 立板、车牌 0.5x0.16、红绿灯 0.78 长边、单一 person 类），
与建模组重建后的 4.2x4.2m 省赛场地完全不符。现在全部改成：
  · 几何比例严格照 smart_community.sdf 里各 visual 的 <size>
  · 人偶/车牌/车背景全部贴【真实贴图】（person_community_NN.png、
    person_noncommunity_NN.png、plate_N.png、car_background.png），
    而不是画色块 —— 这样与 Gazebo 渲染的域差小得多
  · 人偶拆成 person_community / person_noncommunity 两类（赛题要求辨别非社区人员）

SDF 实测几何（长边为 1 归一化）：
    红绿灯(竖) housing 0.14x0.05x0.59 + 灯球 r=0.045，灯心距面板中心 ±0.19
    红绿灯(横) housing 0.59x0.05x0.14 + 灯球 r=0.045，灯心距面板中心 ±0.19
    人偶立牌   0.05 x 0.005 x 0.15   （贴图 200x600，比例 1:3，与之一致）
    车牌       0.095 x 0.002 x 0.03  （贴图 380x120，比例 3.17，与之一致）
    车背景     0.345 x 0.005 x 0.25  （贴图 1380x1000）
                车牌中心相对车中心 = (-0.08, -0.09)（SDF 里的 <pose>）

依赖: pip install pillow
用法::

    python tools/gen_synth_dataset.py --out ../whc_synth --n 2000
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

# ---------------------------------------------------------------- 类别定义
#
# ★ 类别 id / 名字以 traffic_rules 为【唯一来源】，不要在这里再抄一份：
#   2026-10 加类别时就是因为"两处各写一份清单"才差点出现 id 错位。
#   traffic_rules 只依赖 stdlib，可以直接 import。
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
from smart_community_perception.traffic_rules import (  # noqa: E402
    CLASS_NAMES,
    CLS_PERSON_COMMUNITY,
    CLS_PERSON_NONCOMMUNITY,
    CLS_PLATE,
    CLS_TL_GREEN,
    CLS_TL_RED,
    CLS_TL_YELLOW,
)

CLS_TL = {  # 状态 -> 类别 id / 灯色（灯色与插件 TrafficLightSystem.cc 一致）
    "red": (CLS_TL_RED, (255, 40, 40)),
    "yellow": (CLS_TL_YELLOW, (255, 191, 0)),
    "green": (CLS_TL_GREEN, (0, 178, 51)),
}
LAMP_OFF = (32, 32, 32)

# 与插件 TrafficLightSystem.cc 一致的灯色
STATE_COLORS = {k: v[1] for k, v in CLS_TL.items()}

# 由 SDF 推导的比例（全部相对于整体包围盒的长边）
TL_V = dict(box_w=0.14 / 0.59, box_h=1.0, lamp_r=0.045 / 0.59, lamp_off=0.19 / 0.59)
TL_H = dict(box_w=1.0, box_h=0.14 / 0.59, lamp_r=0.045 / 0.59, lamp_off=0.19 / 0.59)

PERSON_W_M, PERSON_H_M = 0.05, 0.15          # 立牌尺寸（贴图 1:3）
PLATE_W_M, PLATE_H_M = 0.095, 0.03           # 车牌（贴图 3.17）
CAR_W_M, CAR_H_M = 0.345, 0.25               # 车背景
# 车牌中心相对车中心的偏移（SDF: <pose>-0.08 0.0035 -0.09</pose>）
PLATE_DX_IN_CAR = -0.08 / CAR_W_M
PLATE_DY_IN_CAR = -0.09 / CAR_H_M


# ---------------------------------------------------------------- 背景


def make_background(w: int, h: int, rng: random.Random) -> Image.Image:
    kind = rng.choice(["asphalt", "asphalt_marked", "grass", "road_sky"])
    if kind == "grass":
        base = (int(rng.uniform(95, 135)), int(rng.uniform(125, 165)), int(rng.uniform(85, 120)))
    else:
        g = int(rng.uniform(60, 100))
        base = (g, g, int(g * rng.uniform(0.96, 1.08)))

    img = Image.new("RGB", (w, h), base)

    if kind == "road_sky":
        horizon = int(h * rng.uniform(0.30, 0.55))
        sky = (int(rng.uniform(150, 220)), int(rng.uniform(170, 230)), int(rng.uniform(200, 250)))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, w, horizon], fill=sky)
        road = (int(rng.uniform(55, 90)),) * 3
        d.rectangle([0, horizon, w, h], fill=road)

    if kind in ("asphalt_marked", "road_sky"):
        d = ImageDraw.Draw(img)
        y = int(h * rng.uniform(0.55, 0.9))
        dash = int(w * rng.uniform(0.05, 0.12))
        x = -dash
        while x < w:
            d.rectangle([x, y, x + dash * 0.6, y + max(3, h // 90)], fill=(230, 230, 225))
            x += dash

    # 噪声：让模型不依赖"干净色块"这种捷径
    px = img.load()
    amount = rng.uniform(4, 16)
    for _ in range(int(w * h * 0.12)):
        x = rng.randrange(w)
        y = rng.randrange(h)
        r, g, b = px[x, y]
        n = rng.uniform(-amount, amount)
        px[x, y] = (
            max(0, min(255, int(r + n))),
            max(0, min(255, int(g + n))),
            max(0, min(255, int(b + n))),
        )
    return img


# ---------------------------------------------------------------- 目标绘制


def draw_traffic_light(draw: ImageDraw.ImageDraw, rng: random.Random, cx: int, cy: int,
                       long_px: float, horizontal: bool):
    """按 SDF 比例画红绿灯；返回 (bbox, cls_id)。

    ``long_px`` 是**整体包围盒的长边**像素长度（两种排布都是 0.59m），
    这样横竖共用同一个尺度参数，和推理端的距离估计口径一致。
    """
    state = rng.choice(["red", "yellow", "green"])
    cls_id = CLS_TL[state][0]

    if horizontal:
        P = TL_H
        W = long_px
        box_w, box_h = P["box_w"] * W, P["box_h"] * W
        lamp_r = max(1.5, P["lamp_r"] * W)
        lamp_off = P["lamp_off"] * W
        x1, y1 = cx - box_w / 2, cy - box_h / 2
        draw.rectangle([x1, y1, x1 + box_w, y1 + box_h], fill=(20, 20, 20), outline=(60, 60, 60))
        # tl_2：绿在 -x、黄居中、红在 +x
        for name, dx in (("red", lamp_off), ("yellow", 0.0), ("green", -lamp_off)):
            lx, ly = cx + dx, cy
            color = STATE_COLORS[name] if name == state else LAMP_OFF
            draw.ellipse([lx - lamp_r, ly - lamp_r, lx + lamp_r, ly + lamp_r], fill=color)
        bbox = (x1, y1, x1 + box_w, y1 + box_h)
    else:
        P = TL_V
        H = long_px
        box_w, box_h = P["box_w"] * H, P["box_h"] * H
        lamp_r = max(1.5, P["lamp_r"] * H)
        lamp_off = P["lamp_off"] * H
        x1, y1 = cx - box_w / 2, cy - H / 2
        draw.rectangle([x1, y1, x1 + box_w, y1 + box_h], fill=(20, 20, 20), outline=(60, 60, 60))
        # tl_1：红在上、黄居中、绿在下
        for name, dy in (("red", -lamp_off), ("yellow", 0.0), ("green", lamp_off)):
            lx, ly = cx, cy + dy
            color = STATE_COLORS[name] if name == state else LAMP_OFF
            draw.ellipse([lx - lamp_r, ly - lamp_r, lx + lamp_r, ly + lamp_r], fill=color)
        bbox = (x1, y1, x1 + box_w, y1 + box_h)

    return bbox, cls_id


def _paste_person(img: Image.Image, rng: random.Random, tex: Image.Image,
                  cx: int, ground_y: int, h_px: float, flip: bool):
    """把真实立牌贴图按 1:3 比例贴上去，返回立牌的像素包围盒。"""
    w_px = max(3.0, h_px * PERSON_W_M / PERSON_H_M)
    t = tex.convert("RGB")
    if flip:
        t = t.transpose(Image.FLIP_LEFT_RIGHT)
    t = t.resize((max(3, int(round(w_px))), max(3, int(round(h_px)))), Image.LANCZOS)

    angle = rng.uniform(-6, 6)          # 立牌被人碰歪一点很常见
    t = t.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=None)
    tw, th = t.size
    x = int(round(cx - tw / 2))
    y = int(round(ground_y - th))
    if t.mode == "RGBA":
        img.paste(t, (x, y), t)
    else:
        img.paste(t, (x, y))
    # 立牌底部有一点底座（SDF 里是贴图自带的白边），包围盒取贴图外接框
    return (float(x), float(y), float(x + tw), float(y + th))


def draw_car_and_plate(img: Image.Image, rng: random.Random, plates, car_tex,
                       cx: int, cy: int, plate_w_px: float):
    """把【车背景 + 真实车牌】按 SDF 的相对位置合成一张小车贴片，旋转后贴到图上。

    返回车牌本身的像素包围盒（class = license_plate）。
    """
    plate = rng.choice(plates).convert("RGB")
    if rng.random() < 0.5:
        plate = plate.transpose(Image.FLIP_LEFT_RIGHT)

    # 由车牌宽度反推整个车的尺寸（SDF: 车宽 0.345 / 车牌宽 0.095）
    car_w = max(12.0, plate_w_px * (CAR_W_M / PLATE_W_M))
    car_h = car_w * (CAR_H_M / CAR_W_M)
    pw = max(6.0, plate_w_px)
    ph = max(3.0, pw * (PLATE_H_M / PLATE_W_M))

    patch = Image.new("RGB", (int(round(car_w)), int(round(car_h))), (60, 60, 60))
    if car_tex is not None:
        patch.paste(car_tex.convert("RGB").resize((patch.width, patch.height), Image.LANCZOS), (0, 0))

    # 车牌中心在车内的位置（SDF 的相对偏移）
    plate_cx = patch.width * (0.5 + PLATE_DX_IN_CAR)
    plate_cy = patch.height * (0.5 + PLATE_DY_IN_CAR)
    pimg = plate.resize((max(3, int(round(pw))), max(3, int(round(ph)))), Image.LANCZOS)
    px0 = int(round(plate_cx - pimg.width / 2))
    py0 = int(round(plate_cy - pimg.height / 2))
    patch.paste(pimg, (px0, py0))

    # 车牌四角在 patch 局部坐标（贴片本身不动时）
    corners = [(px0, py0), (px0 + pimg.width, py0),
               (px0 + pimg.width, py0 + pimg.height), (px0, py0 + pimg.height)]

    # 整体旋转（模拟车不是正对相机的角度）
    angle = rng.uniform(-15, 15)
    rad = math.radians(angle)
    ca, sa = math.cos(rad), math.sin(rad)
    cxp, cyp = patch.width / 2.0, patch.height / 2.0

    def rot(px, py):
        dx, dy = px - cxp, py - cyp
        return (dx * ca - dy * sa + cxp, dx * sa + dy * ca + cyp)

    rc = [rot(*p) for p in corners]
    rot_patch = patch.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=None)
    # rotate(expand=True) 后粘贴原点要按新尺寸平移
    ox = int(round(cx - rot_patch.width / 2))
    oy = int(round(cy - rot_patch.height / 2))
    rcx, rcy = rot_patch.width / 2.0, rot_patch.height / 2.0
    if rot_patch.mode == "RGBA":
        img.paste(rot_patch, (ox, oy), rot_patch)
    else:
        img.paste(rot_patch, (ox, oy))

    # 把车牌四角从「原 patch 坐标系」变换到「旋转后画布 + 粘贴偏移」的图上坐标系
    xs = [ox + (p[0] - cxp) + rcx for p in rc]
    ys = [oy + (p[1] - cyp) + rcy for p in rc]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    return bbox, CLS_PLATE


# ---------------------------------------------------------------- 单张合成


def compose(w: int, h: int, rng: random.Random, plates, community_tex, noncommunity_tex,
            car_tex) -> tuple[Image.Image, list[str]]:
    img = make_background(w, h, rng)

    # 3x3 粗网格放置，避免全部重叠；允许轻微压边模拟遮挡
    cells = [(i, j) for i in range(3) for j in range(3)]
    rng.shuffle(cells)
    n_obj = min(len(cells), rng.choices([0, 1, 2, 3, 4, 5, 6], weights=[5, 10, 16, 20, 20, 16, 13])[0])

    labels: list[str] = []
    for idx in range(n_obj):
        ci, cj = cells[idx]
        cx = int((ci + rng.uniform(0.15, 0.85)) * w / 3)
        cy = int((cj + rng.uniform(0.15, 0.85)) * h / 3)

        # 与真实场景的比例接近：人偶最多，其次车牌，红绿灯最少
        kind = rng.choices(["plate", "person", "tl"], weights=[0.32, 0.42, 0.26])[0]

        if kind == "tl":
            long_px = rng.uniform(0.06, 0.45) * h
            fresh = ImageDraw.Draw(img)
            bbox, cls_id = draw_traffic_light(
                fresh, rng, cx, cy, long_px, horizontal=rng.random() < 0.4
            )
        elif kind == "person":
            # 非社区人员场上只有 2/18，训练时给个略高的采样率帮助学稀有类
            noncommunity = rng.random() < 0.22
            tex = rng.choice(noncommunity_tex if noncommunity else community_tex)
            h_px = rng.uniform(0.06, 0.5) * h
            ground = min(h - 1, cy + int(h_px / 2))
            bbox = _paste_person(img, rng, tex, cx, ground, h_px, flip=rng.random() < 0.5)
            cls_id = CLS_PERSON_NONCOMMUNITY if noncommunity else CLS_PERSON_COMMUNITY
        else:
            plate_w = rng.uniform(0.04, 0.22) * w
            bbox, cls_id = draw_car_and_plate(img, rng, plates, car_tex, cx, cy, plate_w)

        x1 = max(0.0, min(float(w - 1), bbox[0]))
        y1 = max(0.0, min(float(h - 1), bbox[1]))
        x2 = max(0.0, min(float(w - 1), bbox[2]))
        y2 = max(0.0, min(float(h - 1), bbox[3]))
        if x2 - x1 < 6 or y2 - y1 < 6:
            continue
        labels.append(
            f"{cls_id} {((x1 + x2) / 2) / w:.6f} {((y1 + y2) / 2) / h:.6f} "
            f"{(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}"
        )

    # ---------------- 全局增强（模拟不同光照/对焦） ----------------
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.6, 1.45))
    img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.7, 1.35))
    img = ImageEnhance.Color(img).enhance(rng.uniform(0.7, 1.3))
    if rng.random() < 0.35:
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.4)))
    return img, labels


# ---------------------------------------------------------------- 主流程


def _load_dir(textures_dir: str, prefix: str):
    if not os.path.isdir(textures_dir):
        return []
    files = [f for f in sorted(os.listdir(textures_dir))
             if f.startswith(prefix) and f.endswith(".png")]
    return [Image.open(os.path.join(textures_dir, f)) for f in files]


def _load_one(textures_dir: str, name: str):
    p = os.path.join(textures_dir, name)
    return Image.open(p) if os.path.isfile(p) else None


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    default_tex = os.path.normpath(os.path.join(here, "..", "..", "smart_community_sim", "textures"))

    ap = argparse.ArgumentParser(description="离线合成 YOLO 数据集")
    ap.add_argument("--out", required=True, help="输出数据集目录")
    ap.add_argument("--n", type=int, default=2000, help="图片数量")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--textures", default=default_tex, help="贴图目录")
    args = ap.parse_args()

    out = os.path.expanduser(args.out)
    img_dir = os.path.join(out, "images", "all")
    lbl_dir = os.path.join(out, "labels", "all")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(lbl_dir, exist_ok=True)

    rng = random.Random(args.seed)

    plates = _load_dir(args.textures, "plate_")
    community = _load_dir(args.textures, "person_community_")
    noncommunity = _load_dir(args.textures, "person_noncommunity_")
    car_tex = _load_one(args.textures, "car_background.png")

    print(f"贴图目录: {args.textures}")
    print(f"  车牌 {len(plates)} 张 | 社区人偶 {len(community)} 张 | "
          f"非社区人偶 {len(noncommunity)} 张 | 车背景 {'有' if car_tex else '无'}")
    if not plates:
        print("[警告] 没找到车牌贴图，车牌将用纯色占位")
        plates = [Image.new("RGB", (380, 120), (20, 60, 160))]
    if not community:
        print("[警告] 没找到社区人偶贴图，人偶将用纯色占位")
        community = [Image.new("RGB", (200, 600), (26, 140, 77))]
    if not noncommunity:
        noncommunity = [Image.new("RGB", (200, 600), (217, 102, 38))]

    hist = {i: 0 for i in range(len(CLASS_NAMES))}
    for i in range(args.n):
        img, labels = compose(args.width, args.height, rng, plates, community, noncommunity, car_tex)
        stem = f"synth_{i:06d}"
        img.save(os.path.join(img_dir, stem + ".jpg"), quality=rng.randint(78, 95))
        with open(os.path.join(lbl_dir, stem + ".txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(labels) + ("\n" if labels else ""))
        for line in labels:
            hist[int(line.split()[0])] += 1

    print(f"\n已生成 {args.n} 张 -> {out}")
    print("类别实例数:")
    for i, name in enumerate(CLASS_NAMES):
        print(f"  {i} {name:<22} {hist[i]}")
    print("\n下一步: python tools/split_dataset.py --dataset " + out)


if __name__ == "__main__":
    main()
