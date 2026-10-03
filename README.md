# 智慧社区仿真（第八届全球校园人工智能算法精英大赛·算法应用赛）

ROS 2 Jazzy + Gazebo Harmonic 仿真场景，包含巡检机器人与完整社区环境：

- 道路网（十字路口 + 车道中心虚线 + 停止线 + 斑马线）
- 4 组红绿灯（红/黄/绿三色，时序自动循环）
- 楼宇 A/B/C（火灾隐患识别点）、楼宇 D（异常温度识别点）
- 站房（压力表 + 温度表，供仪表读数识别）
- 停车场（3 辆蓝牌轿车，车牌号不同，供 OCR）
- 新能源充电区（绿牌电动车 + 充电桩）
- 两轮电动车停车区（含违停 + 倒伏状态）
- 垃圾分类投放点（可回收/其他/有害/厨余，含开盖/闭盖）
- 人偶立牌（社区人员/外来人员，供计数与框选）
- 指示牌（禁止直行、限速、禁止停车）
- 出发区

> 复赛任务一（巡检场景设计与演示）交付物：ROS 工作空间源代码，根目录含本 README 说明编译运行步骤。

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

启动后出现 Gazebo GUI（社区场景 + 机器人），可用键盘遥控：

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
├── worlds/smart_community.sdf        # 社区世界（由 scripts/generate_world.py 生成）
├── robot/robot.xacro                 # 巡检机器人（差分底盘 + 360° 雷达 + 单目相机）
├── launch/smart_community.launch.py  # 一键启动（世界 + 机器人 + 桥接 + 插件路径）
├── config/bridge.yaml                # ros_gz_bridge 桥接配置（含 frame_id 覆盖）
├── textures/                         # 车牌/仪表/指示牌/标签等 PNG 纹理
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

| 方向 | 绿灯 | 黄灯 | 红灯（隐含） |
|---|---|---|---|
| 南北向 (NS) | 15 s | 3 s | 18 s（= 对向绿+黄） |
| 东西向 (EW) | 15 s | 3 s | 18 s |

循环：NS 绿 → NS 黄 → EW 绿 → EW 黄 → 重复。时长可在
`worlds/smart_community.sdf` 的 `<green_time>` / `<yellow_time>` 中调整。
插件按模型名前缀 `traffic_light_ns` / `traffic_light_ew` 自动发现灯泡
（每个模型内 `red` / `yellow` / `green` 三个 `<visual>`）。

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
- **`/scan` 频率偏低（约 4Hz）**：社区场景几何较多，ogre2 渲染是瓶颈，
  属正常现象；如需提速可降低 lidar 采样数或场景复杂度。
- **无 GPU / WSL 无法开 GUI**：Gazebo GUI 需要 WSLg（`DISPLAY` 已配）。
