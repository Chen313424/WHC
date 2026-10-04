#!/usr/bin/env bash
# =============================================================================
#  一键启动「智慧社区」仿真（在 VM 的图形会话里运行）
#
#  用法：
#      bash ~/run_sim.sh              # 正常启动
#      bash ~/run_sim.sh --software   # 强制软件渲染（VirtualBox 3D 不足时用）
#
#  说明：
#    * 必须在这个 VM 的桌面会话里运行，Gazebo 窗口才会显示出来。
#      通过 SSH 跑的话需要有 DISPLAY/XAUTHORITY，见文末注释。
#    * ROS 的 setup.bash 不是 nounset-clean 的，所以本脚本刻意不开 set -u。
# =============================================================================
set +u

WS="$HOME/smart_community_ws"
USE_SOFTWARE=0
[ "${1:-}" = "--software" ] && USE_SOFTWARE=1

echo "=== 环境检查 ==="
if [ ! -f /opt/ros/jazzy/setup.bash ]; then
  echo "  [X] 没装 ROS 2 Jazzy"
  exit 1
fi
if [ ! -f "$WS/install/setup.bash" ]; then
  echo "  [X] 工作空间未编译：$WS/install/setup.bash 不存在"
  echo "      先执行： sudo bash ~/whc_setup/tools/setup_vm_guest.sh"
  exit 1
fi
echo "  [ok] ROS 2 + 工作空间就绪"

source /opt/ros/jazzy/setup.bash
source "$WS/install/setup.bash"

export GZ_SIM_SYSTEM_PLUGIN_PATH="$WS/install/smart_community_sim/lib${GZ_SIM_SYSTEM_PLUGIN_PATH:+:$GZ_SIM_SYSTEM_PLUGIN_PATH}"
export GZ_SIM_RESOURCE_PATH="$WS/install/smart_community_sim/share${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}"

if [ "$USE_SOFTWARE" = "1" ]; then
  echo "  [i] 已强制软件渲染 (llvmpipe)"
  export LIBGL_ALWAYS_SOFTWARE=1
  export GALLIUM_DRIVER=llvmpipe
fi

echo ""
echo "=== 启动 Gazebo（加载场景 + 小车） ==="
echo "    世界: smart_community.sdf   机器人: robot.xacro"
echo "    关闭窗口或按 Ctrl+C 结束"
echo ""
exec ros2 launch smart_community_sim smart_community.launch.py

# ---------------------------------------------------------------------------
# 想从宿主机 SSH 投屏到 VM 桌面，先登录桌面，再取得会话的显示变量：
#   PID=$(pgrep -u whc -n gnome-shell)
#   export DISPLAY=:0
#   export XAUTHORITY=$(tr '\0' '\n' < /proc/$PID/environ | sed -n 's/^XAUTHORITY=//p')
# 然后以 whc 身份运行本脚本。
# ---------------------------------------------------------------------------
