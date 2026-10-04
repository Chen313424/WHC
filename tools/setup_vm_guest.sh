#!/usr/bin/env bash
# =============================================================================
#  VirtualBox guest 内的收尾脚本
#
#  做完两件事：
#    1. 装最小桌面环境 —— Gazebo 是图形程序，server 版没桌面看不了
#    2. 调用 setup_ros_jazzy.sh 装 ROS 2 Jazzy + Gazebo Harmonic 并编译工程
#
#  用法（在 guest 内，已通过 SSH 或 VM 控制台登录）：
#      bash /media/sf_WHC/tools/setup_vm_guest.sh
#  或（源码已 scp 到 ~/whc_setup 时）：
#      bash ~/whc_setup/tools/setup_vm_guest.sh
#
#  注意：装完桌面需要重启 VM 才会进入图形登录界面：
#      sudo reboot
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

c_info() { printf '\n\033[36m=== %s ===\033[0m\n' "$*"; }
c_ok()   { printf '  \033[32m[OK]\033[0m %s\n' "$*"; }
c_warn() { printf '  \033[33m[!]\033[0m %s\n' "$*"; }

c_info "0. 环境信息"
. /etc/os-release
echo "  $PRETTY_NAME"
echo "  内核: $(uname -r)"
echo "  内存: $(free -h | awk '/^Mem:/{print $2}')"
echo "  磁盘: $(df -h / | awk 'NR==2{print $2" 总 / "$4" 可用"}')"

# ---------------------------------------------------------------- 1. 清华源
c_info "1. 切换 apt 源到清华镜像"
if [ -f /etc/apt/sources.list.d/ubuntu.sources ] && ! grep -q tuna.tsinghua /etc/apt/sources.list.d/ubuntu.sources; then
  $SUDO cp /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources.bak
  $SUDO sed -i \
    -e 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
    -e 's|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
    /etc/apt/sources.list.d/ubuntu.sources
  c_ok "已切换（备份 .bak）"
else
  c_ok "无需切换或已是清华源"
fi
$SUDO apt-get update -qq

# ---------------------------------------------------------------- 2. 桌面
c_info "2. 安装最小桌面环境（Gazebo GUI 需要）"
if dpkg -l 2>/dev/null | grep -q '^ii  ubuntu-desktop-minimal'; then
  c_ok "已安装，跳过"
else
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get install -y ubuntu-desktop-minimal
  c_ok "桌面安装完成"
fi

# ---------------------------------------------------------------- 3. ROS 2
c_info "3. 安装 ROS 2 Jazzy + Gazebo Harmonic 并编译工程"
bash "$HERE/setup_ros_jazzy.sh"

# ---------------------------------------------------------------- 4. 结论
cat <<EOF

$(printf '\033[32mguest 侧配置完成。\033[0m')

重启进入图形界面：
    sudo reboot

重启后用 VM 窗口登录（用户名 whc），然后：
    cd ~/smart_community_ws
    source install/setup.bash
    ros2 launch smart_community_sim smart_community.launch.py

如果 Gazebo 报 OpenGL 相关错误（VirtualBox 3D 加速不足）：
    export LIBGL_ALWAYS_SOFTWARE=1
    export GALLIUM_DRIVER=llvmpipe
再重试上面的 launch 命令。
EOF
