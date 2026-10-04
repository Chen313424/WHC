#!/usr/bin/env python3
"""YOLO 训练封装（Ultralytics）。

关键设计：**不联网也能训**
--------------------------
Ultralytics 默认会去 github.com 下载预训练权重，而这台机器的网络对
github.com 是阻断的。所以：

* 本地已有 ``.pt``           -> 迁移学习（推荐，收敛快、精度高）
* 本地没有 ``.pt``           -> 自动回退到 Ultralytics 包内自带的
                                ``yolo11n.yaml``，从零训练，**零下载**
                                （数据集够大时完全可用，只是需要更多 epoch）

用法::

    python tools/train_yolo.py --data ~/whc_dataset/data.yaml --epochs 120
    # 有本地权重时：
    python tools/train_yolo.py --data ... --weights ./yolo11n.pt

训练完成后把 ``best.pt`` 复制到 ``models/whc_yolo.pt``，
推理节点默认就找这个路径。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys


def resolve_weights(requested: str) -> str:
    requested = os.path.expanduser(requested)
    if os.path.isfile(requested):
        print(f"[ok] 使用本地权重: {requested}")
        return requested

    base = os.path.basename(requested)
    if base.endswith(".pt"):
        yaml_name = base[:-3] + ".yaml"
        print(f"[warn] 找不到本地权重 {requested}")
        print(f"[warn] 回退为「从零训练」: {yaml_name}（该文件随 ultralytics 包提供，无需下载）")
        print("       想迁移学习的话，先把 yolo11n.pt 放到本地，见 docs 里的镜像下载说明。")
        return yaml_name

    return requested


def main() -> int:
    ap = argparse.ArgumentParser(description="训练 WHC 感知模型")
    ap.add_argument("--data", required=True, help="data.yaml 路径（split_dataset.py 生成）")
    ap.add_argument("--weights", default="yolo11n.pt", help="预训练权重；缺失则从零训练")
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=-1, help="-1 = 自动按显存选择")
    ap.add_argument("--device", default="", help="'' = 自动；'0' = 第一块 GPU；'cpu'")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--patience", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--project", default="runs/perception")
    ap.add_argument("--name", default="whc_yolo")
    ap.add_argument("--cache", action="store_true", help="把图像缓存到内存/磁盘，加速")
    ap.add_argument("--export-onnx", action="store_true", help="额外导出 ONNX（可选）")
    ap.add_argument("--models-dir", default=None, help="best.pt 的复制目标目录")
    args = ap.parse_args()

    if not os.path.isfile(os.path.expanduser(args.data)):
        print(f"[error] 找不到 {args.data}", file=sys.stderr)
        return 2

    try:
        from ultralytics import YOLO
    except ImportError:
        print("[error] 没装 ultralytics。先跑： pip install ultralytics", file=sys.stderr)
        print("        或执行 tools/setup_train_env.ps1 一键配好环境。", file=sys.stderr)
        return 2

    weights = resolve_weights(args.weights)
    model = YOLO(weights)

    print("=" * 68)
    print(f"数据    : {args.data}")
    print(f"权重    : {weights}")
    print(f"轮数    : {args.epochs}  imgsz={args.imgsz}  batch={args.batch}")
    print(f"设备    : {args.device or 'auto'}")
    print("=" * 68)

    model.train(
        data=os.path.expanduser(args.data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device or None,
        workers=args.workers,
        patience=args.patience,
        seed=args.seed,
        project=args.project,
        name=args.name,
        exist_ok=True,
        cache=args.cache,
        plots=True,
        val=True,
    )

    # ---------------- 验证 ----------------
    print("\n" + "=" * 68)
    print("验证集评估")
    print("=" * 68)
    metrics = model.val()
    try:
        names = model.names
        print(f"mAP50    : {metrics.box.map50:.4f}")
        print(f"mAP50-95 : {metrics.box.map:.4f}")
        for i, ap50 in enumerate(metrics.box.ap50):
            cname = names.get(i, str(i)) if isinstance(names, dict) else names[i]
            print(f"  {cname:<22} AP50={ap50:.4f}")
    except Exception as exc:  # noqa: BLE001
        print(f"(指标打印失败，不影响训练结果: {exc})")

    # ---------------- 落盘 ----------------
    best = os.path.join(args.project, args.name, "weights", "best.pt")
    if not os.path.isfile(best):
        print(f"[error] 没找到 {best}", file=sys.stderr)
        return 1

    target_dir = args.models_dir or os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")
    )
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, "whc_yolo.pt")
    shutil.copy2(best, target)

    if args.export_onnx:
        try:
            YOLO(best).export(format="onnx", imgsz=args.imgsz, dynamic=False)
            print("已导出 ONNX")
        except Exception as exc:  # noqa: BLE001
            print(f"ONNX 导出失败（可忽略）: {exc}")

    print("\n完成。最佳权重:")
    print(f"  {os.path.abspath(best)}")
    print(f"  已复制到推理节点默认路径: {os.path.abspath(target)}")
    print("\n下一步（在 VM 里运行仿真后）:")
    print("  ros2 run smart_community_perception yolo_detector")
    print("  ros2 run smart_community_perception traffic_controller --ros-args -p mode:=auto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
