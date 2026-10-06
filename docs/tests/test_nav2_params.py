#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nav2 参数版本适配检查 —— 防止"按错误版本写法配置"这类错误
================================================================================
用法：

    cd <工作区根目录>
    python3 docs/tests/test_nav2_params.py

【本项目的版本演进 —— 这一点很重要】
    最初我们在 **ROS 2 Humble + Gazebo Classic** 上开发，配置按 Humble 写。
    后来为了使用建模组已完成的社区场景（它是 **ROS 2 Jazzy + Gazebo Harmonic**），
    整个导航栈迁移到了 Jazzy。

    两个版本的 Nav2 参数有【实质性差异】。最典型的三个：

    | 参数 | Humble | Jazzy (Nav2 1.3+) |
    |---|---|---|
    | 行为树 | **必须**用 plugin_lib_names 逐个列出节点库 | 改用 navigators + plugin；**再写 plugin_lib_names 会重复注册直接失败** |
    | 进度检查器 | progress_checker_plugin（单数） | progress_checker_plugins（复数，列表） |
    | 恢复行为代价地图话题 | costmap_topic / footprint_topic | local_costmap_topic / global_costmap_topic / local_footprint_topic / global_footprint_topic |

    在 Jazzy 上写 Humble 的参数名，**不会报错，只会静默失效**：
      · 进度检查器不生效 → 机器人卡住了也不触发恢复行为
      · 恢复行为拿不到代价地图 → 卡住时无法脱困
    这类"静默失效"最难排查。

【本脚本做什么】
    以 Nav2 Jazzy 官方发布包里的 nav2_params.yaml 为基准：
      · 检查顶层键是否齐全
      · 检查有没有混入【Humble 专有】的旧参数名
      · 抽查关键参数值是否符合本项目的调参意图

    这套"以官方发布包为基准逐项比对"的方法，本身也是
    技术方案文档里"版本适配"一节的内容。
================================================================================
"""

import io
import math
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(_HERE))
PARAMS = os.path.join(WS, 'src', 'community_nav', 'config', 'nav2_params.yaml')

try:
    import yaml
except ImportError:
    print('缺少 PyYAML。请执行： sudo apt install -y python3-yaml', file=sys.stderr)
    sys.exit(1)

# ------------------------------------------------------------------------------
#  Nav2 Jazzy 官方 nav2_params.yaml 的顶层键
# ------------------------------------------------------------------------------
OFFICIAL_TOP = {
    'amcl', 'bt_navigator',
    'bt_navigator_navigate_through_poses_rclcpp_node',
    'bt_navigator_navigate_to_pose_rclcpp_node',
    'controller_server', 'local_costmap', 'global_costmap',
    'map_saver', 'planner_server', 'smoother_server',
    'behavior_server', 'waypoint_follower', 'velocity_smoother',
    # Nav2 1.3（Jazzy）新增：bringup 会把它们一起交给生命周期管理器，
    # 参数文件里少任何一段都会导致 configure 失败、整条链路起不来
    'collision_monitor',
}

# ------------------------------------------------------------------------------
#  【Humble 专有】的参数名 —— 在 Jazzy 上出现就是错误
#
#  注意：必须用【行首 + 完整键名】匹配，不能用子串匹配。
#  否则 'costmap_topic:' 会命中 'local_costmap_topic:'，产生假阳性。
# ------------------------------------------------------------------------------
FORBIDDEN = [
    'plugin_lib_names',
    'progress_checker_plugin',
    'costmap_topic',
    'footprint_topic',
]


def find_forbidden(raw, doc=None):
    """返回真正违规的旧参数名

    ★ 注意区分"全文检查"和"按段检查"：

      · plugin_lib_names / progress_checker_plugin
        —— 在 Jazzy 里【任何地方】都不该出现，全文检查。

      · costmap_topic / footprint_topic
        —— 只有 behavior_server 段把它们改成了 local_/global_ 前缀。
           而 docking_server.controller 段里用的【仍然是】costmap_topic /
           footprint_topic（Nav2 Jazzy 官方 nav2_params.yaml 就是这么写的）。
           所以这里必须【只在 behavior_server 段内】检查，
           否则会对合法配置误报——本项目实际踩过这个误报。
    """
    hits = []
    for key in ('plugin_lib_names', 'progress_checker_plugin'):
        if re.search(r'(?m)^[ \t]*' + re.escape(key) + r'\s*:', raw):
            hits.append(key)

    bs = ((doc or {}).get('behavior_server') or {}).get('ros__parameters') or {}
    for key in ('costmap_topic', 'footprint_topic'):
        if key in bs:
            hits.append(f'behavior_server.{key}')

    return hits


# ------------------------------------------------------------------------------
#  ★ 插件类名分隔符检查
#
#  pluginlib 在较新版本（Jazzy 起）里【去掉了 "/" 分隔符】，
#  只认 "包名::类名"。写成 "包名/类名" 时节点会在 configure 阶段直接 FATAL：
#
#      Failed to create global planner. Exception: According to the loaded
#      plugin descriptions the class nav2_smac_planner/SmacPlanner2D with base
#      class type nav2_core::GlobalPlanner does not exist.
#
#  这类错误【只在运行时炸】，YAML 解析和大多数静态检查都查不出来。
#  而且后果被放大：planner_server 配置失败 → 生命周期管理器中止 →
#  controller / bt_navigator 全都不激活 → 发导航目标毫无反应，
#  表面上像"导航不好使"，实际根因在【一个斜杠】上。
#  本项目就是先踩了 planner 这一处，改了 bt_navigator 和 behavior_server
#  却漏了它。
# ------------------------------------------------------------------------------
def find_slash_plugins(raw):
    """找出所有用 "/" 作分隔符的 plugin 值"""
    vals = re.findall(r'plugin:\s*"([^"]+)"', raw)
    return [v for v in vals if '/' in v]


# ------------------------------------------------------------------------------
#  关键参数值抽查
#
#  【设计取舍】结构类参数用精确匹配（对错是二元的）；
#  调参类参数用【合理区间】——因为场地尺度还在和建模组确认
#  （比赛规定复赛 4.2m / 总决赛 6m，而当前世界是 88m×44m），
#  调参值会随场地大小变化，写死精确值会导致测试无谓地失败。
# ------------------------------------------------------------------------------
def _get(doc, *path):
    cur = doc
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _eq(want):
    return lambda v: v == want


def _between(lo, hi):
    return lambda v: isinstance(v, (int, float)) and lo <= v <= hi


# (名称, 取值函数, 判定, 期望描述, 说明)
CHECKS = [
    ('bt_navigator.navigators',
     lambda d: _get(d, 'bt_navigator', 'ros__parameters', 'navigators'),
     _eq(['navigate_to_pose', 'navigate_through_poses']),
     "['navigate_to_pose', 'navigate_through_poses']",
     'Jazzy 用 navigators 声明导航器'),
    ('bt_navigator.navigate_to_pose.plugin',
     lambda d: _get(d, 'bt_navigator', 'ros__parameters',
                    'navigate_to_pose', 'plugin'),
     _eq('nav2_bt_navigator::NavigateToPoseNavigator'),
     'nav2_bt_navigator::NavigateToPoseNavigator',
     'Jazzy 的插件写法（带 ::）'),
    ('controller.progress_checker_plugins',
     lambda d: _get(d, 'controller_server', 'ros__parameters',
                    'progress_checker_plugins'),
     _eq(['progress_checker']),
     "['progress_checker']",
     'Jazzy 是复数列表'),
    ('controller.FollowPath.plugin',
     lambda d: _get(d, 'controller_server', 'ros__parameters', 'FollowPath', 'plugin'),
     _eq('nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController'),
     '...RegulatedPurePursuitController',
     '选用 RPP 而非默认的 MPPI/DWB'),
    ('planner.GridBased.plugin',
     lambda d: _get(d, 'planner_server', 'ros__parameters', 'GridBased', 'plugin'),
     _eq('nav2_smac_planner::SmacPlanner2D'),
     'nav2_smac_planner::SmacPlanner2D',
     '选用 Smac（A*）而非默认的 NavFn'),
    ('behavior_server.local_costmap_topic',
     lambda d: _get(d, 'behavior_server', 'ros__parameters', 'local_costmap_topic'),
     _eq('local_costmap/costmap_raw'), 'local_costmap/costmap_raw',
     'Jazzy 的参数名'),
    ('behavior_server.global_costmap_topic',
     lambda d: _get(d, 'behavior_server', 'ros__parameters', 'global_costmap_topic'),
     _eq('global_costmap/costmap_raw'), 'global_costmap/costmap_raw',
     'Jazzy 的参数名'),
    ('behavior_server.local_footprint_topic',
     lambda d: _get(d, 'behavior_server', 'ros__parameters', 'local_footprint_topic'),
     _eq('local_costmap/published_footprint'), 'local_costmap/published_footprint',
     'Jazzy 的参数名'),
    # ---- 结构类布尔/话题 ----
    ('local_costmap.always_send_full_costmap',
     lambda d: _get(d, 'local_costmap', 'local_costmap', 'ros__parameters',
                    'always_send_full_costmap'),
     _eq(True), 'True', '否则 RViz 里可能只显示局部，录像会缺内容'),
    ('global_costmap.track_unknown_space',
     lambda d: _get(d, 'global_costmap', 'global_costmap', 'ros__parameters',
                    'track_unknown_space'),
     _eq(True), 'True', '未知区域按障碍处理，防止规划出穿墙路径'),
    ('amcl.set_initial_pose',
     lambda d: _get(d, 'amcl', 'ros__parameters', 'set_initial_pose'),
     _eq(True), 'True', '一键启动的前提：自动设置初始位姿'),
    ('waypoint_follower.stop_on_failure',
     lambda d: _get(d, 'waypoint_follower', 'ros__parameters', 'stop_on_failure'),
     _eq(False), 'False', '单站失败不终止整体流程'),
    ('use_sim_time（抽查 amcl）',
     lambda d: _get(d, 'amcl', 'ros__parameters', 'use_sim_time'),
     _eq(True), 'True', '仿真环境必须为 true'),
    ('global_costmap.map_subscribe_transient_local',
     lambda d: _get(d, 'global_costmap', 'global_costmap', 'ros__parameters',
                    'static_layer', 'map_subscribe_transient_local'),
     _eq(True), 'True', 'map_server 以 transient_local 发布地图，必须匹配'),
    # ---- 调参类（合理区间）----
    ('controller.FollowPath.desired_linear_vel',
     lambda d: _get(d, 'controller_server', 'ros__parameters',
                    'FollowPath', 'desired_linear_vel'),
     _between(0.10, 0.40), '0.10 ~ 0.40 m/s',
     '必须降速：默认 0.5 在狭小场地会冲过停止线'),
    ('goal_checker.xy_goal_tolerance',
     lambda d: _get(d, 'controller_server', 'ros__parameters',
                    'general_goal_checker', 'xy_goal_tolerance'),
     _between(0.05, 0.25), '0.05 ~ 0.25 m',
     '停车位置精度'),
    ('goal_checker.yaw_goal_tolerance',
     lambda d: _get(d, 'controller_server', 'ros__parameters',
                    'general_goal_checker', 'yaw_goal_tolerance'),
     _between(0.05, 0.30), '0.05 ~ 0.30 rad',
     '车头角度精度决定相机能否拍正目标'),
    ('planner.GridBased.cost_penalty',
     lambda d: _get(d, 'planner_server', 'ros__parameters', 'GridBased', 'cost_penalty'),
     _between(1.0, 10.0), '1.0 ~ 10.0',
     '提高代价惩罚，让路径远离道路边缘'),
    ('local_costmap.inflation_radius',
     lambda d: _get(d, 'local_costmap', 'local_costmap', 'ros__parameters',
                    'inflation_layer', 'inflation_radius'),
     _between(0.15, 0.60), '0.15 ~ 0.60 m',
     '★ 官方默认 0.70 会把窄通道完全堵死'),    ('global_costmap.inflation_radius',
     lambda d: _get(d, 'global_costmap', 'global_costmap', 'ros__parameters',
                    'inflation_layer', 'inflation_radius'),
     _between(0.15, 0.60), '0.15 ~ 0.60 m',
     '同局部代价地图'),
    ('local_costmap.robot_radius',
     lambda d: _get(d, 'local_costmap', 'local_costmap', 'ros__parameters',
                    'robot_radius'),
     _between(0.06, 0.18), '0.06 ~ 0.18 m',
     '★★ 本场地车道只有 0.40 m 宽。robot_radius 是【致命半径】——'
     '比它更近的格子会被当作障碍。'
     '写成 TurtleBot3 的 0.22 时 0.40-2*0.22 已经为负，整条车道被堵死，'
     '规划器找不到任何路径，11 个站点全部超时。'
     '自研车底盘 20x14cm、轮距 18cm，正确值约 0.12'),
    ('global_costmap.robot_radius',
     lambda d: _get(d, 'global_costmap', 'global_costmap', 'ros__parameters',
                    'robot_radius'),
     _between(0.06, 0.18), '0.06 ~ 0.18 m',
     '同局部代价地图，必须一致'),
    ('local_costmap.cost_scaling_factor',
     lambda d: _get(d, 'local_costmap', 'local_costmap', 'ros__parameters',
                    'inflation_layer', 'cost_scaling_factor'),
     _between(2.0, 10.0), '2.0 ~ 10.0',
     '越大代价衰减越快，路径越贴障碍'),
]


def main():
    print('=' * 74)
    print('  Nav2 参数版本适配检查（基准：Nav2 Jazzy 官方参数文件）')
    print('=' * 74)
    print(f'  文件: {PARAMS}')

    if not os.path.isfile(PARAMS):
        print('  ❌ 找不到文件')
        return 1

    with open(PARAMS, encoding='utf-8') as f:
        raw = f.read()
    doc = yaml.safe_load(raw)

    n_bad = 0

    # ---- 1. 顶层键 ----
    print()
    print('─ 1. 顶层键对比 ' + '─' * 56)
    mine_top = set(doc.keys())
    missing = sorted(OFFICIAL_TOP - mine_top)
    if missing:
        print(f'  ❌ 缺少官方顶层键: {missing}')
        n_bad += 1
    else:
        print(f'  ✅ 官方 {len(OFFICIAL_TOP)} 个顶层键全部存在')

    # ---- 2. 危险参数名（Humble 专有）----
    print()
    print('─ 2. 危险参数名检查（Jazzy 不该有的 Humble 旧写法）' + '─' * 22)
    bad = find_forbidden(raw, doc)
    if bad:
        for k in bad:
            print(f'  ❌ 发现不该出现的旧参数: {k}')
        n_bad += 1
    else:
        print('  ✅ 未发现 Humble 专有的旧参数名')

    # ---- 2b. 插件分隔符 ----
    print()
    print('─ 2b. 插件类名分隔符检查（Jazzy 只认 ::）' + '─' * 33)
    slashed = find_slash_plugins(raw)
    if slashed:
        for v in slashed:
            print(f'  ❌ plugin 值用了 "/" 分隔符: {v}')
            print(f'     → 应写成: {v.replace("/", "::", 1)}')
        n_bad += 1
    else:
        n = len(re.findall(r'plugin:\s*"[^"]+"', raw))
        print(f'  ✅ {n} 个 plugin 值全部使用 "::" 分隔符')

    # ---- 3. 关键参数值 ----
    print()
    print('─ 3. 关键参数值抽查 ' + '─' * 54)
    for name, getter, judge, want_desc, why in CHECKS:
        try:
            got = getter(doc)
            ok = bool(judge(got))
        except Exception as e:                                  # noqa: BLE001
            print(f'  ❌ {name}: 读取失败 {e}')
            n_bad += 1
            continue
        if not ok:
            n_bad += 1
        mark = '✅' if ok else '❌'
        line = f'  {mark} {name} = {got!r}'
        if not ok:
            line += f'   （期望 {want_desc}）'
        print(line)
        if not ok:
            print(f'        {why}')

    # ---------- 4. AMCL 初始位姿 vs 机器人出生点 ----------
    #
    # 为什么必须交叉检查：
    #   AMCL 的 initial_pose 写在 nav2_params.yaml 里，
    #   机器人的出生点写在 smart_community_sim 的启动文件里（spawn 节点的 x/y/Y）。
    #   两处【没有任何代码关联】，改一处忘了另一处，定位从一开始就是错的：
    #     · 粒子撒在错误位置 -> RViz 里位姿飘着、规划路径对不上
    #     · 而且不会报任何错，表现为"导航莫名其妙走偏"
    #   实测踩过一次：initial_pose=(0,0,0) 而出生点是 (1.3,1.3,-1.5708)。
    print()
    print('─ 4. AMCL 初始位姿 vs 机器人出生点 ' + '─' * 37)

    src_root = os.path.dirname(os.path.dirname(os.path.dirname(PARAMS)))
    sim_launch = os.path.join(src_root, 'smart_community_sim', 'launch',
                              'smart_community.launch.py')
    if not os.path.isfile(sim_launch):
        print(f'  ⚠️ 找不到场景启动文件，跳过：{sim_launch}')
    else:
        lraw = io.open(sim_launch, encoding='utf-8').read()
        m = re.search(r"'x'\s*:\s*([-\d.]+)\s*,\s*"
                      r"'y'\s*:\s*([-\d.]+)\s*,\s*"
                      r"'z'\s*:\s*([-\d.]+)\s*,\s*"
                      r"'Y'\s*:\s*([-\d.]+)", lraw)
        if not m:
            print('  ⚠️ 没在场景启动文件里解析到 spawn 的 x/y/z/Y，跳过')
        else:
            sx, sy, sz, syaw = (float(m.group(i)) for i in range(1, 5))
            ip = _get(doc, 'amcl', 'ros__parameters', 'initial_pose') or {}
            ix = float(ip.get('x', 0.0))
            iy = float(ip.get('y', 0.0))
            iz = float(ip.get('z', 0.0))
            iyaw = float(ip.get('yaw', 0.0))

            print(f'  出生点（场景启动文件）: x={sx}  y={sy}  z={sz}  yaw={syaw}')
            print(f'  initial_pose（导航参数）: x={ix}  y={iy}  z={iz}  yaw={iyaw}')
            print(f'  （z 不比较：出生点的 z={sz} 是【投放高度】，'
                  f'AMCL 的 z={iz} 是【地面投影】，两者本就不同）')

            def _close(a, b, tol=1e-3):
                d = abs((a - b + math.pi) % (2 * math.pi) - math.pi)
                return d < tol

            # ★ 只比 x / y / yaw —— 2D AMCL 只有这三个自由度
            pairs = [('x', sx, ix), ('y', sy, iy), ('yaw', syaw, iyaw)]
            bad = [(n, a, b) for n, a, b in pairs if not _close(a, b)]
            if bad:
                for n, a, b in bad:
                    print(f'  ❌ {n}: 出生点 {a}  !=  initial_pose {b}')
                print('        AMCL 会从错误的位置开始定位，且不会有任何报错。')
                print('        修法：把 nav2_params.yaml 里 amcl.initial_pose '
                      '改成与出生点一致。')
                n_bad += len(bad)
            else:
                print('  ✅ x / y / yaw 三轴一致（2D AMCL 的全部自由度）')

    print()
    print('=' * 74)
    if n_bad:
        print(f'  ❌ 有 {n_bad} 项不符合预期')
        return 1
    print('  ✅ 全部通过（结构与官方 Jazzy 一致，关键参数符合本项目调参意图）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
