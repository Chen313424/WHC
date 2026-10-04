#!/usr/bin/env python3
"""YOLO 推理节点：/camera/image_raw -> 结构化检测结果。

输出
----
* ``/perception/detections``      : std_msgs/String，JSON（主链路，零额外依赖）
* ``/perception/detections_vision``: vision_msgs/Detection2DArray（若该包可用）
* ``/perception/debug_image``      : sensor_msgs/Image，画框图（默认开，便于调试）

设计取舍
--------
* 类别 id 以 **类别名** 为准做一次映射：训练时 ``data.yaml`` 的顺序
  与 ``traffic_rules.CLASS_NAMES`` 一致，但万一模型换顺序，这里也不会错位。
* 推理在后端线程里以固定频率抓最新帧，避免图像回调堆积导致延迟累积
  （Gazebo 相机 30Hz，CPU 推理往往跟不上）。
"""
from __future__ import annotations

import os
import threading
import time

import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String

from .messages import empty_detections_json, detections_to_json
from .traffic_rules import CLASS_NAMES, Detection

try:  # 可选依赖
    from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose

    HAS_VISION_MSGS = True
except Exception:  # pragma: no cover
    HAS_VISION_MSGS = False


class YoloDetector(Node):
    def __init__(self) -> None:
        super().__init__("yolo_detector")

        share = get_package_share_directory("smart_community_perception")
        default_model = os.path.join(share, "models", "whc_yolo.pt")

        self.declare_parameter("model_path", default_model)
        self.declare_parameter("image_topic", "/camera/image_raw")
        self.declare_parameter("detections_topic", "/perception/detections")
        self.declare_parameter("debug_image_topic", "/perception/debug_image")
        self.declare_parameter("conf", 0.35)
        self.declare_parameter("iou", 0.5)
        self.declare_parameter("imgsz", 640)
        self.declare_parameter("max_det", 50)
        self.declare_parameter("device", "")          # "" = 自动；'cpu' / '0'
        self.declare_parameter("half", False)         # GPU 上可开 FP16
        self.declare_parameter("publish_debug_image", True)
        self.declare_parameter("publish_vision_msgs", True)
        self.declare_parameter("min_interval", 0.0)   # >0 时限速（秒）

        gp = self.get_parameter
        requested = str(gp("model_path").value).strip()
        # 参数留空时回退到包内默认路径（参数文件里通常是空串）
        self.model_path = os.path.expanduser(requested) if requested else default_model
        self.conf = float(gp("conf").value)
        self.iou = float(gp("iou").value)
        self.imgsz = int(gp("imgsz").value)
        self.max_det = int(gp("max_det").value)
        self.device = str(gp("device").value)
        self.half = bool(gp("half").value)
        self.publish_debug = bool(gp("publish_debug_image").value)
        self.publish_vision = bool(gp("publish_vision_msgs").value) and HAS_VISION_MSGS
        self.min_interval = float(gp("min_interval").value)

        if not os.path.isfile(self.model_path):
            raise RuntimeError(
                f"找不到模型文件: {self.model_path}\n"
                "请先训练（tools/train_yolo.py），或用 -p model_path:=<你的 .pt> 指定。"
            )

        from ultralytics import YOLO  # 延迟导入，便于 --help 类场景

        self.get_logger().info(f"加载模型: {self.model_path}")
        self.model = YOLO(self.model_path)
        raw_names = getattr(self.model, "names", {}) or {}
        if isinstance(raw_names, dict):
            self.model_names = {int(k): str(v) for k, v in raw_names.items()}
        else:
            self.model_names = {i: str(v) for i, v in enumerate(raw_names)}
        self.name_to_id = {n: i for i, n in enumerate(CLASS_NAMES)}
        self.get_logger().info(f"模型类别: {self.model_names}")
        self.get_logger().info(f"本项目类别: {CLASS_NAMES}")

        self._cv2 = None
        if self.publish_debug:
            try:
                import cv2  # type: ignore

                self._cv2 = cv2
            except Exception:
                self.get_logger().warn("未装 OpenCV，关闭调试图输出")
                self.publish_debug = False

        self._lock = threading.Lock()
        self._latest: Image | None = None
        self._latest_stamp = 0.0
        self._new_frame = threading.Event()

        self.pub_json = self.create_publisher(String, gp("detections_topic").value, 10)
        self.pub_debug = (
            self.create_publisher(Image, gp("debug_image_topic").value, 2)
            if self.publish_debug
            else None
        )
        self.pub_vision = (
            self.create_publisher(Detection2DArray, "/perception/detections_vision", 10)
            if self.publish_vision
            else None
        )

        self.create_subscription(
            Image, gp("image_topic").value, self.on_image, qos_profile_sensor_data
        )

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._infer_loop, daemon=True)
        self._thread.start()

        self.get_logger().info(
            f"推理线程已启动 (imgsz={self.imgsz}, conf={self.conf}, device="
            f"{self.device or 'auto'})"
        )

    # ------------------------------------------------------------ 回调

    def on_image(self, msg: Image) -> None:
        with self._lock:
            self._latest = msg
            self._latest_stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self._new_frame.set()

    # ------------------------------------------------------------ 推理循环

    def _infer_loop(self) -> None:
        last_run = 0.0
        while not self._stop.is_set() and rclpy.ok():
            if not self._new_frame.wait(timeout=0.2):
                continue
            self._new_frame.clear()

            if self.min_interval > 0.0:
                now = time.monotonic()
                if now - last_run < self.min_interval:
                    continue
                last_run = now

            with self._lock:
                msg = self._latest
                stamp = self._latest_stamp
            if msg is None:
                continue

            try:
                frame = self._to_numpy(msg)
            except Exception as exc:  # pragma: no cover
                self.get_logger().error(f"图像解码失败: {exc}")
                continue

            h, w = frame.shape[:2]
            t0 = time.perf_counter()
            try:
                results = self.model.predict(
                    frame,
                    conf=self.conf,
                    iou=self.iou,
                    imgsz=self.imgsz,
                    max_det=self.max_det,
                    device=self.device or None,
                    half=self.half,
                    verbose=False,
                )
            except Exception as exc:  # pragma: no cover
                self.get_logger().error(f"推理失败: {exc}")
                continue
            infer_ms = (time.perf_counter() - t0) * 1000.0

            dets = self._results_to_detections(results)
            self.pub_json.publish(
                String(
                    data=detections_to_json(
                        dets, image_width=w, image_height=h, stamp=stamp, inference_ms=infer_ms
                    )
                )
            )
            if self.pub_vision is not None:
                self.pub_vision.publish(self._to_vision_msgs(dets, msg))

            if self.pub_debug is not None:
                self._publish_debug(frame, dets, msg, infer_ms)

    # ------------------------------------------------------------ 转换

    def _to_numpy(self, msg: Image) -> np.ndarray:
        """sensor_msgs/Image -> BGR ndarray（只支持常见 8UC3/rgb8/bgr8/mono8）。"""
        enc = (msg.encoding or "").lower()
        if enc in ("rgb8", "bgr8", "8uc3"):
            arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 3)
            if enc == "rgb8":
                return arr[:, :, ::-1].copy()
            return arr.copy()
        if enc in ("mono8", "8uc1"):
            arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width)
            return np.stack([arr] * 3, axis=-1)
        if enc in ("rgba8", "bgra8", "8uc4"):
            arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 4)
            if enc == "rgba8":
                return arr[:, :, [2, 1, 0]].copy()
            return arr[:, :, :3].copy()
        raise ValueError(f"不支持的图像编码: {msg.encoding}")

    def _map_class(self, model_cls_id: int) -> int:
        """模型类别 id -> 本项目类别 id（按名字对齐）。"""
        name = self.model_names.get(int(model_cls_id))
        if name is not None and name in self.name_to_id:
            return self.name_to_id[name]
        return int(model_cls_id)

    def _results_to_detections(self, results) -> list[Detection]:
        dets: list[Detection] = []
        if not results:
            return dets
        r = results[0]
        boxes = getattr(r, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return dets
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        for (x1, y1, x2, y2), score, cid in zip(xyxy, confs, clss):
            dets.append(
                Detection(
                    cls_id=self._map_class(cid),
                    score=float(score),
                    x1=float(x1),
                    y1=float(y1),
                    x2=float(x2),
                    y2=float(y2),
                )
            )
        return dets

    def _to_vision_msgs(self, dets: list[Detection], msg: Image):
        arr = Detection2DArray()
        arr.header = msg.header
        for d in dets:
            det = Detection2D()
            det.header = msg.header
            det.bbox.center.position.x = (d.x1 + d.x2) / 2.0
            det.bbox.center.position.y = (d.y1 + d.y2) / 2.0
            det.bbox.size_x = d.width_px
            det.bbox.size_y = d.height_px
            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = d.class_name
            hyp.hypothesis.score = d.score
            det.results.append(hyp)
            arr.detections.append(det)
        return arr

    def _publish_debug(self, frame, dets, msg, infer_ms: float) -> None:
        cv2 = self._cv2
        vis = frame.copy()
        colors = {
            "traffic_light_red": (0, 0, 255),
            "traffic_light_yellow": (0, 200, 255),
            "traffic_light_green": (0, 200, 0),
            "person": (255, 128, 0),
            "license_plate": (255, 0, 255),
        }
        for d in dets:
            color = colors.get(d.class_name, (200, 200, 200))
            p1 = (int(d.x1), int(d.y1))
            p2 = (int(d.x2), int(d.y2))
            cv2.rectangle(vis, p1, p2, color, 2)
            label = f"{d.class_name} {d.score:.2f}"
            cv2.putText(vis, label, (p1[0], max(12, p1[1] - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        cv2.putText(vis, f"{infer_ms:.1f} ms", (8, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

        out = Image()
        out.header = msg.header
        out.height, out.width = vis.shape[:2]
        out.encoding = "bgr8"
        out.is_bigendian = 0
        out.step = vis.shape[1] * 3
        out.data = vis.tobytes()
        self.pub_debug.publish(out)

    # ------------------------------------------------------------ 生命周期

    def destroy_node(self) -> bool:
        self._stop.set()
        self._new_frame.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = YoloDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
