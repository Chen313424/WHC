#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
环境自检脚本 —— 在 WSL 的 Ubuntu 22.04 里运行
================================================================================
用法：
    python3 /mnt/d/project（ai/docs/scripts/check_env.py

作用：
    一次性检查 ROS 2 环境是否装齐（系统版本、ROS、仿真、导航、GUI、GPU、
    网络、Python 依赖），并给出明确的「通过 / 未通过」和修复提示。

为什么要写这个脚本：
    ROS 2 的环境由几十个软件包组成，装在哪儿、装没装全，靠记忆很容易漏。
    这个脚本把"装完了吗"变成一次可重复执行的检查，避免到联调阶段才发现
    某个包根本没装。
================================================================================
"""

import os
import shutil
import sys

OK = '  [ OK ]'
NO = '  [FAIL]'
WARN = '  [WARN]'

results = []      # (状态, 项目, 详情)


def add(status, name, detail=''):
    results.append((status, name, detail))


def exists(path):
    return os.path.exists(path)


# ==============================================================================
# 1. 系统版本
# ==============================================================================
distro_codename = ''
try:
    with open('/etc/os-release', encoding='utf-8') as f:
        info = {}
        for line in f:
            if '=' in line:
                k, v = line.rstrip('\n').split('=', 1)
                info[k] = v.strip('"')
    distro_codename = info.get('VERSION_CODENAME', '')
    pretty = info.get('PRETTY_NAME', 'unknown')
except Exception as e:                                        # noqa: BLE001
    pretty = f'读取失败: {e}'

if distro_codename == 'jammy':
    add(OK, 'Ubuntu 版本', f'{pretty}（jammy，正确）')
elif distro_codename == 'noble':
    add(NO, 'Ubuntu 版本',
        f'{pretty} —— 这是 24.04！Gazebo Classic 装不上。'
        f'请改用 Ubuntu 22.04（jammy）')
else:
    add(WARN, 'Ubuntu 版本', f'{pretty} —— 预期 jammy(22.04)')

# 架构
import platform                                             # noqa: E402
arch = platform.machine()
add(OK if arch == 'x86_64' else NO, 'CPU 架构',
    f'{arch}' + ('' if arch == 'x86_64' else ' —— 必须 x86_64'))

# ==============================================================================
# 2. ROS 2 本体
# ==============================================================================
ros_dirs = []
if exists('/opt/ros'):
    ros_dirs = sorted(os.listdir('/opt/ros'))

if 'humble' in ros_dirs:
    add(OK, 'ROS 2 安装', '/opt/ros/humble 存在')
elif ros_dirs:
    add(NO, 'ROS 2 安装',
        f'找到 /opt/ros/{",".join(ros_dirs)}，但没有 humble')
else:
    add(NO, 'ROS 2 安装', '/opt/ros 不存在 —— ROS 2 尚未安装')

ros2_bin = shutil.which('ros2')
if ros2_bin:
    add(OK, 'ros2 命令', ros2_bin)
else:
    add(NO, 'ros2 命令',
        'PATH 里找不到 ros2 —— 需要 source /opt/ros/humble/setup.bash')

distro = os.environ.get('ROS_DISTRO', '')
if distro == 'humble':
    add(OK, 'ROS_DISTRO', 'humble')
elif distro:
    add(WARN, 'ROS_DISTRO', f'{distro}（期望 humble）')
else:
    add(WARN, 'ROS_DISTRO', '未设置 —— 本终端没有 source 环境，'
                            '执行 source /opt/ros/humble/setup.bash')

# colcon 构建工具（ros-humble-desktop 不包含它，很容易漏装）
colcon_bin = shutil.which('colcon')
if colcon_bin:
    add(OK, 'colcon 构建工具', colcon_bin)
else:
    add(NO, 'colcon 构建工具',
        '缺！ros-humble-desktop 不包含它，没有它无法编译工程。'
        '执行: sudo apt install -y python3-colcon-common-extensions')

# ==============================================================================
# 3. 关键软件包（直接看 /opt/ros/humble/share 下的目录，最可靠）
# ==============================================================================
SHARE = '/opt/ros/humble/share'

PKGS = [
    # (包名, 用途, 对应 apt 包)
    ('gazebo_ros',                'Gazebo 仿真接口',   'ros-humble-gazebo-ros-pkgs'),
    ('gazebo_plugins',            'Gazebo 传感器插件', 'ros-humble-gazebo-ros-pkgs'),
    ('nav2_bringup',              'Nav2 启动文件',     'ros-humble-nav2-bringup'),
    ('nav2_amcl',                 'AMCL 定位',         'ros-humble-navigation2'),
    ('nav2_smac_planner',         '全局规划器',        'ros-humble-nav2-smac-planner'),
    ('nav2_regulated_pure_pursuit_controller',
                                  '局部控制器 RPP',    'ros-humble-nav2-regulated-pure-pursuit-controller'),
    ('nav2_bt_navigator',         '行为树导航器',      'ros-humble-nav2-bt-navigator'),
    ('nav2_map_server',           '地图服务器',        'ros-humble-nav2-map-server'),
    ('slam_toolbox',              'SLAM 建图',         'ros-humble-slam-toolbox'),
    ('turtlebot3_gazebo',         'TurtleBot3 仿真',   'ros-humble-turtlebot3-simulations'),
    ('turtlebot3_description',    'TurtleBot3 模型',   'ros-humble-turtlebot3'),
    ('turtlebot3_teleop',         '键盘遥控',          'ros-humble-turtlebot3-teleop'),
    ('rviz2',                     '可视化',            'ros-humble-rviz2'),
]

missing = []
if not exists(SHARE):
    add(NO, '软件包检查', f'{SHARE} 不存在，无法检查')
else:
    for pkg, purpose, apt in PKGS:
        if exists(os.path.join(SHARE, pkg)):
            add(OK, f'包 {pkg}', purpose)
        else:
            add(NO, f'包 {pkg}', f'{purpose} —— 缺！执行: sudo apt install -y {apt}')
            missing.append(apt)

# ==============================================================================
# 4. 图形界面 (WSLg)
# ==============================================================================
disp = os.environ.get('DISPLAY', '')
wayland = os.environ.get('WAYLAND_DISPLAY', '')
if disp or wayland:
    add(OK, '图形显示', f'DISPLAY={disp}  WAYLAND_DISPLAY={wayland}')
else:
    add(NO, '图形显示',
        'DISPLAY / WAYLAND_DISPLAY 均未设置 —— WSLg 没工作，'
        'Gazebo 和 RViz 窗口弹不出来。请在 Windows 里执行 wsl --update')

# GPU 直通
if exists('/dev/dxg'):
    add(OK, 'GPU 直通', '/dev/dxg 存在（有硬件加速）')
else:
    add(WARN, 'GPU 直通',
        '/dev/dxg 不存在 —— 只能软件渲染，Gazebo 会比较卡。'
        '可尝试 export LIBGL_ALWAYS_SOFTWARE=1')

# X11 socket
if exists('/tmp/.X11-unix'):
    add(OK, 'X11 socket', '/tmp/.X11-unix 存在')
else:
    add(WARN, 'X11 socket', '/tmp/.X11-unix 不存在（Wayland 模式下正常）')

# ==============================================================================
# 5. 机器人型号环境变量
# ==============================================================================
tbm = os.environ.get('TURTLEBOT3_MODEL', '')
if tbm in ('waffle', 'burger', 'waffle_pi'):
    add(OK, 'TURTLEBOT3_MODEL', tbm)
elif tbm:
    add(WARN, 'TURTLEBOT3_MODEL', f'{tbm}（建议 waffle：带 360° 雷达 + 相机）')
else:
    add(NO, 'TURTLEBOT3_MODEL', '未设置 —— 执行: '
        'echo "export TURTLEBOT3_MODEL=waffle" >> ~/.bashrc && source ~/.bashrc')

# ==============================================================================
# 6. Python 依赖
# ==============================================================================
try:
    import yaml                                               # noqa: F401
    add(OK, 'python3-yaml', '已安装（巡检节点需要）')
except ImportError:
    add(NO, 'python3-yaml', '缺！执行: sudo apt install -y python3-yaml')

# ==============================================================================
# 7. Windows 侧路径可达性
# ==============================================================================
CANDIDATES = [
    '/mnt/d/project（ai/ros2_ws',
    '/mnt/c/Users',
]
for p in CANDIDATES:
    add(OK if exists(p) else WARN,
        '路径可达' if exists(p) else '路径不可达',
        p)

# ==============================================================================
# 输出
# ==============================================================================
print()
print('=' * 74)
print('  智慧社区 · 导航组 · 环境自检报告')
print('=' * 74)

n_ok = n_no = n_warn = 0
for status, name, detail in results:
    if status == OK:
        n_ok += 1
    elif status == NO:
        n_no += 1
    else:
        n_warn += 1
    print(f'{status} {name}')
    if detail:
        print(f'         {detail}')

print('=' * 74)
print(f'  通过 {n_ok} 项，失败 {n_no} 项，警告 {n_warn} 项')
print('=' * 74)

if missing:
    uniq = sorted(set(missing))
    print()
    print('  ▶ 直接执行下面这条命令补齐缺失的包：')
    print()
    print('    sudo apt install -y \\')
    for i, p in enumerate(uniq):
        tail = '' if i == len(uniq) - 1 else ' \\'
        print(f'      {p}{tail}')
    print()

if n_no == 0:
    print('  ✅ 环境检查全部通过，可以进入下一步：')
    print('     python3 /mnt/d/project（ai/docs/scripts/deploy_ws.py')
    sys.exit(0)

print('  ❌ 还有未通过项，请按上面的提示修复后重新运行本脚本。')
sys.exit(1)
