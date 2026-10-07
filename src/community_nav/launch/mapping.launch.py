#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自主建图启动文件 —— 复赛「自主建图」使用（无需键盘遥控）
================================================================================
用法：
    # 终端 1：先启动 Gazebo 仿真（world + 机器人）
    ros2 launch community_nav sim.launch.py

    # 终端 2：一键拉起 slam_toolbox + Nav2(SLAM 模式) + 自动走全车道回路
    ros2 launch community_nav mapping.launch.py

    机器人会自动沿回字形道路的 6 段中心线走一遍，边跑边建图。
    跑完后地图闭环，即可存图：
        ros2 run nav2_map_server map_saver_cli -f <map 输出路径>

【为什么从「键盘遥控」改成「自主回路」】
    复赛评分维度明确写的是「自主建图、导航与避障」（20 分），视频要求里
    也要求展示「机器人在场地中的自主移动」。原来的做法是在终端 3 用
    teleop_keyboard 人工遥控建图，这与「自主建图」的字面要求冲突。

    本文件让机器人用 Nav2 的 NavigateToPose 自主驶过全部车道：
      · slam_toolbox 实时发布 /map 和 map→odom TF；
      · Nav2 在 SLAM 模式（不加载静态地图、不跑 AMCL）下规划/跟踪；
      · community_patrol 节点按 mapping_waypoints.yaml 自动走完回路。
    全程不碰键盘，机器人自主建图。

【为什么 slam_toolbox 要【直接启动节点】而不是用 Nav2 的 slam:=true】
    Nav2 bringup 的 slam:=true 会 include slam_toolbox 官方 launch 文件，
    不同 ROS 2 版本间参数名可能不同（params_file / slam_params_file 等），
    而且它只认 nav2_params.yaml 里有没有 slam_toolbox 段——我们的 SLAM 参数
    单独放在 slam_toolbox_params.yaml，直接传会被忽略、退回默认参数。
    所以这里沿用「直接起节点 + 生命周期管理器」的稳定做法，参数从
    我们自己的 YAML 读取，Nav2 侧用 use_localization:=false 只拉导航栈。
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
    use_composition = LaunchConfiguration('use_composition')
    waypoints_file = LaunchConfiguration('waypoints_file')

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

    # ★ 默认 true：建图是 slam_toolbox + Nav2 全栈 + 巡检节点同时跑，
    #   进程数量多，合成进单进程能显著降低 DDS 开销（详见 navigation.launch.py
    #   里 use_composition 的实测说明，独立进程时实时因子会掉到 0.09）。
    declare_use_composition = DeclareLaunchArgument(
        'use_composition', default_value='True',
        description='是否把所有 Nav2 服务端合成到一个进程里跑。'
                    '默认 True（建图负载重，合成省资源）；调试可传 False')

    declare_waypoints = DeclareLaunchArgument(
        'waypoints_file',
        default_value=os.path.join(pkg_community_nav, 'config',
                                   'mapping_waypoints.yaml'),
        description='自主建图回路文件路径')

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
    # 详见 navigation.launch.py / 旧版 mapping.launch.py 的踩坑记录：
    #   · Jazzy 的 async_slam_toolbox_node 是标准生命周期节点，必须被
    #     configure + activate 才会发 /map；
    #   · bond_timeout 必须设 0：slam_toolbox 不支持 bond 心跳，
    #     否则生命周期管理器会误报 "unable to be reached by bond"。
    lifecycle_manager_slam = Node(
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

    # ---------------- Nav2 导航栈（SLAM 模式） ----------------
    # 关键参数：
    #   slam:=false —— 不把 Nav2 自己接管的 slam_toolbox 拉起来（我们已直接启动）
    #   use_localization:=false —— 不启动 map_server 和 AMCL
    # 于是 Nav2 只启动 planner / controller / bt_navigator / behaviors / costmap，
    # 地图来源是上面那个 slam_toolbox 实时发布的 /map 和 map→odom TF。
    # 这是「外部 SLAM」的标准接法：costmap 订阅 /map（默认话题），
    # global_costmap 用 map 帧、local_costmap 用 odom 帧，与我们的
    # nav2_params.yaml 完全一致，无需改动参数文件。
    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_nav2_bringup, 'launch', 'bringup_launch.py')),
        launch_arguments={
            'use_sim_time': use_sim_time,
            'params_file': os.path.join(pkg_community_nav, 'config',
                                        'nav2_params.yaml'),
            'autostart': 'true',
            'use_composition': use_composition,
            # ★ 必须写 Python 风格的 False（首字母大写）：Nav2 内部用
            #   PythonExpression 求值（slam and use_localization），
            #   小写 false 会直接报 "name 'false' is not defined"。
            'slam': 'False',
            'use_localization': 'False',
            'map': '',                     # 无静态地图，由 slam_toolbox 提供 /map
        }.items(),
    )

    # ---------------- 自主建图回路调度（复用巡检节点） ----------------
    # community_patrol 按 mapping_waypoints.yaml 逐点发 NavigateToPose，
    # 机器人自主驶过全车道；所有站点 action=none，不停留、不触发视觉。
    mapping_node = Node(
        package='community_patrol',
        executable='patrol_node',
        name='community_patrol',
        output='screen',
        emulate_tty=True,
        parameters=[{
            'waypoints_file': waypoints_file,
            'autostart': True,             # 启动即开始走回路，无人干预
            'start_delay': 6.0,            # 给 slam_toolbox + Nav2 激活留时间
        }],
    )

    # ---------------- RViz ----------------
    # 直接起 rviz2 节点（不 include nav2 的 rviz_launch）：RViz 崩掉时
    # 只打日志，不影响建图。WSL 下 RViz 易崩，这是踩过坑后的稳妥做法。
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
        declare_use_composition,
        declare_waypoints,
        slam_node,
        lifecycle_manager_slam,
        nav2_launch,
        mapping_node,
        rviz,
    ])
