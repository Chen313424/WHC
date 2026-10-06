#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工程部署脚本 —— 把 Windows 上的工程复制到 WSL 的 Linux 文件系统并编译
================================================================================
用法（在 WSL 的 Ubuntu 里运行）：

    # 先看看它会做什么（不实际改动任何文件）
    python3 /mnt/d/project（ai/docs/scripts/deploy_ws.py --dry-run

    # 正式部署 + 编译
    python3 /mnt/d/project（ai/docs/scripts/deploy_ws.py

    # 只复制不编译
    python3 /mnt/d/project（ai/docs/scripts/deploy_ws.py --skip-build

为什么要复制到 Linux 文件系统，而不是直接在 /mnt/d 下编译：
    WSL2 访问 Windows 文件系统（/mnt/...）是通过 9P 协议转发的，
    I/O 性能只有原生文件系统的十分之一左右，而且不完整支持 Linux 的
    文件权限与符号链接。ROS 2 的 colcon build 会产生大量小文件读写，
    放在 /mnt/d 下编译可能从 1 分钟变成 20 分钟，甚至直接失败。
================================================================================
"""

import argparse
import os
import shutil
import subprocess
import sys

# WSL 里看到的 Windows 路径。注意你的目录名里有全角括号，路径要用引号包起来。
SOURCE_DEFAULT = '/mnt/d/project（ai/ros2_ws/src'
DEST_DEFAULT = os.path.expanduser('~/smart_community_ws')


def detect_ros_distro():
    """自动识别本机装的 ROS 2 发行版，而不是写死

    【为什么要这样】
      本项目最初在 Ubuntu 22.04 + ROS 2 Humble 上开发，
      后来为了使用建模组交付的社区场景（ROS 2 Jazzy + Gazebo Harmonic），
      整个栈迁移到了 Ubuntu 24.04 + Jazzy。

      如果把发行版写死，换环境后脚本要么报错、要么更糟——
      **source 了错误的 setup.bash 却不报错**，后面所有命令都作用在错误的环境上。
      自动识别能避免这类静默错误。
    """
    # 1) 环境变量最可靠
    env = os.environ.get('ROS_DISTRO')
    if env and os.path.isdir(os.path.join('/opt/ros', env)):
        return env
    # 2) /opt/ros 下只有一个发行版时直接用它
    base = '/opt/ros'
    if os.path.isdir(base):
        cands = sorted(d for d in os.listdir(base)
                       if os.path.isdir(os.path.join(base, d)))
        if len(cands) == 1:
            return cands[0]
        if cands:
            # 装了多个（例如 Humble 和 Jazzy 并存）：优先 jazzy，其次 humble
            for pref in ('jazzy', 'iron', 'humble'):
                if pref in cands:
                    return pref
            return cands[-1]
    return None


ROS_DISTRO = detect_ros_distro()
ROS_SETUP = f'/opt/ros/{ROS_DISTRO}/setup.bash' if ROS_DISTRO else None


def banner(text):
    print()
    print('=' * 74)
    print(f'  {text}')
    print('=' * 74)


def find_packages(src):
    """找出 src 下的所有 ROS 包（含 package.xml 的目录）"""
    pkgs = []
    if not os.path.isdir(src):
        return pkgs
    for name in sorted(os.listdir(src)):
        p = os.path.join(src, name)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, 'package.xml')):
            pkgs.append((name, p))
    return pkgs


def main():
    ap = argparse.ArgumentParser(description='部署 ROS 2 工作空间到 Linux 文件系统并编译')
    ap.add_argument('--source', default=SOURCE_DEFAULT,
                    help=f'源 src 目录（默认 {SOURCE_DEFAULT}）')
    ap.add_argument('--dest', default=DEST_DEFAULT,
                    help=f'目标工作空间（默认 {DEST_DEFAULT}）')
    ap.add_argument('--dry-run', action='store_true', help='只显示计划，不实际操作')
    ap.add_argument('--skip-build', action='store_true', help='只复制，不执行 colcon build')
    args = ap.parse_args()

    banner('智慧社区 · 导航组 · 工程部署')

    # ---------------------------------------------------------------- 0. 环境
    if not ROS_DISTRO:
        print('  ❌ 找不到 ROS 2 安装（/opt/ros 下没有发行版）')
        print()
        print('  请先安装 ROS 2。本项目的目标是 Ubuntu 24.04 + ROS 2 Jazzy')
        print('  （建模组的社区场景是 Jazzy + Gazebo Harmonic 的）。')
        return 1
    print(f'  ROS 2  : {ROS_DISTRO}  ({ROS_SETUP})')
    if not os.path.isfile(ROS_SETUP):
        print(f'  ❌ 找不到 {ROS_SETUP}')
        return 1

    # ---------------------------------------------------------------- 1. 检查源
    print(f'  源目录 : {args.source}')
    print(f'  目标   : {args.dest}')
    print(f'  模式   : {"仅演练(dry-run)" if args.dry_run else "实际执行"}')
    print()

    if not os.path.isdir(args.source):
        print(f'  ❌ 源目录不存在：{args.source}')
        print()
        print('  可能原因：')
        print('   1) Windows 上的路径不是这个 —— 用 --source 指定正确路径')
        print('   2) WSL 没挂载 D 盘 —— 执行 ls /mnt/d 看看')
        print('   3) 目录名里的全角括号打错了')
        return 1

    pkgs = find_packages(args.source)
    if not pkgs:
        print(f'  ❌ 在 {args.source} 下没找到任何含 package.xml 的 ROS 包')
        return 1

    print(f'  找到 {len(pkgs)} 个 ROS 包：')
    for name, _ in pkgs:
        print(f'    - {name}')
    print()

    # ---------------------------------------------------------------- 2. 检查 ROS
    if not args.skip_build and not args.dry_run:
        if not os.path.isfile(ROS_SETUP):
            print(f'  ❌ 找不到 {ROS_SETUP} —— ROS 2 还没装好')
            print('     请先按 docs/01-环境搭建指南.md 安装 ROS 2 Humble')
            return 1

    # ---------------------------------------------------------------- 3. 复制
    dest_src = os.path.join(args.dest, 'src')
    print(f'  ▶ 复制到 {dest_src}')

    if args.dry_run:
        for name, p in pkgs:
            print(f'      [演练] {p}  ->  {os.path.join(dest_src, name)}')
    else:
        os.makedirs(dest_src, exist_ok=True)
        for name, p in pkgs:
            target = os.path.join(dest_src, name)
            if os.path.isdir(target):
                shutil.rmtree(target)
            shutil.copytree(p, target)
            print(f'      ✓ {name}')

    # ---------------------------------------------------------------- 4. 编译
    if args.skip_build:
        print()
        print('  (--skip-build：跳过编译)')
    elif args.dry_run:
        print()
        print('  [演练] 将执行: colcon build --symlink-install')
    else:
        banner('开始编译 (colcon build)')
        cmd = (f'source {ROS_SETUP} && '
               f'cd {shlex_quote(args.dest)} && '
               f'colcon build --symlink-install --event-handlers console_direct+')
        print(f'  $ {cmd}')
        print()
        proc = subprocess.run(['bash', '-lc', cmd])
        if proc.returncode != 0:
            print()
            print(f'  ❌ 编译失败（退出码 {proc.returncode}）')
            print('     把上面的报错贴给我，我来定位')
            return proc.returncode

        setup_file = os.path.join(args.dest, 'install', 'setup.bash')
        if os.path.isfile(setup_file):
            print()
            print(f'  ✅ 编译成功，生成 {setup_file}')
        else:
            print()
            print('  ⚠️ 编译命令返回成功，但没找到 install/setup.bash，请检查输出')
            return 1

    # ---------------------------------------------------------------- 5. 下一步
    banner('下一步')
    print(f'''  每次打开新终端，先执行：

    source /opt/ros/{ROS_DISTRO}/setup.bash
    source {args.dest}/install/setup.bash

  然后启动社区场景与建图：

    # 终端 1 —— 社区场景（Gazebo Harmonic，含建模组的完整社区）
    ros2 launch community_nav sim.launch.py

    # 终端 2 —— 建图
    ros2 launch community_nav mapping.launch.py

    # 终端 3 —— 确认地图在发布（应该是 1 Hz 左右）
    ros2 topic hz /map

  启动导航（默认不自动巡检，先在 RViz 里用 2D Goal Pose 手动点目标测）：

    ros2 launch community_nav navigation.launch.py

  正式演示时一键启动并自动巡检：

    ros2 launch community_nav navigation.launch.py autostart_patrol:=true

  ⚠️ 启动仿真前如果报"检测到已经有 Gazebo 在运行"：
     pkill -9 -f "gz sim" ; pkill -9 -f gzserver
''')
    return 0


def shlex_quote(s):
    """简单安全的路径引用（等价 shlex.quote，避免额外 import）"""
    return "'" + s.replace("'", "'\"'\"'") + "'"


if __name__ == '__main__':
    sys.exit(main())
