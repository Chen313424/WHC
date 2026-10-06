"""
用打桩方式真实执行 patrol_node.py 的任务流程，验证核心业务逻辑：
  1. 正常巡检流程（站点顺序、触发识别、结束）
  2. 导航失败后的重试
  3. 超过重试次数后跳过该站、继续后面
  4. 红绿灯等待（红灯等到绿灯 / 等待超时）
  5. 视觉识别结果超时
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import time

# 不生成 __pycache__，避免污染交付目录
sys.dont_write_bytecode = True

# 本文件应放在 <工作区>/docs/tests/ 下。
# 工作区根目录由脚本自身位置推导，保证换机器也能跑。
_HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(_HERE))

NODE_FILE = os.path.join(WS, 'src', 'community_patrol',
                         'community_patrol', 'patrol_node.py')

# 桩模块和临时文件不放工作区里（保持仓库干净），
# 但某些受限环境不允许写系统临时目录，因此带回退。
def _make_tmpdir(prefix):
    """建一个可写的临时目录。

    这里刻意不用 tempfile.mkdtemp：它建出来的目录权限是 0700，
    在某些受限环境（例如带文件沙箱的终端）里会导致"目录建得出来但写不进去"。
    用 os.makedirs + 唯一名字则没有这个问题。
    """
    import uuid
    for base in (os.path.join(WS, '.testtmp'), tempfile.gettempdir()):
        try:
            os.makedirs(base, exist_ok=True)
            d = os.path.join(base, prefix + uuid.uuid4().hex[:8])
            os.makedirs(d)
            return d
        except OSError:
            continue
    raise RuntimeError('找不到可写的临时目录')


_TMPROOT = _make_tmpdir('patrol_test_')
STUB = os.path.join(_TMPROOT, 'stubs')

# ==============================================================================
#  造桩
# ==============================================================================
STUBS = {
    'rclpy/__init__.py': '''
_ok = True
SPIN_HOOK = None
def init(*a, **k): pass
def ok(): return _ok
def shutdown(): pass
def spin(node): pass
def spin_once(node, timeout_sec=None):
    if SPIN_HOOK is not None:
        SPIN_HOOK(node)
def spin_until_future_complete(node, fut, timeout_sec=None): pass
''',
    'rclpy/node.py': '''
class _Param:
    def __init__(self, v): self.value = v
class _Log:
    def __init__(self, sink): self.sink = sink
    def info(self, m, **k): self.sink.append(('INFO', m))
    def warn(self, m, **k): self.sink.append(('WARN', m))
    def error(self, m, **k): self.sink.append(('ERROR', m))
    def debug(self, m, **k): self.sink.append(('DEBUG', m))
class _Clock:
    class _Now:
        def to_msg(self): return 0
    def now(self): return _Clock._Now()
class Node:
    LOG = []
    PARAM_OVERRIDES = {}
    def __init__(self, name): self._name = name; self._params = {}
    def declare_parameter(self, name, default=None):
        self._params[name] = Node.PARAM_OVERRIDES.get(name, default)
    def get_parameter(self, name): return _Param(self._params.get(name))
    def create_publisher(self, *a, **k): return _Pub()
    def create_subscription(self, *a, **k): return _Sub()
    def get_logger(self): return _Log(Node.LOG)
    def get_clock(self): return _Clock()
    def destroy_node(self): pass
class _Pub:
    PUBLISHED = []
    def publish(self, msg): _Pub.PUBLISHED.append(msg)
class _Sub:
    def __init__(self): pass
''',
    'rclpy/action.py': '''
class _Future:
    def __init__(self, result=None, done=True): self._r = result; self._d = done
    def done(self): return self._d
    def result(self): return self._r
class _Result:
    def __init__(self, status): self.status = status
class _GoalHandle:
    def __init__(self, status):
        self.accepted = True
        self._status = status
        self.cancelled = False
    def get_result_async(self): return _Future(_Result(self._status))
    def cancel_goal_async(self): self.cancelled = True
class ActionClient:
    SERVER_READY = True
    OUTCOMES = []          # 每次 send_goal 依次取一个 status
    CALLS = []
    def __init__(self, node, action_type, name): self.handles = []
    def wait_for_server(self, timeout_sec=None): return ActionClient.SERVER_READY
    def send_goal_async(self, goal, feedback_callback=None):
        st = ActionClient.OUTCOMES.pop(0) if ActionClient.OUTCOMES else 4
        ActionClient.CALLS.append(st)
        h = _GoalHandle(st)
        self.handles.append(h)
        return _Future(h)
''',
    'geometry_msgs/__init__.py': '',
    'geometry_msgs/msg.py': '''
class _Header:
    def __init__(self): self.frame_id=''; self.stamp=None
class _Pos:
    def __init__(self): self.x=self.y=self.z=0.0
class _Ori:
    def __init__(self): self.x=self.y=self.z=self.w=0.0
class _Pose:
    def __init__(self): self.position=_Pos(); self.orientation=_Ori()
class PoseStamped:
    def __init__(self): self.header=_Header(); self.pose=_Pose()
''',
    'nav2_msgs/__init__.py': '',
    'nav2_msgs/action.py': '''
class NavigateToPose:
    class Goal:
        def __init__(self): self.pose = None
''',
    'std_msgs/__init__.py': '',
    'std_msgs/msg.py': '''
class String:
    def __init__(self, data=''): self.data = data
''',
}

shutil.rmtree(STUB, ignore_errors=True)
for rel, content in STUBS.items():
    p = os.path.join(STUB, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)


def load_node_module():
    # 注意：不能清理 sys.modules 里的桩模块！
    # 否则 patrol_node 里 import 到的 ActionClient 会和测试里引用的
    # 不是同一个类对象，测试设置的行为就传不进去。
    if STUB not in sys.path:
        sys.path.insert(0, STUB)
    spec = importlib.util.spec_from_file_location('patrol_under_test', NODE_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 让 time.sleep 变成空操作，测试才不会真的等
_real_sleep = time.sleep
time.sleep = lambda s: None

# ★ 必须在这里就把桩目录加进 sys.path，
#   因为下面顶层的 import rclpy 就要用到它
if STUB not in sys.path:
    sys.path.insert(0, STUB)

import rclpy                                          # noqa: E402  (打桩模块)
from rclpy.node import Node, _Pub                     # noqa: E402
from rclpy.action import ActionClient                 # noqa: E402

results = []


def record(status, name, detail=''):
    results.append((status, name, detail))


def write_yaml(path, waypoints, retry=1, light_timeout=1.0, capture_timeout=1.0):
    lines = ['frame_id: map', 'loop: false', f'retry_count: {retry}',
             'default_dwell: 0.0', 'navigate_timeout: 1.0', 'waypoints:']
    for w in waypoints:
        lines.append(f"  - id: {w['id']}")
        for k, v in w.items():
            if k == 'id':
                continue
            lines.append(f'    {k}: {v}')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


TMP = os.path.join(_TMPROOT, 'wptest.yaml')

# ★ 桩节点不解析真实 ROS 参数，这里指定站点表路径，
#   否则 patrol_node 会去读默认的 ~/smart_community_ws/... 而失败
Node.PARAM_OVERRIDES['waypoints_file'] = TMP

# ==============================================================================
#  测试 1：正常流程
# ==============================================================================
try:
    mod = load_node_module()
    write_yaml(TMP, [
        {'id': 'start', 'x': 0.0, 'y': 0.0, 'yaw': 0.0, 'action': 'none', 'dwell': 0.0},
        {'id': 'cap1', 'x': 1.0, 'y': 1.0, 'yaw': 0.0, 'action': 'capture',
         'dwell': 0.0, 'task': 'crowd_count', 'zone': 'A'},
        {'id': 'fin', 'x': 0.0, 'y': 0.0, 'yaw': 0.0, 'action': 'finish', 'dwell': 0.0},
    ])
    ActionClient.OUTCOMES = [4, 4, 4]
    ActionClient.CALLS = []
    mod.rclpy.SPIN_HOOK = None

    node = mod.PatrolNode()
    node._params['waypoints_file'] = TMP
    node.waypoints = []
    node._load_waypoints(TMP)

    # 模拟视觉组返回结果
    orig_capture = node.do_capture

    def fake_capture(wp):
        return '{"count": 16}'
    node.do_capture = fake_capture

    ok = node.run_patrol()
    n_calls = len(ActionClient.CALLS)
    record('PASS' if (ok and n_calls == 3) else 'FAIL',
           '测试1 正常流程',
           f'结果={ok}, 发送导航目标 {n_calls} 次（期望 3）, 站点数={len(node.waypoints)}')
except Exception as exc:                                    # noqa: BLE001
    import traceback
    record('FAIL', '测试1 正常流程', f'{type(exc).__name__}: {exc}\n{traceback.format_exc()[-400:]}')

# ==============================================================================
#  测试 2：第一个站点先失败一次再成功（验证重试）
# ==============================================================================
try:
    mod = load_node_module()
    write_yaml(TMP, [
        {'id': 'a', 'x': 0.0, 'y': 0.0, 'yaw': 0.0, 'action': 'none', 'dwell': 0.0},
        {'id': 'b', 'x': 1.0, 'y': 0.0, 'yaw': 0.0, 'action': 'none', 'dwell': 0.0},
    ], retry=1)
    # 站点 a：第一次 6(失败) → 重试 4(成功)；站点 b：4(成功)
    ActionClient.OUTCOMES = [6, 4, 4]
    ActionClient.CALLS = []
    node = mod.PatrolNode()
    node._load_waypoints(TMP)
    ok = node.run_patrol()
    calls = len(ActionClient.CALLS)
    record('PASS' if (ok and calls == 3) else 'FAIL',
           '测试2 失败后重试',
           f'结果={ok}, 导航调用 {calls} 次（期望 3：失败1次+重试成功1次+第2站1次）')
except Exception as exc:                                    # noqa: BLE001
    record('FAIL', '测试2 失败后重试', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 3：全部重试都失败 → 跳过该站，继续后面的站点
# ==============================================================================
try:
    mod = load_node_module()
    write_yaml(TMP, [
        {'id': 'bad', 'x': 0.0, 'y': 0.0, 'yaw': 0.0, 'action': 'none', 'dwell': 0.0},
        {'id': 'good', 'x': 1.0, 'y': 0.0, 'yaw': 0.0, 'action': 'none', 'dwell': 0.0},
    ], retry=1)
    # bad: 6, 6 (原试+1次重试都失败)  good: 4
    ActionClient.OUTCOMES = [6, 6, 4]
    ActionClient.CALLS = []
    node = mod.PatrolNode()
    node._load_waypoints(TMP)
    ok = node.run_patrol()
    calls = len(ActionClient.CALLS)
    log = ' | '.join(m for lvl, m in Node.LOG if 'bad' in m and '跳过' in m)
    record('PASS' if (not ok and calls == 3 and log) else 'FAIL',
           '测试3 失败站点被跳过、流程继续',
           f'整体成功={ok}(期望False), 导航调用 {calls} 次, 跳过日志={"有" if log else "无"}')
except Exception as exc:                                    # noqa: BLE001
    record('FAIL', '测试3 跳过逻辑', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 4：红绿灯 —— 红灯等待，中途变绿
# ==============================================================================
try:
    mod = load_node_module()
    node = mod.PatrolNode()
    node.current_light = 'RED'
    ticks = {'n': 0}

    def hook(n):
        ticks['n'] += 1
        if ticks['n'] >= 3:
            n.current_light = 'GREEN'
    mod.rclpy.SPIN_HOOK = hook

    wp = {'id': 'tl_1', 'name': '红绿灯1', 'x': 0.0, 'y': 0.0, 'yaw': 0.0,
          'action': 'traffic_light', 'dwell': 0.0, 'light_id': 'tl_1'}
    got = node.do_traffic_light(wp)
    record('PASS' if got else 'FAIL', '测试4 红灯等到绿灯后放行',
           f'返回={got}(期望True), spin 次数={ticks["n"]}(应在第3次变绿)')
except Exception as exc:                                    # noqa: BLE001
    record('FAIL', '测试4 红绿灯等待', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 5：红绿灯 —— 一直红灯，等待超时后不中断流程
# ==============================================================================
try:
    mod = load_node_module()
    node = mod.PatrolNode()
    node.light_wait_timeout = 0.3          # 缩短超时，测试才跑得快
    node.current_light = 'RED'
    mod.rclpy.SPIN_HOOK = lambda n: None

    wp = {'id': 'tl_2', 'name': '红绿灯2', 'x': 0.0, 'y': 0.0, 'yaw': 0.0,
          'action': 'traffic_light', 'dwell': 0.0, 'light_id': 'tl_2'}
    t0 = time.monotonic()
    got = node.do_traffic_light(wp)
    dt = time.monotonic() - t0
    warned = any('超时' in m for lvl, m in Node.LOG)
    record('PASS' if (not got and warned and dt < 5) else 'FAIL',
           '测试5 红灯超时后放行（不卡死）',
           f'返回={got}(期望False), 耗时={dt:.2f}s, 有超时日志={warned}')
except Exception as exc:                                    # noqa: BLE001
    record('FAIL', '测试5 红绿灯超时', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 6：视觉识别结果超时
# ==============================================================================
try:
    mod = load_node_module()
    node = mod.PatrolNode()
    node.capture_wait_timeout = 0.3
    node.last_detection = None
    mod.rclpy.SPIN_HOOK = lambda n: None
    _Pub.PUBLISHED.clear()

    wp = {'id': 'cap_x', 'name': '识别点', 'x': 0.0, 'y': 0.0, 'yaw': 0.0,
          'action': 'capture', 'dwell': 0.0, 'task': 'plate_ocr', 'zone': '',
          'slot': 1}
    got = node.do_capture(wp)
    published = len(_Pub.PUBLISHED) > 0
    payload = _Pub.PUBLISHED[0].data if published else ''
    good = (got is None) and published and ('plate_ocr' in payload)
    record('PASS' if good else 'FAIL', '测试6 识别结果超时处理',
           f'返回={got}(期望None), 已发触发消息={published}, 载荷含task={("plate_ocr" in payload)}')
except Exception as exc:                                    # noqa: BLE001
    record('FAIL', '测试6 识别超时', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 7：站点表校验（缺坐标应报错）
# ==============================================================================
try:
    mod = load_node_module()
    bad = os.path.join(_TMPROOT, 'wptest_bad.yaml')
    with open(bad, 'w', encoding='utf-8') as f:
        f.write('waypoints:\n  - id: x\n    y: 1.0\n')
    node = mod.PatrolNode()
    caught = False
    try:
        node._load_waypoints(bad)
    except ValueError as e:
        caught = 'x/y' in str(e) or '缺少' in str(e)
    record('PASS' if caught else 'FAIL', '测试7 站点表缺少坐标时校验报错',
           f'抛出可识别错误={caught}')
    os.remove(bad)
except Exception as exc:                                    # noqa: BLE001
    record('FAIL', '测试7 配置校验', f'{type(exc).__name__}: {exc}')

# ==============================================================================
time.sleep = _real_sleep
shutil.rmtree(_TMPROOT, ignore_errors=True)
try:
    os.rmdir(os.path.join(WS, '.testtmp'))      # 若已空则一并删掉
except OSError:
    pass

print('=' * 76)
for status, name, detail in results:
    print(f'[{status}] {name}')
    print(f'        {detail}')
print('=' * 76)
bad = [r for r in results if r[0] == 'FAIL']
print(f'{len(results) - len(bad)}/{len(results)} 通过')
sys.exit(1 if bad else 0)
