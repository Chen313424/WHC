# 模型目录

训练好的权重放这里，推理节点默认就会加载 `whc_yolo.pt`：

```
models/whc_yolo.pt
```

也可以不放在这里，用参数指定任意路径：

```bash
ros2 run smart_community_perception yolo_detector \
    --ros-args -p model_path:=/abs/path/to/best.pt
```

## 权重从哪来

```bash
# 训练完成后自动复制过来：
python tools/train_yolo.py --data <数据集>/data.yaml --epochs 120
```

脚本会把 `runs/perception/whc_yolo/weights/best.pt` 复制成本目录下的 `whc_yolo.pt`。

> 训练前建议先下载预训练权重 `yolo11n.pt` 做迁移学习。
> 由于本机 github.com 不可达，用镜像：
> ```powershell
> Invoke-WebRequest `
>   -Uri https://hf-mirror.com/Ultralytics/YOLO11/resolve/main/yolo11n.pt `
>   -OutFile models/yolo11n.pt
> ```
> 没有预训练权重也能训（`train_yolo.py` 会自动回退到从零训练），只是收敛更慢。

`.pt` 文件不进版本库（见根目录 `.gitignore` 的说明），队内共享请走网盘/Release。
