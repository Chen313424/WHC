#!/usr/bin/env python3
"""红绿灯规则控制节点：把检测结果变成 /cmd_vel，实现「红灯停、绿灯行」。

两种模式
--------
``teleop_gate``（默认，推荐）
    人照常遥控，但遥控话题接到 ``/cmd_vel_input``：
        ros2 run teleop_twist_keyboard teleop_twist_keyboard \\
            --ros-args -r cmd_vel:=/cmd_vel_input
    本节点把人的指令**按红绿灯规则门控**后再发到 ``/cmd_vel``。
    红灯且已接近路口 -> 输出零速；绿灯 -> 原样放行。
    好处：遥控手感完全保留，规则只在必要时介入。

``auto``
    不接遥控，节点自己以 ``cruise_speed`` 直行，遇到红灯自动停车、绿灯自动起步。
    适合做「一键演示」。

安全兜底
--------
* 超过 ``detection_timeout`` 没收到检测结果 -> 按 ``fail_safe``（默认 ``go``，
  避免因为相机/模型异常把车永久钉死在原地；要更保守可设 ``stop``）。
* 停车时线速度与角速度一起归零，避免「原地打转」。
"""
from __future__ import annotations

import json
import math
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo
from std_msgs.msg import String

from .geometry import intrinsics_from_hfov
from .messages import detections_from_json, summarize
from .traffic_rules import Action, Detection, decide, any_person_ahead, scale_twist


class TrafficController(Node):
    def __init__(self) -> None:
        super().__init__("traffic_controller")

        self.declare_parameter("mode", "teleop_gate")          # teleop_gate | auto
        self.declare_parameter("input_topic", "/cmd_vel_input")
        self.declare_parameter("output_topic", "/cmd_vel")
        self.declare_parameter("detections_topic", "/perception/detections")
        self.declare_parameter("camera_info_topic", "/camera/camera_info")
        self.declare_parameter("status_topic", "/perception/traffic_decision")

        self.declare_parameter("cruise_speed", 0.6)            # auto 模式直行速度 m/s
        self.declare_parameter("approach_speed", 0.25)         # 减速接近速度 m/s
        self.declare_parameter("stop_distance_m", 5.0)         # 红灯停车距离
        self.declare_parameter("slow_distance_m", 9.0)         # 红灯开始减速距离
        self.declare_parameter("roi_x_ratio", 0.6)             # 中央 ROI 宽度占比
        self.declare_parameter("min_score", 0.35)
        self.declare_parameter("yellow_policy", "stop_if_far")  # stop_if_far | always_stop

        self.declare_parameter("avoid_person", False)          # 前方有人偶时是否也停
        self.declare_parameter("person_danger_distance_m", 3.0)

        self.declare_parameter("fail_safe", "go")              # go | stop
        self.declare_parameter("detection_timeout", 0.7)

        self.declare_parameter("publish_rate", 20.0)
        self.declare_parameter("image_width", 640)
        self.declare_parameter("image_height", 480)
        self.declare_parameter("hfov", 1.0471975)

        gp = self.get_parameter
        self.mode = str(gp("mode").value)
        self.cruise_speed = float(gp("cruise_speed").value)
        self.approach_speed = float(gp("approach_speed").value)
        self.stop_distance_m = float(gp("stop_distance_m").value)
        self.slow_distance_m = float(gp("slow_distance_m").value)
        self.roi_x_ratio = float(gp("roi_x_ratio").value)
        self.min_score = float(gp("min_score").value)
        self.yellow_policy = str(gp("yellow_policy").value)
        self.avoid_person = bool(gp("avoid_person").value)
        self.person_danger = float(gp("person_danger_distance_m").value)
        self.fail_safe = str(gp("fail_safe").value)
        self.detection_timeout = float(gp("detection_timeout").value)
        publish_rate = float(gp("publish_rate").value)
        self.image_width = int(gp("image_width").value)
        self.image_height = int(gp("image_height").value)
        self.hfov = float(gp("hfov").value)

        if self.mode not in ("teleop_gate", "auto"):
            raise RuntimeError(f"未知 mode: {self.mode}（可选 teleop_gate / auto）")

        # 内参：优先用 camera_info，否则由 HFOV 推算
        self.K = intrinsics_from_hfov(self.image_width, self.image_height, self.hfov)
        self.got_camera_info = False

        self.detections: list[Detection] = []
        self.detections_stamp = -1e9
        self.input_vx = 0.0
        self.input_wz = 0.0
        self.last_input_time = 0.0

        self.pub = self.create_publisher(Twist, gp("output_topic").value, 10)
        self.pub_status = self.create_publisher(String, gp("status_topic").value, 10)

        self.create_subscription(
            String, gp("detections_topic").value, self.on_detections, 10
        )
        self.create_subscription(
            CameraInfo, gp("camera_info_topic").value, self.on_camera_info, qos_profile_sensor_data
        )
        if self.mode == "teleop_gate":
            self.create_subscription(
                Twist, gp("input_topic").value, self.on_input, 10
            )

        self.timer = self.create_timer(1.0 / max(publish_rate, 1.0), self.on_timer)
        self.get_logger().info(
            f"控制节点已启动 | mode={self.mode} | 停车距离={self.stop_distance_m}m | "
            f"黄灯策略={self.yellow_policy} | fail_safe={self.fail_safe}"
        )
        if self.mode == "teleop_gate":
            self.get_logger().info(
                "遥控请重映射到输入话题： ros2 run teleop_twist_keyboard "
                "teleop_twist_keyboard --ros-args -r cmd_vel:=/cmd_vel_input"
            )

    # ------------------------------------------------------------ 回调

    def on_camera_info(self, msg: CameraInfo) -> None:
        if self.got_camera_info:
            return
        k = list(msg.k)
        if len(k) >= 6 and k[0] > 1.0 and k[4] > 1.0:
            self.K = (k[0], k[4], k[2], k[5])
            self.got_camera_info = True
            self.get_logger().info(f"使用 camera_info: fy={k[4]:.2f}")

    def on_input(self, msg: Twist) -> None:
        self.input_vx = float(msg.linear.x)
        self.input_wz = float(msg.angular.z)
        self.last_input_time = self._now()

    def on_detections(self, msg: String) -> None:
        try:
            dets, payload = detections_from_json(msg.data)
        except Exception as exc:
            self.get_logger().warn(f"检测消息解析失败: {exc}", throttle_duration_sec=5.0)
            return
        self.detections = dets
        # 用消息里的时间戳（仿真时间）而不是墙钟，和 use_sim_time 保持一致
        self.detections_stamp = float(payload.get("stamp", self._now()))

    # ------------------------------------------------------------ 主循环

    def on_timer(self) -> None:
        now = self._now()
        fresh = (now - self.detections_stamp) <= self.detection_timeout

        if not fresh:
            if self.fail_safe == "stop":
                self._publish(0.0, 0.0, Action.STOP, "检测超时，保守停车", None, None)
                return
            # fail_safe == go：按期望速度直行
            vx, wz = self._desired_input(now)
            self._publish(vx, wz, Action.GO, "检测超时，按兜底策略直行", None, None)
            return

        decision = decide(
            self.detections,
            fy=self.K[1],
            image_width=self.image_width,
            stop_distance_m=self.stop_distance_m,
            slow_distance_m=self.slow_distance_m,
            roi_x_ratio=self.roi_x_ratio,
            min_score=self.min_score,
            yellow_policy=self.yellow_policy,
            cruise_speed=self.cruise_speed,
            approach_speed=self.approach_speed,
        )

        # 可选：前方有人偶也停
        if self.avoid_person and not decision.is_stop:
            dist = any_person_ahead(
                self.detections,
                image_width=self.image_width,
                fy=self.K[1],
                danger_distance_m=self.person_danger,
                roi_x_ratio=self.roi_x_ratio,
                min_score=self.min_score,
            )
            if dist is not None:
                self._publish(0.0, 0.0, Action.STOP, f"前方 {dist:.2f}m 有人偶，停车",
                              decision.state, decision.distance_m)
                return

        vx, wz = self._desired_input(now)
        vx, wz = scale_twist(vx, wz, decision.speed_scale)
        self._publish(vx, wz, decision.action, decision.reason, decision.state, decision.distance_m)

    # ------------------------------------------------------------ 内部

    def _desired_input(self, now: float) -> tuple[float, float]:
        """当前「上层期望速度」。auto 模式恒为直行；teleop 模式取遥最新指令。"""
        if self.mode == "auto":
            return self.cruise_speed, 0.0
        # 遥控有超时保护：500ms 没新指令就当松手
        if now - self.last_input_time > 0.5:
            return 0.0, 0.0
        return self.input_vx, self.input_wz

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _publish(self, vx, wz, action: Action, reason: str, state, dist) -> None:
        tw = Twist()
        tw.linear.x = float(vx)
        tw.angular.z = float(wz)
        self.pub.publish(tw)

        payload = {
            "action": action.value,
            "speed_scale": 0.0 if (vx == 0.0 and wz == 0.0) else 1.0,
            "reason": reason,
            "state": state,
            "distance_m": None if dist is None else round(float(dist), 3),
            "linear_x": round(float(vx), 4),
            "angular_z": round(float(wz), 4),
            "n_detections": len(self.detections),
            "detections": summarize(self.detections),
            "mode": self.mode,
        }
        self.pub_status.publish(String(data=json.dumps(payload, ensure_ascii=False)))

        if action is not Action.GO:
            self.get_logger().info(
                f"[{action.value}] {reason} | 目标: {payload['detections']} | "
                f"vx={vx:.2f} wz={wz:.2f}",
                throttle_duration_sec=1.0,
            )


def main(args=None) -> None:
    rclpy.init(args=args)
    node = TrafficController()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # 退出前把车停住，避免仿真里残留速度
        try:
            node.pub.publish(Twist())
        except Exception:
            pass
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
