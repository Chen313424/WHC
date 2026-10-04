#!/usr/bin/env bash
# =============================================================================
#  guest 内诊断：判断安装/编译到底有没有在推进
#  说明：日志重定向到文件时是「全缓冲」，apt/colcon 可能长时间不刷新，
#        所以不能只看日志有没有新增，要看进程与 CPU。
# =============================================================================
LOG="${HOME}/ros_install.log"

echo "=== 系统 ==="
uptime
echo "内存/交换:"
free -h | sed -n '2,3p'

echo ""
echo "=== 占用 CPU 最高的进程 ==="
ps -eo pid,etime,pcpu,pmem,comm --sort=-pcpu 2>/dev/null | head -n 10

echo ""
echo "=== 是否为编译/安装进程 ==="
if pgrep -x -a colcon >/dev/null 2>&1 || pgrep -x -a cmake >/dev/null 2>&1 \
   || pgrep -x -a cc1plus >/dev/null 2>&1 || pgrep -x -a dpkg >/dev/null 2>&1 \
   || pgrep -x -a apt-get >/dev/null 2>&1 || pgrep -f -a setup_ros_jazzy >/dev/null 2>&1; then
  echo "  >>> 有安装/编译进程在跑"
  pgrep -a -f 'colcon|cmake|cc1plus|dpkg|apt-get|setup_ros_jazzy|setup_vm_guest' | head -n 8
else
  echo "  >>> 没有任何安装/编译进程（脚本可能已退出）"
fi

echo ""
echo "=== 日志实况 ==="
if [ -f "$LOG" ]; then
  ls -la --time-style=+%H:%M:%S "$LOG"
  echo "  行数: $(wc -l < "$LOG")"
  echo "  现在: $(date +%H:%M:%S)"
  echo "  --- 尾部 10 行 ---"
  tail -n 10 "$LOG" | sed 's/^/  /'
else
  echo "  (无日志)"
fi

echo ""
echo "=== 工作空间 ==="
for d in /root/smart_community_ws /home/whc/smart_community_ws; do
  echo "-- $d"
  if [ -d "$d" ]; then
    ls -la "$d" 2>/dev/null | sed 's/^/   /'
    if [ -d "$d/install" ]; then
      echo "   install/ 存在，包列表:"
      ls "$d/install" 2>/dev/null | sed 's/^/     /'
    fi
  else
    echo "   (不存在)"
  fi
done

echo ""
echo "=== ROS / Gazebo ==="
echo "  /opt/ros/jazzy/setup.bash: $([ -f /opt/ros/jazzy/setup.bash ] && echo 存在 || echo 缺失)"
echo "  ros-jazzy 已装包数: $(dpkg -l 2>/dev/null | grep -c '^ii  ros-jazzy')"
