#!/usr/bin/env python3
"""车牌 OCR 离线自检（不需要 ROS / Gazebo / 仿真）。

跑法::

    cd src/smart_community_perception
    python test/test_plate_ocr.py

依赖 numpy + Pillow。覆盖：
  * 三张已知车牌贴图都能被完整还原成正确字符
  * 切字自适应分段数正确（省字/字母/分隔点/5 位 = 7 段）
  * 缩放、模糊、亮度/对比扰动后仍然识别正确（模拟相机成像差异）
  * 整牌匹配（identify）与字符级结果一致
  * 空图/极小图不崩、不给出高置信度错误结果
"""
from __future__ import annotations

import os
import random
import sys

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from smart_community_perception.plate_ocr import (  # noqa: E402
    CANON_H,
    CANON_W,
    PLATE_TEXTS,
    PlateOCR,
    segment_glyphs,
    _to_gray_mask,
)

_RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _RESULTS.append((bool(cond), name, detail))


def _tex_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "smart_community_sim", "textures"))


def _load(stem: str) -> Image.Image:
    return Image.open(os.path.join(_tex_dir(), stem + ".png")).convert("RGB")


def _augment(img: Image.Image, rng: random.Random) -> Image.Image:
    """模拟相机成像差异：尺度、模糊、亮度/对比、噪声。"""
    scale = rng.uniform(0.45, 1.6)
    w, h = max(24, int(380 * scale)), max(8, int(120 * scale))
    out = img.resize((w, h), Image.LANCZOS)
    if rng.random() < 0.6:
        out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.3)))
    out = ImageEnhance.Brightness(out).enhance(rng.uniform(0.65, 1.35))
    out = ImageEnhance.Contrast(out).enhance(rng.uniform(0.7, 1.3))
    arr = np.asarray(out, dtype=np.float32)
    # ★ 噪声必须来自【已播种】的随机源，否则整个用例不可复现
    #   （实测：直接用 np.random.normal 时，12 次连跑里约 5 次会随机失败）。
    #   从已播种的 rng 派生一个 numpy Generator：既确定又不必逐像素调 rng.gauss（太慢）。
    nprng = np.random.default_rng(rng.getrandbits(32))
    arr += nprng.normal(0, rng.uniform(0, 12), arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


#: 倾斜用例的角度与牌宽（都取确定的固定值，保证用例可复现）
_TILT_ANGLES = (-15, -12, -8, -4, 4, 8, 12, 15)
_TILT_WIDTHS = (80, 120)


def _tilted(stem: str, angle: float, width: int) -> np.ndarray:
    """把车牌旋转 angle 度后取它的【轴向包围盒】裁出来（模拟 YOLO 检出的框）。

    车牌在画面里是斜的时候，检出的框是"旋转矩形的轴向包围盒"，宽高比会从
    标准的 3.17 掉到 2.0 左右 —— 这正是识别失败的直接原因，所以必须专门覆盖。
    返回 BGR ndarray（与节点喂给 OCR 的完全一致）。
    """
    img = _load(stem)
    h = max(3, int(round(width * CANON_H / CANON_W)))
    pl = img.resize((width, h), Image.LANCZOS)
    pad, bg = 40, (90, 90, 90)
    canvas = Image.new("RGB", (width + 2 * pad, h + 2 * pad), bg)
    canvas.paste(pl, (pad, pad))
    rot = canvas.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=bg)
    arr = np.asarray(rot)
    diff = np.abs(arr.astype(int) - np.array(bg)).sum(axis=2) > 12
    ys, xs = np.where(diff)
    crop = arr[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1]
    return np.ascontiguousarray(crop[:, :, ::-1])       # RGB -> BGR


def main() -> int:
    ocr = PlateOCR()
    print(f"模板库: 字符 {len(ocr.known_characters)} 个 -> {ocr.known_characters}")
    print(f"        车牌 {len(ocr._plate_texts)} 张")
    check("模板库非空", ocr.ready, f"chars={ocr.known_characters}")

    if not ocr.ready:
        print("[X] 模板库没建起来，后面的用例没有意义")
        return _report()

    # ---------------- 1. 原始贴图 ----------------
    for stem, text in PLATE_TEXTS.items():
        res = ocr.recognize(_load(stem))
        check(f"原图识别 {stem}", res.text == text, f"得到 {res.text!r}（期望 {text!r}）mode={res.mode} score={res.score:.3f}")

    # ---------------- 2. 分段数 ----------------
    for stem, text in PLATE_TEXTS.items():
        mask = _to_gray_mask(_load(stem).resize((CANON_W, CANON_H), Image.LANCZOS))
        segs = segment_glyphs(mask)
        check(
            f"分段数 {stem}",
            len(segs) == len(text),
            f"切出 {len(segs)} 段（车牌 {len(text)} 个字符）",
        )

    # ---------------- 3. 扰动后仍要正确 ----------------
    rng = random.Random(20261006)
    total = 0
    good = 0
    bad: list[str] = []
    for stem, text in PLATE_TEXTS.items():
        base = _load(stem)
        for i in range(12):
            img = _augment(base, rng)
            res = ocr.recognize(img)
            total += 1
            if res.text == text:
                good += 1
            else:
                bad.append(f"{stem}#{i} 得到 {res.text!r} (mode={res.mode}, score={res.score:.3f})")
    check(
        "扰动后识别率",
        good == total,
        f"{good}/{total} 正确" + ("" if not bad else "；失败样例: " + "; ".join(bad[:4])),
    )

    # ---------------- 3b. 真实喂法：BGR ndarray ----------------
    #  节点就是这样传图的：yolo_detector_node._to_numpy 把 rgb8 反成 BGR、
    #  autolabel_capture_node 用 cv_bridge 的 desired_encoding="bgr8"。
    #  ★ 踩过的坑：_as_image 早先"不区分通道顺序"，等于把 BGR 当 RGB 用，
    #    灰度与模板库（PIL 读的 RGB 贴图）对不上 —— 同一张牌传 PIL 能读出
    #    '黑T·U1KG9'（0.817），传 BGR ndarray 就退化成 '黑??????'。
    #    早先的用例全传 PIL Image，所以这条真实路径一直没被覆盖。
    for stem, text in PLATE_TEXTS.items():
        bgr = np.ascontiguousarray(np.asarray(_load(stem))[:, :, ::-1])
        res = ocr.recognize(bgr)
        check(f"BGR ndarray 识别 {stem}", res.text == text,
              f"得到 {res.text!r}（期望 {text!r}）mode={res.mode} score={res.score:.3f}")
        small = np.ascontiguousarray(
            np.asarray(_load(stem).resize((110, 35), Image.LANCZOS))[:, :, ::-1])
        res = ocr.recognize(small)
        check(f"BGR ndarray 缩小后 {stem}", res.text == text,
              f"得到 {res.text!r}（期望 {text!r}）mode={res.mode} score={res.score:.3f}")

    # ---------------- 3c. 倾斜车牌（仿真里相机不会正对车牌）----------------
    #  改造前这批输入只有 16% 能读出（实测 13/81），加"转正 + 裁内容包围盒"后是 75%。
    tilt_total = tilt_ok = 0
    tilt_bad: list[str] = []
    for stem, text in PLATE_TEXTS.items():
        for angle in _TILT_ANGLES:
            for width in _TILT_WIDTHS:
                res = ocr.recognize(_tilted(stem, angle, width))
                tilt_total += 1
                if res.text == text:
                    tilt_ok += 1
                else:
                    tilt_bad.append(f"{stem}@{angle}°/{width}px -> {res.text!r}")
    check("倾斜车牌识别率 >= 65%", tilt_ok >= tilt_total * 0.65,
          f"{tilt_ok}/{tilt_total} 正确"
          + ("" if not tilt_bad else "；失败样例: " + "; ".join(tilt_bad[:4])))

    # ---------------- 3d. 退化输入不能抛异常 ----------------
    #  _crop_glyph_gray 早先对空字符框返回 4x4 零图，_glyph_vector 就只有 16 维，
    #  再与 2048 维模板 np.dot 会直接抛 ValueError（recognize 曾被这个崩掉）。
    crash = ""
    for name, bad_in in (
        ("纯白", np.full((14, 44, 3), 255, np.uint8)),
        ("纯黑", np.zeros((11, 37, 3), np.uint8)),
        ("噪声", (np.random.default_rng(7).random((19, 61, 3)) * 255).astype(np.uint8)),
    ):
        try:
            ocr.recognize(bad_in)
        except Exception as exc:      # noqa: BLE001 - 这里就是要抓所有异常
            crash = f"{name}: {type(exc).__name__}: {exc}"
            break
    check("退化输入不抛异常", not crash, crash)

    # ---------------- 4. 整牌匹配一致性 ----------------
    for stem, text in PLATE_TEXTS.items():
        ident, score = ocr.identify(_load(stem))
        check(f"整牌匹配 {stem}", ident == text, f"得到 {ident!r} score={score:.3f}")

    # ---------------- 5. 退化输入 ----------------
    res = ocr.recognize(None)
    check("None 输入不崩", res.mode == "none" and res.text == "", f"mode={res.mode}")
    res = ocr.recognize(np.zeros((3, 3, 3), dtype=np.uint8))
    check("极小纯黑图不给出高置信结果", res.score < 0.8, f"score={res.score:.3f} text={res.text!r}")
    # 纯蓝（无文字）不应匹配出高置信度
    blank = Image.new("RGB", (CANON_W, CANON_H), (20, 40, 170))
    res = ocr.recognize(blank)
    check("纯色图不误报", not res.ok or res.score < 0.8, f"mode={res.mode} score={res.score:.3f} text={res.text!r}")

    return _report()


def _report() -> int:
    ok = sum(1 for r in _RESULTS if r[0])
    print()
    for passed, name, detail in _RESULTS:
        mark = "PASS" if passed else "FAIL"
        line = f"  {mark}  {name}"
        print(line + (f"\n        {detail}" if detail and not passed else ""))
    print(f"\n{ok}/{len(_RESULTS)} 通过")
    return 0 if ok == len(_RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
