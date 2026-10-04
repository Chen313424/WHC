#!/usr/bin/env bash
# =============================================================================
#  Gazebo 冒烟测试（无界面）
#
#  目的：在切到图形界面之前，先确认
#    1. 世界 SDF 能被解析、场景能加载
#    2. 红绿灯 C++ 插件 libTrafficLightSystem.so 能被 gz-sim 找到并初始化
#       （插件自报日志形如 "TrafficLight: 找到 6 盏灯"）
#
#  刻意不用 pipefail：本环境脚本里 `cmd | grep -q` 会因 grep 提前退出触发
#  SIGPIPE 返回 141，导致「明明存在却判定不存在」，之前已经因此误报过两次。
# =============================================================================
set +u

source /opt/ros/jazzy/setup.bash 2>/dev/null
source "$HOME/smart_community_ws/install/setup.bash" 2>/dev/null

LIB="$HOME/smart_community_ws/install/smart_community_sim/lib"
WORLD="$HOME/smart_community_ws/install/smart_community_sim/share/smart_community_sim/worlds/smart_community.sdf"
export GZ_SIM_SYSTEM_PLUGIN_PATH="$LIB"
export GZ_SIM_RESOURCE_PATH="$HOME/smart_community_ws/install/smart_community_sim/share"

echo "=== 环境 ==="
echo "  gz:        $(command -v gz)"
echo "  plugin 目录: $LIB"
echo "  world:     $(readlink -f "$WORLD")"
echo "  world 大小: $(stat -L -c '%s bytes' "$WORLD" 2>/dev/null)"
echo "  插件 .so:  $(ls "$LIB"/libTrafficLightSystem.so 2>/dev/null || echo 缺失)"

echo ""
echo "=== 启动 gz sim 服务器（-s 无界面）25 秒 ==="
# 用 -v 4：插件里的自报日志走 gzdbg（debug 级），-v 3 不会输出，
# 会导致「插件其实加载了但看不到证据」的误判。
# timeout 返回 124 属正常（我们主动掐掉长时间运行的仿真）
timeout 25 gz sim -s -r -v 4 "$WORLD" > /tmp/gz_smoke.log 2>&1
RC=$?
echo "  退出码: $RC  (124 = 被 timeout 正常结束)"

echo ""
echo "=== 插件是否被加载（关键） ==="
if grep -q 'TrafficLight' /tmp/gz_smoke.log; then
  grep 'TrafficLight' /tmp/gz_smoke.log | head -n 5 | sed 's/^/  /'
  echo "  [ok] 红绿灯插件已加载"
else
  echo "  [!] 日志里没有 TrafficLight 字样"
fi

echo ""
echo "=== 错误/警告检查 ==="
if grep -iE 'error|failed|unable to|cannot' /tmp/gz_smoke.log > /tmp/gz_err.log 2>/dev/null; then
  echo "  发现以下可疑行（前 12 条）："
  head -n 12 /tmp/gz_err.log | sed 's/^/  /'
else
  echo "  [ok] 无 error/failed 关键字"
fi

echo ""
echo "=== 日志尾部 25 行 ==="
tail -n 25 /tmp/gz_smoke.log | sed 's/^/  /'
