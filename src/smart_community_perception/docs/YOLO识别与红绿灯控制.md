# YOLO 识别与「红灯停、绿灯行」控制

本包为 `smart_community_sim` 提供三件事：

1. **检测** 红绿灯（红/黄/绿三态）、人偶、车牌
2. **数据集** 用 Gazebo 的 3D 真值**自动标注**，零人工成本
3. **控制** 把检测结果变成 `/cmd_vel`，实现红灯停、绿灯行

---

## 1. 类别定义

> ★ 2026-10 更新：仿真组把场景从 88×44m 城市重建为 **4.2×4.2m 省赛场地**，
> 物料尺寸整体小了一个量级；同时按赛题要求把「人偶」拆成
> **社区 / 非社区** 两类。下表是新场景下 `smart_community_sdf` 实测值。

| id | 类别 | 来源 | 真实尺寸（SDF 实测，宽×厚×高） |
|----|------|------|-------------------------------|
| 0 | `traffic_light_red` | `traffic_light_1/2` 的 housing+灯球 | 竖排 0.14×0.096×0.59 / 横排 0.59×0.096×0.14 |
| 1 | `traffic_light_yellow` | 同上 | 同上 |
| 2 | `traffic_light_green` | 同上 | 同上 |
| 3 | `person_community` | `person_a1~a5`、`person_b1~b5`、`person_s1~s6`（16 个） | 0.05×0.005×0.15 |
| 4 | `person_noncommunity` | `person_f1`、`person_f2`（2 个） | 0.05×0.005×0.15 |
| 5 | `license_plate` | `car_1/2/3` 里的 `plate` visual | 0.095×0.002×0.03 |

**为什么人偶要拆成两类**：赛题要求「非社区人员辨别」，靠后处理分析颜色
既不可靠也不好解释。世界 SDF 里这两类用的是不同贴图
（`person_community_NN.png` / `person_noncommunity_NN.png`），
自动标注时按 albedo 直接给不同类别 id，模型一步到位。

**为什么把红绿灯拆成三个类**：控制端只关心「现在能不能走」。
拆成三个类后，模型直接输出状态，控制端不需要再做颜色分析，
也不会出现「检测到灯但判断不出颜色」的中间态。

> 灯箱的包围盒**故意不包含灯腿**（`leg_l` / `leg_r`，0.025×0.025×0.3~0.48）。
> 灯腿又细又长，混进包围盒会把标注框拉成细长条，既不利于训练，
> 也会破坏距离估计。`tools/sdf_to_objects.py` 里用 `TRAFFIC_LIGHT_SKIP` 排除它们。

---

## 2. 红绿灯状态的真值从哪来

`worlds/smart_community.sdf` 里的插件时序是：

```
phase = fmod(simTime, 30)         # 30 = 15 + 5 + 10
phase < 15        -> 绿灯
15 <= phase < 20  -> 黄灯
phase >= 20       -> 红灯
```

★ **黄灯是 5 秒不是 3 秒**：官方赛题文件写的是 3 秒，但仿真组交付的 SDF 里
插件参数是 `green_time=15 / yellow_time=5 / red_time=10`。
本文件与 `traffic_rules.py`、`autolabel_capture` 的黄灯默认值**必须跟 SDF 一致**，
否则自动标注会在黄灯那一段把真值标成红/绿，训练出来的颜色判断是错的
（这个坑实际踩过：三处默认值一度不一致）。改插件参数时三处都要同步改。

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
   （新场景默认 `[1.3, 1.3, 0.05, 0, 0, -1.5708]`；旧城市场景是 `[32, 14, ...]`，
   两者混用会让标注框整体偏移/方向反了）
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

不需要 ROS / Gazebo，只要有 numpy + Pillow：

```bash
cd src/smart_community_perception
python test/test_offline.py        # 24 个用例：时序真值、针孔投影、光学系方向约定、
                                   #   世界→相机位姿链端到端投影、决策边界、
                                   #   生成的真值框与 SDF 是否一致
python test/test_plate_ocr.py      # 22 个用例：车牌切字、三张已知车牌的字符还原、
                                   #   缩放/模糊/亮度扰动后的鲁棒性、BGR ndarray 真实喂法、
                                   #   倾斜车牌（±15°）、退化输入不崩不误报
python test/test_result_payload.py # 巡检接口的结果 JSON 构造（三种 task 的正常与缺数据分支）
```

改代码后先跑这三个。

---

## 9. 目录结构

```
smart_community_perception/
├── smart_community_perception/
│   ├── geometry.py                 # 纯几何：投影、位姿链（无 ROS 依赖）
│   ├── traffic_rules.py            # 类别、时序真值、决策（无 ROS 依赖）
│   ├── messages.py                 # 检测结果 JSON 编解码
│   ├── autolabel_capture_node.py   # Gazebo 真值自动标注
│   ├── yolo_detector_node.py       # 推理 + 巡检接口（发灯态、响应 capture）
│   └── traffic_controller_node.py  # 红绿灯控制
│   └── plate_ocr.py                # 车牌字符输出（模板匹配，无 ROS 依赖）
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
├── test/
│   ├── test_offline.py             # 几何/时序/决策（24）
│   ├── test_plate_ocr.py           # 车牌字符输出（22）
│   └── test_result_payload.py      # 巡检接口结果 JSON（51）
└── models/                         # 权重放这里（默认 whc_yolo.pt）
```

---

## 10. 与巡检节点（导航组）的接口契约

`community_patrol` 的站点表里每个站点带 `action` / `task`，它靠下面三个话题
跟视觉组对接。**这套契约是导航组定的，感知侧只负责实现**：

| 话题 | 类型 | 方向 | 说明 |
|------|------|------|------|
| `/perception/detections` | `std_msgs/String`(JSON) | 感知 → 全车 | 每帧检测结果（`messages.py` 编解码） |
| `/traffic_light/state` | `std_msgs/String` | 感知 → 巡检 | `RED` / `YELLOW` / `GREEN` / `UNKNOWN`（巡检 `strip().upper()` 后**只在 GREEN 时放行**） |
| `/patrol/capture` | `std_msgs/String`(JSON) | 巡检 → 感知 | 到站后请求识别：`{waypoint_id, task, zone, slot, target, stamp}` |
| `/detection/result` | `std_msgs/String`(JSON) | 感知 → 巡检 | 对上面请求的回应（`schema = "whc.result/1"`） |

站点表里的三种 `task`：

| task | 站点 | 输出 |
|------|------|------|
| `outsider_detect` | `outsider_n`(target=person_f1)、`outsider_s`(target=person_f2) | 社区/非社区人偶数量 + `target_found` |
| `crowd_count` | `sidewalk_n`、`zone_a`、`zone_b` | 社区/非社区人偶数量 |
| `plate_ocr` | `park_2`(slot=2)、`park_3`(slot=3) | `plate`（如 `苏A·B8Q62`）+ 逐字 + OCR 得分 |

**实现位置**：这些发布/订阅都做在 `yolo_detector_node` 里，不另开节点 ——
本机是内存紧张的虚拟机，再订阅一路 `/camera/image_raw` 会多出约
4.6 MB/s 的 DDS 流量；检测节点本来就持有最新图像与检测结果，复用最省。

> ⚠️ 两个实测注意点：
> 1. **`/detection/result` 必须"有问必答"**：哪怕还没出图、task 不认识、
>    非法 JSON，也要回一条 `ok:false` + `reason`。不然巡检会一直等到
>    `capture_wait_timeout` 超时才继续（日志表现为「未在 60s 内收到识别结果」）。
> 2. **停车点必须能看到灯箱**：契约只有 `GREEN` 才放行，视野里没灯就只能发
>    `UNKNOWN`，巡检会等满 `light_wait_timeout` 再走（不中断流程但浪费时间）。
>    好在本场地两组灯由同一个插件驱动、相位同步，按面积取最大那盏是安全的。

### 车牌字符输出（`plate_ocr.py`）

仿真的 3 张车牌是固定贴图（`苏A·B8Q62` / `黑T·U1KG9` / `京C·HUU42`），
字体排版完全一致，所以用**模板匹配**而不是训练字符分类器：

1. 车牌框**转正**，再缩放到贴图标准尺寸（380×120）→ **Otsu 自适应二值化**
   （原来写死阈值 150，画面偏暗时切不出字）
2. 先剥掉外圈白框（`plate_1` 的框是内缩 2~3 像素的），再按竖直投影自适应切字
3. 逐字与模板库做归一化互相关，取最高分
4. 用「已知车牌白名单 + 编辑距离」做一致性校对；字符级不可信时才用整牌匹配兜底，
   且**整牌得分不够高就报不确定**（宁可返回 `?` 也不给乱猜的字符串）

字符模板用**灰度 32×64** 而不是二值小图：低分辨率下 `8`/`B`、`0`/`Q` 在
二值小图上会混淆（实测把 `苏A·B8Q62` 认成 `苏A·BBQ62`）。

两个实测踩到的坑，改动都在这里：

* **车牌一定是斜的**（仿真里相机不会正对车牌，合成数据里整车贴片还带 ±15° 旋转）。
  斜着看时检测框是"旋转矩形的轴向包围盒"，宽高比会从标准的 3.17 掉到 2.0 左右，
  缩放到 380×120 后字形被纵向压扁 —— 实测**偏 3° 就开始认错、15° 直接认不出**。
  所以正立试一次不够就按 2° 网格（±20°）逐个"反向旋转 + 裁到非填充内容包围盒"重试，
  取排序最好的结果。实测倾斜牌识别率 **16% → 75%**（81 例对照）。
* **ndarray 输入一律按 BGR 解释**（`yolo_detector_node._to_numpy` 把 `rgb8` 反成 BGR，
  `autolabel_capture_node` 用 `desired_encoding="bgr8"`）。早先这里"不区分通道顺序"，
  等于把 BGR 当 RGB 用，灰度与模板库（PIL 读的 RGB 贴图）对不上：同一张牌传 PIL 能读出
  `黑T·U1KG9`（整牌 0.817），传 BGR ndarray 就退化成 `黑??????`。
  自检用例因此专门覆盖了 BGR ndarray 路径。

`plate_textures_dir` 留空时会自动找 `smart_community_sim` 的 share 目录；
离线跑测试时用源码树相对路径即可。
