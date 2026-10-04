# YOLO 识别与「红灯停、绿灯行」控制

本包为 `smart_community_sim` 提供三件事：

1. **检测** 红绿灯（红/黄/绿三态）、人偶、车牌
2. **数据集** 用 Gazebo 的 3D 真值**自动标注**，零人工成本
3. **控制** 把检测结果变成 `/cmd_vel`，实现红灯停、绿灯行

---

## 1. 类别定义

| id | 类别 | 来源 | 真实尺寸（SDF 实测） |
|----|------|------|----------------------|
| 0 | `traffic_light_red` | `traffic_light_1/2` 灯箱+灯球 | 竖排 0.24×0.18×0.78 / 横排 0.18×0.74×0.24 |
| 1 | `traffic_light_yellow` | 同上 | 同上 |
| 2 | `traffic_light_green` | 同上 | 同上 |
| 3 | `person` | `person_a1~a5`、`person_b1~b5` | 0.5×0.63×1.83 |
| 4 | `license_plate` | `car_a/b/c` 的 `plate_front/rear` | 0.5×0.02×0.16 |

**为什么把红绿灯拆成三个类**：控制端只关心「现在能不能走」。
拆成三个类后，模型直接输出状态，控制端不需要再做颜色分析，
也不会出现「检测到灯但判断不出颜色」的中间态。

> 灯箱的包围盒**故意不包含灯杆**。灯杆又细又高（2.6 m），
> 混进包围盒会把标注框拉成细长条，既不利于训练，也会破坏距离估计。

---

## 2. 红绿灯状态的真值从哪来

`worlds/smart_community.sdf` 里的插件时序是：

```
phase = fmod(simTime, 28)         # 28 = 15 + 3 + 10
phase < 15        -> 绿灯
15 <= phase < 18  -> 黄灯
phase >= 18       -> 红灯
```

这是**仿真时间的纯函数**，所以自动标注不需要看画面就能拿到绝对正确的类别。
`smart_community_perception/traffic_rules.py` 里的
`traffic_light_state_from_sim_time()` 与插件逐字一致，
并且有专门的单元测试盯着（改插件参数时必须同步改这里，否则标注类别会全错）。

---

## 3. 数据管道

```
                     ┌──────────────────────────────────────┐
  world.sdf ─────────┤ tools/sdf_to_objects.py              │
                     │  -> config/world_objects.yaml        │  3D 真值框
                     └──────────────────────────────────────┘
                                     │
  Gazebo 运行中                       ▼
  /camera/image_raw ──► autolabel_capture ──► images/all + labels/all
  /odom             ──►   (3D 框投影)              │
  /clock            ──►   (时序真值)                │
                                                   ▼
                              tools/split_dataset.py ──► images/train|val + data.yaml
                                                   │
                                                   ▼
                              tools/train_yolo.py ──► models/whc_yolo.pt
                                                   │
                                                   ▼
  /camera/image_raw ──► yolo_detector ──► /perception/detections
                                                   │
                                                   ▼
                                          traffic_controller ──► /cmd_vel
```

---

## 4. 快速开始

### 路线 A：先跑通链路（不需要仿真）

适合先验证「训练 → 推理 → 控制」是否通，或者仿真环境还没配好。

```bash
# 1) 配训练环境（Windows 本机，RTX 5060 需要 CUDA 12.8+ 的 torch）
powershell -ExecutionPolicy Bypass -File tools/setup_train_env.ps1

# 2) 合成一批数据（不需要 Gazebo，用真实车牌纹理 + SDF 比例绘制）
python tools/gen_synth_dataset.py --out ../whc_synth --n 1500

# 3) 划分 + 生成 data.yaml
python tools/split_dataset.py --dataset ../whc_synth --val-ratio 0.2

# 4) 训练
python tools/train_yolo.py --data ../whc_synth/data.yaml --epochs 120
```

### 路线 B：正式路线（Gazebo 自动标注）

在 VM 里（Ubuntu 24.04 + ROS 2 Jazzy）：

```bash
# 终端 1：仿真
ros2 launch smart_community_sim smart_community.launch.py

# 终端 2：键盘遥控
ros2 run teleop_twist_keyboard teleop_twist_keyboard

# 终端 3：一边开一边自动采数据
ros2 run smart_community_perception autolabel_capture \
    --ros-args -p output_dir:=$HOME/whc_dataset -p sample_period:=0.4
```

开几圈，让**红、黄、绿三种状态**都拍到（黄灯只有 3 秒，要多绕几次），
然后把数据拷回训练机：

```bash
python tools/split_dataset.py --dataset ~/whc_dataset --val-ratio 0.2
python tools/train_yolo.py --data ~/whc_dataset/data.yaml --epochs 120
```

> **推荐混合训练**：把合成图和真实帧都放进 `images/all` 再划分。
> 合成图补稀有类别（黄灯），真实帧保证域一致。

### 路线 C：部署运行

```bash
# 1) 仿真照常起
ros2 launch smart_community_sim smart_community.launch.py

# 2) 起感知 + 控制
ros2 launch smart_community_perception perception.launch.py

# 3) 遥控（注意重映射到 /cmd_vel_input，让规则节点把关）
ros2 run teleop_twist_keyboard teleop_twist_keyboard \
    --ros-args -r cmd_vel:=/cmd_vel_input
```

现在开车到路口：**红灯会自动刹停，绿灯自动放行**。

看一眼调试画面和决策：

```bash
ros2 run rqt_image_view rqt_image_view /perception/debug_image
ros2 topic echo /perception/traffic_decision
```

**一键演示模式**（不用遥控）：把 `smart_community.launch.py` 里
`spawn` 的初始位姿改成 `x=0, y=8, Y=1.5708`（中央路，朝北正对 `traffic_light_1`），然后

```bash
ros2 launch smart_community_perception perception.launch.py mode:=auto
```

车会直行到路口，红灯停、绿灯走。

---

## 5. 控制逻辑说明

`traffic_rules.decide()` 是纯函数，规则如下：

| 情况 | 动作 |
|------|------|
| 绿灯 | 全速通过 |
| 红灯，距离 ≤ `stop_distance_m` | **停车** |
| 红灯，距离 > `stop_distance_m` | 减速接近 |
| 黄灯，能安全停（距离够） | 停车 |
| 黄灯，已过停车线（距离很近） | 通过（避免急刹） |
| 画面里没有相关红绿灯 | 保持行驶 |

几个设计细节：

* **只认画面中央的红绿灯**（`roi_x_ratio`，默认中央 60% 宽度）。
  避免侧向路口的灯干扰，也避免「另一组红灯让你在本方绿灯时停车」。
* **多条检测取面积最大者**。面积最大 ≈ 最近、最醒目。
* **距离用长边估算**：`Z = fy × 真实长边 / 像素长边`。
  横排和竖排灯箱都用「长边」，所以共用一套参数（见 `OBJECT_MAX_EXTENT_M`）。
* **停车时角速度一并归零**，不会出现「原地打转但不动」。
* **检测超时有兜底**：超过 `detection_timeout` 没有新检测，
  按 `fail_safe` 处理（默认 `go`，避免相机异常把车永久钉死；可改 `stop`）。

---

## 6. 调参

都在 `config/perception.yaml`，改完重启 launch 即可。

| 现象 | 改什么 |
|------|--------|
| 红灯停得太早 | 调小 `stop_distance_m`（默认 5.0） |
| 冲过停车线才停 | 调大 `stop_distance_m`，或调大 `approach_speed` 让减速更早 |
| 漏检红绿灯 | 调小 `conf`（如 0.25） |
| 误检太多 | 调大 `conf` / `min_score` |
| 侧向灯干扰 | 调小 `roi_x_ratio`（如 0.4） |
| 黄灯总急刹 | 用 `yellow_policy: stop_if_far`（默认），或调大 `stop_distance_m` |
| 推理太慢 | 调小 `imgsz`（416/320），或设 `min_interval: 0.1` 限频 |
| 想让人偶也触发停车 | `avoid_person: true` |

---

## 7. 故障排查

**`找不到模型文件`**
没训练过。先走路线 A 或 B，或临时指定：
`ros2 run ... yolo_detector --ros-args -p model_path:=/path/best.pt`

**`torch.cuda.is_available() == False`**
装成了 CPU 版 torch。RTX 5060 是 Blackwell（sm_120），必须 CUDA 12.8+：
```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
```

**训练报 `no kernel image is available for execution on the device`**
同上，torch 版本太老，不支持 sm_120。

**下载 `yolo11n.pt` 失败**
github.com 在本机被重置。用镜像：
```powershell
Invoke-WebRequest -Uri https://hf-mirror.com/Ultralytics/YOLO11/resolve/main/yolo11n.pt -OutFile models/yolo11n.pt
```
不下载也行，`train_yolo.py` 会自动回退到从零训练。

**标注框整体偏移 / 方向反了**
大概率是世界位姿链对不上。检查：
1. `smart_community_sim/launch/smart_community.launch.py` 里 `spawn` 的
   `x/y/z/Y` 是否与 `autolabel_capture` 的 `world_to_odom` 参数一致
   （默认 `[32, 14, 0.05, 0, 0, 1.5708]`）
2. `robot.xacro` 改过相机外参的话，同步 `base_link_to_camera`（默认 `[0.15, 0, 0.10]`）

**红绿灯标注全是同一个颜色**
`green_time/yellow_time/red_time` 与 SDF 里插件的参数不一致。
两者必须相同（默认 15/3/10）。

**车在路口前不动也不报错**
看 `ros2 topic echo /perception/traffic_decision`。
如果是 `检测超时`，说明检测话题没数据：确认 `yolo_detector` 在跑、
`/perception/detections` 有输出、两个节点的 `use_sim_time` 一致。

---

## 8. 离线自检

不需要 ROS / Gazebo，只要有 numpy：

```bash
python test/test_offline.py
```

覆盖 21 个用例：时序真值、针孔投影、**光学系方向约定**、
世界→相机位姿链端到端投影、决策边界、生成的真值框与 SDF 是否一致。
改代码后先跑它。

---

## 9. 目录结构

```
smart_community_perception/
├── smart_community_perception/
│   ├── geometry.py                 # 纯几何：投影、位姿链（无 ROS 依赖）
│   ├── traffic_rules.py            # 类别、时序真值、决策（无 ROS 依赖）
│   ├── messages.py                 # 检测结果 JSON 编解码
│   ├── autolabel_capture_node.py   # Gazebo 真值自动标注
│   ├── yolo_detector_node.py       # 推理
│   └── traffic_controller_node.py  # 红绿灯控制
├── config/
│   ├── world_objects.yaml          # 自动生成：18 个目标的 3D 真值框
│   └── perception.yaml             # 节点参数
├── launch/perception.launch.py
├── tools/
│   ├── sdf_to_objects.py           # SDF -> 3D 真值框
│   ├── gen_synth_dataset.py        # 离线合成数据
│   ├── split_dataset.py            # train/val 划分 + data.yaml
│   ├── train_yolo.py               # 训练
│   └── setup_train_env.ps1         # Windows 训练环境一键配置
├── test/test_offline.py
└── models/                         # 权重放这里（默认 whc_yolo.pt）
```
