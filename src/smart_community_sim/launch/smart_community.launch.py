"""智慧社区仿真环境启动文件。

启动内容：
  1. Gazebo Harmonic (gz-sim)，加载 smart_community.sdf 世界
  2. robot_state_publisher（从 robot.xacro 发布 TF 与 robot_description）
  3. ros_gz_sim create（把机器人 spawn 进 Gazebo）
  4. ros_gz_bridge（桥接 clock / cmd_vel / odom / tf / lidar / camera）
"""
import os

from ament_index_python.packages import get_package_share_directory, get_package_prefix
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetEnvironmentVariable
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import xacro


def generate_launch_description():
    pkg = get_package_share_directory('smart_community_sim')
    pkg_ros_gz_sim = get_package_share_directory('ros_gz_sim')

    # 红绿灯插件 libTrafficLightSystem.so 装在 <prefix>/lib，
    # 通过 GZ_SIM_SYSTEM_PLUGIN_PATH 让 gz-sim 能按文件名加载。
    plugin_lib = os.path.join(get_package_prefix('smart_community_sim'), 'lib')

    world_file = os.path.join(pkg, 'worlds', 'smart_community.sdf')
    xacro_file = os.path.join(pkg, 'robot', 'robot.xacro')

    robot_description = {'robot_description': xacro.process_file(xacro_file).toxml()}

    use_sim_time = LaunchConfiguration('use_sim_time', default='true')

    # Gazebo Sim
    gz_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_ros_gz_sim, 'launch', 'gz_sim.launch.py')),
        launch_arguments={'gz_args': ['-r ', world_file]}.items(),
    )

    # TF + robot_description
    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[robot_description, {'use_sim_time': use_sim_time}],
    )

    # 把机器人 spawn 进 Gazebo（URDF -> SDF 自动转换）
    spawn = Node(
        package='ros_gz_sim',
        executable='create',
        name='spawn_robot',
        output='screen',
        parameters=[{
            'name': 'robot',
            'topic': 'robot_description',
            'x': 1.3,
            'y': 1.3,
            'z': 0.05,
            'Y': -1.5708,
        }],
    )

    # ROS2 <-> Gazebo 桥接（YAML 配置，含 frame_id 覆盖）
    # 注意：Jazzy 的 YAML 配置桥接可执行文件是 bridge_node，不是 ros_gz_bridge。
    bridge = Node(
        package='ros_gz_bridge',
        executable='bridge_node',
        name='ros_gz_bridge',
        output='screen',
        parameters=[{
            'config_file': os.path.join(pkg, 'config', 'bridge.yaml'),
            'use_sim_time': use_sim_time,
        }],
    )

    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time', default_value='true'),
        SetEnvironmentVariable('GZ_SIM_SYSTEM_PLUGIN_PATH', plugin_lib),
        gz_sim,
        robot_state_publisher,
        spawn,
        bridge,
    ])
