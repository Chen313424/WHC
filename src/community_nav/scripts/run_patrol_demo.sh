#!/usr/bin/env bash
# ============================================================================
#  智慧社区巡检 —— 一键演示脚本（导航组）
# ============================================================================
#
#  用法：
#      bash src/community_nav/scripts/run_patrol_demo.sh              # 自动巡检
#      bash src/community_nav/scripts/run_patrol_demo.sh --no-rviz    # 不开 RViz
#      bash src/community_nav/scripts/run_patrol_demo.sh --build      # 先编译
#
#  这个脚本做三件事：
#      1. 彻底清理上一次的残留进程与 Fast DDS 共享内存
#      2. 启动 Gazebo 社区场景
#      3. 以【加载地图 + AMCL 定位】模式启动 Nav2，并自动开始多点巡检
#
# ----------------------------------------------------------------------------
#  ★★ 为什么要"彻底清理"，而不是简单 pkill
#
#  实测踩到过：连续第二次启动 Nav2 时，节点全部卡在 inactive/unconfigured，
#  日志里刷的是：
#      [RTPS_TRANSPORT_SHM Error] Failed init_port fastrtps_port7014:
#          open_and_lock_file failed
#      [lifecycle_manager_navigation] Failed to change state for node:
#          planner_server. Exception: change_state service is not available!
#
#  原因：Fast DDS 用共享内存做进程间传输，会在 /dev/shm 下建
#  fastrtps_port* 文件。上一轮的 Nav2 节点被 kill 后，这些文件没被清理，
#  下一轮新进程抢不到端口，【连 DDS 都初始化不了】—— 于是生命周期服务
#  根本不存在，Nav2 必然配置失败。
#
#  而 `ros2 launch` 是父进程，它派生的各个节点（controller_server、
#  planner_server...）的进程名里没有 "ros2 launch"，
#  只 kill 父进程【杀不干净】。必须按节点名逐个杀，再清 /dev/shm。
#
#  这个坑很隐蔽：报错指向 DDS 端口，完全看不出是"上一轮没清干净"。
# ============================================================================

# ★ 不要用 `set -u`（nounset）！
#   ROS 2 的 /opt/ros/jazzy/setup.bash 里有 `$AMENT_TRACE_SETUP_FILES` 这类
#   未定义变量，开了 -u 会在 source 那一步直接报
#       /opt/ros/jazzy/setup.bash: line 8: AMENT_TRACE_SETUP_FILES: unbound variable
#   脚本当场退出。用 pipefail 就够了。
set -o pipefail

BUILD=0
USE_RVIZ=1
for arg in "$@"; do
    case "$arg" in
        --build)   BUILD=1 ;;
        --no-rviz) USE_RVIZ=0 ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
    esac
done

# ---- 定位工作空间 ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="$(cd "$SCRIPT_DIR/../../.." && pwd)"
if [ ! -f "$WS/install/setup.bash" ] && [ "$BUILD" -eq 0 ]; then
    echo "找不到 $WS/install/setup.bash，自动改为先编译"
    BUILD=1
fi

echo "=============================================================="
echo "  智慧社区巡检演示"
echo "  工作空间: $WS"
echo "=============================================================="

if [ "$BUILD" -eq 1 ]; then
    echo
    echo "[0/4] 编译"
    # shellcheck disable=SC1091
    source /opt/ros/jazzy/setup.bash
    (cd "$WS" && colcon build --symlink-install) || exit 1
fi

# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1091
source "$WS/install/setup.bash"

# WSL 下没有 GPU 直通时，必须走软件渲染，否则 Gazebo 一起来就段错误
if [ ! -d /dev/dri ]; then
    export LIBGL_ALWAYS_SOFTWARE=1
fi

# ---------------------------------------------------------------------------
# 彻底清理
# ---------------------------------------------------------------------------
echo
echo "[1/4] 清理上一轮残留"

KILL_PATTERNS=(
    "gz sim" "gz-sim" "ruby.*gz"
    "ros2 launch" "ros2 daemon"
    "nav2" "controller_server" "planner_server" "bt_navigator"
    "behavior_server" "velocity_smoother" "smoother_server"
    "waypoint_follower" "collision_monitor" "docking_server" "route_server"
    "map_server" "amcl" "lifecycle_manager"
    "slam_toolbox"
    "bridge_node" "robot_state_publisher"
    "community_patrol" "rviz2"
    "gz sim server" "gz sim gui"
)
for p in "${KILL_PATTERNS[@]}"; do
    pkill -9 -f "$p" 2>/dev/null
done
sleep 5

# ★ 清 Fast DDS 的共享内存残留（不清就会 "Failed init_port ... open_and_lock_file"）
rm -f /dev/shm/fastrtps_* /dev/shm/Fast* 2>/dev/null
ros2 daemon stop >/dev/null 2>&1
sleep 2

left=$(pgrep -f "gz sim|nav2|slam_toolbox|bridge_node" | wc -l)
echo "      残留进程: $left    /dev/shm 剩余: $(ls /dev/shm 2>/dev/null | wc -l) 项"

# ---------------------------------------------------------------------------
# 启动场景
# ---------------------------------------------------------------------------
echo
echo "[2/4] 启动 Gazebo 社区场景（约 45 秒）"
nohup ros2 launch community_nav sim.launch.py > /tmp/demo_scene.log 2>&1 &
sleep 45

if ! pgrep -f "gz sim server" >/dev/null; then
    echo "      ❌ 场景启动失败，日志末尾："
    tail -20 /tmp/demo_scene.log
    exit 1
fi
echo "      ✅ 场景已启动"

# ---------------------------------------------------------------------------
# 启动导航 + 自动巡检
# ---------------------------------------------------------------------------
echo
echo "[3/4] 启动 Nav2（加载地图 + AMCL 定位）+ 自动多点巡检"
RVIZ_ARG="rviz:=true"
[ "$USE_RVIZ" -eq 0 ] && RVIZ_ARG="rviz:=false"
echo "      参数: slam:=False  $RVIZ_ARG  autostart_patrol:=true"

nohup ros2 launch community_nav navigation.launch.py \
    slam:=False $RVIZ_ARG autostart_patrol:=true > /tmp/demo_nav.log 2>&1 &

# ★★ 必须【轮询等待】，不能固定 sleep 一个数字
#
#   原因：Gazebo 软件渲染会吃掉 400%+ 的 CPU，Nav2 有 15 个节点要逐个
#   走完 configure -> activate。CPU 抢不过来时，某些节点要一两分钟才就绪。
#   实测：固定等 70 秒时，controller/planner/bt 已经 active，
#         但 map_server / amcl 还停在 "Creating" —— 看起来像启动失败，
#         其实再等一会儿就好。
#   固定等待要么等太久（浪费时间），要么等不够（误报失败）。轮询两者都能避免。
echo "      等待 Nav2 激活（轮询，最多 240 秒）…"
NODES="map_server amcl controller_server planner_server bt_navigator"
DEADLINE=$((SECONDS + 240))
while [ $SECONDS -lt $DEADLINE ]; do
    ok=0
    for n in $NODES; do
        s=$(timeout 5 ros2 lifecycle get "/$n" 2>/dev/null | head -1 | tr -d '\r')
        case "$s" in
            *active*) ok=$((ok + 1)) ;;
        esac
    done
    if [ "$ok" -eq 5 ]; then
        break
    fi
    printf '        … %ds  %d/5 已激活\n' "$SECONDS" "$ok"
    sleep 10
done

echo
echo "      --- 生命周期节点状态 ---"
ok=0
for n in $NODES; do
    s=$(timeout 5 ros2 lifecycle get "/$n" 2>/dev/null | head -1 | tr -d '\r')
    case "$s" in
        *active*) ok=$((ok + 1)) ;;
    esac
    printf '        %-20s %s\n' "$n" "${s:-（无响应）}"
done

if [ "$ok" -lt 5 ]; then
    echo
    echo "      ⚠️ 只有 $ok/5 个节点激活（已等待 ${SECONDS}s）。"
    echo "         ① 最常见是上一轮的 DDS 共享内存没清干净 —— 重跑本脚本即可。"
    echo "         ② 也可能是 CPU 被软件渲染占满，再等一会儿或关掉 RViz。"
    grep -a -iE "\[ERROR\]" /tmp/demo_nav.log | grep -av "collision_monitor" | tail -6
    exit 1
fi

# ---------------------------------------------------------------------------
echo
echo "[4/4] 巡检进行中（11 个站点，约 10 分钟）"
echo "=============================================================="
echo "  实时跟踪： tail -f /tmp/demo_nav.log"
echo "  只看巡检： tail -f /tmp/demo_nav.log | grep community_patrol"
echo "  停止全部： pkill -9 -f 'gz sim'; pkill -9 -f nav2; pkill -9 -f ros2"
echo "=============================================================="
echo

exec tail -f /tmp/demo_nav.log
