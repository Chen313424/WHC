#!/usr/bin/env bash
# =============================================================================
#  guest 侧状态探针（供宿主机轮询用）
#
#  为什么必须是独立文件而不是 ssh 的内联命令：
#    `ssh host 'pgrep -f tools/setup_vm_guest.sh'` 时，远端 bash 的 argv 里
#    就包含 "tools/setup_vm_guest.sh"，pgrep -f 会匹配到自己 -> 永远返回 RUNNING。
#    放进独立文件后，远端 argv 只是 "bash ~/probe_guest.sh"，不再自匹配。
# =============================================================================
LOG="${HOME}/ros_install.log"
TARGET="tools/setup_vm_guest.sh"

if pgrep -f "$TARGET" >/dev/null 2>&1; then
  echo "STATE=RUNNING"
else
  echo "STATE=DONE"
fi

echo "--- 日志尾部 ---"
tail -n 3 "$LOG" 2>/dev/null || echo "(无日志)"
