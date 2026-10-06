#!/usr/bin/env python3
"""把 ``images/all`` + ``labels/all`` 划分成 Ultralytics 需要的 train/val 结构。

输出结构::

    <dataset>/
      images/all/*.jpg        采集/合成的原始数据
      labels/all/*.txt
      images/train/*.jpg      划分结果
      images/val/*.jpg
      labels/train/*.txt
      labels/val/*.txt
      data.yaml               训练配置（路径写成绝对路径，避免 cwd 影响）

用法::

    python3 tools/split_dataset.py --dataset ~/whc_dataset --val-ratio 0.2
"""
from __future__ import annotations

import argparse
import os
import random
import shutil
import sys

# ★ 类别清单以 smart_community_perception.traffic_rules.CLASS_NAMES 为【唯一来源】，
#   不要在这里再抄一份。2026-10 就把人偶拆成了社区/非社区两类；如果这里留着旧的
#   5 类清单，写出的 data.yaml 会和模型/推理端对不上（类别 id 整体错位）。
#   traffic_rules 是纯逻辑模块（只依赖 stdlib），可以直接 import，无需 ROS 环境。
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
from smart_community_perception.traffic_rules import CLASS_NAMES  # noqa: E402


def collect(dataset: str) -> list[str]:
    img_dir = os.path.join(dataset, "images", "all")
    lbl_dir = os.path.join(dataset, "labels", "all")
    if not os.path.isdir(img_dir):
        raise SystemExit(f"找不到 {img_dir}，请先采集或合成数据")
    stems = []
    for name in sorted(os.listdir(img_dir)):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in (".jpg", ".jpeg", ".png", ".bmp"):
            continue
        if not os.path.isfile(os.path.join(lbl_dir, stem + ".txt")):
            continue
        stems.append(stem)
    return stems


def class_histogram(dataset: str, stems: list[str]) -> dict[int, int]:
    hist: dict[int, int] = {}
    lbl_dir = os.path.join(dataset, "labels", "all")
    for stem in stems:
        with open(os.path.join(lbl_dir, stem + ".txt"), "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if not parts:
                    continue
                cid = int(float(parts[0]))
                hist[cid] = hist.get(cid, 0) + 1
    return hist


def write_data_yaml(dataset: str, val_ratio: float) -> str:
    path = os.path.join(dataset, "data.yaml")
    root = os.path.abspath(dataset)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# 由 tools/split_dataset.py 生成\n")
        fh.write(f"path: {root}\n")
        fh.write("train: images/train\n")
        fh.write("val: images/val\n")
        fh.write("names:\n")
        for i, n in enumerate(CLASS_NAMES):
            fh.write(f"  {i}: {n}\n")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="划分 YOLO 数据集")
    ap.add_argument("--dataset", required=True, help="数据集根目录")
    ap.add_argument("--val-ratio", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--move", action="store_true", help="移动而不是复制（省磁盘）")
    ap.add_argument("--clean", action="store_true", help="划分前清空 train/val")
    args = ap.parse_args()

    dataset = os.path.expanduser(args.dataset)
    stems = collect(dataset)
    if not stems:
        raise SystemExit("没有找到「图片 + 同名标注」的配对，检查采集是否成功")

    rng = random.Random(args.seed)
    rng.shuffle(stems)
    n_val = max(1, int(len(stems) * args.val_ratio)) if len(stems) > 1 else 0
    val = set(stems[:n_val])

    splits = ("train", "val")
    for split in splits:
        for sub in ("images", "labels"):
            d = os.path.join(dataset, sub, split)
            if args.clean and os.path.isdir(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)

    op = shutil.move if args.move else shutil.copy2
    moved = {"train": 0, "val": 0}
    for stem in stems:
        split = "val" if stem in val else "train"
        for ext in (".jpg", ".jpeg", ".png", ".bmp"):
            src = os.path.join(dataset, "images", "all", stem + ext)
            if os.path.isfile(src):
                op(src, os.path.join(dataset, "images", split, stem + ext))
                break
        op(
            os.path.join(dataset, "labels", "all", stem + ".txt"),
            os.path.join(dataset, "labels", split, stem + ".txt"),
        )
        moved[split] += 1

    yaml_path = write_data_yaml(dataset, args.val_ratio)

    hist = class_histogram(dataset, stems)
    print(f"数据集: {dataset}")
    print(f"train={moved['train']}  val={moved['val']}  (共 {len(stems)} 帧)")
    print("类别分布（实例数）:")
    for i, name in enumerate(CLASS_NAMES):
        print(f"  {i} {name:<22} {hist.get(i, 0)}")
    print(f"已写出 {yaml_path}")

    missing = [CLASS_NAMES[i] for i in range(len(CLASS_NAMES)) if hist.get(i, 0) == 0]
    if missing:
        print("\n[警告] 以下类别没有任何样本，模型学不会它们:")
        for m in missing:
            print(f"  - {m}")
        print("  提示：自动标注时开着车多跑几圈，让红/黄/绿三种状态都被拍到。")


if __name__ == "__main__":
    main()
