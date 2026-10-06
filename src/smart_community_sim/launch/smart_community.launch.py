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

    # 世界 SDF 里纹理用 model://smart_community_sim/... 引用，这里把包的
    # share 目录加入 GZ_SIM_RESOURCE_PATH，让 Gazebo 能解析到 textures/。
    # share 目录 = pkg 的上一级（install/smart_community_sim/share）。
    share_root = os.path.dirname(pkg)
    resource_path = share_root
    if os.environ.get('GZ_SIM_RESOURCE_PATH'):
        resource_path = share_root + os.pathsep + os.environ['GZ_SIM_RESOURCE_PATH']

    world_file = os.path.join(pkg, 'worlds', 'smart_community.sdf')
    xacro_file = os.path.join(pkg, 'robot', 'robot.xacro')

    robot_description = {'robot_description': xacro.process_file(xacro_file).toxml()}

    use_sim_time = LaunchConfiguration('use_sim_time', default='true')

    # GPU 渲染后端自动检测：WSLg 通过 /dev/dxg（DirectX）暴露显卡给 Mesa 的
    # d3d12 驱动，原生直通则通过 /dev/dri。两者都没有就退回 llvmpipe 软件渲染
    # （此时强制 d3d12 会因找不到 GPU 而段错误）。可用 gpu_driver:=xxx 覆盖。
    has_gpu = os.path.isdir('/dev/dxg') or (
        os.path.isdir('/dev/dri') and bool(os.listdir('/dev/dri')))
    default_gpu_driver = 'd3d12' if has_gpu else 'llvmpipe'
    gpu_driver = LaunchConfiguration('gpu_driver')

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
        DeclareLaunchArgument('gpu_driver', default_value=default_gpu_driver),
        SetEnvironmentVariable('GZ_SIM_SYSTEM_PLUGIN_PATH', plugin_lib),
        SetEnvironmentVariable('GZ_SIM_RESOURCE_PATH', resource_path),
        # 渲染后端：WSLg 有 /dev/dxg 时走 d3d12 硬件渲染，否则退回 llvmpipe。
        SetEnvironmentVariable('GALLIUM_DRIVER', gpu_driver),
        SetEnvironmentVariable('LIBGL_ALWAYS_SOFTWARE', '1' if not has_gpu else '0'),
        gz_sim,
        robot_state_publisher,
        spawn,
        bridge,
    ])
