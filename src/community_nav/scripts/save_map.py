#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
存图工具 —— 直接订阅 /map 并写出 PGM + YAML
================================================================================
用法：

    # 1. 先把仿真和 SLAM 跑起来
    ros2 launch community_nav sim.launch.py
    ros2 launch community_nav mapping.launch.py

    # 2. 另开终端存图
    ros2 run community_nav save_map.py -f ~/smart_community_ws/src/community_nav/map/community_map

    # 或者直接用源码跑
    python3 src/community_nav/scripts/save_map.py -f <路径前缀>

【为什么不用 nav2_map_server 的 map_saver_cli】
    实测在 Jazzy + slam_toolbox 组合下，`map_saver_cli` 会报
        [ERROR] [map_saver]: Failed to spin map subscription
    即使显式传 `-p map_subscribe_transient_local:=false` 仍然失败。

    原因是 map_saver 的订阅 QoS 和 slam_toolbox 发布 /map 的 QoS 对不上，
    而它的重试窗口很短（几秒），错过就报错退出。
    这个工具的订阅用【默认 QoS】（VOLATILE），对两种发布方式都兼容，
    并且会一直等到收到地图为止，不会因为超时误报失败。

    【教训】诊断/工具类程序应该用最宽松的 QoS —— 它的职责是"看到真相"，
    不是"按理想配置去挑剔"。同一条经验在本项目里踩过两次
    （另一次是 docs/scripts/diagnose_slam.py 的 /map 订阅）。
================================================================================
"""

import argparse
import os
import sys

import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node

TIMEOUT_DEFAULT = 30.0


class MapSaver(Node):

    def __init__(self):
        super().__init__('community_map_saver')
        self.msg = None
        # ★ 用默认 QoS（VOLATILE）：既能收 VOLATILE 也能收 TRANSIENT_LOCAL 的发布者。
        #   写成 TRANSIENT_LOCAL 的话，遇到 VOLATILE 发布者会一条都收不到。
        self.create_subscription(OccupancyGrid, '/map', self._on_map, 10)

    def _on_map(self, msg):
        # 记录数据最"满"的那一帧（已探索格子最多）
        known = sum(1 for v in msg.data if v >= 0)
        if self.msg is None:
            self.msg = (msg, known)
        elif known > self.msg[1]:
            self.msg = (msg, known)


def write_pgm(path, msg):
    """写 PGM（P5 二进制灰度）。ROS 栅格约定：
         -1  未知   -> 205
          0  空闲   -> 254
        100  占据   -> 0
    """
    w, h = msg.info.width, msg.info.height
    # OccupancyGrid 的数据是【行优先、从下到上】，PGM 是从上到下，所以要翻转行序
    px = bytearray(w * h)
    for y in range(h):
        src_row = (h - 1 - y) * w
        dst_row = y * w
        for x in range(w):
            v = msg.data[src_row + x]
            if v < 0:
                px[dst_row + x] = 205
            elif v >= 65:
                px[dst_row + x] = 0
            else:
                px[dst_row + x] = 254

    with open(path, 'wb') as f:
        f.write(b'P5\n')
        # 注意：PGM 的注释必须是【纯 ASCII】—— bytes 字面量不能含中文
        f.write(b'# smart community SLAM map\n')
        f.write(f'{w} {h}\n255\n'.encode())
        f.write(bytes(px))


def write_yaml(path, pgm_name, msg):
    o = msg.info.origin.position
    res = msg.info.resolution
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'image: {pgm_name}\n')
        f.write('mode: trinary\n')
        # 分辨率取整到 4 位：Nav2 传来的是浮点数，
        # 直接写会得到 0.029999999329447746 这种难看的数值
        f.write(f'resolution: {res:.4f}\n')
        f.write(f'origin: [{o.x:.4f}, {o.y:.4f}, 0.0]\n')
        f.write('negate: 0\n')
        f.write('occupied_thresh: 0.65\n')
        # ★★ free_thresh 必须写 0.196（ROS 标准值），不能写 0.25
        #
        #   Nav2 的 map_io 用这个公式算占用率：
        #       occ = (255 - 像素值) / 255
        #   然后：occ > occupied_thresh -> 占据
        #         occ < free_thresh     -> 空闲
        #         否则                  -> 未知
        #
        #   本工具把【未知】写成 205，对应 occ = (255-205)/255 = 0.196。
        #   如果 free_thresh 写成 0.25：0.196 < 0.25  → 未知被误判成【空闲】，
        #   机器人会把没扫到的区域当成可通行，可能一头撞上去。
        #   写成 0.196 时正好压线，判成【未知】—— 这才是正确行为。
        f.write('free_thresh: 0.196\n')


def main():
    ap = argparse.ArgumentParser(description='订阅 /map 并存成 PGM + YAML')
    ap.add_argument('-f', '--prefix', required=True,
                    help='输出路径前缀，会生成 <前缀>.pgm 和 <前缀>.yaml')
    ap.add_argument('--timeout', type=float, default=TIMEOUT_DEFAULT,
                    help=f'等待地图的超时秒数（默认 {TIMEOUT_DEFAULT}）')
    args = ap.parse_args()

    rclpy.init()
    node = MapSaver()
    print(f'等待 /map …（最多 {args.timeout:.0f} 秒）')

    import time
    t0 = time.monotonic()
    while rclpy.ok() and node.msg is None and (time.monotonic() - t0) < args.timeout:
        rclpy.spin_once(node, timeout_sec=0.2)

    if node.msg is None:
        print('❌ 没收到 /map。请确认：')
        print('   1) 仿真在跑：      ros2 launch community_nav sim.launch.py')
        print('   2) SLAM 在跑：     ros2 launch community_nav mapping.launch.py')
        print('   3) 地图确实在发：  ros2 topic hz /map')
        node.destroy_node()
        rclpy.shutdown()
        return 1

    msg, known = node.msg
    info = msg.info
    ppm = f'{args.prefix}.pgm'
    pym = f'{args.prefix}.yaml'
    os.makedirs(os.path.dirname(os.path.abspath(args.prefix)) or '.', exist_ok=True)

    write_pgm(ppm, msg)
    write_yaml(pym, os.path.basename(ppm), msg)

    total = info.width * info.height
    print()
    print('=' * 66)
    print('  地图已保存')
    print('=' * 66)
    print(f'  尺寸     : {info.width} x {info.height} 栅格')
    print(f'  分辨率   : {info.resolution} m/格')
    print(f'  实际范围 : {info.width * info.resolution:.2f} m x '
          f'{info.height * info.resolution:.2f} m')
    print(f'  原点     : ({info.origin.position.x:.3f}, '
          f'{info.origin.position.y:.3f})')
    print(f'  已知栅格 : {known} / {total}  ({known / total * 100:.1f}%)')
    print()
    print(f'  {ppm}')
    print(f'  {pym}')
    print()
    print('  ⚠️ 地图是运行时产物，写完记得重新编译：')
    print('     cd ~/smart_community_ws && colcon build --symlink-install')

    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
