#!/usr/bin/env bash
# 检查 Gazebo 启动日志，定位它为何退出
set +u
LOG="$HOME/gazebo_launch.log"

echo "=== 日志行数 ==="
wc -l < "$LOG" 2>/dev/null || echo "(无日志)"

echo ""
echo "=== 错误/图形相关行 ==="
grep -inE 'error|fail|cannot|unable|libGL|GLX|EGL|GL |render|ogre|vulkan|display|abort|segmentation|core|exit code|died|signal' "$LOG" 2>/dev/null \
  | head -n 40 | sed 's/^/  /'
[ -z "$(grep -inE 'error|fail|cannot|unable|libGL|GLX|EGL|render|ogre|vulkan|display|abort|segmentation|signal' "$LOG" 2>/dev/null)" ] \
  && echo "  (没有匹配到错误关键字)"

echo ""
echo "=== gazebo 相关全部行 ==="
grep -n 'gazebo' "$LOG" 2>/dev/null | head -n 20 | sed 's/^/  /'

echo ""
echo "=== 日志头部 40 行 ==="
head -n 40 "$LOG" 2>/dev/null | sed 's/^/  /'

echo ""
echo "=== ROS 日志目录 ==="
ls -la "$HOME/.ros/log/" 2>/dev/null | tail -n 5 | sed 's/^/  /'

echo ""
echo "=== 图形能力自检 ==="
echo "  libGL:  $(ldconfig -p 2>/dev/null | grep -c libGL)"
echo "  DISPLAY=$DISPLAY  XAUTHORITY=${XAUTHORITY:-未设置}"
if command -v glxinfo >/dev/null 2>&1; then
  echo "  glxinfo 摘要:"
  glxinfo -B 2>&1 | head -n 8 | sed 's/^/    /'
else
  echo "  (glxinfo 未安装，可用: sudo apt install mesa-utils)"
fi
