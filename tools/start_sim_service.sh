#!/usr/bin/env bash
# =============================================================================
#  把仿真启动成 systemd --user 的 transient service
#
#  为什么不能用 `setsid nohup ... &`：
#    在 systemd 系统上，SSH 会话里的进程被放进 session-N.scope 这个 cgroup，
#    会话一断开，该 scope 内所有进程都会被回收 —— setsid/nohup 只能挡 SIGHUP，
#    挡不住 cgroup 级的清理。实测：通过 SSH 后台启动后，Gazebo 能跑 30 秒，
#    脚本一结束就整体消失。
#
#    做成 systemd --user 的 transient service 后，进程挂在用户管理器下，
#    与 SSH 会话解耦，断开终端也不会退出。
#
#  用法: bash ~/start_sim_service.sh [--software]
# =============================================================================
set +u

USE_SOFTWARE=0
[ "${1:-}" = "--software" ] && USE_SOFTWARE=1

# ---------------------------------------------------------------- 1. 显示环境
GS_PID="$(pgrep -u "$(id -u)" -n gnome-shell 2>/dev/null)"
if [ -n "$GS_PID" ]; then
  eval "export $(tr '\0' '\n' < "/proc/$GS_PID/environ" 2>/dev/null \
        | grep -E '^(DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|WAYLAND_DISPLAY)=' | tr '\n' ' ')"
fi
[ -z "${XAUTHORITY:-}" ] && for f in /run/user/"$(id -u)"/.mutter-Xwaylandauth.* "$HOME/.Xauthority"; do
  [ -f "$f" ] && export XAUTHORITY="$f" && break
done
[ -z "${DISPLAY:-}" ] && for d in :0 :1; do
  [ -e "/tmp/.X11-unix/X${d#:}" ] && export DISPLAY="$d" && break
done

echo "=== 显示环境 ==="
echo "  DISPLAY=${DISPLAY:-未设置}"
echo "  XAUTHORITY=${XAUTHORITY:-未设置}"
echo "  XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-未设置}"
[ -z "${DISPLAY:-}" ] && { echo "  [X] 拿不到 DISPLAY，请先在 VM 窗口登录桌面"; exit 1; }

# ---------------------------------------------------------------- 2. 清理旧实例
echo ""
echo "=== 清理旧实例 ==="
systemctl --user stop whc-sim.service 2>/dev/null && echo "  已停止旧的 whc-sim.service"
systemctl --user reset-failed whc-sim.service 2>/dev/null
pkill -f 'gz sim' 2>/dev/null && echo "  已清理残留 gz sim"
pkill -f 'ros2 launch' 2>/dev/null && echo "  已清理残留 ros2 launch"
sleep 2

# ---------------------------------------------------------------- 3. 启动服务
echo ""
echo "=== 以 transient service 启动 ==="
ENV_ARGS=(
  --setenv=DISPLAY="$DISPLAY"
  --setenv=XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
)
[ -n "${XAUTHORITY:-}" ] && ENV_ARGS+=(--setenv=XAUTHORITY="$XAUTHORITY")
[ -n "${WAYLAND_DISPLAY:-}" ] && ENV_ARGS+=(--setenv=WAYLAND_DISPLAY="$WAYLAND_DISPLAY")
if [ "$USE_SOFTWARE" = "1" ]; then
  ENV_ARGS+=(--setenv=LIBGL_ALWAYS_SOFTWARE=1 --setenv=GALLIUM_DRIVER=llvmpipe)
  echo "  (强制软件渲染)"
fi

systemd-run --user --unit=whc-sim --collect \
  --description="智慧社区 Gazebo 仿真" \
  "${ENV_ARGS[@]}" \
  "$HOME/run_sim.sh" 2>&1 | sed 's/^/  /'

sleep 8
echo ""
echo "=== 单元状态 ==="
systemctl --user is-active whc-sim.service 2>&1 | sed 's/^/  active: /'

echo ""
echo "=== 进程 ==="
pgrep -a -f 'gz sim|ros2 launch' 2>/dev/null | head -n 6 | sed 's/^/  /' || echo "  (无)"

echo ""
echo "=== 服务日志尾部（Gazebo 启动输出） ==="
journalctl --user -u whc-sim.service -n 25 --no-pager 2>/dev/null | sed 's/^/  /' \
  || echo "  (journalctl 不可用)"

cat <<'EOF'

后续操作：
  查看状态:  systemctl --user status whc-sim.service
  查看日志:  journalctl --user -u whc-sim.service -f
  停止仿真:  systemctl --user stop whc-sim.service

EOF
