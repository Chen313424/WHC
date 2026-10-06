#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SLAM 管线诊断 —— 一眼看出问题出在哪一环
================================================================================
用法（先 source 好环境，且仿真与建图已在运行）：

    python3 "/mnt/d/project（ai/docs/scripts/diagnose_slam.py"

    # 想跑久一点（默认 20 秒）
    python3 ".../diagnose_slam.py" --duration 40

    # 边跑边遥控机器人，效果最好

【为什么需要它】
    "车不动 / 地图不长 / RViz 没反应"这类现象，靠一条条敲命令排查很慢，
    而且容易问错方向。本脚本同时盯住整条链路的五个环节，每 3 秒打印一次：

        Gazebo 时钟  →  激光雷达  →  里程计  →  控制指令  →  地图

    并且在结束时给出【明确结论】：问题出在哪一环。

【关键诊断原理】
    Gazebo 的 /odom 是【开环】的 —— 它由轮速积分得来，**不测量实际位移**。
    所以"轮子在转、/odom 在变"并不能证明机器人真的动了。
    真正能证明机器人移动的是【激光读数发生改变】。

    本脚本同时记录这两者，因此能区分：
      · 机器人真的在动，只是 RViz 没刷新   → 显示问题
      · 机器人卡住空转（/odom 变、激光不变）→ 出生点或物理问题
================================================================================
"""

import argparse
import math
import sys
import time

try:
    import rclpy
    from rclpy.node import Node
except ImportError:
    print('导入 rclpy 失败。请先在已 source ROS 2 环境的终端里运行：', file=sys.stderr)
    print('    source /opt/ros/humble/setup.bash', file=sys.stderr)
    sys.exit(1)

try:
    from sensor_msgs.msg import LaserScan
    from nav_msgs.msg import Odometry, OccupancyGrid
    from geometry_msgs.msg import Twist
except ImportError as exc:
    print(f'缺少消息包：{exc}', file=sys.stderr)
    sys.exit(1)

try:
    from rosgraph_msgs.msg import Clock
    HAS_CLOCK = True
except ImportError:
    HAS_CLOCK = False


def fingerprint(scan):
    """给一帧激光数据算个"指纹"：有效点数 + 距离之和。

    机器人一移动，这个值几乎必然改变。用它判断"激光读数有没有变"。
    """
    finite = [r for r in scan.ranges if math.isfinite(r) and r > 0.01]
    if not finite:
        return 0, 0.0
    return len(finite), round(sum(finite), 2)


class Diag(Node):

    def __init__(self, spin_rate=0.0):
        super().__init__('slam_diagnose')

        self.spin_rate = spin_rate

        self.n_clock = 0
        self.n_scan = 0
        self.n_odom = 0
        self.n_cmd = 0
        self.n_map = 0

        self.last_scan_fp = None
        self.scan_fp_changes = 0
        self.scan_first_fp = None

        self.last_odom = None
        self.odom_start = None
        self.odom_moved = 0.0

        self.map_cells = None
        self.map_changes = 0
        self.map_total = 0        # ★ 累计计数，不被 snapshot 重置

        self.got_cmd = False

        if HAS_CLOCK:
            self.create_subscription(Clock, '/clock', self._on_clock, 10)

        self.create_subscription(LaserScan, '/scan', self._on_scan, 10)
        self.create_subscription(Odometry, '/odom', self._on_odom, 10)
        self.create_subscription(Twist, '/cmd_vel', self._on_cmd, 10)

        # ★★ /map 的 QoS 必须用【默认的 VOLATILE】，不能用 TRANSIENT_LOCAL。
        #
        #    ROS 2 的 QoS 兼容规则：
        #      · VOLATILE 订阅者  ← 能收 VOLATILE 和 TRANSIENT_LOCAL 两种发布者 ✅
        #      · TRANSIENT_LOCAL 订阅者 ← 只能收 TRANSIENT_LOCAL 发布者
        #        如果发布者是 VOLATILE，两者不兼容，【一条消息都收不到】❌
        #
        #    slam_toolbox 发布 /map 用的是 VOLATILE。
        #    早先这个订阅写了 TRANSIENT_LOCAL，导致明明 SLAM 正常，
        #    却一条地图都收不到，被误判成"SLAM 没工作"。
        #    对诊断工具来说，**用最宽松的 QoS 才不会产生假阴性**。
        self.create_subscription(OccupancyGrid, '/map', self._on_map, 10)

        # ★ --spin：由本脚本自己发控制指令，让机器人【原地旋转】
        #   为什么用旋转而不是前进：
        #     · 旋转必然让激光读数剧烈变化，最容易看出"到底动没动"
        #     · 原地转【永远不会撞墙】，避免"撞墙后不动"被误判成"卡住"
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        if spin_rate != 0.0:
            self.create_timer(0.1, self._publish_spin)

        self.get_logger().info('开始诊断…（下面每 3 秒一行）')

    # ------------------------------------------------------------------ 控制
    def _publish_spin(self):
        t = Twist()
        t.angular.z = self.spin_rate
        self.cmd_pub.publish(t)

    def stop(self):
        """诊断结束，停车"""
        t = Twist()
        self.cmd_pub.publish(t)

    # ------------------------------------------------------------------ 回调
    def _on_clock(self, _msg):
        self.n_clock += 1

    def _on_scan(self, msg):
        self.n_scan += 1
        fp = fingerprint(msg)
        if self.scan_first_fp is None:
            self.scan_first_fp = fp
        if self.last_scan_fp is not None and fp != self.last_scan_fp:
            self.scan_fp_changes += 1
        self.last_scan_fp = fp

    def _on_odom(self, msg):
        self.n_odom += 1
        p = msg.pose.pose.position
        cur = (p.x, p.y)
        if self.odom_start is None:
            self.odom_start = cur
        if self.last_odom is not None:
            self.odom_moved += math.hypot(cur[0] - self.last_odom[0],
                                          cur[1] - self.last_odom[1])
        self.last_odom = cur

    def _on_cmd(self, msg):
        self.n_cmd += 1
        if abs(msg.linear.x) > 1e-3 or abs(msg.angular.z) > 1e-3:
            self.got_cmd = True

    def _on_map(self, msg):
        self.n_map += 1
        self.map_total += 1
        occ = sum(1 for v in msg.data if v > 50)
        if self.map_cells is not None and occ != self.map_cells:
            self.map_changes += 1
        self.map_cells = occ

    # ------------------------------------------------------------------ 报告
    def snapshot(self, dt):
        def rate(n):
            return f'{n / dt:.1f} Hz'

        lines = []
        clock_s = rate(self.n_clock) if HAS_CLOCK else '(未安装 rosgraph_msgs)'
        lines.append(f'  Gazebo 时钟 /clock : {clock_s}   '
                     f'{"✅" if self.n_clock > 0 else "❌ 仿真没在跑"}')
        lines.append(f'  激光 /scan         : {rate(self.n_scan)}   '
                     f'{"✅" if self.n_scan > 0 else "❌ 雷达没数据"}')
        lines.append(f'  里程计 /odom       : {rate(self.n_odom)}   '
                     f'{"✅" if self.n_odom > 0 else "❌ 里程计没数据"}')
        lines.append(f'  控制 /cmd_vel      : {rate(self.n_cmd)}   '
                     f'{"✅ 收到过非零指令" if self.got_cmd else "（还没收到非零指令，按几下 w）"}')
        lines.append(f'  地图 /map          : {rate(self.n_map)}   '
                     f'{"✅" if self.map_total > 0 else "❌ SLAM 没发地图"}'
                     f'（累计 {self.map_total} 条）')

        if self.last_scan_fp:
            lines.append(f'  激光指纹           : {self.last_scan_fp}   '
                         f'变化次数 {self.scan_fp_changes}')
        if self.last_odom:
            lines.append(f'  里程计累计位移     : {self.odom_moved:.2f} m')
        if self.map_cells is not None:
            lines.append(f'  地图占据栅格       : {self.map_cells}   变化次数 {self.map_changes}')

        # 重置计数
        self.n_clock = self.n_scan = self.n_odom = self.n_cmd = self.n_map = 0
        return '\n'.join(lines)

    # ------------------------------------------------------------------ 结论
    def verdict(self):
        print()
        print('=' * 74)
        print('  诊断结论')
        print('=' * 74)

        if self.n_scan == 0 and self.last_scan_fp is None:
            print('  ❌ 完全没收到激光数据')
            print('     → 仿真没起来，或机器人没生成成功。')
            print('       先看 sim.launch.py 那个终端里有没有 Successfully spawned entity')
            return 1

        # ★★ 必须先排掉"SLAM 根本没发地图"这一种。
        #    否则会出现误判：机器人确实在动、激光也在变，
        #    但 SLAM 压根没工作 —— 那种情况说"问题在 RViz 显示"是错的。
        if self.map_total == 0:
            print('  ❌ 激光有数据，但【地图 /map 一条都没收到】')
            print()
            print('     → 说明 slam_toolbox 没有在工作，问题不在 RViz。')
            print()
            print('     ⚠️ 先用这条交叉验证一下（它用默认 QoS，不会因 QoS 不兼容漏收）：')
            print('         ros2 topic hz /map')
            print('       如果它显示有频率，那是本诊断脚本的问题，不是 SLAM 的问题。')
            print()
            print('     确认确实没有地图后，按顺序查：')
            print('       1) ros2 lifecycle get /slam_toolbox')
            print('          应该输出 active。如果是 unconfigured / inactive，')
            print('          说明生命周期管理器没把它激活。')
            print('       2) 看 mapping.launch.py 那个终端里有没有报错')
            print('       3) 手动激活试试：')
            print('          ros2 lifecycle set /slam_toolbox configure')
            print('          ros2 lifecycle set /slam_toolbox activate')
            return 5

        moving = self.scan_fp_changes > 2
        odom_moved = self.odom_moved

        if moving:
            print('  ✅ 激光读数在变化 —— 机器人【确实在移动】')
            print('  ✅ 地图 /map 也在发布 —— SLAM 工作正常')
            print()
            print('     所以问题不在仿真、也不在建图，而在【RViz 显示】。')
            print('     下一步：')
            print('       1) 在 RViz 里滚动滚轮【缩小视野】—— 默认视角太近，')
            print('          地图可能只是在长，但你没看见')
            print('       2) 看 RViz 左下角的 Fixed Frame，临时改成 odom 试试')
            print('       3) 详见 docs/06-Gazebo与WSLg排障指南.md 第 6.1 节')
            return 2

        if odom_moved > 0.3 and not moving:
            print('  ⚠️ 里程计显示走了 {:.2f} m，但激光读数【完全没变】'.format(odom_moved))
            print()
            print('     这就是典型的"机器人卡住空转"：')
            print('     · /odom 是开环的（由轮速积分得来），所以它变了不代表车真的动了')
            print('     · 激光读数不变，说明传感器视角根本没变 —— 车没动')
            print()
            print('     处理办法：换一个空闲的出生点重新生成机器人，例如：')
            print('         ros2 launch community_nav sim.launch.py gui:=false \\')
            print('             x_pose:=-2.0 y_pose:=-0.5')
            return 3

        print('  ⚠️ 激光读数没有变化，里程计也没怎么动')
        print()
        print('     → 可能是：')
        print('       1) 你没按遥控键（试试按住 w 多按几下，再跑一次本脚本）')
        print('       2) 机器人卡在障碍物里')
        print('       3) 机器人出生在半空/地下')
        return 4


def main():
    ap = argparse.ArgumentParser(description='SLAM 管线诊断')
    ap.add_argument('--duration', type=float, default=20.0,
                    help='诊断时长（秒），默认 20')
    ap.add_argument('--interval', type=float, default=3.0,
                    help='报告间隔（秒），默认 3')
    ap.add_argument('--spin', type=float, default=0.4,
                    help='诊断期间让机器人原地旋转的角速度（rad/s）。'
                         '设 0 表示不自动控制，改为自己用遥控操作。默认 0.4')
    args = ap.parse_args()

    rclpy.init()
    node = Diag(spin_rate=args.spin)

    print()
    print('=' * 74)
    print('  SLAM 管线诊断')
    print('=' * 74)
    if args.spin != 0.0:
        print(f'  ✅ 本脚本会自己让机器人【原地旋转】（角速度 {args.spin} rad/s）')
        print('     —— 不需要你开遥控终端，也不会撞墙')
    else:
        print('  ⚠️ --spin 0：请自己用遥控让机器人动起来（切到遥控终端按 w）')
    print(f'  诊断时长 {args.duration:.0f} 秒，每 {args.interval:.0f} 秒报告一次')
    print('=' * 74)

    t_end = time.monotonic() + args.duration
    t_next = time.monotonic() + args.interval
    t_last = time.monotonic()

    while rclpy.ok() and time.monotonic() < t_end:
        rclpy.spin_once(node, timeout_sec=0.1)
        now = time.monotonic()
        if now >= t_next:
            dt = now - t_last
            t_last = now
            t_next = now + args.interval
            print()
            print(f'--- t = {args.duration - (t_end - now):5.1f} s ' + '-' * 45)
            print(node.snapshot(dt))

    node.stop()          # 先停车，再出结论
    time.sleep(0.3)
    rclpy.spin_once(node, timeout_sec=0.1)

    rc = node.verdict()
    node.destroy_node()
    rclpy.shutdown()
    return rc


if __name__ == '__main__':
    sys.exit(main())
