#!/usr/bin/env python3
"""生成智慧社区仿真所需的全部 PNG 纹理（离线、可复现）。

产物输出到 ../textures/（相对本脚本目录），SDF 中用相对路径引用。
依赖：Pillow + 一个中文字体（默认取 Windows 的 simhei.ttf，可 --font 覆盖）。
"""
import argparse
import math
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "textures"))

FONT_CANDIDATES = [
    "/mnt/c/Windows/Fonts/simhei.ttf",
    "/mnt/c/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def load_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def centered_text(draw, xy, text, font, fill):
    """在 (x, y) 居中绘制文本，返回文本包围盒宽度。"""
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x, y = xy
    draw.text((x - w / 2 - bbox[0], y - h / 2 - bbox[1]), text, font=font, fill=fill)
    return w


# ---------------------------------------------------------------------------
# 车牌
# ---------------------------------------------------------------------------
def make_plate(path, number, bg):
    """标准车牌：蓝/绿底白字。number 形如 '京A·5Q138' 或 '京AD12345'。"""
    w, h = 440, 140
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    # 白色边框
    d.rectangle([6, 6, w - 7, h - 7], outline=(255, 255, 255), width=3)
    font = load_font(64)
    centered_text(d, (w / 2, h / 2), number, font, (255, 255, 255))
    img.save(path)


# ---------------------------------------------------------------------------
# 仪表盘（带指针读数）
# ---------------------------------------------------------------------------
def make_gauge(path, unit, lo, hi, value, major_step, label):
    """圆形压力/温度表。量程 lo..hi，指针指向 value。"""
    size = 256
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    R = size / 2 - 14
    # 表盘底
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(245, 245, 245),
              outline=(60, 60, 60), width=4)

    # 刻度从 -135° 到 +135°（顺时针，0°=正右）
    start_a, end_a = -135, 135
    span = end_a - start_a
    frac = (value - lo) / (hi - lo)

    def pt(angle_deg, r):
        a = math.radians(angle_deg)
        return (cx + r * math.cos(a), cy + r * math.sin(a))

    # 主刻度 + 数字
    n_major = round((hi - lo) / major_step)
    fnum = load_font(16)
    for i in range(n_major + 1):
        v = lo + i * major_step
        ang = start_a + span * (i / n_major)
        p1 = pt(ang, R - 8)
        p2 = pt(ang, R - 20)
        d.line([p1, p2], fill=(40, 40, 40), width=2)
        # 数字放在稍内侧
        pn = pt(ang, R - 34)
        txt = ("%g" % v)
        bb = d.textbbox((0, 0), txt, font=fnum)
        d.text((pn[0] - (bb[2] - bb[0]) / 2, pn[1] - (bb[3] - bb[1]) / 2),
               txt, font=fnum, fill=(40, 40, 40))
    # 小刻度
    for i in range(n_major):
        for k in range(1, 4):
            ang = start_a + span * ((i + k / 4) / n_major)
            d.line([pt(ang, R - 8), pt(ang, R - 14)], fill=(120, 120, 120), width=1)

    # 单位 / 标签
    centered_text(d, (cx, cy - R + 40), label, load_font(18), (60, 60, 60))
    centered_text(d, (cx, cy + R - 40), unit, load_font(16), (90, 90, 90))

    # 指针
    ang = start_a + span * frac
    tip = pt(ang, R - 34)
    d.line([cx, cy, tip[0], tip[1]], fill=(200, 30, 30), width=4)
    # 中心轴
    d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(40, 40, 40))
    img.save(path)


# ---------------------------------------------------------------------------
# 交通指示牌
# ---------------------------------------------------------------------------
def make_no_straight(path):
    """禁止直行：蓝底圆 + 白色直行箭头 + 红斜杠。"""
    size = 256
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    R = size / 2 - 10
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(0, 80, 200),
              outline=(255, 255, 255), width=6)
    # 白色向上箭头（粗）
    d.polygon([(cx, cy - 70), (cx - 34, cy - 20), (cx - 16, cy - 20),
               (cx - 16, cy + 55), (cx + 16, cy + 55), (cx + 16, cy - 20),
               (cx + 34, cy - 20)], fill=(255, 255, 255))
    # 红色斜杠（从左下到右上）
    d.line([cx - 72, cy + 72, cx + 72, cy - 72], fill=(220, 30, 30), width=16)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(255, 255, 255), width=4)
    img.save(path)


def make_speed_limit(path, num):
    """限速牌：白底红圈 + 数字。"""
    size = 256
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    R = size / 2 - 10
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(255, 255, 255),
              outline=(200, 30, 30), width=16)
    centered_text(d, (cx, cy), num, load_font(96), (30, 30, 30))
    img.save(path)


def make_no_parking(path):
    """禁止停车：蓝底 + 红色斜杠 + 大写 P。"""
    size = 256
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    R = size / 2 - 10
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(0, 80, 200),
              outline=(255, 255, 255), width=6)
    centered_text(d, (cx, cy), "P", load_font(120), (255, 255, 255))
    d.line([cx - 72, cy + 72, cx + 72, cy - 72], fill=(220, 30, 30), width=16)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(255, 255, 255), width=4)
    img.save(path)


# ---------------------------------------------------------------------------
# 文字标牌 / 标签
# ---------------------------------------------------------------------------
def make_label(path, text, fg=(255, 255, 255), bg=(40, 40, 40), size=64):
    """矩形文字标牌。"""
    font = load_font(size)
    tmp = Image.new("RGB", (16, 16))
    td = ImageDraw.Draw(tmp)
    bb = td.textbbox((0, 0), text, font=font)
    w = bb[2] - bb[0] + 40
    h = bb[3] - bb[1] + 28
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    centered_text(d, (w / 2, h / 2), text, font, fg)
    img.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", help="中文字体文件路径")
    args = ap.parse_args()
    if args.font:
        FONT_CANDIDATES.insert(0, args.font)

    os.makedirs(OUT, exist_ok=True)

    # 车牌（蓝牌 3 辆 + 绿牌电动车 1 辆）
    make_plate(os.path.join(OUT, "plate_A.png"), "京A·5Q138", (0, 51, 153))
    make_plate(os.path.join(OUT, "plate_B.png"), "京B·8T926", (0, 51, 153))
    make_plate(os.path.join(OUT, "plate_C.png"), "京C·3P571", (0, 51, 153))
    make_plate(os.path.join(OUT, "plate_ev.png"), "京AD12345", (0, 150, 80))

    # 仪表盘
    make_gauge(os.path.join(OUT, "meter_pressure.png"), "MPa", 0.0, 1.0,
               0.65, 0.2, "压力")
    make_gauge(os.path.join(OUT, "meter_temp.png"), "°C", 0.0, 100.0,
               45.0, 20.0, "温度")

    # 指示牌
    make_no_straight(os.path.join(OUT, "sign_no_straight.png"))
    make_speed_limit(os.path.join(OUT, "sign_speed.png"), "15")
    make_no_parking(os.path.join(OUT, "sign_no_parking.png"))

    # 区域标签
    make_label(os.path.join(OUT, "label_building_a.png"), "楼宇A")
    make_label(os.path.join(OUT, "label_building_b.png"), "楼宇B")
    make_label(os.path.join(OUT, "label_building_c.png"), "楼宇C")
    make_label(os.path.join(OUT, "label_building_d.png"), "楼宇D")
    make_label(os.path.join(OUT, "label_station.png"), "站房")
    make_label(os.path.join(OUT, "label_parking.png"), "停车场")
    make_label(os.path.join(OUT, "label_trash.png"), "垃圾分类投放点")
    make_label(os.path.join(OUT, "label_ev.png"), "电动车停车区")
    make_label(os.path.join(OUT, "label_start.png"), "出发区")

    # 人偶身份
    make_label(os.path.join(OUT, "label_community.png"), "社区人员",
               fg=(255, 255, 255), bg=(0, 120, 60))
    make_label(os.path.join(OUT, "label_visitor.png"), "外来人员",
               fg=(255, 255, 255), bg=(200, 90, 20))

    # 垃圾桶分类
    make_label(os.path.join(OUT, "trash_recyclable.png"), "可回收",
               bg=(0, 90, 180))
    make_label(os.path.join(OUT, "trash_other.png"), "其他",
               bg=(80, 80, 80))
    make_label(os.path.join(OUT, "trash_hazardous.png"), "有害",
               bg=(200, 30, 30))
    make_label(os.path.join(OUT, "trash_kitchen.png"), "厨余",
               bg=(0, 140, 60))

    print("生成完成 ->", OUT)
    for f in sorted(os.listdir(OUT)):
        print("  ", f)


if __name__ == "__main__":
    main()
