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
