# 智慧社区仿真（第八届全球校园人工智能算法精英大赛·算法应用赛）

ROS 2 Jazzy + Gazebo Harmonic 仿真场景，包含巡检机器人与社区环境（道路、红绿灯、人偶立牌、车牌车辆、垃圾桶、楼宇、站房等）。

> 复赛任务一（巡检场景设计与演示）交付物：ROS 工作空间源代码，根目录含本 README 说明编译运行步骤。

## 环境要求

- Ubuntu 24.04 (Noble) / WSL2
- ROS 2 Jazzy
- Gazebo Harmonic (`gz-sim`)
- `ros_gz_sim`、`ros_gz_bridge`、`robot_state_publisher`、`xacro`

## 编译

```bash
cd ~/smart_community_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## 运行

```bash
ros2 launch smart_community_sim smart_community.launch.py
```

启动后会出现 Gazebo GUI（社区场景 + 机器人），可用键盘遥控：

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
├── worlds/smart_community.sdf      # 社区世界（道路/建筑/功能区/目标模型）
├── robot/robot.xacro               # 巡检机器人（差分底盘 + 360°雷达 + 单目相机）
├── launch/smart_community.launch.py# 一键启动（世界 + 机器人 + 桥接）
├── config/                         # 预留配置目录
└── plugins/                        # 自定义 gz-sim 插件（红绿灯时序等）
```

## 传感器/话题

| 数据 | ROS2 话题 | 方向 |
|---|---|---|
| 速度指令 | `/cmd_vel` | ros -> gz |
| 里程计 | `/odom` | gz -> ros |
| 坐标变换 | `/tf` | gz + rsp -> ros |
| 激光雷达 | `/scan` | gz -> ros |
| 相机图像 | `/camera/image_raw` | gz -> ros |
| 相机内参 | `/camera/camera_info` | gz -> ros |

> 说明：SLAM（slam_toolbox）与导航（Nav2）由队友负责，本场景只需保证上述话题正常输出即可交接。
