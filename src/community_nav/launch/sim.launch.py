#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧社区场景启动（ROS 2 Jazzy + Gazebo Harmonic）
================================================================================
用法：
    ros2 launch community_nav sim.launch.py

【这个文件为什么这么薄】
    场景本身由建模组交付的 `smart_community_sim` 包提供：

      · worlds/smart_community.sdf        完整社区世界（含 2 组红绿灯）
      · robot/robot.xacro                 巡检机器人（差速底盘 + 360° 雷达 + 相机）
      · config/bridge.yaml                ros_gz_bridge 桥接配置
      · launch/smart_community.launch.py  世界 + 机器人 + 桥接，一键启动

    他们的启动文件已经把世界、机器人、桥接都处理好了，话题名和坐标系
    也按导航组的要求做了清洗（无 model/robot 前缀），所以这里【直接复用】，
    不重复造一遍——重复实现两套启动逻辑，只会让出错的地方变多。

    我们在这个文件里只加一件事：**启动前的残留进程检查**（见下）。

【为什么整个栈从 Humble + Classic 迁到了 Jazzy + Harmonic】
    建模组的场景用了三样 Gazebo Classic【完全不支持】的东西：
      · gz-sim 插件体系（6 个 world 级插件 + 1 个自研红绿灯插件）
      · PBR 材质（<pbr><metal><albedo_map>）—— Classic 用的是 Ogre 材质脚本
      · SDF 1.8 —— Classic 11 上限是 1.7
    把场景移植到 Classic 等于重做（39 处贴图材质 + 插件重写 + 版本降级），
    而迁移导航栈只需要改 3 处已知参数差异 + 换一个启动文件。
    所以选择迁移。
================================================================================
"""

import os
import subprocess

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

# Gazebo 进程的特征名（Classic 的 gzserver/gzclient，Harmonic 的 gz sim）
GAZEBO_PATTERNS = ['gzserver', 'gzclient', 'gz sim', 'gz-sim', 'ruby.*gz']


def find_stale_gazebo():
    """返回仍在运行的 Gazebo 进程 PID 列表

    为什么需要这个检查：
      上一次的 Gazebo 没退干净时，新的实例会因为端口/资源冲突而启动失败。
      失败信息往往只有一行 "process has died"，之后就是
      "没有 /scan、没有 /odom、没有 /map"——看起来像 SLAM 或导航坏了，
      实际是仿真根本没起来。在这里提前拦下来，能省掉大量无效排查。
    """
    pids = []
    for pat in GAZEBO_PATTERNS:
        try:
            out = subprocess.run(['pgrep', '-f', pat],
                                 capture_output=True, text=True, timeout=5)
        except Exception:                                      # noqa: BLE001
            return []          # 查不了就不阻断启动
        if out.returncode == 0 and out.stdout.strip():
            pids.extend(out.stdout.split())
    # 排除自己（pgrep 模式本身可能命中本进程）
    me = str(os.getpid())
    return sorted({p for p in pids if p != me})


def generate_launch_description():

    stale = find_stale_gazebo()
    if stale:
        raise RuntimeError(
            '\n' + '=' * 70 + '\n'
            f'检测到已经有 Gazebo 在运行（PID: {", ".join(stale)}）。\n\n'
            f'如果直接启动，新的实例会因资源冲突而失败退出，\n'
            f'表现为"仿真没起来、/scan 没有、/map 没有"。\n\n'
            f'请先清理：\n'
            f'    pkill -9 -f "gz sim" ; pkill -9 -f gzserver\n'
            f'然后再运行本 launch 文件。\n'
            + '=' * 70 + '\n'
        )

    # ★ 场景包的查找要包在 try 里：
    #   get_package_share_directory 在包不存在时抛的是 LookupError，
    #   直接冒出去的话，用户看到的是一句
    #     "package 'smart_community_sim' not found"
    #   完全不知道该怎么办。这里换成能直接照做的提示。
    try:
        pkg_scene = get_package_share_directory('smart_community_sim')
    except Exception as exc:                                   # noqa: BLE001
        raise RuntimeError(
            '\n' + '=' * 70 + '\n'
            f'找不到建模组的场景包 smart_community_sim。\n\n'
            f'这通常意味着【还没有编译】。请执行：\n'
            f'    cd <你的 ROS2 工作空间根目录>\n'
            f'    colcon build --symlink-install\n'
            f'    source install/setup.bash\n'
            f'然后再运行本 launch 文件。\n'
            + '=' * 70 + '\n'
        ) from exc

    scene_launch = os.path.join(pkg_scene, 'launch', 'smart_community.launch.py')

    if not os.path.isfile(scene_launch):
        raise RuntimeError(
            '\n' + '=' * 70 + '\n'
            f'找不到建模组的场景启动文件：\n  {scene_launch}\n\n'
            f'请确认 smart_community_sim 包已编译：\n'
            f'    cd <你的 ROS2 工作空间根目录> && colcon build --symlink-install\n'
            + '=' * 70 + '\n'
        )

    use_sim_time = LaunchConfiguration('use_sim_time')

    declare_use_sim_time = DeclareLaunchArgument(
        'use_sim_time', default_value='true',
        description='使用 Gazebo 仿真时钟（仿真环境必须为 true）')

    # 直接复用建模组的启动文件：世界 + 机器人 + 桥接
    scene = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(scene_launch),
        launch_arguments={'use_sim_time': use_sim_time}.items(),
    )

    return LaunchDescription([
        declare_use_sim_time,
        scene,
    ])
