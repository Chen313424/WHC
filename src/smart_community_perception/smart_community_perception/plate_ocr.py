"""车牌识别：从车牌检测框输出【字符】。

设计思路（为什么不训练字符分类器）
----------------------------------
仿真的 3 张车牌是完全一致渲染出来的固定贴图
（``smart_community_sim/textures/plate_1/2/3.png``，380x120，
蓝底白字、同一套字体与排版）。这种情况下**模板匹配**比训练字符分类器
更稳、更省事，而且不需要额外造字符级数据集：

    1. 把 YOLO 给出的车牌框裁出来；车牌在画面里往往是斜的，先在小角度网格上
       "转正 + 裁内容包围盒"再继续（模板匹配对旋转极其敏感，实测偏 3° 就认错）
    2. 缩放到贴图的标准尺寸
    3. 二值化后按【竖直投影】自适应切出每个字符的位置（先剥掉外圈白框）
    4. 每个字符与模板库做归一化互相关，取最高分 -> 输出该字符
    5. 用「已知车牌白名单 + 编辑距离」做一致性校对；字符级不可信时
       才用整牌匹配兜底（且必须整牌得分够高，否则宁可报不确定）

字符模板用**灰度**而不是二值图、尺寸 32x64：低分辨率下 `8` 与 `B`、
`0` 与 `Q` 这类字形在二值小图上会混淆（实测踩过 `苏A·B8Q62` 认成
`苏A·BBQ62`），灰度保留了抗锯齿细节后判别力明显更好。

本模块**不依赖 ROS**，只依赖 numpy + Pillow，可以离线自测
（见 ``test/test_plate_ocr.py``）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

# ---------------------------------------------------------------- 已知真值

#: 车牌贴图 stem -> 车牌号。改贴图时必须同步改这里，否则字符输出会错。
PLATE_TEXTS: dict[str, str] = {
    "plate_1": "苏A·B8Q62",
    "plate_2": "黑T·U1KG9",
    "plate_3": "京C·HUU42",
}

#: 车牌贴图标准尺寸（与 textures/plate_*.png 一致）
CANON_W, CANON_H = 380, 120

#: 单个字符归一化后的大小，用于模板比对（灰度）
GLYPH_W, GLYPH_H = 32, 64

#: 字符列分组的最小宽度（按标准尺寸的像素），小于它的碎片会被丢掉
_MIN_GLYPH_W = 6

#: 像素列里白色像素少于该比例就认为是字间空白
_COL_EMPTY_RATIO = 0.05

#: 车牌贴图四边有一圈白框（plate_2/3 还是完整的四边框）。不剥掉的话，
#: 每个像素列都会因为边框而"含白色"，竖直投影直接退化成「一整段」。
#: ★ 注意 plate_1 的框是【内缩 2~3 像素】的，并不在最外沿，
#:   所以不能"从最外一列开始逐层剥"，而要「在边缘这条窄带里先找到框线、再连续切掉」。
_FRAME_WHITE_RATIO = 0.8     # 整行/整列白占比超过它就当成边框线
_FRAME_BAND_FRACTION = 0.10  # 只在这个比例宽度的边缘窄带里找框线

#: 字符级结果可信度下限：低于它就不相信逐字结果
_GLYPH_MIN_SCORE = 0.45

#: 整牌兜底的门槛：整牌相关度达不到就不许用已知车牌号（否则等于瞎猜）
_PLATE_FALLBACK_MIN_SCORE = 0.55

#: 白名单校对门槛：整牌相关度至少要有这么高，才允许把逐字结果"纠"成已知车牌
_PLATE_SNAP_MIN_SCORE = 0.40

#: 转正搜索：车牌在图像里可能是斜的（仿真里相机不会正对车牌；数据合成里也会把整车
#: 贴片旋转 ±15°），而逐字模板匹配对旋转极其敏感 —— 实测偏 3° 就开始认错、15° 直接
#: 认不出。根因是轴向包围盒把牌压扁（宽高比从标准的 3.17 掉到 2.0 左右），
#: 缩放到 CANON 后字形被纵向压扁。所以按角度网格逐个"转正 + 裁内容包围盒"重试。
_ROT_SEARCH_STEP_DEG = 2.0
_ROT_SEARCH_SPAN_DEG = 20.0

#: 正立视角就已经读出【已知车牌】且分数不低于它时直接返回，省掉整轮搜索
_ROT_FAST_PATH_SCORE = 0.55

#: 转正时判定"某像素属于填充区"的通道差阈值
_DEROT_DIFF_TOL = 14


def _default_textures_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "smart_community_sim", "textures"))


# ---------------------------------------------------------------- 基础图像处理


def _to_gray_array(img: Image.Image) -> np.ndarray:
    """转灰度 float32（0~255）。"""
    return np.asarray(img.convert("L"), dtype=np.float32)


def _otsu_threshold(gray: np.ndarray) -> float:
    """Otsu 自适应阈值（类间方差最大）。

    ★ 原来这里写死 150：整幅画面偏暗/偏亮时白字会全部落到阈值以下，
    二值化切不出任何字符（实测有一例扰动图直接返回空车牌）。
    改成自适应后对亮度/对比变化鲁棒得多。
    """
    vals = gray.reshape(-1)
    if vals.size == 0:
        return 128.0
    hist, edges = np.histogram(vals, bins=256, range=(0.0, 256.0))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return 128.0
    omega = np.cumsum(hist) / total
    mu = np.cumsum(hist * (edges[:-1] + 0.5)) / total
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    denom[denom <= 1e-12] = 1e-12
    sigma_b = (mu_t * omega - mu) ** 2 / denom
    return float(edges[int(np.argmax(sigma_b))] + 0.5)


def _mask_from_gray(gray: np.ndarray) -> np.ndarray:
    """自适应二值化：亮的那一类（白字）为 1。"""
    if gray.size == 0:
        return np.zeros_like(gray)
    return (gray > _otsu_threshold(gray)).astype(np.float32)


def _border_fill(img: Image.Image) -> tuple[int, int, int]:
    """取四边像素的中位数，作为旋转时的填充色。

    用中位数而不是黑/白：填充区若与车牌底色差异过大会干扰后面的二值化。
    """
    a = np.asarray(img.convert("RGB"), dtype=np.int16)
    edges = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]], axis=0)
    return tuple(int(v) for v in np.median(edges, axis=0))


def _derotate(img: Image.Image, angle: float) -> Image.Image | None:
    """把「看起来偏了 angle 度」的车牌转回正立，并裁到内容包围盒。

    ★ 只旋转是不够的：旋转后的画布仍带着填充区，把它直接缩放到 CANON 会把车牌
      再压扁一次（这正是"偏 3° 就认不出"的直接原因）。必须再裁到【非填充内容】
      的包围盒，让车牌本身填满画面，字符的宽高比才回到标准。
    """
    fill = _border_fill(img)
    rot = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=fill)
    arr = np.asarray(rot.convert("RGB"), dtype=np.int16)
    diff = np.abs(arr - np.array(fill, dtype=np.int16)).max(axis=2) > _DEROT_DIFF_TOL
    ys, xs = np.where(diff)
    if len(xs) < 12:
        return None
    crop = rot.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    if crop.width < 8 or crop.height < 4:
        return None
    return crop


def _to_gray_mask(img: Image.Image) -> np.ndarray:
    """转灰度并自适应二值化：白字为 1、蓝底为 0。返回 float32 0/1 矩阵。"""
    return _mask_from_gray(_to_gray_array(img))


def _column_profile(mask: np.ndarray) -> np.ndarray:
    """每列白色像素占比（长度 = 宽度）。"""
    if mask.size == 0:
        return np.zeros((0,), dtype=np.float32)
    return mask.mean(axis=0)


def _frame_cut(profile: np.ndarray, white_ratio: float, band: int) -> tuple[int, int]:
    """在长度 n 的一维投影上找两端框线，返回要保留的 [start, end)。"""
    n = int(profile.size)
    if n == 0:
        return 0, 0
    band = max(1, min(band, n))

    # 左 / 上端：先在窄带里找第一条框线，找到后把连续的框线列一起切掉
    start = 0
    i = 0
    while i < band and profile[i] <= white_ratio:
        i += 1
    if i < band:
        while i < n and profile[i] > white_ratio:
            i += 1
        start = i

    # 右 / 下端：同理，从末端往内找
    end = n
    j = n - 1
    limit = n - 1 - band
    while j > limit and profile[j] <= white_ratio:
        j -= 1
    if j > limit:                     # 找到框线：从它开始把相邻的框线列一起切掉
        end = j + 1
        while end > 0 and profile[end - 1] > white_ratio:
            end -= 1

    return (start, end) if start < end else (0, n)


def strip_frame(
    mask: np.ndarray,
    white_ratio: float = _FRAME_WHITE_RATIO,
    band_fraction: float = _FRAME_BAND_FRACTION,
) -> np.ndarray:
    """剥掉车牌外圈的白色边框（含内缩的框线）。"""
    h, w = mask.shape
    if h == 0 or w == 0:
        return mask

    top, bot = _frame_cut(mask.mean(axis=1), white_ratio, int(h * band_fraction))
    inner = mask[top:bot, :]
    if inner.size == 0:
        return mask

    left, right = _frame_cut(inner.mean(axis=0), white_ratio, int(w * band_fraction))
    out = inner[:, left:right]
    return out if out.size else inner


def segment_glyphs(mask: np.ndarray) -> list[tuple[int, int]]:
    """按竖直投影切出字符的 (x0, x1) 区间。

    自适应做法：先剥掉车牌外圈白框，再把「白色占比 > 阈值」的连续列当成
    一个字符；太窄的碎片（噪点、螺栓孔边缘）丢掉。
    对 380x120 的标准车牌应当得到 7 段（省字 / 字母 / 分隔点 / 5 位）。
    """
    mask = strip_frame(mask)
    prof = _column_profile(mask)
    if prof.size == 0:
        return []

    runs: list[list[int]] = []
    in_run = False
    for x, v in enumerate(prof):
        if v > _COL_EMPTY_RATIO:
            if not in_run:
                runs.append([x, x])
                in_run = True
            else:
                runs[-1][1] = x
        else:
            in_run = False

    runs = [r for r in runs if (r[1] - r[0] + 1) >= _MIN_GLYPH_W]
    return [(r[0], r[1] + 1) for r in runs]


def _glyph_box(mask: np.ndarray, x0: int, x1: int) -> tuple[int, int, int, int] | None:
    """在给定列区间内，按墨迹把字符的上下/左右边界收紧，返回 (y0,y1,x0,x1)。"""
    sub = mask[:, x0:x1]
    rows = np.where(sub.sum(axis=1) > 0)[0]
    if rows.size == 0:
        return None
    sub = sub[rows[0]:rows[-1] + 1, :]
    cols = np.where(sub.sum(axis=0) > 0)[0]
    if cols.size == 0:
        return None
    return (int(rows[0]), int(rows[-1] + 1), int(x0 + cols[0]), int(x0 + cols[-1] + 1))


def _crop_glyph_gray(gray: np.ndarray, mask: np.ndarray, x0: int, x1: int) -> Image.Image:
    """按墨迹边界裁出【灰度】字符图并归一化大小。

    用灰度而不是二值图：低分辨率下 8/B、0/Q 这类字形在二值小图上会混淆。
    """
    box = _glyph_box(mask, x0, x1)
    if box is None:
        # ★ 尺寸必须是 GLYPH_H x GLYPH_W：早先这里返回 4x4，_glyph_vector 就只有 16 维，
        #   再和 2048 维模板做 np.dot 会直接抛 ValueError（recognize 曾被这个崩掉）。
        return Image.fromarray(np.zeros((GLYPH_H, GLYPH_W), dtype=np.uint8))
    y0, y1, cx0, cx1 = box
    sub = gray[y0:y1, cx0:cx1]
    if sub.size == 0:
        # ★ 尺寸必须是 GLYPH_H x GLYPH_W：早先这里返回 4x4，_glyph_vector 就只有 16 维，
        #   再和 2048 维模板做 np.dot 会直接抛 ValueError（recognize 曾被这个崩掉）。
        return Image.fromarray(np.zeros((GLYPH_H, GLYPH_W), dtype=np.uint8))
    img = Image.fromarray(np.clip(sub, 0, 255).astype(np.uint8))
    return img.resize((GLYPH_W, GLYPH_H), Image.BILINEAR)


def _glyph_vector(img: Image.Image) -> np.ndarray:
    """把字符图变成归一化向量（去均值 + 单位长度），便于做互相关。"""
    a = np.asarray(img, dtype=np.float32).reshape(-1) / 255.0
    a = a - a.mean()
    n = float(np.linalg.norm(a))
    return a / n if n > 1e-6 else a


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    """两个已归一化向量的相关系数，范围 [-1, 1]。

    长度不一致时返回 -1（最低分）而不是抛异常：宁可判成"不像"，
    也不能让识别过程崩掉 —— 巡检接口要求任何情况下都得给出回答。
    """
    if a.shape != b.shape:
        return -1.0
    return float(np.dot(a, b))


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein 距离（短字符串用，纯 Python 足够）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------- OCR


@dataclass
class PlateResult:
    """一次车牌识别的结果。"""

    text: str                      # 识别出来的车牌号（可能含 '?' 表示不确定）
    score: float                   # 得分（0~1，越大越可信）
    #: 'glyph' = 纯字符级；'glyph+check' = 逐字结果经白名单校对；
    #: 'template' = 整牌兜底；'none' = 失败
    mode: str
    chars: list[str] = field(default_factory=list)       # 逐字结果
    char_scores: list[float] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.mode != "none" and bool(self.text)

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"{self.text} (mode={self.mode}, score={self.score:.3f})"


class PlateOCR:
    """车牌字符识别器（模板匹配）。模板在构造时从贴图生成一次，之后复用。"""

    def __init__(self, textures_dir: str | None = None) -> None:
        self.textures_dir = textures_dir or _default_textures_dir()
        # 字符 -> 归一化向量（同一字符出现在多张牌里时保留多个模板，取最高分）
        self._glyph_templates: dict[str, list[np.ndarray]] = {}
        # 整牌 -> 归一化向量
        self._plate_templates: dict[str, np.ndarray] = {}
        self._plate_texts: dict[str, str] = {}
        self._build()

    # ---------------- 构建模板 ----------------

    def _load(self, stem: str) -> Image.Image | None:
        path = os.path.join(self.textures_dir, stem + ".png")
        if not os.path.isfile(path):
            return None
        return Image.open(path).convert("RGB")

    def _build(self) -> None:
        for stem, text in PLATE_TEXTS.items():
            img = self._load(stem)
            if img is None:
                continue
            img = img.resize((CANON_W, CANON_H), Image.LANCZOS)
            gray = _to_gray_array(img)
            mask = _mask_from_gray(gray)

            # 整牌模板
            self._plate_templates[stem] = _glyph_vector(
                Image.fromarray(np.clip(strip_frame(mask) * 255, 0, 255).astype(np.uint8))
                .resize((64, 20), Image.BILINEAR)
            )
            self._plate_texts[stem] = text

            # 字符模板：切段的段数必须与车牌号字符数一致，否则该牌不参与字符模板
            segs = segment_glyphs(mask)
            if len(segs) == len(text):
                for (x0, x1), ch in zip(segs, text):
                    self._glyph_templates.setdefault(ch, []).append(
                        _glyph_vector(_crop_glyph_gray(gray, mask, x0, x1))
                    )

    @property
    def known_characters(self) -> str:
        return "".join(sorted(self._glyph_templates))

    @property
    def known_plates(self) -> tuple[str, ...]:
        return tuple(self._plate_texts.values())

    @property
    def ready(self) -> bool:
        return bool(self._glyph_templates) and bool(self._plate_templates)

    # ---------------- 识别 ----------------

    def recognize(self, plate_crop_bgr) -> PlateResult:
        """输入车牌区域的图像（PIL.Image 或 HxWx3 ndarray，BGR/RGB 都行），输出字符。

        ★ 车牌在画面里往往是【斜的】（仿真里相机不会正对车牌，数据合成里整车贴片也
          会被旋转 ±15°），而逐字模板匹配对旋转极其敏感。所以这里先在"正立"假设下
          试一次；只要没读出已知车牌，就在角度网格上逐个"转正 + 裁内容包围盒"重试，
          取排序最好的结果（见 ``_derotate``）。正立就已经读出已知车牌时直接返回。
        """
        img = self._as_image(plate_crop_bgr)
        if img is None:
            return PlateResult("", 0.0, "none")

        best = self._recognize_upright(img)
        if best.text in self._plate_texts.values() and best.score >= _ROT_FAST_PATH_SCORE:
            return best

        n = int(round(_ROT_SEARCH_SPAN_DEG / _ROT_SEARCH_STEP_DEG))
        for i in range(-n, n + 1):
            angle = i * _ROT_SEARCH_STEP_DEG
            if angle == 0.0:
                continue
            view = _derotate(img, angle)
            if view is None:
                continue
            r = self._recognize_upright(view)
            if self._rank(r) > self._rank(best):
                best = r
        return best

    def _rank(self, r: PlateResult) -> tuple:
        """候选视角之间排序：读出已知车牌 > 有结果 > 分数高。"""
        return (1 if r.text in self._plate_texts.values() else 0,
                1 if r.ok else 0,
                round(float(r.score), 4))

    def _recognize_upright(self, img: Image.Image) -> PlateResult:
        """【正立】假设下的一次识别。recognize 的搜索会对每个候选视角各调一次。"""
        img = img.resize((CANON_W, CANON_H), Image.LANCZOS)
        gray = _to_gray_array(img)
        mask = _mask_from_gray(gray)

        # --- 整牌匹配（用于白名单校对与兜底） ---
        whole = _glyph_vector(
            Image.fromarray(np.clip(strip_frame(mask) * 255, 0, 255).astype(np.uint8))
            .resize((64, 20), Image.BILINEAR)
        )
        best_stem, best_plate_score = "", -1.0
        for stem, vec in self._plate_templates.items():
            s = _corr(whole, vec)
            if s > best_plate_score:
                best_stem, best_plate_score = stem, s
        best_plate_text = self._plate_texts.get(best_stem, "")

        # --- 字符级识别 ---
        segs = segment_glyphs(mask)
        chars: list[str] = []
        scores: list[float] = []
        for x0, x1 in segs:
            vec = _glyph_vector(_crop_glyph_gray(gray, mask, x0, x1))
            best_ch, best_s = "", -1.0
            for ch, tmpls in self._glyph_templates.items():
                for t in tmpls:
                    s = _corr(vec, t)
                    if s > best_s:
                        best_ch, best_s = ch, s
            chars.append(best_ch)
            scores.append(best_s)

        text = "".join(chars)
        mean_score = float(np.mean(scores)) if scores else 0.0

        # --- 白名单校对：逐字结果离某张已知车牌只差 1 个字符、且整牌也像它 ---
        if text and best_plate_text and best_plate_score >= _PLATE_SNAP_MIN_SCORE:
            if text != best_plate_text and _edit_distance(text, best_plate_text) <= 1:
                return PlateResult(best_plate_text, max(best_plate_score, 0.0), "glyph+check",
                                   chars=list(best_plate_text),
                                   char_scores=[max(best_plate_score, 0.0)] * len(best_plate_text))

        # --- 字符级是否可信：段数要对，平均相关度也要够 ---
        #   段数用「已知车牌长度」卡：实测有一次扰动图多切出一段，
        #   逐字结果变成 9 个字符的乱码（'KA·BBQ62'），却因为当时整牌得分低
        #   而走了"没有参照就放行"的分支。宁可报不确定，也不给乱码。
        expect = len(best_plate_text) if best_plate_text else 0
        if segs and (not expect or len(segs) == expect) and mean_score >= _GLYPH_MIN_SCORE:
            return PlateResult(text, mean_score, "glyph", chars=chars, char_scores=scores)

        # --- 兜底：整牌匹配，但必须够像，否则宁可报不确定性 ---
        if best_plate_text and best_plate_score >= _PLATE_FALLBACK_MIN_SCORE:
            return PlateResult(best_plate_text, max(best_plate_score, 0.0), "template",
                               chars=list(best_plate_text),
                               char_scores=[max(best_plate_score, 0.0)] * len(best_plate_text))

        if chars:
            # 逐字给出，但把低分字符标成 '?'，避免给出看起来确定其实乱猜的字符串
            marked = [c if s >= _GLYPH_MIN_SCORE else "?" for c, s in zip(chars, scores)]
            text_marked = "".join(marked)
            if not any(ch != "?" for ch in text_marked):
                # 一个字都没把握 -> 直接认输，别拿 '??????' 冒充结果
                return PlateResult("", mean_score, "none")
            return PlateResult(text_marked, mean_score, "glyph",
                               chars=chars, char_scores=scores)

        return PlateResult("", max(best_plate_score, 0.0), "none")

    def identify(self, plate_crop_bgr) -> tuple[str, float]:
        """只做「这是哪张车牌」的整牌匹配，返回 (车牌号, 得分)；失败返回 ('', 0)。"""
        img = self._as_image(plate_crop_bgr)
        if img is None or not self._plate_templates:
            return "", 0.0
        img = img.resize((CANON_W, CANON_H), Image.LANCZOS)
        mask = _mask_from_gray(_to_gray_array(img))
        whole = _glyph_vector(
            Image.fromarray(np.clip(strip_frame(mask) * 255, 0, 255).astype(np.uint8))
            .resize((64, 20), Image.BILINEAR)
        )
        best_stem, best = "", -1.0
        for stem, vec in self._plate_templates.items():
            s = _corr(whole, vec)
            if s > best:
                best_stem, best = stem, s
        return self._plate_texts.get(best_stem, ""), max(best, 0.0)

    # ---------------- 工具 ----------------

    @staticmethod
    def _as_image(src) -> Image.Image | None:
        if src is None:
            return None
        if isinstance(src, Image.Image):
            return src.convert("RGB")
        arr = np.asarray(src)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return None
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        # ★ ndarray 一律按 OpenCV/ROS 的 **BGR** 约定解释，必须先换成 RGB。
        #   本项目所有帧都是 BGR：yolo_detector_node._to_numpy 明确把 rgb8 反成 BGR，
        #   autolabel_capture_node 也用 cv_bridge 的 desired_encoding="bgr8"。
        #   早先这里"不区分通道顺序"，实际是把 BGR 当 RGB 用，于是 ndarray 路径的灰度
        #   与模板库（由 PIL 读的 RGB 贴图建立）不一致 —— 实测同一张牌：
        #   传 PIL(RGB) 能读出 '黑T·U1KG9'（整牌 0.817），传 BGR ndarray 就退化成
        #   '黑??????'。既有测试只传 PIL Image，所以这条真实路径一直没被覆盖。
        return Image.fromarray(np.ascontiguousarray(arr[:, :, 2::-1]))
