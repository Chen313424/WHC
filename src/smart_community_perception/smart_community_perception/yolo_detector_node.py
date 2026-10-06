#!/usr/bin/env python3
"""YOLO 推理节点：/camera/image_raw -> 结构化检测结果。

输出
----
* ``/perception/detections``      : std_msgs/String，JSON（主链路，零额外依赖）
* ``/perception/detections_vision``: vision_msgs/Detection2DArray（若该包可用）
* ``/perception/debug_image``      : sensor_msgs/Image，画框图（默认开，便于调试）

巡检接口（导航组 community_patrol 的契约，见本文件「巡检接口」一节）
------------------------------------------------------------------
* ``/traffic_light/state`` : std_msgs/String，大写 ``RED``/``YELLOW``/``GREEN``/``UNKNOWN``
* ``/patrol/capture``      : 订阅巡检的识别请求（JSON）
* ``/detection/result``    : 回识别结果（JSON，巡检收到任意一条即继续）

为什么不给巡检单开一个节点：本机是 4GB 内存的虚拟机，再订阅一路
``/camera/image_raw`` 会额外增加约 4.6 MB/s 的 DDS 流量。本节点本来就已经
持有最新图像帧与最新检测结果，直接复用最省。

设计取舍
--------
* 类别 id 以 **类别名** 为准做一次映射：训练时 ``data.yaml`` 的顺序
  与 ``traffic_rules.CLASS_NAMES`` 一致，但万一模型换顺序，这里也不会错位。
* 推理在后端线程里以固定频率抓最新帧，避免图像回调堆积导致延迟累积
  （Gazebo 相机 30Hz，CPU 推理往往跟不上）。
"""
from __future__ import annotations

import json
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

from .messages import (
    TASK_PLATE_OCR,
    UNKNOWN_LIGHT_STATE,
    best_plate_detection,
    build_result_payload,
    empty_detections_json,
    detections_to_json,
    traffic_light_state_text,
)
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

        # ---- 巡检接口（community_patrol 的契约，见文件末尾「巡检接口」一节）----
        self.declare_parameter("publish_light_state", True)
        self.declare_parameter("light_state_topic", "/traffic_light/state")
        self.declare_parameter("light_state_rate", 8.0)     # Hz；巡检只认 GREEN
        self.declare_parameter("capture_topic", "/patrol/capture")
        self.declare_parameter("result_topic", "/detection/result")
        self.declare_parameter("result_min_score", 0.35)    # 计数/选车牌框的最小置信度
        self.declare_parameter("plate_textures_dir", "")    # 留空 = 自动找 sim 的 textures

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

        self.publish_light_state = bool(gp("publish_light_state").value)
        self.light_state_rate = float(gp("light_state_rate").value)
        self.result_min_score = float(gp("result_min_score").value)
        self.plate_textures_dir = str(gp("plate_textures_dir").value)

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
        # 巡检接口要复用的「最新一帧 + 最新检测结果」（推理线程写、回调线程读）
        self._latest_frame: np.ndarray | None = None
        self._latest_dets: list[Detection] | None = None
        self._latest_frame_stamp: float | None = None
        self._light_state = UNKNOWN_LIGHT_STATE
        self._ocr = None            # PlateOCR 实例（只构造一次）
        self._ocr_error = ""
        self._ocr_tried = False

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

        self.pub_light_state = (
            self.create_publisher(String, gp("light_state_topic").value, 10)
            if self.publish_light_state
            else None
        )
        self.pub_result = self.create_publisher(String, gp("result_topic").value, 10)
        self.create_subscription(
            String, gp("capture_topic").value, self.on_capture, 10
        )

        self.create_subscription(
            Image, gp("image_topic").value, self.on_image, qos_profile_sensor_data
        )

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._infer_loop, daemon=True)
        self._thread.start()

        # 红绿灯状态用**定时器**发，而不是「每帧发一次」：虚拟机 CPU 吃满时
        # 推理会抖动，而巡检在停止线前等 GREEN 的整段时间里都需要稳定收到状态。
        if self.pub_light_state is not None:
            self.create_timer(
                1.0 / max(self.light_state_rate, 1.0), self.on_light_state_timer
            )

        self.get_logger().info(
            f"推理线程已启动 (imgsz={self.imgsz}, conf={self.conf}, device="
            f"{self.device or 'auto'})"
        )
        self.get_logger().info(
            f"巡检接口: 灯态={'开 ' + str(gp('light_state_topic').value) if self.publish_light_state else '关'} "
            f"@ {self.light_state_rate:g}Hz | 请求 {gp('capture_topic').value} "
            f"-> 结果 {gp('result_topic').value} (min_score={self.result_min_score:g})"
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

            # 巡检接口复用这一帧：动作只在回调里做，这里只更新「最新快照」。
            # frame 是 _to_numpy 出来的副本（不指向 DDS 缓冲），
            # 所以出了锁之后回调线程读它也是安全的。
            state = traffic_light_state_text(dets)
            with self._lock:
                self._latest_frame = frame
                self._latest_dets = dets
                self._latest_frame_stamp = stamp
                self._light_state = state

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

    # ============================================================ 巡检接口
    # 对接导航组 community_patrol/patrol_node.py，契约逐字如下：
    #
    #   1) 灯态：本节点持续发布 /traffic_light/state（std_msgs/String）。
    #      巡检拿到后 .strip().upper()，**只有等于 'GREEN' 才放行**，
    #      否则停在停止线前继续等（等不到会打「等待绿灯超时」）。
    #      -> 大写由 messages.traffic_light_state_text 保证，
    #         没有红绿灯时发 'UNKNOWN'（绝不能不发）。
    #
    #   2) 识别：巡检发布 /patrol/capture（JSON），然后阻塞等
    #      /detection/result，最多 capture_wait_timeout 秒（默认 10s，演示会调 60s）。
    #      它把收到的 data 原样记录打印，**收到任意一条就算完成**。
    #      -> 所以这里无论算不算得出来都必须回一条：
    #         刚启动还没有图像/检测时回 {"ok": false, "reason": ...}，
    #         否则巡检每个 capture 站点都会空等到超时（实测就是这么超时的）。
    #
    # 结果 JSON 的组装全部在 messages.build_result_payload（纯函数、可离线测），
    # 本节点只负责：搬最新快照 -> 裁车牌框 -> 调 PlateOCR -> 发出去。
    # ============================================================

    def on_light_state_timer(self) -> None:
        """按 light_state_rate 把最新灯态发出去（大写）。"""
        with self._lock:
            state = self._light_state
        self.pub_light_state.publish(String(data=state))

    def on_capture(self, msg: String) -> None:
        """收到巡检的识别请求 -> 用最新一帧 + 最新检测结果立刻算一次并回结果。"""
        try:
            request = json.loads(msg.data)
        except Exception as exc:  # 非法 JSON 也必须回一条，不能沉默
            self.get_logger().error(f"/patrol/capture 收到非法 JSON: {exc}")
            self._publish_result(
                build_result_payload(
                    None, task="", reason=f"capture 请求不是合法 JSON: {exc}"
                )
            )
            return
        if not isinstance(request, dict):
            self.get_logger().error(f"/patrol/capture 期望 JSON 对象，收到 {type(request).__name__}")
            self._publish_result(
                build_result_payload(None, task="", reason="capture 请求不是 JSON 对象")
            )
            return

        try:
            payload = self._build_capture_result(request)
        except Exception as exc:  # 兜底：任何意外都要回一条，宁可回失败也不能让巡检空等
            self.get_logger().error(f"处理 /patrol/capture 异常: {exc}")
            payload = build_result_payload(
                None,
                task=str(request.get("task", "")),
                waypoint_id=request.get("waypoint_id", ""),
                reason=f"节点处理异常: {exc}",
            )
        self._publish_result(payload)

    def _build_capture_result(self, request: dict) -> dict:
        """把一次 capture 请求算成结果 payload（不含发布）。"""
        task = str(request.get("task", ""))
        with self._lock:
            dets = self._latest_dets
            frame = self._latest_frame
            frame_stamp = self._latest_frame_stamp

        common = {
            "task": task,
            "waypoint_id": request.get("waypoint_id", ""),
            "zone": request.get("zone", ""),
            "slot": request.get("slot", ""),
            "target": request.get("target", ""),
            "stamp": request.get("stamp", 0.0) or 0.0,
            "min_score": self.result_min_score,
            "frame_stamp": frame_stamp,
        }
        self.get_logger().info(
            f"[巡检] capture 请求 waypoint={common['waypoint_id']} task={task} "
            f"zone={common['zone']} slot={common['slot']} target={common['target']}"
        )

        if dets is None:
            # 刚启动/相机没出图：不能什么都不回（巡检会一直等到超时）
            return build_result_payload(
                None, reason="尚未收到图像或检测结果（推理还没跑完第一帧）", **common
            )

        if task != TASK_PLATE_OCR:
            return build_result_payload(dets, **common)

        # 车牌任务需要图像裁框，是这里唯一碰图像的分支
        plate_result, reason = self._recognize_plate(dets, frame)
        return build_result_payload(
            dets, plate_result=plate_result, reason=reason, **common
        )

    def _recognize_plate(self, dets: list[Detection], frame) -> tuple[object | None, str]:
        """裁出置信度最高的车牌框并做字符识别。返回 (PlateResult | None, reason)。"""
        box = best_plate_detection(dets, self.result_min_score)
        if box is None:
            return None, f"未检测到车牌（score >= {self.result_min_score:g}）"
        if frame is None:
            return None, "还没有可用的图像帧，无法裁剪车牌"

        ocr = self._get_ocr()
        if ocr is None:
            return None, self._ocr_error or "车牌识别模块不可用"
        if not bool(getattr(ocr, "ready", False)):
            return None, f"车牌模板库为空（贴图目录 {getattr(ocr, 'textures_dir', '?')}）"

        h, w = frame.shape[:2]
        x1 = max(0, min(int(box.x1), w - 1))
        x2 = max(x1 + 1, min(int(box.x2), w))
        y1 = max(0, min(int(box.y1), h - 1))
        y2 = max(y1 + 1, min(int(box.y2), h))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None, f"车牌框无效（{x1},{y1},{x2},{y2} 超出 {w}x{h} 画面）"

        try:
            return ocr.recognize(crop), ""
        except Exception as exc:  # 识别失败只影响这一个任务
            self.get_logger().error(f"车牌识别异常: {exc}")
            return None, f"车牌识别异常: {exc}"

    def _get_ocr(self):
        """PlateOCR 只构造一次（模板构建有开销），失败也只影响 plate_ocr 任务。"""
        if self._ocr is not None or self._ocr_tried:
            return self._ocr
        self._ocr_tried = True
        try:
            from .plate_ocr import PlateOCR  # 延迟导入：不装 Pillow 也能跑主链路

            textures = self._resolve_plate_textures_dir()
            self._ocr = PlateOCR(textures) if textures else PlateOCR()
            self.get_logger().info(
                f"PlateOCR 就绪: 字符 {len(self._ocr.known_characters)} 个, "
                f"贴图目录 {self._ocr.textures_dir}"
            )
        except Exception as exc:
            self._ocr = None
            self._ocr_error = f"车牌识别模块初始化失败: {exc}"
            self.get_logger().error(self._ocr_error)
        return self._ocr

    def _resolve_plate_textures_dir(self) -> str:
        """车牌贴图目录：参数优先；留空时按 ament 索引去 sim 包里找。

        plate_ocr 的默认路径是按**源码树**写的（``../../smart_community_sim/textures``），
        colcon install 之后那个相对位置就不存在了，所以这里补一条走 share 目录的解析；
        两条都找不到时返回空串，交回 PlateOCR 自己的默认路径。
        """
        requested = self.plate_textures_dir.strip()
        if requested:
            return os.path.expanduser(requested)
        try:
            candidate = os.path.join(
                get_package_share_directory("smart_community_sim"), "textures"
            )
            if os.path.isdir(candidate):
                return candidate
        except Exception:  # pragma: no cover - sim 包没装时走 PlateOCR 默认
            pass
        return ""

    def _publish_result(self, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        self.pub_result.publish(String(data=text))
        self.get_logger().info(f"[巡检] 结果 -> {text}")

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
