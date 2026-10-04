#!/usr/bin/env bash
# 前台跑完整 launch，观察「小车 spawn 之后 gazebo 是否退出」
set +u

WS="$HOME/smart_community_ws"

# 取图形会话显示环境
GS_PID="$(pgrep -u "$(id -u)" -n gnome-shell 2>/dev/null)"
[ -n "$GS_PID" ] && eval "export $(tr '\0' '\n' < "/proc/$GS_PID/environ" 2>/dev/null \
    | grep -E '^(DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|WAYLAND_DISPLAY)=' | tr '\n' ' ')"
[ -z "${XAUTHORITY:-}" ] && for f in /run/user/"$(id -u)"/.mutter-Xwaylandauth.*; do
  [ -f "$f" ] && export XAUTHORITY="$f" && break
done

source /opt/ros/jazzy/setup.bash 2>/dev/null
source "$WS/install/setup.bash" 2>/dev/null
export GZ_SIM_SYSTEM_PLUGIN_PATH="$WS/install/smart_community_sim/lib"

echo "DISPLAY=$DISPLAY  XAUTHORITY=${XAUTHORITY:-未设置}"
echo ""
echo "=== 先清掉可能残留的 gz / ros2 launch ==="
pkill -f 'gz sim' 2>/dev/null; pkill -f 'ros2 launch' 2>/dev/null; sleep 2
echo "残留 gz 进程: $(pgrep -c -f 'gz sim' 2>/dev/null || echo 0)"

echo ""
echo "=== 前台跑完整 launch，60 秒 ==="
timeout 60 ros2 launch smart_community_sim smart_community.launch.py > /tmp/full_launch.log 2>&1
RC=$?
echo "  launch 退出码: $RC   (124=被 timeout 正常掐掉，说明一直在跑)"

echo ""
echo "=== 关键事件 ==="
grep -nE 'gazebo-[0-9]|create-[0-9]|Entity creation|finished|error|Error|Err|died|exit' /tmp/full_launch.log 2>/dev/null | head -n 30 | sed 's/^/  /'

echo ""
echo "=== gazebo 是否还活着 ==="
echo "  gz sim 进程数: $(pgrep -c -f 'gz sim' 2>/dev/null || echo 0)"

echo ""
echo "=== 日志最后 40 行 ==="
tail -n 40 /tmp/full_launch.log 2>/dev/null | sed 's/^/  /'
