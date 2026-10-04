#!/usr/bin/env bash
# 前台直接跑 gz sim，抓取 GUI 初始化失败的真实原因
set +u

WS="$HOME/smart_community_ws"

# --- 取图形会话显示环境 ---
GS_PID="$(pgrep -u "$(id -u)" -n gnome-shell 2>/dev/null)"
if [ -n "$GS_PID" ]; then
  eval "export $(tr '\0' '\n' < "/proc/$GS_PID/environ" 2>/dev/null \
        | grep -E '^(DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|WAYLAND_DISPLAY)=' | tr '\n' ' ')"
fi
if [ -z "${XAUTHORITY:-}" ]; then
  for f in /run/user/"$(id -u)"/.mutter-Xwaylandauth.* "$HOME/.Xauthority"; do
    [ -f "$f" ] && export XAUTHORITY="$f" && break
  done
fi

source /opt/ros/jazzy/setup.bash 2>/dev/null
source "$WS/install/setup.bash" 2>/dev/null
export GZ_SIM_SYSTEM_PLUGIN_PATH="$WS/install/smart_community_sim/lib"
export GZ_SIM_RESOURCE_PATH="$WS/install/smart_community_sim/share"
WORLD="$WS/install/smart_community_sim/share/smart_community_sim/worlds/smart_community.sdf"

echo "DISPLAY=${DISPLAY:-未设置}"
echo "XAUTHORITY=${XAUTHORITY:-未设置}"
echo "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-未设置}"

echo ""
echo "=== 检查 X 连接是否可用（xdpyinfo / glxinfo 若有） ==="
if command -v xdpyinfo >/dev/null 2>&1; then
  xdpyinfo 2>&1 | head -n 5 | sed 's/^/  /'
else
  echo "  (xdpyinfo 未安装)"
fi
if command -v glxinfo >/dev/null 2>&1; then
  glxinfo -B 2>&1 | head -n 10 | sed 's/^/  /'
else
  echo "  (glxinfo 未安装，稍后装 mesa-utils)"
fi

echo ""
echo "=== 前台跑 gz sim（带 GUI）25 秒 ==="
timeout 25 gz sim -r -v 3 "$WORLD" 2>&1 | head -n 80
echo "  管道退出码: ${PIPESTATUS[0]:-?}"

echo ""
echo "=== 再试：强制软件渲染 ==="
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
timeout 20 gz sim -r -v 3 "$WORLD" 2>&1 | head -n 40
echo "  管道退出码: ${PIPESTATUS[0]:-?}"
