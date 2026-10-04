#!/usr/bin/env bash
# =============================================================================
#  验证 guest 环境是否真的可用（ROS 2 + Gazebo + 工程编译产物）
#
#  注意：ROS 的 setup.bash 不是 nounset-clean 的，本脚本刻意不开 set -u。
# =============================================================================
set -o pipefail

WS="$HOME/smart_community_ws"

echo "=== 1. 工作空间归属 ==="
ls -ld "$WS" "$WS/build" "$WS/install" 2>/dev/null

echo ""
echo "=== 2. source 环境 ==="
source /opt/ros/jazzy/setup.bash 2>/dev/null && echo "  /opt/ros/jazzy OK"
if [ -f "$WS/install/setup.bash" ]; then
  source "$WS/install/setup.bash" && echo "  workspace OK"
else
  echo "  [X] workspace install/setup.bash 不存在"
fi

echo ""
echo "=== 3. 关键可执行 ==="
echo "  ros2 : $(command -v ros2 || echo 未找到)"
echo "  gz   : $(command -v gz || echo 未找到)"
echo "  colcon: $(command -v colcon || echo 未找到)"

echo ""
echo "=== 4. Gazebo 版本 ==="
gz sim --versions 2>&1 | head -n 4 || echo "  (gz sim --versions 失败)"

echo ""
echo "=== 5. 红绿灯 C++ 插件是否编译出来 ==="
PLUGIN="$(find "$WS/install" -name 'libTrafficLightSystem.so' 2>/dev/null | head -n 1)"
if [ -n "$PLUGIN" ]; then
  echo "  [ok] $PLUGIN"
  ls -la "$PLUGIN"
else
  echo "  [X] 没找到 libTrafficLightSystem.so"
fi

echo ""
echo "=== 6. ROS 2 包是否注册 ==="
ros2 pkg list 2>/dev/null | grep smart_community | sed 's/^/  /' || echo "  (未找到 smart_community 包)"

echo ""
echo "=== 7. 世界文件 ==="
WORLD="$WS/install/smart_community_sim/share/smart_community_sim/worlds/smart_community.sdf"
if [ -f "$WORLD" ]; then
  echo "  [ok] $(ls -la "$WORLD" | awk '{print $5" bytes"}')"
else
  echo "  [X] 世界文件未安装到 share/"
  find "$WS/install" -name '*.sdf' 2>/dev/null | head -n 3 | sed 's/^/     /'
fi

echo ""
echo "=== 8. 关键 ROS 依赖 ==="
for p in ros_gz_sim ros_gz_bridge xacro robot_state_publisher; do
  if ros2 pkg list 2>/dev/null | grep -qx "$p"; then
    echo "  [ok] $p"
  else
    echo "  [X] 缺 $p"
  fi
done
