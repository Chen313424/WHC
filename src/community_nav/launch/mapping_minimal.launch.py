#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建图启动文件（最小版）—— 排查专用后备方案
================================================================================
用法：
    ros2 launch community_nav mapping_minimal.launch.py

【什么时候用它】
    正常的 `mapping.launch.py` 不出地图（`ros2 topic hz /map` 一直没输出）时，
    用这个来快速二分定位问题。

    `mapping.launch.py` 从 **YAML 参数文件** 读配置，而这个文件里参数很多，
    任何一个参数名或类型不对，都可能导致 slam_toolbox 在 configure 阶段
    失败——而它失败得很安静，你只看到"/map 没有"。

    本文件**完全不用参数文件**，所有参数以最小的字典形式直接传给节点，
    只保留最核心、最不容易出错的十几项。

| 结果 | 说明 |
|---|---|
| 本文件**能出 `/map`** | 问题在 `slam_toolbox_params.yaml` 里某个参数 → 逐项删减定位 |
| 本文件**也不出 `/map`** | 问题不在参数文件，是更底层的原因（依赖/版本/环境） |

【它有意识地放弃了什么】
    · 不做针对 4.2m 小场地的精细调参（那在正式参数文件里）
    · 分辨率用 0.05 而不是 0.03
    · 只是为了让 SLAM "先跑起来"，**不是**最终配置
================================================================================
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# ------------------------------------------------------------------------------
#  最小参数集：每一项都是最核心、最不容易出错的
# ------------------------------------------------------------------------------
SLAM_MINIMAL_PARAMS = {
    'use_sim_time': True,
    'odom_frame': 'odom',
    'map_frame': 'map',
    'base_frame': 'base_footprint',
    'scan_topic': '/scan',
    'mode': 'mapping',

    'resolution': 0.05,
    'max_laser_range': 5.0,
    'minimum_travel_distance': 0.2,
    'minimum_travel_heading': 0.2,
    'map_update_interval': 1.0,
    'transform_publish_period': 0.02,
    'transform_timeout': 0.5,
    'tf_buffer_duration': 30.0,

    'use_scan_matching': True,
    'use_scan_barycenter': True,
    'do_loop_closing': True,

    'debug_logging': False,
    'throttle_scans': 1,
    'enable_interactive_mode': True,
}


def generate_launch_description():

    use_sim_time = LaunchConfiguration('use_sim_time')
    use_rviz = LaunchConfiguration('rviz')

    declare_use_sim_time = DeclareLaunchArgument(
        'use_sim_time', default_value='true',
        description='使用 Gazebo 仿真时钟')

    declare_rviz = DeclareLaunchArgument(
        'rviz', default_value='true', description='是否启动 RViz')

    # ---- slam_toolbox 建图节点（参数直接内联，不读文件）----
    slam_node = Node(
        package='slam_toolbox',
        executable='async_slam_toolbox_node',
        name='slam_toolbox',
        output='screen',
        parameters=[SLAM_MINIMAL_PARAMS, {'use_sim_time': use_sim_time}],
    )

    # ---- 生命周期管理器 ----
    # ★ Jazzy 上 async_slam_toolbox_node 是标准生命周期节点，
    #   必须被 configure + activate 才会开始发布 /map。
    #   （Humble 上它不是生命周期节点，所以那边反而不需要管理器。
    #     同一个组件跨大版本行为不同——迁移后运行时行为必须重新验证。）
    lifecycle_manager = Node(
        package='nav2_lifecycle_manager',
        executable='lifecycle_manager',
        name='lifecycle_manager_slam',
        output='screen',
        parameters=[{
            'use_sim_time': use_sim_time,
            'autostart': True,
            'node_names': ['slam_toolbox'],
            # ★ slam_toolbox 不支持 Nav2 的 bond 心跳，必须关掉检查，
            #   否则会报 "unable to be reached by bond ... Aborting bringup"，
            #   看起来像失败，实际节点已激活并正常发 /map。
            'bond_timeout': 0.0,
        }],
    )

    # ---- RViz（自己起节点，on_exit 只打日志，崩了不影响建图）----
    pkg_nav2_bringup = get_package_share_directory('nav2_bringup')
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
        on_exit=LogInfo(msg='⚠️  RViz 已退出，但【建图仍在继续运行】。'),
    )

    return LaunchDescription([
        declare_use_sim_time,
        declare_rviz,
        slam_node,
        lifecycle_manager,
        rviz,
    ])
