#!/usr/bin/env python3
"""离线合成数据集生成器（不需要跑 Gazebo）。

什么时候用
----------
* 想先验证「训练 -> 推理 -> 控制」整条链路是否通
* 仿真环境还没配好，但想先有一个能用的模型
* 采集到的真实帧不够，需要补充**稀有类别**（尤其黄灯只有 3s，很难拍到）

注意
----
合成图与 Gazebo 真实渲染**存在域差**（光照/材质/背景不同）。
推荐做法：先合成一批把链路跑通，再用 ``autolabel_capture`` 采集真实帧，
最后**混合训练**（把两批数据都放进 images/all）。

几何比例严格按 world SDF 来画，保证包围盒定义与自动标注完全一致：
    红绿灯(竖) 灯箱 0.24x0.16x0.62 + 灯球 r=0.09 -> 整体包围盒高 0.78
    红绿灯(横) 灯箱 0.16x0.62x0.24 + 灯球 r=0.09 -> 整体包围盒宽 0.74
    人偶       板 0.6x1.6 + 头球 r=0.18            -> 整体 0.6 x 1.83
    车牌       0.5 x 0.16

依赖: pip install pillow
用法::

    python tools/gen_synth_dataset.py --out ~/whc_synth --n 1500
"""
from __future__ import annotations

import argparse
import math
import os
import random

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

CLASS_NAMES = [
    "traffic_light_red",
    "traffic_light_yellow",
    "traffic_light_green",
    "person",
    "license_plate",
]
CLS_TL = {  # 状态 -> 类别 id / 灯色
    "red": (0, (255, 40, 40)),
    "yellow": (1, (255, 191, 0)),
    "green": (2, (0, 178, 51)),
}
CLS_PERSON = 3
CLS_PLATE = 4
LAMP_OFF = (32, 32, 32)

# 与插件 TrafficLightSystem.cc 一致的灯色
STATE_COLORS = {k: v[1] for k, v in CLS_TL.items()}

# 由 SDF 推导的比例（全部相对于整体包围盒）
TL_V = dict(box_w=0.24 / 0.78, box_h=0.62 / 0.78, lamp_r=0.09 / 0.78, lamp_dy=0.30 / 0.78)
TL_H = dict(box_w=0.62 / 0.74, box_h=0.24 / 0.74, lamp_r=0.09 / 0.74, lamp_dx=0.28 / 0.74)
PERSON = dict(board_w=0.60 / 1.83, board_h=1.60 / 1.83, head_r=0.18 / 1.83,
              label_w=0.60 / 1.83, label_h=0.30 / 1.83)


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

    # 噪声：让模型不依赖“干净色块”这种捷径
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

    ``long_px`` 是**整体包围盒的长边**像素长度（竖排=高 0.78m，横排=宽 0.74m），
    这样两种排布共用同一个尺度参数，和推理端的距离估计口径一致。
    """
    state = rng.choice(["red", "yellow", "green"])
    cls_id = CLS_TL[state][0]

    if horizontal:
        W = long_px
        box_w, box_h = TL_H["box_w"] * W, TL_H["box_h"] * W
        lamp_r = TL_H["lamp_r"] * W
        lamp_dx = TL_H["lamp_dx"] * W
        x1, y1 = cx - box_w / 2, cy - box_h / 2
        draw.rectangle([x1, y1, x1 + box_w, y1 + box_h], fill=(20, 20, 20), outline=(60, 60, 60))
        for name, dx in (("red", lamp_dx), ("yellow", 0.0), ("green", -lamp_dx)):
            lx = cx + dx
            ly = cy
            color = STATE_COLORS[name] if name == state else LAMP_OFF
            draw.ellipse([lx - lamp_r, ly - lamp_r, lx + lamp_r, ly + lamp_r], fill=color)
        bbox = (cx - W / 2, cy - box_h / 2, cx + W / 2, cy + box_h / 2)
    else:
        H = long_px
        box_w = TL_V["box_w"] * H
        box_h = TL_V["box_h"] * H
        lamp_r = TL_V["lamp_r"] * H
        lamp_dy = TL_V["lamp_dy"] * H
        x1, y1 = cx - box_w / 2, cy - H / 2
        draw.rectangle([x1, y1, x1 + box_w, y1 + box_h], fill=(20, 20, 20), outline=(60, 60, 60))
        for name, dy in (("red", -lamp_dy), ("yellow", 0.0), ("green", lamp_dy)):
            lx, ly = cx, cy + dy
            color = STATE_COLORS[name] if name == state else LAMP_OFF
            draw.ellipse([lx - lamp_r, ly - lamp_r, lx + lamp_r, ly + lamp_r], fill=color)
        bbox = (cx - box_w / 2, cy - H / 2, cx + box_w / 2, cy + H / 2)

    return bbox, cls_id


def draw_person(draw: ImageDraw.ImageDraw, rng: random.Random, cx: int, ground_y: int, H: float):
    """按 SDF 比例画人偶立牌；返回 (bbox, cls_id)。"""
    visitor = rng.random() < 0.35
    board = (217, 102, 38) if visitor else (26, 140, 77)
    board_w = PERSON["board_w"] * H
    board_h = PERSON["board_h"] * H
    head_r = PERSON["head_r"] * H

    top = ground_y - H
    bx1 = cx - board_w / 2
    # 立板
    draw.rectangle([bx1, top, bx1 + board_w, top + board_h], fill=board)
    # 底座
    base_w = 0.5 / 1.83 * H
    draw.rectangle([cx - base_w / 2, ground_y - 0.04 / 1.83 * H,
                    cx + base_w / 2, ground_y], fill=(102, 102, 102))
    # 头
    head_cy = top + 1.65 / 1.83 * H
    draw.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r], fill=(245, 214, 179))
    # 标签牌
    lw, lh = PERSON["label_w"] * H, PERSON["label_h"] * H
    ly = top + 1.0 / 1.83 * H - lh / 2
    draw.rectangle([cx - lw / 2, ly, cx + lw / 2, ly + lh], fill=(245, 245, 240))
    for k in range(3):  # 文字示意
        yy = ly + lh * (0.25 + 0.25 * k)
        draw.rectangle([cx - lw * 0.34, yy, cx + lw * 0.34, yy + max(1, lh * 0.08)], fill=(70, 70, 70))

    bbox = (cx - board_w / 2, top, cx + board_w / 2, ground_y)
    return bbox, CLS_PERSON


def draw_car_and_plate(img: Image.Image, rng: random.Random, plates, cx: int, cy: int, pw: float):
    """画一块车身色 + 贴上真实车牌纹理，返回 (bbox, cls_id)。"""
    plate = rng.choice(plates).convert("RGB")
    if rng.random() < 0.5:
        plate = plate.transpose(Image.FLIP_LEFT_RIGHT)
    ph = max(8, int(round(pw * plate.height / plate.width)))
    plate = plate.resize((max(8, int(pw)), ph), Image.LANCZOS)

    angle = rng.uniform(-12, 12)
    plate = plate.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=None)
    pw2, ph2 = plate.size

    # 车身底板（车牌总是贴在车上）
    body = tuple(int(c * rng.uniform(0.7, 1.0)) for c in
                 rng.choice([(140, 26, 26), (26, 60, 140), (230, 230, 230), (40, 40, 40)]))
    pad_x, pad_y = pw2 * 0.8, ph2 * 1.6
    x1, y1 = cx - pw2 / 2, cy - ph2 / 2
    d = ImageDraw.Draw(img)
    d.rectangle([x1 - pad_x, y1 - pad_y, x1 + pw2 + pad_x, y1 + ph2 + pad_y], fill=body)

    img.paste(plate, (int(x1), int(y1)), plate if plate.mode == "RGBA" else None)
    return (x1, y1, x1 + pw2, y1 + ph2), CLS_PLATE


# ---------------------------------------------------------------- 单张合成


def compose(w: int, h: int, rng: random.Random, plates) -> tuple[Image.Image, list[str]]:
    img = make_background(w, h, rng)
    draw = ImageDraw.Draw(img)

    # 3x3 粗网格放置，避免全部重叠；允许轻微压边模拟遮挡
    cells = [(i, j) for i in range(3) for j in range(3)]
    rng.shuffle(cells)
    n_obj = min(len(cells), rng.choices([0, 1, 2, 3, 4, 5, 6], weights=[6, 12, 18, 22, 20, 14, 8])[0])

    labels: list[str] = []
    for idx in range(n_obj):
        ci, cj = cells[idx]
        cx = int((ci + rng.uniform(0.15, 0.85)) * w / 3)
        cy = int((cj + rng.uniform(0.15, 0.85)) * h / 3)

        kind = rng.choices(
            ["plate", "person", "tl"],
            weights=[0.30, 0.28, 0.42],
        )[0]

        if kind == "tl":
            long_px = rng.uniform(0.06, 0.42) * h
            bbox, cls_id = draw_traffic_light(
                draw, rng, cx, cy, long_px, horizontal=rng.random() < 0.4
            )
        elif kind == "person":
            H = rng.uniform(0.15, 0.75) * h
            ground = min(h - 1, cy + int(H / 2))
            bbox, cls_id = draw_person(draw, rng, cx, ground, H)
        else:
            pw = rng.uniform(0.05, 0.28) * w
            bbox, cls_id = draw_car_and_plate(img, rng, plates, cx, cy, pw)
            draw = ImageDraw.Draw(img)

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


def find_plates(textures_dir: str):
    if not os.path.isdir(textures_dir):
        return None
    files = [f for f in sorted(os.listdir(textures_dir)) if f.startswith("plate_") and f.endswith(".png")]
    if not files:
        return None
    return [Image.open(os.path.join(textures_dir, f)) for f in files]


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    default_tex = os.path.normpath(os.path.join(here, "..", "..", "smart_community_sim", "textures"))

    ap = argparse.ArgumentParser(description="离线合成 YOLO 数据集")
    ap.add_argument("--out", required=True, help="输出数据集目录")
    ap.add_argument("--n", type=int, default=1500, help="图片数量")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--textures", default=default_tex, help="plate_*.png 所在目录")
    args = ap.parse_args()

    out = os.path.expanduser(args.out)
    img_dir = os.path.join(out, "images", "all")
    lbl_dir = os.path.join(out, "labels", "all")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(lbl_dir, exist_ok=True)

    rng = random.Random(args.seed)
    plates = find_plates(args.textures)
    if plates:
        print(f"使用真实车牌纹理 {len(plates)} 张: {args.textures}")
    else:
        print(f"[警告] 没找到车牌纹理（{args.textures}），车牌将用纯色占位")

    hist = {i: 0 for i in range(len(CLASS_NAMES))}
    for i in range(args.n):
        img, labels = compose(args.width, args.height, rng, plates or [Image.new("RGB", (440, 140), (20, 60, 160))])
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
