#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建图启动文件 —— SLAM 建图阶段使用
================================================================================
用法：
    # 终端 1：先启动 Gazebo 仿真（world + 机器人）
    ros2 launch community_nav sim.launch.py

    # 终端 2：启动本文件，拉起 slam_toolbox + 生命周期管理 + RViz
    ros2 launch community_nav mapping.launch.py

    # 终端 3：键盘遥控建图
    ros2 run turtlebot3_teleop teleop_keyboard

存图：
    ros2 run nav2_map_server map_saver_cli -f ~/smart_community_ws/src/community_nav/map/community_map

【设计说明 —— 为什么不直接 include slam_toolbox 自己的 launch 文件】
    slam_toolbox 提供 online_async_launch.py 等启动文件，但**不同版本之间
    参数名可能有差异**（params_file / slam_params_file 等）。一旦名字不对，
    就会报一个很难理解的 "argument not declared" 错误。

    所以我们**直接启动节点本身**：
        slam_toolbox/async_slam_toolbox_node
      + nav2_lifecycle_manager/lifecycle_manager
    这与 Nav2 官方 slam_launch.py 的做法完全一致，只依赖两个稳定的可执行文件名，
    参数从我们自己的 YAML 读取，不经过任何第三方 launch 文件的转手。

    少一层依赖，就少一类难以定位的故障。
================================================================================
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

ROS_DISTRO = 'jazzy'

# 包名 -> 对应的 apt 包名，用于在缺包时给出可照做的提示
APT_HINT = {
    'slam_toolbox': f'ros-{ROS_DISTRO}-slam-toolbox',
    'nav2_lifecycle_manager': f'ros-{ROS_DISTRO}-navigation2',
    'nav2_bringup': f'ros-{ROS_DISTRO}-nav2-bringup',
    'rviz2': f'ros-{ROS_DISTRO}-rviz2',
}


def share(pkg):
    """获取包的 share 目录；找不到时给出明确的安装提示"""
    try:
        return get_package_share_directory(pkg)
    except Exception as exc:                                    # noqa: BLE001
        raise RuntimeError(
            '\n' + '=' * 70 + '\n'
            f'找不到 ROS 包：{pkg}\n'
            f'（{type(exc).__name__}: {exc}）\n\n'
            f'它可能没有安装。请执行：\n'
            f'    sudo apt install -y {APT_HINT.get(pkg, pkg)}\n'
            f'然后重新运行本 launch 文件。\n'
            + '=' * 70 + '\n'
        )


def generate_launch_description():

    pkg_community_nav = share('community_nav')
    pkg_nav2_bringup = share('nav2_bringup')

    # ---------------- ★ 启动前自检：参数文件必须真的在 ----------------
    #   如果这个文件不存在，slam_toolbox 会在启动瞬间失败退出，
    #   而表面上只看到"节点没起来、/map 没有"——很难往参数文件上想。
    #   在这里显式检查，把问题暴露在启动的第一秒。
    default_slam_params = os.path.join(pkg_community_nav, 'config',
                                       'slam_toolbox_params.yaml')
    if not os.path.isfile(default_slam_params):
        raise RuntimeError(
            '\n' + '=' * 70 + '\n'
            f'找不到 SLAM 参数文件：\n  {default_slam_params}\n\n'
            f'常见原因：改了 src 下的文件但没有重新编译。\n'
            f'解决办法：\n'
            f'    cd <你的 ROS2 工作空间根目录> && colcon build --symlink-install\n'
            f'或者用参数直接指定：\n'
            f'    ros2 launch community_nav mapping.launch.py \\\n'
            f'        slam_params_file:=$HOME/smart_community_ws/src/community_nav/'
            f'config/slam_toolbox_params.yaml\n'
            + '=' * 70 + '\n'
        )
    print(f'[community_nav] SLAM 参数文件 = {default_slam_params}')

    # ---------------- 可配置参数 ----------------
    use_sim_time = LaunchConfiguration('use_sim_time')
    slam_params = LaunchConfiguration('slam_params_file')
    use_rviz = LaunchConfiguration('rviz')
    autostart = LaunchConfiguration('autostart')

    declare_use_sim_time = DeclareLaunchArgument(
        'use_sim_time', default_value='true',
        description='使用 Gazebo 仿真时钟（仿真环境必须为 true）')

    declare_slam_params = DeclareLaunchArgument(
        'slam_params_file',
        default_value=default_slam_params,
        description='slam_toolbox 参数文件路径')

    declare_rviz = DeclareLaunchArgument(
        'rviz', default_value='true', description='是否启动 RViz')

    declare_autostart = DeclareLaunchArgument(
        'autostart', default_value='true',
        description='是否自动 configure + activate slam_toolbox（Jazzy 上必须为 true）')

    # ---------------- slam_toolbox 建图节点 ----------------
    # 节点名必须叫 slam_toolbox，才能匹配参数文件里的 slam_toolbox: 段
    slam_node = Node(
        package='slam_toolbox',
        executable='async_slam_toolbox_node',
        name='slam_toolbox',
        output='screen',
        parameters=[slam_params, {'use_sim_time': use_sim_time}],
    )

    # ---------------- ★ 生命周期管理器（Jazzy 上必须有）----------------
    #
    # 【一段跨版本踩坑的真实记录，值得记住】
    #
    # 在 ROS 2 **Humble** 上，我们实测发现 async_slam_toolbox_node
    # **不提供生命周期服务**（`ros2 service list | grep get_state` 什么都没有）。
    # 那时加生命周期管理器是【错的】—— 它会永远卡在
    #     Waiting for service slam_toolbox/get_state...
    # 上刷屏，而 slam_toolbox 自己就能正常发 /map。
    # 我们据此把管理器删掉了。
    #
    # 迁移到 ROS 2 **Jazzy** 之后，同样的配置下 `/map` **一条都不发**，
    # 而且日志里【没有任何报错】。再查一次服务列表：
    #     /slam_toolbox/change_state
    #     /slam_toolbox/get_state        ← 这次有了！
    # 也就是说 **Jazzy 的 async_slam_toolbox_node 是标准生命周期节点**，
    # 必须被 configure + activate 才会开始工作。
    #
    # 【教训】
    #   · 同一个组件在不同 ROS 2 大版本之间可能有【行为性】差异，
    #     不只是参数名不同；
    #   · "在我这个版本上不需要"不能推广成"永远不需要"；
    #   · 迁移后【所有运行时行为都要重新验证】，静态检查保证不了这一点。
    #
    # 官方 online_async_launch.py 的做法与此等价：
    # 它通过 lifecycle ChangeState 事件自行 configure + activate。
    #
    # ★ bond_timeout 必须设为 0（关闭）：
    #   Nav2 的生命周期管理器在激活节点后，还会要求节点通过
    #   【bond 心跳】持续证明自己活着。这套机制是 nav2_util::LifecycleNode
    #   提供的，而 slam_toolbox 用的是自己的生命周期实现，**不支持 bond**。
    #   于是会出现：
    #       Server slam_toolbox was unable to be reached after 4.00s by bond.
    #       Failed to bring up all requested nodes. Aborting bringup.
    #   节点其实已经激活、/map 也在正常发布，但日志报得像失败了，
    #   会让人以为建图坏了而去查错方向。
    #   设 bond_timeout: 0.0 关闭心跳检查即可。
    lifecycle_manager = Node(
        package='nav2_lifecycle_manager',
        executable='lifecycle_manager',
        name='lifecycle_manager_slam',
        output='screen',
        parameters=[{
            'use_sim_time': use_sim_time,
            'autostart': autostart,
            'node_names': ['slam_toolbox'],
            'bond_timeout': 0.0,
        }],
    )

    # ---------------- RViz ----------------
    # ★★ 这里【不】用 nav2_bringup 的 rviz_launch.py，而是直接起 rviz2 节点。
    #
    #    原因很重要：RViz 在 WSL 下容易因为图形问题崩掉。
    #    而用 IncludeLaunchDescription 引入时，RViz 一死会触发【整个 launch 关闭】——
    #    slam_toolbox 被一起带走，/map 就永远出不来。
    #    实际踩到过：日志里 rviz2 先崩，紧接着 lifecycle_manager 走关闭流程。
    #
    #    改成直接起节点 + on_exit 只打一条日志，
    #    这样 【RViz 挂了建图照样继续】，地图还能建出来。
    rviz_config = os.path.join(pkg_nav2_bringup, 'rviz', 'nav2_default_view.rviz')
    rviz_args = ['-d', rviz_config] if os.path.isfile(rviz_config) else []

    rviz = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        output='screen',
        arguments=rviz_args,
        parameters=[{'use_sim_time': use_sim_time}],
        condition=IfCondition(use_rviz),
        on_exit=LogInfo(
            msg='⚠️  RViz 已退出，但【建图仍在继续运行】—— SLAM 不受影响。'),
    )

    return LaunchDescription([
        declare_use_sim_time,
        declare_slam_params,
        declare_rviz,
        declare_autostart,
        slam_node,
        lifecycle_manager,
        rviz,
    ])
