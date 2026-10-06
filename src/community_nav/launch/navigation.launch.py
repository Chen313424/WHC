#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导航 + 多点巡检启动文件 —— 复赛演示使用
================================================================================
用法：
    # 终端 1：先启动 Gazebo 仿真
    export TURTLEBOT3_MODEL=waffle
    ros2 launch turtlebot3_gazebo turtlebot3_world.launch.py

    # 终端 2：一键拉起 定位 + 导航 + 巡检
    ros2 launch community_nav navigation.launch.py

它会一次性启动：
    map_server       加载静态地图
    AMCL             蒙特卡洛定位（自动设置初始位姿，无需人工干预）
    Nav2 导航栈       planner / controller / bt_navigator / behaviors / costmap
    RViz             可视化
    community_patrol 多点巡检调度节点（自动开始巡检）

前置条件：必须先完成建图并存出 community_map.pgm / .yaml
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


def generate_launch_description():

    pkg_community_nav = get_package_share_directory('community_nav')
    pkg_nav2_bringup = get_package_share_directory('nav2_bringup')

    # ---------------- 可配置参数 ----------------
    use_sim_time = LaunchConfiguration('use_sim_time')
    params_file = LaunchConfiguration('params_file')
    map_yaml = LaunchConfiguration('map')
    use_rviz = LaunchConfiguration('rviz')
    slam = LaunchConfiguration('slam')
    autostart_patrol = LaunchConfiguration('autostart_patrol')

    declare_use_sim_time = DeclareLaunchArgument(
        'use_sim_time', default_value='true')

    declare_params = DeclareLaunchArgument(
        'params_file',
        default_value=os.path.join(pkg_community_nav, 'config', 'nav2_params.yaml'),
        description='Nav2 参数文件路径')

    # 地图用包自带的那份（community_nav/map/community_map.yaml）。
    # 用 --symlink-install 编译时，pkg_community_nav 直接指向 src 下的包目录，
    # 重新保存地图覆盖同路径即可；也可用 map:=<路径> 覆盖默认值。
    default_map = os.path.join(pkg_community_nav, 'map', 'community_map.yaml')

    declare_map = DeclareLaunchArgument(
        'map',
        default_value=default_map,
        description='SLAM 建图得到的地图 yaml 路径')

    declare_rviz = DeclareLaunchArgument(
        'rviz', default_value='true', description='是否启动 RViz')

    declare_slam = DeclareLaunchArgument(
        'slam', default_value='False',
        description='为 True 时用 slam_toolbox 边建图边导航（还没有地图时用）；'
                    '为 False 时加载 map 参数指定的地图并启用 AMCL 定位。'
                    '★ 必须写 Python 风格的 True/False（首字母大写）——'
                    'Nav2 内部用 IfCondition 把它当 Python 表达式求值，'
                    '小写 true 会直接报 "name \'true\' is not defined" 并中止启动')

    # ★ 默认 false（不自动巡检）。理由：
    #   调试阶段站点表里往往还是占位坐标，一启动就让车自己乱跑会干扰
    #   单点导航的测试。此时应该先在 RViz 里用 "2D Goal Pose" 手动点几个目标，
    #   确认定位、规划、控制都正常，再去填站点坐标。
    #   比赛演示/录制视频时再加 autostart_patrol:=true 实现"一键启动"。
    declare_autostart = DeclareLaunchArgument(
        'autostart_patrol', default_value='false',
        description='是否在启动后自动开始巡检（比赛演示用 true）')

    # ---------------- Nav2 完整导航栈 ----------------
    # 复用官方 nav2_bringup/bringup_launch.py，只覆盖我们自己的参数文件与地图。
    # use_composition 设为 False：每个 Nav2 节点独立进程，
    # 虽然内存占用略高，但**便于单独查看日志、单独重启某个节点**，
    # 在调试期这个好处远大于性能损失。
    #
    # ★ slam 参数：为 true 时 bringup 会把 map_server + AMCL 换成 slam_toolbox，
    #   也就是【边建图边导航】。这有两个用途：
    #     · 还没有地图时，先跑 slam:=true 让机器人一边探索一边建图
    #     · 场地改动后重新建图，不用分两步走
    #   正常演示时应该用 slam:=false（加载已建好的地图 + AMCL 定位），
    #   因为 AMCL 的定位精度和稳定性都比在线 SLAM 好。
    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_nav2_bringup, 'launch', 'bringup_launch.py')),
        launch_arguments={
            'map': map_yaml,
            'use_sim_time': use_sim_time,
            'params_file': params_file,
            'autostart': 'true',
            'use_composition': 'False',
            'slam': slam,
        }.items(),
    )

    # ---------------- RViz ----------------
    # ★★ 这里【不】用 nav2_bringup 的 rviz_launch.py，而是直接起 rviz2 节点。
    #
    #    原因：RViz 在 WSL 下容易因图形问题崩掉，而用 IncludeLaunchDescription
    #    引入时，它一死会触发【整个 launch 关闭】—— 整条 Nav2 栈被一起带走。
    #    建图阶段已经实际踩到过这个坑（rviz2 先崩，紧接着生命周期管理器走关闭流程）。
    #
    #    改成直接起节点 + on_exit 只打日志，
    #    这样【RViz 挂了导航照样继续】，最后的演示和录制不会被可视化工具拖垮。
    rviz_config = os.path.join(pkg_nav2_bringup, 'rviz', 'nav2_default_view.rviz')
    rviz_args = ['-d', rviz_config] if os.path.isfile(rviz_config) else []

    rviz_launch = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        output='screen',
        arguments=rviz_args,
        parameters=[{'use_sim_time': use_sim_time}],
        condition=IfCondition(use_rviz),
        on_exit=LogInfo(
            msg='⚠️  RViz 已退出，但【导航仍在继续运行】—— Nav2 不受影响。'),
    )

    # ---------------- 多点巡检调度节点（自研） ----------------
    # 延迟启动，等 Nav2 各节点完成生命周期激活后再开始发导航目标。
    patrol_node = Node(
        package='community_patrol',
        executable='patrol_node',
        name='community_patrol',
        output='screen',
        emulate_tty=True,
        parameters=[{
            'waypoints_file': os.path.join(
                pkg_community_nav, 'config', 'community_waypoints.yaml'),
            'autostart': autostart_patrol,
            'start_delay': 5.0,       # 给 Nav2 生命周期激活留时间
        }],
    )

    return LaunchDescription([
        declare_use_sim_time,
        declare_params,
        declare_map,
        declare_rviz,
        declare_slam,
        declare_autostart,
        nav2_launch,
        rviz_launch,
        patrol_node,
    ])
