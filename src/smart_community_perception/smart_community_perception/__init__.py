"""智慧社区感知包：YOLO 检测 + 红绿灯规则控制。

模块划分
--------
* ``geometry``       : 纯几何/投影数学，无 ROS 依赖，可离线单测
* ``traffic_rules``  : 类别定义、时序真值、决策纯逻辑，无 ROS 依赖
* ``messages``       : 检测结果的 JSON 编解码（零额外消息依赖）
* ``autolabel_capture_node`` : 在 Gazebo 里用 3D 真值自动生成 YOLO 数据集
* ``yolo_detector_node``     : 推理节点
* ``traffic_controller_node``: 红灯停/绿灯行控制节点
"""

__version__ = "0.1.0"
