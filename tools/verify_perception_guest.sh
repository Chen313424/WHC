#!/usr/bin/env bash
# =============================================================================
#  感知端到端验证（在 guest 里跑，不需要 Nav2）
#
#  验证四件事：
#    1. yolo_detector 能加载 whc_yolo.pt（6 类）并持续输出 /perception/detections
#    2. /traffic_light/state 会随时间变化（绿→黄→红，15/5/10 周期 30s）
#    3. 模拟巡检发 /patrol/capture，能收到 /detection/result（三种 task 都测）
#    4. 结果 JSON 里的字段符合契约（人数/车牌字符）
#
#  用法： bash verify_perception_guest.sh
# =============================================================================
set +e
set +u

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export DISPLAY=:0
export XAUTHORITY=$(ls -t /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
export LP_NUM_THREADS=4
unset MESA_LOADER_DRIVER_OVERRIDE LIBGL_DRI3_DISABLE

# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1091
source "$HOME/smart_community_ws/install/setup.bash"

echo "=============================================================="
echo "  感知端到端验证"
echo "=============================================================="

echo
echo "[1/4] 清理旧进程"
for pat in 'gz sim' 'ros2 launch' yolo_detector traffic_controller bridge_node \
           robot_state_publisher; do
    pkill -9 -f "$pat" 2>/dev/null
done
sleep 5
rm -f /dev/shm/fastrtps_* /dev/shm/Fast* 2>/dev/null

echo
echo "[2/4] 起 Gazebo 场景（约 45 秒）"
nohup ros2 launch community_nav sim.launch.py > /tmp/v_scene.log 2>&1 &
sleep 45
if pgrep -f 'gz sim server' >/dev/null; then
    echo "      ✅ 场景已启动"
else
    echo "      ❌ 场景失败:"; tail -12 /tmp/v_scene.log; exit 1
fi

echo
echo "[3/4] 起感知（yolo_detector + traffic_controller）"
nohup ros2 launch smart_community_perception perception.launch.py > /tmp/v_perc.log 2>&1 &
sleep 45
echo "      节点: $(ros2 node list 2>/dev/null | grep -cE 'yolo_detector|traffic_controller') / 2"
echo "      --- 感知日志关键行 ---"
grep -a -iE '加载模型|模型类别|项目类别|已启动|error|Traceback' /tmp/v_perc.log 2>/dev/null | head -8 | cut -c1-150

echo
echo "[4/4] 验证"
echo "  --- /perception/detections 频率 ---"
timeout 12 ros2 topic hz /perception/detections 2>/dev/null | grep -a 'average rate' | head -1
echo "  --- 检测消息样本 ---"
timeout 10 ros2 topic echo /perception/detections --once 2>/dev/null | head -c 600
echo
echo "  --- /traffic_light/state（采样 36 秒，应看到状态变化）---"
timeout 36 ros2 topic echo /traffic_light/state 2>/dev/null | grep -a 'data:' | head -8

echo
echo "  --- 模拟巡检请求（三种 task）---"
python3 - <<'PY'
import json, sys, time
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class Probe(Node):
    def __init__(self):
        super().__init__('patrol_contract_probe')
        self.pub = self.create_publisher(String, '/patrol/capture', 10)
        self.got = []
        self.create_subscription(String, '/detection/result', self.on_result, 10)

    def on_result(self, msg):
        self.got.append(msg.data)

    def ask(self, payload, wait=8.0):
        self.got.clear()
        self.pub.publish(String(data=json.dumps(payload, ensure_ascii=False)))
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and not self.got:
            rclpy.spin_once(self, timeout_sec=0.1)
        return self.got[0] if self.got else None


rclpy.init()
n = Probe()
time.sleep(2.0)          # 等订阅建立
cases = [
    {'waypoint_id': 'park_3', 'task': 'plate_ocr', 'slot': 3, 'zone': '', 'target': '', 'stamp': 0},
    {'waypoint_id': 'zone_a', 'task': 'crowd_count', 'slot': '', 'zone': 'A', 'target': '', 'stamp': 0},
    {'waypoint_id': 'outsider_n', 'task': 'outsider_detect', 'slot': '', 'zone': '', 'target': 'person_f1', 'stamp': 0},
    {'waypoint_id': 'x', 'task': '不认识的task', 'slot': '', 'zone': '', 'target': '', 'stamp': 0},
]
ok = 0
for c in cases:
    raw = n.ask(c, wait=8.0)
    if raw is None:
        print(f"  [FAIL] task={c['task']:<16} 8 秒内没有回应（巡检会等到超时！）")
        continue
    try:
        d = json.loads(raw)
    except Exception as exc:
        print(f"  [FAIL] task={c['task']:<16} 回应不是合法 JSON: {raw[:80]} ({exc})")
        continue
    ok += 1
    keys = {k: d.get(k) for k in ('ok', 'reason', 'person_community', 'person_noncommunity',
                                  'person_total', 'plate', 'plate_mode', 'target_hint', 'target_found')
            if k in d}
    print(f"  [ok]   task={c['task']:<16} -> {json.dumps(keys, ensure_ascii=False)}")
print(f"\n  接口契约: {ok}/{len(cases)} 有回应")
n.destroy_node()
rclpy.shutdown()
sys.exit(0 if ok == len(cases) else 1)
PY
echo "  客户端退出码=$?"

echo
echo "=============================================================="
echo "  验证结束（场景与感知仍在运行，方便继续观察）"
echo "=============================================================="
