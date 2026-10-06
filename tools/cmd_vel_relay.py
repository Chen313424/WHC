#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 /cmd_vel_smoothed 直通到 /cmd_vel，绕过把速度指令【整个吞掉】的 collision_monitor。

为什么需要它 —— 本机实测（ROS 2 Jazzy + Nav2）：
    /cmd_vel_nav       : 20.00 Hz   ← controller_server 满速下发
    /cmd_vel_smoothed  : 19.99 Hz   ← velocity_smoother 正常转发
    /cmd_vel           : 无数据      ← collision_monitor 一条都不发
    gz /model/robot/cmd_vel : 无数据

  collision_monitor 明明 `active [3]`、输入 20Hz 有数据，却完全不往
  cmd_vel_out_topic 发布 —— 于是 bridge 没有可桥接的内容，Gazebo 收不到速度，
  小车一步不动。表现极像"导航算法坏了"或"TF 配置不对"。

  导航组的参数注释里写过：
      「它会持续查询 base_footprint -> odom 的 TF，日志里会出现
        Extrapolation Error ... 这是它的时钟与 TF 时间戳不同步导致的，
        【不影响导航功能】」
  —— 后半句判断有误：TF 查询失败时它不会退回"直通"，而是整条指令被丢弃。

本中继是【临时绕行】而非正解。正解应当是修 collision_monitor 的 TF 时钟
问题（提高 transform_tolerance / 对齐 TF 时间戳 / 或按需关掉
FootprintApproach 的 approach 行为），或者干脆不把它串在速度链上。

用法（必须与 Nav2 用同一个 RMW）：
    export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
    python3 cmd_vel_relay.py
"""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist


class CmdVelRelay(Node):
    def __init__(self):
        super().__init__('cmd_vel_relay')
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.sub = self.create_subscription(Twist, '/cmd_vel_smoothed', self.on_msg, 10)
        self.n = 0
        self.create_timer(5.0, self.report)
        self.get_logger().info('cmd_vel 中继已启动：/cmd_vel_smoothed -> /cmd_vel')

    def on_msg(self, msg):
        self.n += 1
        self.pub.publish(msg)

    def report(self):
        self.get_logger().info(f'已转发 {self.n} 条 /cmd_vel_smoothed -> /cmd_vel')


def main():
    rclpy.init()
    node = CmdVelRelay()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
