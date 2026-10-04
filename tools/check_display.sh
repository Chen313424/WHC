#!/usr/bin/env bash
# 检查图形会话状态，判断 Gazebo GUI 能否投到 VM 屏幕上
set +u

echo "=== 登录会话 ==="
loginctl list-sessions --no-legend

echo ""
echo "=== 各会话详情 ==="
while read -r sid rest; do
  [ -z "$sid" ] && continue
  echo "--- session $sid ---"
  loginctl show-session "$sid" -p Name -p Type -p Class -p State -p Display -p Active -p TTY -p Remote 2>/dev/null | sed 's/^/  /'
done < <(loginctl list-sessions --no-legend)

echo ""
echo "=== 相关进程计数 ==="
for p in gnome-shell Xorg Xwayland gdm gdm3; do
  n=$(pgrep -c "$p" 2>/dev/null)
  echo "  $p: ${n:-0}"
done

echo ""
echo "=== 显示套接字 ==="
echo "X11 (/tmp/.X11-unix):"
ls -la /tmp/.X11-unix/ 2>/dev/null | sed 's/^/  /' || echo "  (无)"
echo "Wayland (/run/user/1000):"
ls /run/user/1000/wayland-* 2>/dev/null | sed 's/^/  /' || echo "  (无)"

echo ""
echo "=== 图形会话环境变量（从 gnome-shell 进程读取） ==="
GS_PID=$(pgrep -n gnome-shell 2>/dev/null)
if [ -n "$GS_PID" ]; then
  echo "  gnome-shell pid=$GS_PID"
  tr '\0' '\n' < "/proc/$GS_PID/environ" 2>/dev/null | grep -E '^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_SESSION_TYPE)=' | sed 's/^/    /' || echo "    (读不到)"
else
  echo "  gnome-shell 未运行 —— 说明还没有用户登录桌面"
fi
