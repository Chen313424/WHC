#!/usr/bin/env python3
"""Gazebo 真值自动标注节点 —— 零人工标注生成 YOLO 数据集。

原理
----
世界是静态的，每个目标的 3D 包围盒已知（``config/world_objects.yaml``，
由 ``tools/sdf_to_objects.py`` 从 world SDF 生成）。相机又是标准针孔模型，
于是：

    1. 相机在世界系下的位姿
       = world->odom(spawn 位姿) ∘ odom->base_footprint(/odom)
         ∘ base_footprint->base_link(0,0,+wheel_radius) ∘ base_link->camera_link
    2. 目标 8 个角点世界坐标 = model_pose ∘ (center ± size/2)
    3. 投影到像素 -> 取包围盒 -> 写成 YOLO 归一化标注

红绿灯的**颜色类别**同样不需要人工判断：插件的时序是
``绿15s -> 黄3s -> 红10s``，``phase = fmod(simTime, 28)``，
所以直接用图像时间戳算出真值状态即可（见 ``traffic_rules``）。

用法（在 VM 里，仿真运行中）
----------------------------
    # 终端 A
    ros2 launch smart_community_sim smart_community.launch.py
    # 终端 B：一边用键盘开车一边采数据（车走到哪就采到哪）
    ros2 run teleop_twist_keyboard teleop_twist_keyboard
    # 终端 C
    ros2 run smart_community_perception autolabel_capture \
        --ros-args -p output_dir:=$HOME/whc_dataset

采集完成后用 ``tools/split_dataset.py`` 划分 train/val。
"""
from __future__ import annotations

import os

import numpy as np
import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import String

from .geometry import (
    aabb_to_bbox,
    camera_center_in_world,
    intrinsics_from_hfov,
    odom_pose_to_pose6,
    yolo_line,
)
from .traffic_rules import (
    CLASS_NAMES,
    CLS_PERSON_COMMUNITY,
    CLS_PERSON_NONCOMMUNITY,
    CLS_PLATE,
    STATE_TO_TL_CLASS,
    traffic_light_state_from_sim_time,
)

CLASS_TRAFFIC_LIGHT = "traffic_light"


class AutoLabelCapture(Node):
    def __init__(self) -> None:
        super().__init__("autolabel_capture")

        share = get_package_share_directory("smart_community_perception")
        default_objects = os.path.join(share, "config", "world_objects.yaml")

        # ---------------- 参数 ----------------
        self.declare_parameter("objects_file", default_objects)
        self.declare_parameter("output_dir", os.path.expanduser("~/whc_dataset"))
        self.declare_parameter("image_topic", "/camera/image_raw")
        self.declare_parameter("camera_info_topic", "/camera/camera_info")
        self.declare_parameter("odom_topic", "/odom")

        # spawn 位姿，必须与 launch 里 ros_gz_sim create 的 x/y/z/Y 一致
        # ★ 世界系 -> odom 系：必须与本车在 world 里的出生位姿一致，
        #   否则投影出来的框会整体偏移/方向反了。
        #   2026-10 场地重建后 spawn 是 (1.3, 1.3, 0.05) 朝 -Y（yaw = -1.5708）；
        #   旧值 [32, 14, ..., 1.5708] 是 88x44m 城市场景的，会导致标注全错。
        self.declare_parameter("world_to_odom", [1.3, 1.3, 0.05, 0.0, 0.0, -1.5708])
        # robot.xacro: base_footprint -> base_link = (0, 0, wheel_radius)
        self.declare_parameter("base_footprint_to_base_link_z", 0.05)
        # robot.xacro: base_link -> camera_link = (base_length/2, 0, base_height/2+0.05)
        self.declare_parameter("base_link_to_camera", [0.15, 0.0, 0.10, 0.0, 0.0, 0.0])

        self.declare_parameter("image_width", 640)
        self.declare_parameter("image_height", 480)
        # ★ 必须与 robot.xacro 里相机的 <horizontal_fov> 逐字一致：
        #   场景重建后 xacro 是 1.745329（100°），而这里原来写着 1.0471975（60°）。
        #   内参错 1.7 倍会让 3D->2D 投影整体偏差，自动标注的框位置就不对了。
        self.declare_parameter("hfov", 1.745329)

        self.declare_parameter("sample_period", 0.5)   # 仿真秒；同一时刻最多采一帧
        self.declare_parameter("min_visible", 0.30)   # 框至少 30% 在画面内才标注
        self.declare_parameter("min_box_px", 10.0)    # 太小的目标丢掉
        self.declare_parameter("keep_empty_frames", True)  # 保留纯背景帧做负样本
        self.declare_parameter("jpeg_quality", 92)
        self.declare_parameter("subdir", "all")       # images/<subdir>, labels/<subdir>

        # 红绿灯时序，必须与 world SDF 的插件参数一致
        self.declare_parameter("green_time", 15.0)
        # ★ 必须与 world SDF 里 TrafficLightSystem 插件的参数逐字一致（新场景是 15/5/10），
        #   否则自动标注在黄灯那一段会把真值标成红/绿，训练出来的颜色判断是错的。
        self.declare_parameter("yellow_time", 5.0)
        self.declare_parameter("red_time", 10.0)

        gp = self.get_parameter
        self.objects_file = gp("objects_file").value
        self.output_dir = os.path.expanduser(gp("output_dir").value)
        self.world_to_odom = list(gp("world_to_odom").value)
        self.z_footprint_to_base = float(gp("base_footprint_to_base_link_z").value)
        self.base_link_to_camera = list(gp("base_link_to_camera").value)
        self.image_width = int(gp("image_width").value)
        self.image_height = int(gp("image_height").value)
        self.hfov = float(gp("hfov").value)
        self.sample_period = float(gp("sample_period").value)
        self.min_visible = float(gp("min_visible").value)
        self.min_box_px = float(gp("min_box_px").value)
        self.keep_empty = bool(gp("keep_empty_frames").value)
        self.jpeg_quality = int(gp("jpeg_quality").value)
        self.subdir = str(gp("subdir").value)
        self.green_time = float(gp("green_time").value)
        self.yellow_time = float(gp("yellow_time").value)
        self.red_time = float(gp("red_time").value)

        # ---------------- 目标清单 ----------------
        with open(self.objects_file, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        self.objects = data.get("objects", [])
        if not self.objects:
            raise RuntimeError(f"{self.objects_file} 里没有 objects")

        # ---------------- 输出目录 ----------------
        self.images_dir = os.path.join(self.output_dir, "images", self.subdir)
        self.labels_dir = os.path.join(self.output_dir, "labels", self.subdir)
        os.makedirs(self.images_dir, exist_ok=True)
        os.makedirs(self.labels_dir, exist_ok=True)

        # ---------------- 运行时状态 ----------------
        self.K = intrinsics_from_hfov(self.image_width, self.image_height, self.hfov)
        self.got_camera_info = False
        self.odom_pose6: list[float] | None = None

        self._bridge = None
        self._cv2 = None
        try:
            import cv2  # type: ignore
            from cv_bridge import CvBridge  # type: ignore

            self._cv2 = cv2
            self._bridge = CvBridge()
        except Exception as exc:  # pragma: no cover - 环境相关
            raise RuntimeError(
                "需要 cv_bridge 与 OpenCV 才能保存图像：\n"
                "  sudo apt install ros-jazzy-cv-bridge python3-opencv\n"
                f"原始错误: {exc}"
            ) from exc

        self.last_sample_time = -1e9
        self.frame_index = 0
        self.saved = 0
        self.skipped_empty = 0

        # ---------------- 订阅 ----------------
        self.create_subscription(Image, gp("image_topic").value, self.on_image, qos_profile_sensor_data)
        self.create_subscription(
            CameraInfo, gp("camera_info_topic").value, self.on_camera_info, qos_profile_sensor_data
        )
        self.create_subscription(Odometry, gp("odom_topic").value, self.on_odom, 50)
        self.status_pub = self.create_publisher(String, "/perception/autolabel_status", 10)

        self.get_logger().info(
            f"自动标注已就绪 | 目标 {len(self.objects)} 个 | 输出 {self.output_dir} | "
            f"类别 {CLASS_NAMES}"
        )

    # ------------------------------------------------------------ 回调

    def on_camera_info(self, msg: CameraInfo) -> None:
        if self.got_camera_info:
            return
        k = list(msg.k)
        if len(k) >= 6 and k[0] > 1.0 and k[4] > 1.0:
            self.K = (k[0], k[4], k[2], k[5])
            self.got_camera_info = True
            self.get_logger().info(
                f"使用 camera_info 内参: fx={k[0]:.2f} fy={k[4]:.2f} cx={k[2]:.2f} cy={k[5]:.2f}"
            )

    def on_odom(self, msg: Odometry) -> None:
        self.odom_pose6 = odom_pose_to_pose6(msg.pose.pose)

    def on_image(self, msg: Image) -> None:
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if stamp <= 0.0:
            stamp = self.get_clock().now().nanoseconds * 1e-9

        if stamp - self.last_sample_time < self.sample_period:
            return
        if self.odom_pose6 is None:
            self.get_logger().warn(
                "还没收到 /odom，无法定位相机；确认 bridge 已启动", once=True
            )
            return
        self.last_sample_time = stamp

        world_to_cam = self.build_world_to_cam()
        if world_to_cam is None:
            return

        try:
            cv_img = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception as exc:  # pragma: no cover
            self.get_logger().error(f"图像转换失败: {exc}")
            return

        h, w = cv_img.shape[:2]
        if (w, h) != (self.image_width, self.image_height):
            # 内参按实际分辨率缩放，避免配置不一致导致标注漂移
            sx = w / float(self.image_width)
            sy = h / float(self.image_height)
            fx, fy, cx, cy = self.K
            K = (fx * sx, fy * sy, cx * sx, cy * sy)
            width, height = w, h
        else:
            K = self.K
            width, height = w, h

        state = traffic_light_state_from_sim_time(
            stamp, self.green_time, self.yellow_time, self.red_time
        )

        lines: list[str] = []
        cam_pos = camera_center_in_world(world_to_cam)

        for obj in self.objects:
            cls_name = obj["class"]
            if cls_name == CLASS_TRAFFIC_LIGHT:
                cls_id = STATE_TO_TL_CLASS[state]
            elif cls_name == "person_community":
                cls_id = CLS_PERSON_COMMUNITY
            elif cls_name == "person_noncommunity":
                cls_id = CLS_PERSON_NONCOMMUNITY
            elif cls_name == "license_plate":
                cls_id = CLS_PLATE
            else:
                continue

            model_pose = obj["model_pose"]
            center = np.array(obj["center"], dtype=float)
            size = np.array(obj["size"], dtype=float)

            # 粗筛：距离相机太远（>60m）直接跳过
            world_center = (
                np.array(model_pose[:3])
                + _rot_apply(model_pose[3:], center)
            )
            if float(np.linalg.norm(world_center - cam_pos)) > 60.0:
                continue

            bbox, depth = aabb_to_bbox(
                world_to_cam, model_pose, center, size, K, width, height, self.min_visible
            )
            if bbox is None:
                continue
            if (bbox[2] - bbox[0]) < self.min_box_px or (bbox[3] - bbox[1]) < self.min_box_px:
                continue
            lines.append(yolo_line(cls_id, bbox, width, height))

        self.frame_index += 1
        if not lines and not self.keep_empty:
            self.skipped_empty += 1
            return

        stem = f"{self.frame_index:06d}_{stamp:010.3f}"
        img_path = os.path.join(self.images_dir, stem + ".jpg")
        lbl_path = os.path.join(self.labels_dir, stem + ".txt")

        ok = self._cv2.imwrite(
            img_path, cv_img, [int(self._cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        )
        if not ok:
            self.get_logger().error(f"写图失败: {img_path}")
            return
        with open(lbl_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))

        self.saved += 1
        if self.saved % 20 == 0:
            self.get_logger().info(
                f"已保存 {self.saved} 帧 (当前状态={state}, 本帧目标={len(lines)})"
            )
        self.status_pub.publish(
            String(
                data=f'{{"saved": {self.saved}, "state": "{state}", '
                f'"objects_in_frame": {len(lines)}, "sim_time": {stamp:.3f}}}'
            )
        )

    # ------------------------------------------------------------ 内部

    def build_world_to_cam(self):
        """world -> camera_link 的 4x4 变换（直接复用几何库，避免两处实现分叉）。"""
        from .geometry import world_to_camera

        if self.odom_pose6 is None:
            return None
        try:
            return world_to_camera(
                self.world_to_odom,
                self.odom_pose6,
                self.z_footprint_to_base,
                self.base_link_to_camera,
            )
        except Exception as exc:  # pragma: no cover
            self.get_logger().error(f"位姿链构造失败: {exc}")
            return None


def _rot_apply(rpy, v):
    """局部向量 v 用 rpy 旋转到父系（只旋转不平移）。"""
    from .geometry import rpy_to_matrix

    return rpy_to_matrix(rpy[0], rpy[1], rpy[2]) @ np.asarray(v, dtype=float)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = AutoLabelCapture()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            node.get_logger().info(
                f"采集结束：保存 {node.saved} 帧，跳过空帧 {node.skipped_empty}，"
                f"输出目录 {node.output_dir}"
            )
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
