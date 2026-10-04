# 智慧社区仿真（第八届全球校园人工智能算法精英大赛·算法应用赛·省赛）

ROS 2 Jazzy + Gazebo Harmonic 仿真场景，对齐省赛 4.2m×4.2m 场地规格与示意图布局：

- **场地**：4.2m×4.2m 方形地板，四周围墙（厚 0.5cm、高 50cm）
- **环形闭合车道**：带边线与道路中心虚线，道路侧边留白 60cm，路宽 40cm；
  车道上橙黄色箭头标记行驶方向（顺时针：顶→东、右→南、底→西、左→北）
- **2 组红绿灯**（顶部直道 / 底部直道，整体 48×64×5cm，箱体 59×14×5cm 带双腿），
  各带停止线；下方红绿灯北侧有斑马线人行横道。时序：红灯 10s / 绿灯 15s / 黄灯 5s
- **18 个人偶立牌**（高 15cm × 宽 5cm × 厚 0.5cm）：
  A 街区（左上）6 人 + B 街区（左下）6 人 + 人行道 6 人，其中 2 人为非社区人员（F1/F2）
- **右侧停车场**：3/2/1 号 3 个停车位（各宽 60cm，从上到下），每车位停 1 个车背景立牌
  （34.5×25cm）+ 车牌（9.5×3cm）；另设 1 台车辆背景模型
- **起点/终点合并**：场地右上角

> 交付物：ROS 工作空间源代码，根目录含本 README 说明编译运行步骤。

## 环境要求

- Ubuntu 24.04 (Noble) / WSL2
- ROS 2 Jazzy
- Gazebo Harmonic (`gz-sim`，需含开发头文件 `libgz-sim8-dev` 以编译红绿灯插件)
- `ros_gz_sim`、`ros_gz_bridge`、`robot_state_publisher`、`xacro`、`teleop_twist_keyboard`
- Python 3 + Pillow（仅在**重新生成**纹理/世界时需要，仓库已带生成产物）

## 编译

```bash
cd ~/smart_community_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

首次编译会同时编译红绿灯系统插件 `libTrafficLightSystem.so`，安装到
`install/smart_community_sim/lib/`，启动脚本会自动把它加入
`GZ_SIM_SYSTEM_PLUGIN_PATH`。

## 运行

```bash
ros2 launch smart_community_sim smart_community.launch.py
```

启动后出现 Gazebo GUI（省赛场地 + 机器人），可用键盘遥控：

```bash
ros2 run teleop_twist_keyboard teleop_twist_keyboard
```

查看传感器数据：

```bash
ros2 topic echo /odom
ros2 topic echo /scan
ros2 topic echo /camera/image_raw   # 或 rqt_image_view / rviz2 查看
```

## 目录结构

```
src/smart_community_sim/
├── worlds/smart_community.sdf        # 省赛世界（由 scripts/generate_world.py 生成）
├── robot/robot.xacro                 # 巡检机器人（差分底盘 + 360° 雷达 + 单目相机）
├── launch/smart_community.launch.py  # 一键启动（世界 + 机器人 + 桥接 + 插件路径）
├── config/bridge.yaml                # ros_gz_bridge 桥接配置（含 frame_id 覆盖）
├── materials/                        # 原始模型图（人偶 / 车牌 / 车背景 / 红绿灯）
├── textures/                         # 由 materials 生成的 PNG 纹理（立牌贴图等）
├── scripts/
│   ├── generate_textures.py          # 生成 textures/（离线、可复现）
│   └── generate_world.py             # 生成 worlds/smart_community.sdf
└── plugins/traffic_light/            # 红绿灯时序控制 gz-sim 系统插件（C++）
```

### 重新生成场景

仓库已附带生成产物（`worlds/*.sdf`、`textures/*.png`），直接使用即可。
如需改动布局/纹理，改对应脚本后重跑：

```bash
cd src/smart_community_sim
python3 scripts/generate_textures.py   # 重新生成 PNG 纹理
python3 scripts/generate_world.py      # 重新生成世界 SDF
```

## 红绿灯时序

| 灯色 | 时长 |
|---|---|
| 绿灯 | 15 s |
| 黄灯 | 5 s |
| 红灯 | 10 s |

循环：绿(15s) → 黄(5s) → 红(10s) → 重复。**2 组红绿灯同一时序、同步切换**。
时长可在 `worlds/smart_community.sdf` 的 `<green_time>` / `<yellow_time>` /
`<red_time>` 中调整。插件按模型名前缀 `traffic_light` 自动发现灯泡
（每个模型内 `red` / `yellow` / `green` 三个 `<visual>`，与灯泡排列方向无关）。

## 传感器 / 话题

| 数据 | ROS2 话题 | 帧名 | 方向 |
|---|---|---|---|
| 速度指令 | `/cmd_vel` | — | ros → gz |
| 里程计 | `/odom` | `odom` → `base_footprint` | gz → ros |
| 坐标变换 | `/tf` | `odom` → `base_footprint` | gz → ros |
| 激光雷达 | `/scan` | `lidar_link` | gz → ros |
| 相机图像 | `/camera/image_raw` | `camera_link` | gz → ros |
| 相机内参 | `/camera/camera_info` | `camera_link` | gz → ros |

TF 树：`odom` → `base_footprint`（OdometryPublisher 插件）→
`base_link` → `lidar_link` / `camera_link` / 车轮（robot_state_publisher）。

> 说明：SLAM（slam_toolbox）与导航（Nav2）由队友负责，本场景保证上述话题
> 与帧名干净（无 `model/robot` 前缀），可直接对接。

## 常见问题

- **启动报 `executable 'ros_gz_bridge' not found`**：本机 Jazzy 的 YAML 配置
  桥接可执行文件名为 `bridge_node`（启动脚本已用正确名称）。
- **红绿灯插件加载失败**：确认已 `colcon build`，插件装在
  `install/smart_community_sim/lib/`；启动脚本会自动设置
  `GZ_SIM_SYSTEM_PLUGIN_PATH`。
- **无 GPU / WSL 无法开 GUI**：Gazebo GUI 需要 WSLg（`DISPLAY` 已配）。
