#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROS 2 Humble + Gazebo Classic + TurtleBot3 + Nav2 一键安装脚本
================================================================================
用法（在 WSL 的 Ubuntu 22.04 终端里运行）：

    python3 "/mnt/d/project（ai/docs/scripts/install_ros2.py"

    # 只检查当前状态，不做任何安装
    python3 "/mnt/d/project（ai/docs/scripts/install_ros2.py" --check

【这个脚本解决什么问题】
    手工装 ROS 2 需要二十多条命令，中间任何一条出错都很难定位。
    本脚本把整个过程拆成 6 个可验证的步骤，每步都会：
      · 先检查是否已经完成（可重复运行，不会重复装）
      · 执行并实时显示输出
      · 执行后立即验证结果
      · 失败时明确指出是哪一步、下一步该做什么

    总下载量约 2–3 GB，视网速需要 20–60 分钟。

【为什么用 Python 而不是 bash】
    Python 能把错误处理、步骤校验、幂等判断写清楚，
    出错时给出可操作的提示，而不是 bash 那种"默默退出"。
================================================================================
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

# ------------------------------------------------------------------------------
#  配置
# ------------------------------------------------------------------------------
# ★ 用 wsl --import 装出来的 Ubuntu 默认以 root 登录，此时没有 sudo（也不需要）。
#   这里判断一下身份，后面所有特权命令都据此决定要不要加 sudo。
IS_ROOT = hasattr(os, 'geteuid') and os.geteuid() == 0

ROS_DISTRO = 'humble'
ROS_SETUP = f'/opt/ros/{ROS_DISTRO}/setup.bash'
ROS_SHARE = f'/opt/ros/{ROS_DISTRO}/share'

MIRROR_KEY = 'https://mirrors.tuna.tsinghua.edu.cn/rosdistro/ros.key'
FALLBACK_KEY = 'https://raw.githubusercontent.com/ros/rosdistro/master/ros.key'
KEYRING = '/usr/share/keyrings/ros-archive-keyring.gpg'
SOURCES_FILE = '/etc/apt/sources.list.d/ros2.list'
MIRROR_URL = 'https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu'

ROS_PACKAGES = [
    f'ros-{ROS_DISTRO}-desktop',
]

EXTRA_PACKAGES = [
    # 仿真
    f'ros-{ROS_DISTRO}-gazebo-ros-pkgs',
    f'ros-{ROS_DISTRO}-gazebo-ros',
    # 导航
    f'ros-{ROS_DISTRO}-navigation2',
    f'ros-{ROS_DISTRO}-nav2-bringup',
    f'ros-{ROS_DISTRO}-nav2-smac-planner',
    f'ros-{ROS_DISTRO}-nav2-regulated-pure-pursuit-controller',
    # 建图
    f'ros-{ROS_DISTRO}-slam-toolbox',
    # 机器人
    f'ros-{ROS_DISTRO}-turtlebot3',
    f'ros-{ROS_DISTRO}-turtlebot3-msgs',
    f'ros-{ROS_DISTRO}-turtlebot3-simulations',
    f'ros-{ROS_DISTRO}-turtlebot3-teleop',
    f'ros-{ROS_DISTRO}-turtlebot3-navigation2',
    f'ros-{ROS_DISTRO}-turtlebot3-cartographer',
    # 工具
    'python3-yaml',
    'x11-apps',
    # ★ 中文字体：没有它 RViz/Gazebo 里的中文会显示成方块 □□□，
    #   而答辩视频要求画面文字清晰可辨，所以必须装
    'fonts-noto-cjk',
    # ★★ colcon 构建工具 ★★
    #   ros-humble-desktop **不包含** colcon！它不是 ROS 的一部分，
    #   而是独立的构建工具包。没有它就无法编译我们的工程，
    #   报错是 "colcon: command not found"。
    #   这是个非常容易漏掉的依赖（本项目的部署脚本第一次就跑失败了）。
    'python3-colcon-common-extensions',
]

BASHRC_LINES = [
    f'source /opt/ros/{ROS_DISTRO}/setup.bash',
    'export TURTLEBOT3_MODEL=waffle',
]

# ------------------------------------------------------------------------------
#  输出工具
# ------------------------------------------------------------------------------
BAR = '=' * 74


def banner(text):
    print()
    print(BAR)
    print(f'  {text}')
    print(BAR)
    sys.stdout.flush()


def step(n, total, title):
    print()
    print(f'┌─ 步骤 {n}/{total} ─ {title}')
    sys.stdout.flush()


def ok(msg):
    print(f'└─ ✅ {msg}')
    sys.stdout.flush()


def fail(msg):
    print(f'└─ ❌ {msg}')
    sys.stdout.flush()


def info(msg):
    print(f'   {msg}')
    sys.stdout.flush()


def run(cmd, shell=False, check=True, quiet=False):
    """执行命令，实时回显输出"""
    if not quiet:
        info('$ ' + (cmd if shell else ' '.join(cmd)))
    if shell:
        proc = subprocess.run(['bash', '-c', cmd])
    else:
        proc = subprocess.run(cmd)
    if check and proc.returncode != 0:
        raise RuntimeError(f'命令失败（退出码 {proc.returncode}）：'
                           + (cmd if shell else ' '.join(cmd)))
    return proc.returncode


def sudo(cmd, shell=False, check=True, quiet=False):
    """以特权身份执行命令。

    以 root 运行时直接执行；否则加 sudo 前缀。
    这样脚本在 root 容器（wsl --import 装出来的）和普通用户环境下都能工作。
    """
    if IS_ROOT:
        return run(cmd, shell=shell, check=check, quiet=quiet)
    if shell:
        return run(['sudo', 'bash', '-c', cmd], check=check, quiet=quiet)
    return run(['sudo'] + cmd, check=check, quiet=quiet)


def have(cmd):
    return shutil.which(cmd) is not None


# ------------------------------------------------------------------------------
#  步骤 0：环境检查
# ------------------------------------------------------------------------------
def check_os():
    if not os.path.isfile('/etc/os-release'):
        return None, 'not-linux'
    info_map = {}
    with open('/etc/os-release', encoding='utf-8') as f:
        for line in f:
            if '=' in line:
                k, v = line.rstrip('\n').split('=', 1)
                info_map[k] = v.strip('"')
    return info_map.get('VERSION_CODENAME', ''), info_map.get('PRETTY_NAME', '')


# ------------------------------------------------------------------------------
#  主流程
# ------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description='一键安装 ROS 2 Humble 仿真导航环境')
    ap.add_argument('--check', action='store_true', help='只检查状态，不安装')
    ap.add_argument('--yes', '-y', action='store_true', help='跳过确认直接开始')
    args = ap.parse_args()

    TOTAL = 6

    banner('智慧社区 · 导航组 · ROS 2 Humble 一键安装')

    # ---------------------------------------------------------------- 步骤 1
    step(1, TOTAL, '环境检查')
    codename, pretty = check_os()

    if codename is None:
        fail('这不是 Linux 环境。本脚本必须在 WSL 的 Ubuntu 终端里运行。')
        return 1

    info(f'系统      : {pretty}')
    info(f'版本代号  : {codename}')

    if codename != 'jammy':
        fail(f'版本不对！需要 Ubuntu 22.04（代号 jammy），当前是 {codename}。')
        info('')
        info('Ubuntu 24.04 装不了 Gazebo Classic，而官方培训 PPT 教的正是 Gazebo Classic，')
        info('所以必须用 22.04。请参考 docs/01-环境搭建指南.md 装一个 Ubuntu 22.04。')
        return 1

    import platform
    info(f'架构      : {platform.machine()}')
    info(f'CPU 核心  : {os.cpu_count()}')
    info(f'运行身份  : {"root（无需 sudo）" if IS_ROOT else "普通用户（将使用 sudo）"}')

    st = os.statvfs('/')
    free_gb = st.f_bavail * st.f_frsize / (1024 ** 3)
    info(f'可用空间  : {free_gb:.1f} GB')
    if free_gb < 15:
        fail(f'磁盘空间不足（需要至少 15 GB，当前 {free_gb:.1f} GB）')
        return 1

    ros_installed = os.path.isfile(ROS_SETUP)
    info(f'ROS 2 状态: {"已安装" if ros_installed else "未安装"}')
    ok('环境检查通过')

    if args.check:
        banner('--check 模式，仅检查，不执行安装')
        return 0

    # ---------------------------------------------------------------- 步骤 2
    step(2, TOTAL, '配置 ROS 2 软件源（清华镜像）')

    configure_source = True
    if os.path.isfile(SOURCES_FILE):
        with open(SOURCES_FILE, encoding='utf-8') as f:
            content = f.read()
        if MIRROR_URL in content and codename in content:
            info('软件源已配置且正确，跳过')
            configure_source = False

    if configure_source:
        if IS_ROOT:
            info('当前以 root 身份运行（wsl --import 安装的默认情况），无需 sudo')
        else:
            if not have('sudo'):
                fail('当前不是 root，但系统里没有 sudo 命令。')
                info('解决办法二选一：')
                info('  1) 用 root 身份运行本脚本： sudo python3 <本脚本>')
                info('  2) 先装 sudo： apt-get update && apt-get install -y sudo')
                return 1
            info('请求 sudo 权限（请输入你的 Ubuntu 密码，输入时不显示）')
            if run(['sudo', '-v'], check=False, quiet=True) != 0:
                fail('sudo 授权失败')
                return 1

        # 先更新索引并装基础工具：
        #   curl  → 下载 GPG 密钥
        #   gnupg2 → gpg --dearmor 转换密钥格式
        #   software-properties-common → 提供 add-apt-repository
        # 全新安装的 WSL 里这些可能都没有，必须先补齐，否则后面必然失败。
        info('更新软件包列表…')
        sudo(['apt-get', 'update'])

        info('安装基础工具（curl / gnupg2 / software-properties-common）…')
        sudo(['apt-get', 'install', '-y', 'curl', 'gnupg2', 'lsb-release',
              'software-properties-common', 'ca-certificates'])

        # 启用 universe 仓库（Gazebo Classic 11 在其中）
        info('启用 universe 仓库…')
        sudo(['add-apt-repository', 'universe', '-y'], check=False, quiet=True)

        # GPG 密钥
        info('导入 ROS 2 GPG 密钥…')
        key_ok = False
        for url in (MIRROR_KEY, FALLBACK_KEY):
            sudo(f'curl -sSL {url} | gpg --dearmor -o {KEYRING}',
                 shell=True, check=False, quiet=True)
            if os.path.isfile(KEYRING) and os.path.getsize(KEYRING) > 0:
                info(f'密钥导入成功（来源 {url.split("/")[2]}），'
                     f'大小 {os.path.getsize(KEYRING)} 字节')
                key_ok = True
                break
            info('该来源失败，尝试下一个…')

        if not key_ok:
            fail('GPG 密钥导入失败。检查网络后重试，或手动执行：')
            info(f'  curl -sSL {MIRROR_KEY} | sudo gpg --dearmor -o {KEYRING}')
            return 1

        # 写软件源（先写临时文件再用 sudo 移动，避免 shell 引号问题）
        tmp = '/tmp/ros2.list'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(f'deb [arch=amd64 signed-by={KEYRING}] '
                    f'{MIRROR_URL} {codename} main\n')
        sudo(['mv', tmp, SOURCES_FILE])
        info(f'软件源已写入 {SOURCES_FILE}')

    # 更新索引。这里不用 check=True：apt 失败时我们想给出更有针对性的提示，
    # 而不是抛一个通用的"命令失败"异常。
    sudo(['apt-get', 'update'], check=False)

    with open(SOURCES_FILE, encoding='utf-8') as f:
        info('当前软件源：' + f.read().strip())

    # ★★ 关键验证：确认软件源真的可用 —— 能不能查到 ros-humble-desktop 这个包。
    #    如果 GPG 密钥不对，apt update 只会打印一行 NO_PUBKEY（很容易被忽略），
    #    真正的失败会推迟到安装时才暴露，那时报错信息非常难懂。
    #    在这里提前截住，给出可照做的排查步骤。
    probe = subprocess.run(['apt-cache', 'policy', f'ros-{ROS_DISTRO}-desktop'],
                           capture_output=True, text=True)
    out = probe.stdout or ''
    if not out.strip() or 'Candidate: (none)' in out or 'Unable to locate' in out:
        fail(f'配置软件源后仍查不到 ros-{ROS_DISTRO}-desktop，说明源或密钥有问题。')
        info('')
        info('最常见原因：GPG 密钥不正确（apt update 时会打印 NO_PUBKEY）')
        info('')
        info('手动排查：')
        info('  1) sudo apt-get update          # 看有没有 NO_PUBKEY 报错')
        info(f'  2) ls -l {KEYRING}             # 密钥文件大小应该在 1–3 KB')
        info('  3) 重新导入密钥：')
        info(f'     curl -sSL {MIRROR_KEY} | sudo gpg --dearmor -o {KEYRING}')
        info(f'  4) 若仍然不行，改用官方源：编辑 {SOURCES_FILE}，')
        info(f'     把 {MIRROR_URL} 换成 http://packages.ros.org/ros2/ubuntu')
        return 1

    info(f'软件源验证通过（能查到 ros-{ROS_DISTRO}-desktop）')
    ok('软件源配置完成')

    # ---------------------------------------------------------------- 步骤 3
    step(3, TOTAL, '安装 ROS 2 Humble Desktop')

    if ros_installed:
        info(f'{ROS_SETUP} 已存在，跳过')
    else:
        info('下载量约 2–3 GB，请耐心等待（这一步最慢）')
        info('提示：屏幕上会不停滚动 Get: / Unpacking / Setting up 之类的行，这是正常的。')
        t0 = time.time()
        # ★ 这里刻意不加 --no-install-recommends：
        #   少装几个依赖可能导致 RViz 或某个仿真组件缺失，
        #   而新手很难定位"为什么 RViz 起不来"。
        #   磁盘有近 1TB，多装一点完全不是问题。
        rc = sudo(['apt-get', 'install', '-y'] + ROS_PACKAGES, check=False)
        info(f'耗时 {time.time() - t0:.0f} 秒')

        if rc != 0:
            fail(f'ROS 2 安装未成功（apt 返回 {rc}）')
            info('')
            info('常见原因与处理办法：')
            info('  1) 网络中途断了 —— 直接重新运行本脚本，')
            info('     已经下载好的包不会重复下载，会从断点继续。')
            info('  2) 磁盘空间不足 —— 执行 df -h / 查看。')
            info('  3) 软件源不可用 —— 执行 sudo apt-get update 看具体报错。')
            info('  4) 上面滚动的内容里有 E: 开头的行 —— 把那几行贴出来。')
            info('')
            info('重新运行的命令：')
            info('  python3 "/mnt/d/project（ai/docs/scripts/install_ros2.py"')
            return 1

    if not os.path.isfile(ROS_SETUP):
        fail(f'安装后仍找不到 {ROS_SETUP}')
        return 1
    ok('ROS 2 Humble 安装完成')

    # ---------------------------------------------------------------- 步骤 4
    step(4, TOTAL, '安装 Gazebo + TurtleBot3 + Nav2 + SLAM')

    info('将安装：')
    for p in EXTRA_PACKAGES:
        info(f'  · {p}')
    t0 = time.time()

    # 同样不加 --no-install-recommends，避免漏掉 Gazebo 模型等推荐包
    #
    # ★★ 重要：apt 是"全有或全无"的 —— 只要列表里有【一个】包名不存在，
    #    整条命令就会失败，结果一个包都装不上，而且报错信息夹在几千行输出里
    #    非常难找。所以这里先整批装；失败就自动改为逐个安装，把问题包揪出来。
    rc = sudo(['apt-get', 'install', '-y'] + EXTRA_PACKAGES, check=False)

    if rc != 0:
        info('')
        info('整批安装未成功，改为逐个安装以定位问题包（已完成的不受影响）…')
        failed = []
        for pkg in EXTRA_PACKAGES:
            if sudo(['apt-get', 'install', '-y', pkg], check=False) != 0:
                failed.append(pkg)
        if failed:
            info('')
            info('以下包安装失败（很可能包名在当前版本里不存在）：')
            for f in failed:
                info(f'  · {f}')
            info('')
            info('这不一定会影响主流程 —— 例如 turtlebot3-cartographer 装不上，')
            info('我们用的是 slam_toolbox，不影响。先继续往下走。')
        else:
            info('逐个安装全部成功。')

    info(f'耗时 {time.time() - t0:.0f} 秒')

    # 检查 ROS 包的 share 目录（apt 包名用连字符，share 目录用下划线）
    prefix = f'ros-{ROS_DISTRO}-'
    ros_pkgs = [p for p in EXTRA_PACKAGES if p.startswith(prefix)]
    missing = []
    for p in ros_pkgs:
        share_name = p[len(prefix):].replace('-', '_')
        if not os.path.isdir(os.path.join(ROS_SHARE, share_name)):
            missing.append((p, share_name))

    if missing:
        info('')
        info('以下包对应的 share 目录没找到（可能包名或目录名与预期不同，不一定有问题）：')
        for apt_name, share_name in missing:
            info(f'  · {apt_name}  →  {ROS_SHARE}/{share_name}')
    ok('仿真与导航组件安装完成')

    # ---------------------------------------------------------------- 步骤 5
    step(5, TOTAL, '配置环境变量')

    bashrc = os.path.expanduser('~/.bashrc')
    existing = ''
    if os.path.isfile(bashrc):
        with open(bashrc, encoding='utf-8') as f:
            existing = f.read()

    added = []
    with open(bashrc, 'a', encoding='utf-8') as f:
        f.write(f'\n# ===== 智慧社区赛项 · ROS 2 环境 =====\n')
        for line in BASHRC_LINES:
            if line not in existing:
                f.write(line + '\n')
                added.append(line)
                info(f'已添加：{line}')
            else:
                info(f'已存在：{line}')

    if not added:
        info('环境变量此前已配置好')

    os.environ['ROS_DISTRO'] = ROS_DISTRO
    os.environ['TURTLEBOT3_MODEL'] = 'waffle'
    ok('环境变量配置完成')

    # ---------------------------------------------------------------- 步骤 6
    step(6, TOTAL, '验证安装')

    checks = [
        ('ROS 2 目录', os.path.isdir(f'/opt/ros/{ROS_DISTRO}')),
        ('gazebo 可执行文件', have('gazebo')),
        ('ros2 命令可用',
         subprocess.run(['bash', '-c', f'source {ROS_SETUP} && command -v ros2'],
                        capture_output=True).returncode == 0),
        ('TurtleBot3 仿真包', os.path.isdir(os.path.join(ROS_SHARE, 'turtlebot3_gazebo'))),
        ('Nav2 bringup', os.path.isdir(os.path.join(ROS_SHARE, 'nav2_bringup'))),
        ('slam_toolbox', os.path.isdir(os.path.join(ROS_SHARE, 'slam_toolbox'))),
        ('gazebo_ros', os.path.isdir(os.path.join(ROS_SHARE, 'gazebo_ros'))),
    ]

    all_ok = True
    for name, passed in checks:
        print(f'   {"[ OK ]" if passed else "[FAIL]"} {name}')
        if not passed:
            all_ok = False
    sys.stdout.flush()

    if not all_ok:
        fail('有项目未通过，请把上面的输出贴给导航组')
        return 1

    ok('全部验证通过')

    # ---------------------------------------------------------------- 完成
    banner('安装完成')

    print(f'''  环境已经就绪。接下来请：

  1) 关闭当前终端，重新打开一个新的 Ubuntu 终端
     （让 ~/.bashrc 的环境变量生效）

  2) 确认环境变量生效：
        echo $ROS_DISTRO          # 应输出 humble
        echo $TURTLEBOT3_MODEL    # 应输出 waffle

  3) 测试图形界面能否显示：
        xeyes
     屏幕上出现一个跟着鼠标转的眼睛窗口 = WSLg 正常，Gazebo 也能显示。

  4) 跑通 ROS 2 的发布/订阅测试（第一个里程碑）：
     终端 A:  ros2 run demo_nodes_cpp talker
     终端 B:  ros2 run demo_nodes_cpp listener
     看到 listener 不断打印 "I heard: [Hello World: N]" 就成功了。

  然后把结果告诉导航组，我们进入下一步。
''')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n\n用户中断。已完成的步骤不会重复执行，可以重新运行本脚本继续。')
        sys.exit(130)
    except RuntimeError as exc:
        print(f'\n\n❌ 安装中断：{exc}')
        print('   已完成的步骤会被记录，重新运行本脚本可从断点继续。')
        sys.exit(1)
