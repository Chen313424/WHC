"""感知与红绿灯控制一键启动。

启动内容：
  1. yolo_detector      : /camera/image_raw -> /perception/detections
  2. traffic_controller : 检测 + 规则 -> /cmd_vel

用法::

    # 默认：遥控门控模式（先用 teleop 开车，红灯会自动拦住）
    ros2 launch smart_community_perception perception.launch.py

    # 自动直行演示模式
    ros2 launch smart_community_perception perception.launch.py mode:=auto

    # 指定自己训练的模型
    ros2 launch smart_community_perception perception.launch.py \\
        model_path:=/abs/path/to/best.pt

注意：仿真本身（Gazebo + 桥接）由 smart_community_sim 的 launch 负责，
本文件只起感知与控制，两者可以分别在不同终端启动，互不干扰。
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    pkg = get_package_share_directory("smart_community_perception")
    params_file = os.path.join(pkg, "config", "perception.yaml")

    mode = LaunchConfiguration("mode")
    model_path = LaunchConfiguration("model_path")
    use_sim_time = LaunchConfiguration("use_sim_time")

    detector = Node(
        package="smart_community_perception",
        executable="yolo_detector",
        name="yolo_detector",
        output="screen",
        parameters=[params_file, {"model_path": model_path, "use_sim_time": use_sim_time}],
    )

    controller = Node(
        package="smart_community_perception",
        executable="traffic_controller",
        name="traffic_controller",
        output="screen",
        parameters=[params_file, {"mode": mode, "use_sim_time": use_sim_time}],
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument("mode", default_value="teleop_gate",
                                  description="teleop_gate | auto"),
            DeclareLaunchArgument("model_path", default_value="",
                                  description="留空则用包内 models/whc_yolo.pt"),
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            detector,
            controller,
        ]
    )
