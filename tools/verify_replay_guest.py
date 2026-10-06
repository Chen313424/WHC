#!/usr/bin/env python3
"""把合成数据集里的图灌到 /camera/image_raw，验证感知链路端到端。

为什么需要它：本机 VM 的 Gazebo 传感器渲染走 EGL，而 VMSVGA 的 3D 通道是坏的
（vmwgfx "Failed to open channel"），相机话题出来的是**全黑图**，没法在仿真里
直接验证视觉识别。于是改成"喂图"：从合成数据集里按标注内容挑出指定场景的图
（红灯 / 绿灯 / 人群 / 车牌），发布到相机话题，看检测与决策是否正确。

这验证的是【图像 → 检测 → 红停绿行 → 接口契约】整条链；
唯一没被验证的是"Gazebo 渲染出图"这一步（那是虚拟机显卡限制，不是代码问题）。
"""
import json
import os
import sys
import time

import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String

DATA = "/media/sf_WHC/whc_synth"
CLASS_NAMES = {0: "traffic_light_red", 1: "traffic_light_yellow", 2: "traffic_light_green",
               3: "person_community", 4: "person_noncommunity", 5: "license_plate"}


def pick_images(limit_each=1):
    """按标注内容挑图：只含红灯 / 只含绿灯 / 含两类人偶 / 含车牌。"""
    img_dir = os.path.join(DATA, "images", "all")
    lbl_dir = os.path.join(DATA, "labels", "all")
    if not os.path.isdir(img_dir):
        raise SystemExit(f"找不到数据集 {img_dir}（共享文件夹是否挂上？）")

    want = {"red": [], "green": [], "person": [], "plate": []}
    for name in sorted(os.listdir(img_dir)):
        if not name.endswith(".jpg"):
            continue
        stem = os.path.splitext(name)[0]
        lp = os.path.join(lbl_dir, stem + ".txt")
        if not os.path.isfile(lp):
            continue
        with open(lp, encoding="utf-8") as fh:
            cls = [int(l.split()[0]) for l in fh if l.strip()]
        cnt = {c: cls.count(c) for c in set(cls)}
        tl = sum(cnt.get(c, 0) for c in (0, 1, 2))
        if cnt.get(0) == 1 and tl == 1 and len(cls) == 1:
            want["red"].append(stem)
        if cnt.get(2) == 1 and tl == 1 and len(cls) == 1:
            want["green"].append(stem)
        if cnt.get(3, 0) >= 2 and cnt.get(4, 0) >= 1:
            want["person"].append(stem)
        if cnt.get(5, 0) >= 1:
            want["plate"].append(stem)

    out = {}
    for k, v in want.items():
        if v:
            out[k] = os.path.join(img_dir, v[0] + ".jpg")
    return out


class Replayer(Node):
    def __init__(self, path):
        super().__init__("image_replayer")
        self.pub = self.create_publisher(Image, "/camera/image_raw", 10)
        img = cv2.imread(path)
        if img is None:
            raise SystemExit(f"读不到图 {path}")
        self.rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        self.msg = Image()
        self.msg.height, self.msg.width = self.rgb.shape[:2]
        self.msg.encoding = "rgb8"
        self.msg.is_bigendian = 0
        self.msg.step = self.rgb.shape[1] * 3
        self.msg.data = self.rgb.tobytes()
        self.create_timer(0.2, self.tick)      # 5 Hz
        self.get_logger().info(f"回放: {os.path.basename(path)}  {self.msg.width}x{self.msg.height}")

    def tick(self):
        self.msg.header.stamp = self.get_clock().now().to_msg()
        self.msg.header.frame_id = "camera_link"
        self.pub.publish(self.msg)


def main() -> int:
    paths = pick_images()
    print("挑到的场景图:")
    for k, v in paths.items():
        print(f"  {k:8s} -> {os.path.basename(v)}")
    need = [k for k in ("red", "green", "person", "plate") if k not in paths]
    if "red" not in paths or "green" not in paths:
        print(f"[X] 缺少场景图: {need}（数据集里没找到合适的单目标图）")
        return 1

    rclpy.init()
    node = Replayer(paths["red"])         # 先放红灯图
    dets = []
    node.create_subscription(String, "/perception/detections",
                             lambda m: dets.append(m.data), 10)
    decisions = []
    node.create_subscription(String, "/perception/traffic_decision",
                             lambda m: decisions.append(m.data), 10)
    import threading
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()

    def sample(seconds, label):
        dets.clear(); decisions.clear()
        t0 = time.time()
        while time.time() - t0 < seconds:
            time.sleep(0.2)
        seen = {}
        for raw in dets[-20:]:
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            for d in payload.get("detections", []):
                nm = CLASS_NAMES.get(d.get("cls_id"), str(d.get("cls_id")))
                seen[nm] = seen.get(nm, 0) + 1
        dec = decisions[-1] if decisions else "（无决策）"
        print(f"  [{label}] 检测到: {seen if seen else '无'}")
        print(f"           决策: {dec[:160]}")
        return seen, dec

    print("\n=== 场景 A：红灯 ===")
    sample(12, "red")
    print("\n=== 场景 B：绿灯 ===")
    node.rgb = cv2.imread(paths["green"])
    node.rgb = cv2.cvtColor(node.rgb, cv2.COLOR_BGR2RGB)
    node.msg.data = node.rgb.tobytes()
    sample(12, "green")

    for key, label in (("person", "人群计数"), ("plate", "车牌")):
        if key not in paths:
            continue
        print(f"\n=== 场景 C：{label} ===")
        img = cv2.imread(paths[key])
        node.rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        node.msg.data = node.rgb.tobytes()
        sample(12, label)

    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
