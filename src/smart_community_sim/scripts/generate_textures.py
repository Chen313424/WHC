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
import random

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.normpath(os.path.join(HERE, ".."))
MAT = os.path.join(PKG, "materials")
OUT = os.path.normpath(os.path.join(HERE, "..", "textures"))

# 纹理分辨率（px/cm）。原为 100，但在 WSLg 的 d3d12 核显渲染下，车辆背景
# 贴图（34.5×25cm -> 3450×2500）会超出 ogre2 的纹理显存预算被丢弃、渲染成
# 黑卡。降到 40 px/cm 后，车牌字符仍有 ~60px 高，OCR/识别完全够用。
PX_PER_CM = 40

# 车牌字体（黑体，贴近样例蓝牌字体；从 Windows 字体目录读取）
_FONT_CANDIDATES = [
    "/mnt/c/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

# 省简称（31 个）与车牌可用字符（不含 I/O，避免与 1/0 混淆）
_PROVINCES = list("京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼")
_PLATE_CHARS = list("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789")


def _plate_font(size):
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render_plate(text, size=(200, 81)):
    """按样例蓝牌样式生成一张车牌：蓝底 + 白边 + 白色高瘦字符。

    样例牌为 200×81，字符约 20px 宽、60px 高（竖长形）。这里先用黑体渲染
    整串文字，再按样例文字框（194×75）等比缩放 + 纵向拉伸到同样比例。
    """
    w, h = size
    top, bot = (0, 12, 105), (0, 28, 130)  # 蓝色渐变（上深下浅）
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        c = (int(top[0] + (bot[0] - top[0]) * t),
             int(top[1] + (bot[1] - top[1]) * t),
             int(top[2] + (bot[2] - top[2]) * t))
        for x in range(w):
            px[x, y] = c

    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=(255, 255, 255), width=2)

    font = _plate_font(40)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((-bbox[0], -bbox[1]), text,
                               font=font, fill=(255, 255, 255, 255))

    target_w, target_h = w - 6, h - 6  # 194 × 75
    layer = layer.resize((target_w, int(th * target_w / tw)), Image.LANCZOS)
    layer = layer.resize((target_w, target_h), Image.LANCZOS)  # 纵向拉伸
    img.paste(layer, (3, 3), layer)
    return img


def random_license(rng):
    """生成随机车牌号：省简称 + 字母 + · + 5 位字符。"""
    return (rng.choice(_PROVINCES) + rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")
            + "·" + "".join(rng.choice(_PLATE_CHARS) for _ in range(5)))


def render_label(text, fg=(0, 0, 0), bg=(255, 255, 255), font_size=56, pad=6):
    """把中文/数字标注渲染成一张白底黑字的小图（供地面/标牌贴图用）。

    返回 (Image, 宽cm, 高cm)——宽高按 100 px/cm 折算，供世界按比例铺贴。
    """
    font = _plate_font(font_size)
    probe = Image.new("RGBA", (8, 8))
    d = ImageDraw.Draw(probe)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    img = Image.new("RGB", (tw + pad * 2, th + pad * 2), bg)
    ImageDraw.Draw(img).text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=fg)
    return img


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

    # 车牌：plate_1 用样例图；plate_2 / plate_3 按样例蓝牌样式随机生成。
    # 固定随机种子，保证重跑脚本产物可复现（改种子即可换车牌号）。
    rng = random.Random(20261005)
    for i in range(1, 4):
        src = os.path.join(MAT, "plates", f"plate_{i}.png")
        if i == 1 and os.path.exists(src):
            process_plate(src, os.path.join(OUT, "plate_1.png"))
        elif i >= 2:
            lic = random_license(rng)
            render_plate(lic).resize(board_canvas(9.5, 3),
                                     Image.LANCZOS).save(
                os.path.join(OUT, f"plate_{i}.png"))
            print(f"  随机车牌 plate_{i}: {lic}")
    car_bg = os.path.join(MAT, "plates", "car_background.png")
    if os.path.exists(car_bg):
        # 车背景立牌 34.5cm × 25cm，等比拉伸到 3450×2500（100 px/cm）
        Image.open(car_bg).convert("RGB").resize(
            board_canvas(34.5, 25), Image.LANCZOS).save(os.path.join(OUT, "car_background.png"))

    # 文字标注（地面/标牌贴图）：起点、终点、A/B临区、停车位编号、60cm 尺寸
    labels = {
        "label_start": "起点",
        "label_end": "终点",
        "label_a": "A临区",
        "label_b": "B临区",
        "label_60cm": "60cm",
        "label_spot_3": "3号停车位",
        "label_spot_2": "2号停车位",
        "label_spot_1": "1号停车位",
    }
    for name, text in labels.items():
        img = render_label(text)
        img.save(os.path.join(OUT, f"{name}.png"))
        print(f"  标注 {name}: {text} ({img.size[0]}x{img.size[1]}px)")

    print("生成完成 ->", OUT)
    for f in sorted(os.listdir(OUT)):
        print("  ", f)


if __name__ == "__main__":
    main()
