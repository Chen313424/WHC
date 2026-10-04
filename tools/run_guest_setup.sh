#!/usr/bin/env bash
# =============================================================================
#  在 guest 内后台启动完整环境配置，并立刻返回（不阻塞 SSH 会话）
#
#  为什么单独做成文件而不是从宿主机用 heredoc 传：
#    PowerShell 内联 here-string 会带 CRLF，bash 会把 \r 当成文件名的一部分，
#    导致 "cannot open '/home/whc/xxx.log'$'\r'"。写成 .sh 文件（LF）再 scp，
#    行尾完全可控。
#
#  用法（宿主机）：
#      scp -P 2222 tools/run_guest_setup.sh whc@127.0.0.1:~/
#      ssh -p 2222 whc@127.0.0.1 'bash ~/run_guest_setup.sh'
# =============================================================================
set -u

WS="$HOME/whc_setup"
LOG="$HOME/ros_install.log"

if [ ! -d "$WS/tools" ]; then
  echo "[X] 找不到 $WS/tools —— 请先 scp 上传 tools 与 src"
  exit 1
fi

if pgrep -f "tools/setup_vm_guest.sh" >/dev/null 2>&1; then
  echo "[!] 已有一个 setup_vm_guest.sh 在运行，先等它结束或手动终止："
  pgrep -af "tools/setup_vm_guest.sh"
  exit 0
fi

rm -f "$LOG"
chmod +x "$WS"/tools/*.sh 2>/dev/null || true

cd "$WS" || exit 1
nohup sudo bash tools/setup_vm_guest.sh > "$LOG" 2>&1 &
PID=$!
echo "已启动，PID=$PID，日志=$LOG"

# 等一会儿，把开头几行回显出来，便于立刻发现早期错误
sleep 20
echo "--- 日志前 20 行 ---"
head -n 20 "$LOG" 2>/dev/null || echo "(日志暂时为空)"
