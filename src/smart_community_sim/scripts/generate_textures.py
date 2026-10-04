#!/usr/bin/env python3
"""省赛（4.2m×4.2m 场地）纹理生成：把复赛资料里的模型图处理成可用纹理。

素材放在 materials/（从 D:\\DESK\\复赛资料 复制），输出到 ../textures/。
- 人偶立牌：16 个社区人员 + 2 个非社区人员，透明底人物照合成到白色
  底板（高 15cm × 宽 5cm，即 3:1）。
- 车牌：3 个（高 3cm × 宽 9.5cm）。
- 车背景：1 个（高 25cm × 宽 34.5cm）。

分辨率按 100 px/cm 输出（立牌 1500×500，车牌 950×300）。
运行：python3 scripts/generate_textures.py
"""
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.normpath(os.path.join(HERE, ".."))
MAT = os.path.join(PKG, "materials")
OUT = os.path.normpath(os.path.join(HERE, "..", "textures"))

# 100 px/cm
PX_PER_CM = 100


def board_canvas(w_cm, h_cm):
    return (int(w_cm * PX_PER_CM), int(h_cm * PX_PER_CM))


def process_person(src, dst):
    """人物透明底照片 -> 白色 15cm×5cm 立牌底板（等比缩放、居中）。"""
    img = Image.open(src).convert("RGBA")
    canvas = board_canvas(5, 15)  # 宽 5cm、高 15cm
    cw, ch = canvas
    iw, ih = img.size
    scale = min(cw / iw, ch / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)
    out = Image.new("RGBA", canvas, (255, 255, 255, 255))
    out.paste(resized, ((cw - nw) // 2, (ch - nh) // 2), resized)
    out.convert("RGB").save(dst)


def process_plate(src, dst):
    """车牌 200×81 等比拉伸到 9.5cm×3cm（950×300）。"""
    img = Image.open(src).convert("RGB")
    img.resize(board_canvas(9.5, 3), Image.LANCZOS).save(dst)


def main():
    os.makedirs(OUT, exist_ok=True)

    # 人偶：16 社区 + 2 非社区
    for i in range(1, 17):
        src = os.path.join(MAT, "people_community", f"{i}.png")
        dst = os.path.join(OUT, f"person_community_{i:02d}.png")
        if os.path.exists(src):
            process_person(src, dst)
    for i, name in enumerate(["F1", "F2"], start=1):
        src = os.path.join(MAT, "people_noncommunity", f"{name}.png")
        dst = os.path.join(OUT, f"person_noncommunity_{i:02d}.png")
        if os.path.exists(src):
            process_person(src, dst)

    # 车牌 3 个 + 车背景 1 个
    for i in range(1, 4):
        src = os.path.join(MAT, "plates", f"plate_{i}.png")
        if os.path.exists(src):
            process_plate(src, os.path.join(OUT, f"plate_{i}.png"))
    car_bg = os.path.join(MAT, "plates", "car_background.png")
    if os.path.exists(car_bg):
        # 车背景立牌 34.5cm × 25cm，等比拉伸到 3450×2500（100 px/cm）
        Image.open(car_bg).convert("RGB").resize(
            board_canvas(34.5, 25), Image.LANCZOS).save(os.path.join(OUT, "car_background.png"))

    print("生成完成 ->", OUT)
    for f in sorted(os.listdir(OUT)):
        print("  ", f)


if __name__ == "__main__":
    main()
