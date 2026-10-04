#!/usr/bin/env bash
# =============================================================================
#  在「已登录的图形会话」里启动 Gazebo，让窗口真正显示在 VM 屏幕上
#
#  为什么不能只跑 ros2 launch：
#    通过 SSH 启动的进程继承的是 SSH 会话环境，没有 DISPLAY/XAUTHORITY，
#    Gazebo 会报 "cannot open display" 或静默失败。必须先定位到用户图形会话
#    （gnome-shell）的显示变量，再以同样的环境启动。
#
#    Ubuntu 24.04 的 GNOME 默认走 Wayland，X11 程序经由 XWayland 显示，
#    所以 DISPLAY 通常是 :0/:1，XAUTHORITY 指向 .mutter-Xwaylandauth.*。
#
#  用法:  bash ~/launch_sim_on_display.sh [--software]
# =============================================================================
set +u

WS="$HOME/smart_community_ws"
LOG="$HOME/gazebo_launch.log"
USE_SOFTWARE=0
[ "${1:-}" = "--software" ] && USE_SOFTWARE=1

echo "=== 1. 定位图形会话 ==="
GS_PID="$(pgrep -u "$(id -u)" -n gnome-shell 2>/dev/null)"
if [ -z "$GS_PID" ]; then
  echo "  [X] 没找到 gnome-shell —— 说明桌面尚未登录，请先在 VM 窗口登录"
  exit 1
fi
echo "  gnome-shell pid = $GS_PID"

# 从 gnome-shell 的环境里取显示相关变量（同用户，可读）
ENV_DUMP="$(tr '\0' '\n' < "/proc/$GS_PID/environ" 2>/dev/null \
            | grep -E '^(DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|WAYLAND_DISPLAY)=' || true)"
if [ -n "$ENV_DUMP" ]; then
  echo "  会话环境:"
  printf '%s\n' "$ENV_DUMP" | sed 's/^/    /'
  eval "export $(printf '%s' "$ENV_DUMP" | tr '\n' ' ')"
fi

# environ 里没有 XAUTHORITY 时自行探测（Wayland 下常见）
if [ -z "${XAUTHORITY:-}" ]; then
  for f in /run/user/"$(id -u)"/.mutter-Xwaylandauth.* "$HOME/.Xauthority"; do
    if [ -f "$f" ]; then export XAUTHORITY="$f"; break; fi
  done
fi
# DISPLAY 兜底
if [ -z "${DISPLAY:-}" ]; then
  for d in :0 :1; do
    [ -e "/tmp/.X11-unix/X${d#:}" ] && export DISPLAY="$d" && break
  done
fi

echo "  --> DISPLAY=${DISPLAY:-未设置}"
echo "      XAUTHORITY=${XAUTHORITY:-未设置}"
echo "      WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-未设置}"

if [ -z "${DISPLAY:-}" ]; then
  echo "  [X] 仍拿不到 DISPLAY，无法投屏"
  exit 1
fi

echo ""
echo "=== 2. 准备 ROS 环境 ==="
source /opt/ros/jazzy/setup.bash 2>/dev/null
source "$WS/install/setup.bash" 2>/dev/null
export GZ_SIM_SYSTEM_PLUGIN_PATH="$WS/install/smart_community_sim/lib${GZ_SIM_SYSTEM_PLUGIN_PATH:+:$GZ_SIM_SYSTEM_PLUGIN_PATH}"
export GZ_SIM_RESOURCE_PATH="$WS/install/smart_community_sim/share${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}"
echo "  ros2 = $(command -v ros2)"
echo "  插件目录 = $GZ_SIM_SYSTEM_PLUGIN_PATH"

if [ "$USE_SOFTWARE" = "1" ]; then
  echo "  [i] 强制软件渲染 (llvmpipe)"
  export LIBGL_ALWAYS_SOFTWARE=1
  export GALLIUM_DRIVER=llvmpipe
fi

echo ""
echo "=== 3. 启动（脱离 SSH 会话，日志: $LOG） ==="
rm -f "$LOG"
setsid nohup ros2 launch smart_community_sim smart_community.launch.py \
  > "$LOG" 2>&1 < /dev/null &
echo "  已放到后台，PID=$!"

echo "  等待 30 秒让它加载世界……"
sleep 30

echo ""
echo "=== 4. 启动日志（尾部） ==="
tail -n 30 "$LOG" 2>/dev/null | sed 's/^/  /'

echo ""
echo "=== 5. 进程检查 ==="
for p in gz ruby ros2; do
  printf '  %-6s: %s\n' "$p" "$(pgrep -c "$p" 2>/dev/null || echo 0)"
done
echo "  gazebo 相关进程:"
pgrep -a -f 'gz sim|ruby.*gz|ros2 launch' 2>/dev/null | head -n 5 | sed 's/^/    /'
