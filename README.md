# 智慧社区 · 复赛工程代码

> 第八届全球校园人工智能算法精英大赛 · 算法应用赛（赛马制）· 智慧社区
>
> 本工作空间包含 **社区仿真场景**、**SLAM 建图**、**Nav2 自主导航**、**多点巡检调度** 四部分，
> 由仿真组、导航组共同交付，视觉组在本接口约定上开发识别节点。

## 1. 系统组成

| 包 | 交付方 | 职责 |
|---|---|---|
| `smart_community_sim` | 仿真组 | 社区世界、机器人模型、贴图、红绿灯插件、ROS-Gazebo 桥接 |
| `community_nav` | 导航组 | 建图与导航启动文件、Nav2/SLAM 参数、巡检站点表、地图 |
| `community_patrol` | 导航组 | 多点巡检调度节点（编排导航 + 触发视觉识别 + 响应红绿灯） |

## 2. 技术栈

| 项目 | 版本 |
|---|---|
| 操作系统 | Ubuntu 24.04 (Noble)，WSL2 亦可 |
| ROS 2 | Jazzy |
| 仿真器 | Gazebo Harmonic（gz-sim 8） |
| 导航 | Nav2 1.3 |
| 建图 | slam_toolbox（2D 激光 SLAM） |
| 定位 | AMCL |
| 全局规划器 | SmacPlanner2D（A\*） |
| 局部控制器 | Regulated Pure Pursuit |

## 3. 环境要求

```bash
sudo apt update
sudo apt install -y \
  ros-jazzy-desktop \
  ros-jazzy-ros-gz \
  ros-jazzy-navigation2 \
  ros-jazzy-nav2-bringup \
  ros-jazzy-slam-toolbox \
  ros-jazzy-xacro \
  ros-jazzy-teleop-twist-keyboard \
  python3-colcon-common-extensions \
  python3-yaml \
  build-essential cmake \
  fonts-noto-cjk \
  libgz-sim8-dev
```

- `libgz-sim8-dev`：编译 `plugins/traffic_light/` 红绿灯插件所需。
- `fonts-noto-cjk`：Gazebo / RViz 中文显示。
- `ros-jazzy-ros-gz`：`ros_gz_bridge`、`ros_gz_sim`。

## 4. 编译

```bash
cd ~/smart_community_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

工作空间须位于 Linux 原生文件系统（如 `~/smart_community_ws`），放在 `/mnt/c`、`/mnt/d` 下编译会显著变慢。

## 5. 运行

### 5.1 启动社区场景

```bash
ros2 launch community_nav sim.launch.py
```

等价于直接 `ros2 launch smart_community_sim smart_community.launch.py`（sim.launch.py 只是多加了启动前残留进程检查）。启动后可自检：

```bash
ros2 topic hz /scan            # 激光雷达
ros2 topic hz /odom            # 里程计
ros2 topic hz /camera/image_raw  # 相机图像
```

### 5.2 建图

```bash
ros2 launch community_nav mapping.launch.py
ros2 run teleop_twist_keyboard teleop_twist_keyboard   # 键盘遥控走遍场地
```

### 5.3 保存地图

```bash
mkdir -p ~/smart_community_ws/src/community_nav/map
ros2 run community_nav save_map.py \
    -f ~/smart_community_ws/src/community_nav/map/community_map
```

产出 `community_map.pgm` 与 `community_map.yaml`，两个文件均须提交。

### 5.4 导航 + 多点巡检

```bash
# 演示模式：加载地图 + AMCL 定位，自动按站点表巡检
ros2 launch community_nav navigation.launch.py autostart_patrol:=true

# 调试模式：巡检节点启动但不自动开始，可在 RViz 用 "2D Goal Pose" 手动逐个验证
ros2 launch community_nav navigation.launch.py

# 边建图边导航（尚无地图时）
ros2 launch community_nav navigation.launch.py slam:=True
```

> Nav2 的 launch 参数按 **Python 字面量** 求值，布尔值须写 `True` / `False`。

### 5.5 一键演示脚本

```bash
bash src/community_nav/scripts/run_patrol_demo.sh            # 自动巡检
bash src/community_nav/scripts/run_patrol_demo.sh --no-rviz  # 不开 RViz
bash src/community_nav/scripts/run_patrol_demo.sh --build    # 先编译
```

## 6. 视觉组对接接口（重要）

完整说明见 [`docs/vision_interface.md`](docs/vision_interface.md)，这里给最简版：

**导航组 → 视觉组（视觉组订阅）：**

| 话题 | 类型 | 含义 |
|---|---|---|
| `/camera/image_raw` | `sensor_msgs/Image` | 相机画面（帧名 `camera_link`） |
| `/camera/camera_info` | `sensor_msgs/CameraInfo` | 相机内参 |
| `/patrol/capture` | `std_msgs/String`(JSON) | 到点后触发一次识别：`{"waypoint_id","task","zone","slot","target","stamp"}` |
| `/patrol/status` | `std_msgs/String`(JSON) | 巡检进度/事件 |

**视觉组 → 导航组（导航组订阅）：**

| 话题 | 类型 | 内容 |
|---|---|---|
| `/traffic_light/state` | `std_msgs/String` | `RED` / `YELLOW` / `GREEN`（大写） |
| `/detection/result` | `std_msgs/String` | 识别结果（任意字符串，建议 JSON） |

## 7. 目录结构

```
smart_community_ws/
├── README.md                        ← 本文件
├── docs/vision_interface.md         ← 视觉组接口约定
└── src/
    ├── smart_community_sim/         ← 仿真组交付
    │   ├── worlds/smart_community.sdf    社区世界
    │   ├── robot/robot.xacro             巡检机器人（差速底盘 + 360° 雷达 + 相机）
    │   ├── textures/                     贴图
    │   ├── plugins/traffic_light/        红绿灯时序 gz-sim 插件（C++）
    │   ├── config/bridge.yaml            ros_gz_bridge 桥接配置
    │   └── launch/smart_community.launch.py
    │
    ├── community_nav/               ← 导航组交付
    │   ├── launch/
    │   │   ├── sim.launch.py             启动社区场景
    │   │   ├── mapping.launch.py         建图
    │   │   ├── mapping_minimal.launch.py 建图（后备版本）
    │   │   └── navigation.launch.py      导航 + 巡检
    │   ├── config/
    │   │   ├── nav2_params.yaml          Nav2 全部参数
    │   │   ├── slam_toolbox_params.yaml  SLAM 参数
    │   │   └── community_waypoints.yaml  巡检站点表
    │   ├── map/                          建图产物
    │   └── scripts/                      存图 / keepout 掩膜 / 一键演示
    │
    └── community_patrol/            ← 导航组交付
        └── community_patrol/patrol_node.py   巡检调度核心逻辑
```

## 8. 巡检站点表

`src/community_nav/config/community_waypoints.yaml` 定义巡检站点，每站含 `id`、`name`、
`x`、`y`、`yaw` 与到点后动作（`none` / `capture` / `traffic_light` / `finish`）。
`capture` 站带 `task`（`outsider_detect` / `crowd_count` / `plate_ocr`）、`zone`、`slot`、`target` 字段，
这些字段会原样放进 `/patrol/capture` 触发消息里，视觉组据此决定识别什么。

## 9. 本次合并修复说明

- 机器人底盘抬高 0.02、DiffDrive 显式指定 `/model/robot/cmd_vel` 话题（修复车趴地 / 车不动）。
- 启动脚本 GPU 后端自动检测 `dev/dxg`（WSLg）与 `/dev/dri`，无 GPU 退回 llvmpipe。
- 移除 6 块 2cm 路面薄板的碰撞体（只保留视觉），车辆可正常压过路面；4 面围墙保留碰撞。
- `mapping.launch.py` 的 `ROS_DISTRO` 由 `humble` 修正为 `jazzy`。
- 移除 `nav2_params.yaml` 里依赖 `opennav_docking` 的 `docking_server` 段（本场地无充电桩）。
- 修复巡检节点对 `target` 字段的透传，并统一工作空间路径（原代码多处写死 `~/ros2_ws`）。

> ⚠️ 地图 `src/community_nav/map/community_map.pgm/.yaml` 是当前世界版本的建图产物。
> **最终世界布局锁定后，请导航组重新建图覆盖**，保证地图与场景完全一致。

## 10. 常见问题

- **启动报 `executable 'ros_gz_bridge' not found`**：Jazzy 的 YAML 桥接可执行文件名是 `bridge_node`（启动脚本已用正确名称）。
- **红绿灯插件加载失败**：确认已 `colcon build`，插件装在 `install/smart_community_sim/lib/`。
- **无 GPU / WSL 无法开 GUI**：Gazebo GUI 需要 WSLg（`DISPLAY` 已配）；无 GPU 时启动脚本自动退回 llvmpipe 软件渲染。
