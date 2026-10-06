#!/usr/bin/env bash
# 在 guest 安装感知推理依赖：CPU 版 torch + ultralytics
# （guest 里跑 YOLO 推理不需要 GPU；CPU 版 wheel 小得多，8 vCPU 足够）
set +u
export DEBIAN_FRONTEND=noninteractive

echo "=== $(date '+%F %T') 开始 ==="
echo "--- 环境 ---"
python3 --version
df -h / | tail -1

echo
echo "--- 1) 装 CPU 版 torch / torchvision（官方 cpu 索引）---"
pip3 install --user --break-system-packages --no-warn-script-location \
    --index-url https://download.pytorch.org/whl/cpu torch torchvision 2>&1 | tail -6

echo
echo "--- 2) 装 ultralytics（清华源）---"
pip3 install --user --break-system-packages --no-warn-script-location \
    -i https://pypi.tuna.tsinghua.edu.cn/simple ultralytics 2>&1 | tail -6

echo
echo "--- 3) 验证 ---"
python3 - <<'PY'
import importlib
for m in ("torch", "torchvision", "ultralytics", "cv2", "numpy", "yaml", "PIL"):
    try:
        mod = importlib.import_module(m)
        print(f"  [ok]   {m:12s} {getattr(mod, '__version__', '?')}")
    except Exception as e:
        print(f"  [FAIL] {m:12s} {type(e).__name__}: {e}")
PY

echo
echo "--- 4) 用真模型跑一次推理自检（如果模型已经同步过来）---"
MODEL="$HOME/smart_community_ws/src/smart_community_perception/models/whc_yolo.pt"
ALT="/media/sf_WHC/src/smart_community_perception/models/whc_yolo.pt"
for m in "$MODEL" "$ALT"; do
    if [ -f "$m" ]; then
        echo "  找到模型: $m ($(du -h "$m" | cut -f1))"
        python3 - "$m" <<'PY'
import sys
from ultralytics import YOLO
m = YOLO(sys.argv[1])
print("  模型类别:", m.names)
import numpy as np
r = m.predict(np.zeros((480, 640, 3), dtype=np.uint8), imgsz=640, verbose=False)
print("  推理 OK，输出形状:", [tuple(x.shape) for x in r[0].boxes.data.shape] if hasattr(r[0], "boxes") else "?")
PY
        break
    fi
done

echo
echo "=== $(date '+%F %T') 结束 ==="
