#!/usr/bin/env bash
# =============================================================================
#  低开销启动：Nav2 全栈合成到【一个】进程 + 等 Nav2 真激活后再开始巡检
#
#  踩过的两个坑（都跟"慢机器"有关，不是算法问题）：
#   1) 15 个独立 Nav2 进程的 DDS 开销把 8 vCPU 压爆：
#      只跑 Gazebo 时实时因子 0.93 → 拉全栈后掉到 0.09~0.11，
#      /tf 只剩 1.9Hz，控制器持续 "extrapolation into the future" 而 abort。
#      对策：use_composition:=True（Nav2 只占一个进程、一个 DDS 参与者）。
#   2) patrol_node 的 start_delay 只有 5 秒，Nav2 还没激活它就发目标；
#      action server 不存在时 send_goal 会【立即】失败，
#      它于是 1 秒内把 11 个站点全部判失败（0 成功 / 11 失败），
#      看起来像"导航全崩"，其实是一次竞态。
#      对策：navigation.launch.py 传 autostart_patrol:=false，
#            轮询到 5/5 激活后，再把 patrol_node 用 autostart:=true 单独拉起。
# =============================================================================
set +e
set +u

export DISPLAY=:0
export XAUTHORITY=$(ls -t /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
export LP_NUM_THREADS=4
unset MESA_LOADER_DRIVER_OVERRIDE LIBGL_DRI3_DISABLE

WS="$HOME/smart_community_ws"
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1091
source "$WS/install/setup.bash"

SHARE=$(ros2 pkg prefix community_nav)/share/community_nav
WAYPOINTS="$SHARE/config/community_waypoints.yaml"

echo "=============================================================="
echo "  低开销演示（合成 Nav2 + Nav2 就绪后才开始巡检）"
echo "=============================================================="

echo
echo "[1/5] 彻底清理"
systemctl --user stop whc-demo whc-nav whc-sim whc-scene 2>/dev/null
for pat in 'gz sim' 'ros2 launch' controller_server planner_server bt_navigator \
           map_server amcl smoother_server behavior_server velocity_smoother \
           collision_monitor route_server waypoint_follower opennav_docking \
           lifecycle_manager component_container community_patrol bridge_node \
           robot_state_publisher rviz2; do
    pkill -9 -f "$pat" 2>/dev/null
done
sleep 6
rm -f /dev/shm/fastrtps_* /dev/shm/Fast* 2>/dev/null
rm -f /tmp/demo_scene.log /tmp/demo_nav.log /tmp/patrol.log
echo "      残留 gz=$(pgrep -f 'gz sim' | wc -l)  /dev/shm=$(ls /dev/shm | wc -l)项  可用内存=$(free -m | awk '/Mem:/{print $7}')MB"

echo
echo "[2/5] 启动 Gazebo 场景（约 45 秒）"
nohup ros2 launch community_nav sim.launch.py > /tmp/demo_scene.log 2>&1 &
sleep 45
if pgrep -f 'gz sim server' >/dev/null; then
    echo "      ✅ 场景已启动   RTF=$(timeout 12 gz topic -e -t /world/smart_community/stats -n 1 2>/dev/null | grep -a real_time_factor | awk '{print $2}')"
else
    echo "      ❌ 场景启动失败:"; tail -15 /tmp/demo_scene.log; exit 1
fi

echo
echo "[3/5] 启动 Nav2（合成模式；autostart_patrol=false，先不巡检）"
nohup ros2 launch community_nav navigation.launch.py \
    slam:=False rviz:=false autostart_patrol:=false use_composition:=True \
    > /tmp/demo_nav.log 2>&1 &
sleep 5

echo
echo "[4/5] 等 Nav2 真正激活（最多 300 秒）"
NODES="map_server amcl controller_server planner_server bt_navigator"
DEADLINE=$((SECONDS + 300))
while [ $SECONDS -lt $DEADLINE ]; do
    ok=0
    for n in $NODES; do
        s=$(timeout 8 ros2 lifecycle get "/$n" 2>/dev/null | head -1 | tr -d '\r')
        case "$s" in *active*) ok=$((ok + 1)) ;; esac
    done
    [ "$ok" -eq 5 ] && break
    printf '      … %3ds  %d/5 已激活   RTF=%s\n' "$SECONDS" "$ok" \
        "$(timeout 8 gz topic -e -t /world/smart_community/stats -n 1 2>/dev/null | grep -a real_time_factor | awk '{print $2}')"
    sleep 10
done

ok=0
for n in $NODES; do
    s=$(timeout 8 ros2 lifecycle get "/$n" 2>/dev/null | head -1 | tr -d '\r')
    case "$s" in *active*) ok=$((ok + 1)) ;; esac
    printf '        %-18s %s\n' "$n" "${s:-（无响应）}"
done

if [ "$ok" -lt 5 ]; then
    echo "      ⚠️  只有 $ok/5 激活（等待 ${SECONDS}s），仍然继续，日志见 /tmp/demo_nav.log"
else
    echo "      ✅ 5/5 全部激活"
fi

echo
echo "[5/5] Nav2 就绪，现在才开始巡检（替换掉 launch 里那个未启动的 patrol 节点）"
pkill -9 -f 'community_patrol' 2>/dev/null
sleep 2
nohup ros2 run community_patrol patrol_node --ros-args \
    -r __node:=community_patrol \
    -p use_sim_time:=true \
    -p waypoints_file:="$WAYPOINTS" \
    -p autostart:=true \
    -p start_delay:=2.0 \
    > /tmp/patrol.log 2>&1 &
sleep 8
echo "      patrol 进程数=$(pgrep -f community_patrol | wc -l)"
echo "      --- 巡检日志开头 ---"
head -12 /tmp/patrol.log 2>/dev/null | sed 's/^/        /'

echo
echo "      --- 最终快照 ---"
echo "        RTF=$(timeout 10 gz topic -e -t /world/smart_community/stats -n 1 2>/dev/null | grep -a real_time_factor | awk '{print $2}')"
for t in /odom /tf /scan; do
    printf '        %-7s %s\n' "$t" "$(timeout 8 ros2 topic hz $t 2>/dev/null | grep -a 'average rate' | head -1)"
done
echo "        load=$(cut -d' ' -f1 /proc/loadavg)  mem=$(free -m | awk '/Mem:/{print $7}')MB"

echo
echo "=============================================================="
echo "  巡检进行中： tail -f /tmp/patrol.log"
echo "=============================================================="
exec tail -f /tmp/patrol.log
