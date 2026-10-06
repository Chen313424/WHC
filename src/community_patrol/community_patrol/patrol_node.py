#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
community_patrol —— 智慧社区多点巡检调度节点
================================================================================

【这个节点在整个系统里的位置】
    Nav2 只负责"把我从 A 送到 B"。但比赛要求的是"跑完一圈、每到一个点
    停车、拍照识别、遇红灯停"——这一层业务编排 Nav2 不管，由本节点负责。

【职责】
    1. 读取 YAML 站点表，按顺序逐个导航（调用 Nav2 的 NavigateToPose Action）
    2. 到点后按站点类型执行动作：
         - capture       → 停车稳定后触发视觉识别，并等待结果
         - traffic_light → 停车等待绿灯（订阅视觉组的信号灯状态话题）
         - finish        → 结束并播报总结
    3. 单站失败可重试；重试仍失败则跳过该站，保证整体流程不中断
    4. 全程超时保护，避免卡死某一点

【为什么用 Action 而不是 Topic/Service 做导航】
    Topic：单向、高频，无法知道"到了没有"，也无法取消。
    Service：请求-响应，但导航是长耗时任务（可能几十秒），
             Service 会长时间阻塞，且无法回传中间进度。
    Action：专为长耗时任务设计 —— 有目标(goal)、反馈(feedback)、
             结果(result)，并且**可以被取消**。导航正是它的典型应用场景。

【为什么不用 nav2_simple_commander】
    它可以少写几十行，但它把 Action 的生命周期封装掉了。
    本节点直接使用 ActionClient，代码逻辑完全暴露，
    便于针对比赛需求插入"红绿灯等待""触发识别""失败重试"等自定义逻辑。

作者：智慧社区赛项 · 导航组
================================================================================
"""

import json
import math
import os
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient

from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from std_msgs.msg import String

try:
    import yaml
except ImportError:
    print('缺少 PyYAML，请执行： pip3 install pyyaml', file=sys.stderr)
    raise

# 站点表的默认路径：优先用 ament 索引定位 community_nav 包的 config 目录。
# 正常流程里 navigation.launch.py 会显式传入 waypoints_file，这里的默认值
# 只服务于 `ros2 run community_patrol patrol_node` 单独调试。
try:
    from ament_index_python.packages import get_package_share_directory
    _DEFAULT_WAYPOINTS_FILE = os.path.join(
        get_package_share_directory('community_nav'),
        'config', 'community_waypoints.yaml')
except Exception:                                      # noqa: BLE001
    _DEFAULT_WAYPOINTS_FILE = os.path.join(
        os.getcwd(), 'community_waypoints.yaml')


# ==============================================================================
#  工具函数
# ==============================================================================
def yaw_to_quaternion(yaw):
    """
    偏航角(绕 Z 轴) -> 四元数 (x, y, z, w)

    ROS 用四元数表示姿态，但我们在 YAML 里只写一个 yaw（更直观）。
    平面机器人只有偏航角自由度，所以 roll=0, pitch=0，公式退化为：
        z = sin(yaw/2),  w = cos(yaw/2)
    （这行代码答辩时可以讲：为什么平面机器人只需要一个角度参数）
    """
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def make_pose(frame_id, x, y, yaw, stamp):
    """构造一个带位姿的 PoseStamped 消息"""
    pose = PoseStamped()
    pose.header.frame_id = frame_id
    pose.header.stamp = stamp
    pose.pose.position.x = float(x)
    pose.pose.position.y = float(y)
    pose.pose.position.z = 0.0
    qx, qy, qz, qw = yaw_to_quaternion(float(yaw))
    pose.pose.orientation.x = qx
    pose.pose.orientation.y = qy
    pose.pose.orientation.z = qz
    pose.pose.orientation.w = qw
    return pose


# ==============================================================================
#  主节点
# ==============================================================================
class PatrolNode(Node):

    # 站点类型
    ACT_NONE = 'none'
    ACT_CAPTURE = 'capture'
    ACT_TRAFFIC_LIGHT = 'traffic_light'
    ACT_FINISH = 'finish'

    def __init__(self):
        super().__init__('community_patrol')

        # ---------------- 参数（可通过 --ros-args -p 覆盖） ----------------
        self.declare_parameter('waypoints_file', _DEFAULT_WAYPOINTS_FILE)
        self.declare_parameter('autostart', True)
        self.declare_parameter('start_delay', 3.0)      # 启动后等待 Nav2 就绪
        self.declare_parameter('light_wait_timeout', 60.0)   # 等绿灯最长秒数
        self.declare_parameter('capture_wait_timeout', 10.0)  # 等识别结果最长秒数

        wp_file = self.get_parameter('waypoints_file').value
        self.autostart = self.get_parameter('autostart').value
        self.start_delay = self.get_parameter('start_delay').value
        self.light_wait_timeout = self.get_parameter('light_wait_timeout').value
        self.capture_wait_timeout = self.get_parameter('capture_wait_timeout').value

        # ---------------- 读取站点表 ----------------
        self.frame_id = 'map'
        self.loop = False
        self.retry_count = 2
        self.default_dwell = 2.0
        self.navigate_timeout = 90.0
        self.waypoints = []
        self._load_waypoints(wp_file)

        # ---------------- 通信接口 ----------------
        # 调用 Nav2 的导航动作服务端
        self.nav_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')

        # 发布：请求视觉组执行一次识别
        self.capture_pub = self.create_publisher(String, '/patrol/capture', 10)
        # 发布：巡检进度/状态（供播报组和调试使用）
        self.status_pub = self.create_publisher(String, '/patrol/status', 10)

        # 订阅：视觉组发布的红绿灯状态  ("RED" / "YELLOW" / "GREEN")
        self.current_light = 'UNKNOWN'
        self.create_subscription(String, '/traffic_light/state', self._on_light_state, 10)

        # 订阅：视觉组的识别结果
        self.last_detection = None
        self.create_subscription(String, '/detection/result', self._on_detection, 10)

        self.get_logger().info('=' * 68)
        self.get_logger().info('community_patrol 多点巡检调度节点已启动')
        self.get_logger().info(f'  站点文件   : {wp_file}')
        self.get_logger().info(f'  站点数量   : {len(self.waypoints)}')
        self.get_logger().info(f'  坐标系     : {self.frame_id}')
        self.get_logger().info(f'  失败重试   : {self.retry_count} 次')
        self.get_logger().info(f'  单站超时   : {self.navigate_timeout} 秒')
        self.get_logger().info('=' * 68)

    # --------------------------------------------------------------------------
    #  站点表读取
    # --------------------------------------------------------------------------
    def _load_waypoints(self, path):
        """解析 YAML 站点表。这里做完整校验，因为坐标写错会导致车撞墙。"""
        if not os.path.isfile(path):
            self.get_logger().error(f'站点文件不存在：{path}')
            raise FileNotFoundError(path)

        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        self.frame_id = data.get('frame_id', 'map')
        self.loop = bool(data.get('loop', False))
        self.retry_count = int(data.get('retry_count', 2))
        self.default_dwell = float(data.get('default_dwell', 2.0))
        self.navigate_timeout = float(data.get('navigate_timeout', 90.0))

        # ★ 先清空。否则本方法被重复调用时，会把站点追加成两份，
        #   导致同一站点被跑两次（这个坑是单元测试发现的）。
        self.waypoints = []
        raw = data.get('waypoints') or []
        if not raw:
            self.get_logger().error('站点表里没有任何站点（waypoints 为空）')
            raise ValueError('empty waypoints')

        for i, w in enumerate(raw):
            if 'x' not in w or 'y' not in w:
                raise ValueError(f'第 {i+1} 个站点缺少 x/y 坐标')
            self.waypoints.append({
                'id': str(w.get('id', f'wp_{i}')),
                'name': str(w.get('name', '')),
                'x': float(w['x']),
                'y': float(w['y']),
                'yaw': float(w.get('yaw', 0.0)),
                'action': str(w.get('action', 'none')).lower(),
                'dwell': float(w.get('dwell', self.default_dwell)),
                # capture 专用
                'task': w.get('task', ''),
                'zone': w.get('zone', ''),
                'slot': w.get('slot', ''),
                'target': w.get('target', ''),
                # traffic_light 专用
                'light_id': w.get('light_id', ''),
            })

    # --------------------------------------------------------------------------
    #  订阅回调（会在 spin_once 期间被触发，即使正在等导航结果）
    # --------------------------------------------------------------------------
    def _on_light_state(self, msg):
        state = msg.data.strip().upper()
        if state != self.current_light:
            self.get_logger().info(f'[信号灯] {self.current_light} -> {state}')
        self.current_light = state

    def _on_detection(self, msg):
        self.last_detection = msg.data
        self.get_logger().info(f'[识别结果] {msg.data}')

    # --------------------------------------------------------------------------
    #  导航：把机器人送到某个站点
    # --------------------------------------------------------------------------
    def navigate_to(self, wp, timeout):
        """
        发送 NavigateToPose 目标并等待结果。

        返回 True 表示成功到达，False 表示失败/超时。
        注意：等待期间仍会 spin，所以信号灯订阅回调依然能收到消息
              —— 这正是我们能在导航途中响应红绿灯的前提。
        """
        if not self.nav_client.wait_for_server(timeout_sec=10.0):
            self.get_logger().error('Nav2 的 navigate_to_pose 服务端未就绪')
            return False

        goal = NavigateToPose.Goal()
        goal.pose = make_pose(self.frame_id, wp['x'], wp['y'], wp['yaw'],
                              self.get_clock().now().to_msg())

        self.get_logger().info(
            f"→ 前往 [{wp['id']}] {wp['name']}  "
            f"({wp['x']:.2f}, {wp['y']:.2f}, yaw={wp['yaw']:.2f})")

        send_future = self.nav_client.send_goal_async(
            goal, feedback_callback=self._nav_feedback)

        # --- 等目标被接受 ---
        if not self._wait_future(send_future, timeout=10.0):
            self.get_logger().error('发送导航目标超时（等待接受）')
            return False
        goal_handle = send_future.result()
        if goal_handle is None or not goal_handle.accepted:
            self.get_logger().warn('导航目标被拒绝')
            return False

        # --- 等结果 ---
        result_future = goal_handle.get_result_async()
        ok = self._wait_future(result_future, timeout=timeout)
        if not ok:
            self.get_logger().warn(f"[{wp['id']}] 导航超时（{timeout:.0f}s），取消目标")
            goal_handle.cancel_goal_async()
            return False

        status = result_future.result().status
        # 4 = STATUS_SUCCEEDED
        if status != 4:
            self.get_logger().warn(f"[{wp['id']}] 导航失败，status={status}")
            return False

        self.get_logger().info(f"✓ 到达 [{wp['id']}]")
        return True

    def _nav_feedback(self, feedback_msg):
        """导航过程中的反馈回调（这里是剩余距离，可用于进度显示）"""
        fb = feedback_msg.feedback
        self.get_logger().debug(
            f'导航中… 剩余 {fb.distance_remaining:.2f} m',
            throttle_duration_sec=2.0)

    def _wait_future(self, future, timeout):
        """
        等待一个 future 完成，期间持续 spin 以处理回调。
        返回 True 表示在超时前完成。
        """
        deadline = time.monotonic() + timeout
        while rclpy.ok() and not future.done():
            if time.monotonic() > deadline:
                return False
            # ★ 关键：spin_once 让订阅回调在等待期间也能触发。
            #   没有这一行，导航途中就收不到红绿灯状态。
            rclpy.spin_once(self, timeout_sec=0.1)
        return future.done()

    # --------------------------------------------------------------------------
    #  动作 1：触发视觉识别
    # --------------------------------------------------------------------------
    def do_capture(self, wp):
        """停车稳定后，通知视觉组执行一次识别，并等待结果"""
        payload = {
            'waypoint_id': wp['id'],
            'task': wp['task'],
            'zone': wp['zone'],
            'slot': wp['slot'],
            'target': wp['target'],
            'stamp': time.time(),
        }
        self.last_detection = None
        self.capture_pub.publish(String(data=json.dumps(payload, ensure_ascii=False)))
        self.get_logger().info(
            f"[{wp['id']}] 已触发识别任务 task={wp['task']} "
            f"zone={wp['zone']} slot={wp['slot']}")

        # 等待视觉组返回结果（带超时，避免卡死整体流程）
        deadline = time.monotonic() + self.capture_wait_timeout
        while rclpy.ok() and self.last_detection is None:
            if time.monotonic() > deadline:
                self.get_logger().warn(
                    f"[{wp['id']}] 未在 {self.capture_wait_timeout:.0f}s 内收到识别结果，跳过")
                return None
            rclpy.spin_once(self, timeout_sec=0.1)

        return self.last_detection

    # --------------------------------------------------------------------------
    #  动作 2：红绿灯等待
    # --------------------------------------------------------------------------
    def do_traffic_light(self, wp):
        """
        停在停止线前，等绿灯再走。

        比赛要求：红灯期间车身不得越过停止线。
        实现方式：视觉组把信号灯状态发到 /traffic_light/state，
                 本节点在停止线前保持不动，直到状态变为 GREEN。
        """
        self.get_logger().info(f"[{wp['id']}] 到达停止线，等待绿灯…")
        deadline = time.monotonic() + self.light_wait_timeout
        waited = 0.0

        while rclpy.ok():
            if self.current_light == 'GREEN':
                self.get_logger().info(
                    f"[{wp['id']}] 绿灯，等待 {waited:.1f}s 后通过")
                return True
            if time.monotonic() > deadline:
                self.get_logger().warn(
                    f"[{wp['id']}] 等待绿灯超时（{self.light_wait_timeout:.0f}s），"
                    f'当前状态={self.current_light}，为不中断流程继续前进')
                return False
            rclpy.spin_once(self, timeout_sec=0.1)
            waited += 0.1

        return False

    # --------------------------------------------------------------------------
    #  主任务流程
    # --------------------------------------------------------------------------
    def run_patrol(self):
        self.get_logger().info('等待 Nav2 导航栈就绪…')
        time.sleep(self.start_delay)

        if not self.nav_client.wait_for_server(timeout_sec=60.0):
            self.get_logger().error('60 秒内未等到 Nav2，巡检中止')
            return False

        self.get_logger().info('=' * 68)
        self.get_logger().info(f'开始巡检，共 {len(self.waypoints)} 个站点')
        self.get_logger().info('=' * 68)

        t_start = time.monotonic()
        summary = []
        n_ok = 0
        n_fail = 0

        for idx, wp in enumerate(self.waypoints, 1):
            self.get_logger().info(
                f'--- 站点 {idx}/{len(self.waypoints)}: {wp["id"]} ---')

            # ---------- 1. 导航到站点（带重试） ----------
            arrived = False
            for attempt in range(1, self.retry_count + 2):
                if self.navigate_to(wp, self.navigate_timeout):
                    arrived = True
                    break
                if attempt <= self.retry_count:
                    self.get_logger().warn(
                        f"[{wp['id']}] 第 {attempt} 次失败，重试…")

            if not arrived:
                n_fail += 1
                summary.append({'id': wp['id'], 'result': 'NAV_FAILED'})
                self.get_logger().error(
                    f"[{wp['id']}] 导航失败已超过重试次数，跳过该站点")
                continue

            n_ok += 1

            # ---------- 2. 到点后的业务动作 ----------
            action = wp['action']

            if action == self.ACT_TRAFFIC_LIGHT:
                self.do_traffic_light(wp)

            elif action == self.ACT_CAPTURE:
                # 先停稳再拍：给底盘一点时间消除残余速度，避免画面糊
                time.sleep(0.5)
                result = self.do_capture(wp)
                summary.append({
                    'id': wp['id'],
                    'task': wp['task'],
                    'result': result if result else 'NO_RESULT',
                })

            elif action == self.ACT_FINISH:
                self.get_logger().info(f"[{wp['id']}] 巡检结束")
                break

            # ---------- 3. 停留（给视觉/播报留时间） ----------
            if wp['dwell'] > 0 and action != self.ACT_FINISH:
                time.sleep(wp['dwell'])

        # ---------------- 总结 ----------------
        elapsed = time.monotonic() - t_start
        self.get_logger().info('=' * 68)
        self.get_logger().info('巡检完成')
        self.get_logger().info(f'  总耗时   : {elapsed:.1f} 秒')
        self.get_logger().info(f'  成功站点 : {n_ok}')
        self.get_logger().info(f'  失败站点 : {n_fail}')
        self.get_logger().info('=' * 68)

        self.status_pub.publish(String(data=json.dumps({
            'event': 'patrol_finished',
            'elapsed_sec': round(elapsed, 1),
            'ok': n_ok,
            'failed': n_fail,
            'details': summary,
        }, ensure_ascii=False)))

        return n_fail == 0


# ==============================================================================
#  入口
# ==============================================================================
def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = PatrolNode()
        if node.autostart:
            node.run_patrol()
            node.get_logger().info('巡检流程结束，节点保持运行以便查看状态（Ctrl+C 退出）')
            rclpy.spin(node)
        else:
            node.get_logger().info('autostart=false，等待外部触发（当前不自动开始）')
            rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as exc:                       # noqa: BLE001
        msg = str(exc)
        if node is not None:
            node.get_logger().error(f'巡检异常终止：{msg}')
        else:
            # 节点连初始化都没过（通常是站点表路径不对）。
            # 这里刻意不抛 traceback —— 比赛现场看到一堆调用栈没有意义，
            # 打印一条能照着做的提示更有用。
            print('\n' + '=' * 68, file=sys.stderr)
            print(f'节点启动失败：{msg}', file=sys.stderr)
            if 'waypoints' in msg.lower() or '站点' in msg or 'No such file' in msg:
                print('', file=sys.stderr)
                print('  站点表读不到，请依次检查：', file=sys.stderr)
                print('    1) community_waypoints.yaml 文件是否存在', file=sys.stderr)
                print('    2) 启动参数里的路径是否正确，例如：', file=sys.stderr)
                print('       ros2 run community_patrol patrol_node --ros-args \\',
                      file=sys.stderr)
                print('         -p waypoints_file:=<community_nav 包>/config/'
                      'community_waypoints.yaml', file=sys.stderr)
            print('=' * 68 + '\n', file=sys.stderr)
        return 1
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    return 0


if __name__ == '__main__':
    main()
