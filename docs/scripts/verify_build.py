#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
编译后验证脚本 —— 在 colcon build 成功之后运行
================================================================================
用法（在 WSL 的 Ubuntu 里运行）：

    python3 "/mnt/d/project（ai/docs/scripts/verify_build.py"

【它解决什么问题】
    colcon build 成功 ≠ 工程能用。常见问题：
      · launch 文件里有 Python 错误 —— 直到你真正启动时才暴露
      · 依赖的包没装 —— get_package_share_directory 报错
      · YAML 配置没被正确安装到 share 目录
      · 节点没注册成功，ros2 run 找不到

    这些问题如果等到"启动 Gazebo 建图"那一步才暴露，排查成本高得多。

    本脚本用 `ros2 launch <包> <文件> --show-args` 来**解析 launch 文件但不执行**，
    能在几秒内把上面这些错误全部提前抓出来。

【为什么 --show-args 这个技巧好用】
    它会完整执行 launch 文件的 Python 代码（包括所有 import 和
    get_package_share_directory 调用），把参数列出来，然后退出。
    也就是说：任何语法错误、导入错误、依赖缺失，都会在这一步暴露，
    但不会真的启动任何节点、不会占用资源。
================================================================================
"""

import os
import subprocess
import sys

def detect_ros_distro():
    """自动识别 ROS 2 发行版（本项目从 Humble 迁到了 Jazzy，不能写死）"""
    env = os.environ.get('ROS_DISTRO')
    if env and os.path.isdir(os.path.join('/opt/ros', env)):
        return env
    base = '/opt/ros'
    if os.path.isdir(base):
        cands = sorted(d for d in os.listdir(base)
                       if os.path.isdir(os.path.join(base, d)))
        if cands:
            for pref in ('jazzy', 'iron', 'humble'):
                if pref in cands:
                    return pref
            return cands[-1]
    return None


ROS_DISTRO = detect_ros_distro()
ROS_SETUP = f'/opt/ros/{ROS_DISTRO}/setup.bash' if ROS_DISTRO else None
WS = os.path.expanduser('~/smart_community_ws')
WS_SETUP = os.path.join(WS, 'install', 'setup.bash')

# community_nav 是我们自己的导航包，
# smart_community_sim 是仿真组交付的社区场景包（一并纳入工作空间）
PACKAGES = ['community_nav', 'community_patrol', 'smart_community_sim']
LAUNCH_FILES = ['sim.launch.py', 'mapping.launch.py', 'mapping_minimal.launch.py',
                'navigation.launch.py']

results = []


def record(ok, name, detail=''):
    results.append((ok, name, detail))


def ros(cmd, timeout=60):
    """在已 source 好环境的 shell 里执行命令"""
    full = (f'source {ROS_SETUP} && source {WS_SETUP} && {cmd}')
    return subprocess.run(['bash', '-lc', full],
                          capture_output=True, text=True, timeout=timeout)


print('=' * 74)
print('  智慧社区 · 导航组 · 编译后验证')
print('=' * 74)

# ---------------------------------------------------------------- 0. 前置
if not os.path.isfile(ROS_SETUP):
    print(f'  ❌ 找不到 {ROS_SETUP}，ROS 2 没装好')
    sys.exit(1)
if not os.path.isfile(WS_SETUP):
    print(f'  ❌ 找不到 {WS_SETUP}')
    print(f'     说明还没编译。先运行：')
    print(f'     python3 "/mnt/d/project（ai/docs/scripts/deploy_ws.py"')
    sys.exit(1)

print(f'  工作空间: {WS}')
print()

# ---------------------------------------------------------------- 1. 包是否注册
print('─ 1. 包注册检查 ' + '─' * 56)
for pkg in PACKAGES:
    p = ros(f'ros2 pkg prefix {pkg}')
    ok = p.returncode == 0
    record(ok, f'ros2 能找到包 {pkg}',
           p.stdout.strip() if ok else (p.stderr or '').strip()[:200])

# ---------------------------------------------------------------- 2. share 目录内容
print('─ 2. 安装文件检查 ' + '─' * 54)
EXPECT = [
    ('community_nav', 'config/nav2_params.yaml'),
    ('community_nav', 'config/slam_toolbox_params.yaml'),
    ('community_nav', 'config/community_waypoints.yaml'),
    ('community_nav', 'launch/sim.launch.py'),
    ('community_nav', 'launch/mapping.launch.py'),
    ('community_nav', 'launch/navigation.launch.py'),
]
for pkg, rel in EXPECT:
    p = ros(f'ros2 pkg prefix {pkg}')
    if p.returncode != 0:
        record(False, f'{pkg}/{rel}', '包没找到，跳过')
        continue
    share = os.path.join(p.stdout.strip(), 'share', pkg)
    full = os.path.join(share, rel)
    record(os.path.isfile(full), f'{pkg}/{rel}',
           full if not os.path.isfile(full) else '')

# ---------------------------------------------------------------- 3. 可执行文件
print('─ 3. 节点可执行文件检查 ' + '─' * 50)
p = ros('ros2 pkg executables community_patrol')
ok = p.returncode == 0 and 'patrol_node' in (p.stdout or '')
record(ok, 'community_patrol 提供 patrol_node',
       (p.stdout or p.stderr or '').strip()[:200])

# ---------------------------------------------------------------- 4. ★ launch 解析
print('─ 4. launch 文件解析检查（关键）' + '─' * 41)
print('     用 --show-args 完整解析但不启动任何节点')
print()
for lf in LAUNCH_FILES:
    p = ros(f'ros2 launch community_nav {lf} --show-args', timeout=90)
    out = (p.stdout or '') + (p.stderr or '')
    ok = p.returncode == 0 and 'arguments' in out.lower()
    if ok:
        # 提取参数名
        args = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("'") and ':' in line:
                args.append(line.split(':')[0].strip("'"))
        detail = '参数: ' + ', '.join(args) if args else '解析成功'
    else:
        detail = out.strip()[-400:]
    record(ok, f'解析 {lf}', detail)

# ---------------------------------------------------------------- 输出
print()
print('=' * 74)
n_ok = sum(1 for ok, _, _ in results if ok)
for ok, name, detail in results:
    print(f'  {"[ OK ]" if ok else "[FAIL]"} {name}')
    if detail:
        for line in detail.splitlines():
            print(f'          {line}')
print('=' * 74)
print(f'  {n_ok}/{len(results)} 通过')
print('=' * 74)

bad = [r for r in results if not r[0]]
if not bad:
    print('''
  ✅ 全部通过。工程已经可以运行了。下一步：

     # 终端 1 —— 启动 Gazebo 仿真
     source /opt/ros/humble/setup.bash
     source ~/smart_community_ws/install/setup.bash
     export TURTLEBOT3_MODEL=waffle
     ros2 launch community_nav sim.launch.py

     # 终端 2 —— 键盘遥控（按方向键，看 Gazebo 里的车是否跟着动）
     ros2 run turtlebot3_teleop teleop_keyboard

     # 终端 3 —— 确认激光雷达有数据
     ros2 topic echo /scan --once
''')
    sys.exit(0)

print()
print('  ❌ 有项目未通过。请把上面完整输出贴给导航组。')
sys.exit(1)
