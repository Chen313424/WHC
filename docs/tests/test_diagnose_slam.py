"""
用打桩方式验证 diagnose_slam.py 的诊断逻辑：
  1. 激光读数在变            -> 结论应为"机器人确实在移动"（返回 2）
  2. 里程计在变但激光不变     -> 结论应为"卡住空转"（返回 3）
  3. 完全收不到激光           -> 结论应为"仿真没起来"（返回 1）
  4. 里程计和激光都不动       -> 结论应为"没按遥控键/卡住"（返回 4）
  5. 快照输出包含全部五个环节
"""
import importlib.util
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(_HERE))
SCRIPT = os.path.join(WS, 'docs', 'scripts', 'diagnose_slam.py')


def _make_tmpdir(prefix):
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


TMP = _make_tmpdir('diag_test_')
STUB = os.path.join(TMP, 'stubs')

STUBS = {
    'rclpy/__init__.py': (
        'def init(*a, **k): pass\n'
        'def ok(): return True\n'
        'def shutdown(): pass\n'
        'def spin_once(node, timeout_sec=None): pass\n'),
    'rclpy/node.py': (
        'class _Log:\n'
        '    def info(self, m, **k): pass\n'
        '    def warn(self, m, **k): pass\n'
        '    def error(self, m, **k): pass\n'
        'class _Pub:\n'
        '    PUBLISHED = []\n'
        '    def publish(self, msg): _Pub.PUBLISHED.append(msg)\n'
        'class Node:\n'
        '    def __init__(self, name): self.name = name; self.subs = {}; self.qos = {}\n'
        '    def create_subscription(self, msg_type, topic, cb, qos=None):\n'
        '        self.subs[topic] = cb\n'
        '        self.qos[topic] = qos\n'
        '        return None\n'
        '    def create_publisher(self, msg_type, topic, qos=10):\n'
        '        return _Pub()\n'
        '    def create_timer(self, period, cb):\n'
        '        self.timer_cb = cb\n'
        '        return None\n'
        '    def get_logger(self): return _Log()\n'
        '    def destroy_node(self): pass\n'),
    'rclpy/qos.py': (
        'class QoSProfile:\n'
        '    def __init__(self, depth=10):\n'
        '        self.depth = depth\n'
        '        self.durability = None\n'
        '        self.reliability = None\n'
        'class QoSDurabilityPolicy:\n'
        '    TRANSIENT_LOCAL = 1\n'
        'class QoSReliabilityPolicy:\n'
        '    RELIABLE = 1\n'),
    'sensor_msgs/__init__.py': '',
    'sensor_msgs/msg.py': (
        'class LaserScan:\n'
        '    def __init__(self, ranges=None): self.ranges = ranges or []\n'),
    'nav_msgs/__init__.py': '',
    'nav_msgs/msg.py': (
        'class _Pos:\n'
        '    def __init__(self, x=0.0, y=0.0): self.x = x; self.y = y\n'
        'class _Pose:\n'
        '    def __init__(self, x=0.0, y=0.0): self.pose = type("P",(),{"position":_Pos(x,y)})()\n'
        'class Odometry:\n'
        '    def __init__(self, x=0.0, y=0.0):\n'
        '        self.pose = type("Q",(),{"pose": type("R",(),{"position":_Pos(x,y)})()})()\n'
        'class OccupancyGrid:\n'
        '    def __init__(self, data=None): self.data = data or []\n'),
    'geometry_msgs/__init__.py': '',
    'geometry_msgs/msg.py': (
        'class _V:\n'
        '    def __init__(self, x=0.0): self.x = x\n'
        'class _T:\n'
        '    def __init__(self, z=0.0): self.z = z\n'
        'class Twist:\n'
        '    def __init__(self, lx=0.0, az=0.0):\n'
        '        self.linear = _V(lx); self.angular = _T(az)\n'),
    'rosgraph_msgs/__init__.py': '',
    'rosgraph_msgs/msg.py': 'class Clock:\n    def __init__(self): pass\n',
}
shutil.rmtree(STUB, ignore_errors=True)
for rel, content in STUBS.items():
    p = os.path.join(STUB, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)

if STUB not in sys.path:
    sys.path.insert(0, STUB)

spec = importlib.util.spec_from_file_location('diag_mod', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

LaserScan = mod.LaserScan
Odometry = mod.Odometry
OccupancyGrid = mod.OccupancyGrid
Twist = mod.Twist
Clock = mod.Clock

results = []


def record(ok, name, detail=''):
    results.append((bool(ok), name, detail))


def feed(node, key, msg):
    node.subs[key](msg)


# ------------------------------------------------------------------ 测试 1
try:
    n = mod.Diag()
    for i in range(10):
        feed(n, '/scan', LaserScan([1.0 + i * 0.5, 2.0, 3.0]))
        feed(n, '/odom', Odometry(x=i * 0.1, y=0.0))
        feed(n, '/clock', Clock())
        feed(n, '/cmd_vel', Twist(lx=0.15))
    feed(n, '/map', OccupancyGrid([100] * 50))
    feed(n, '/map', OccupancyGrid([100] * 80))
    snap = n.snapshot(3.0)
    rc = n.verdict()
    ok = (rc == 2) and ('/scan' in snap) and ('/odom' in snap) and ('/map' in snap)
    record(ok, '测试1 激光在变 → 判定为"机器人在移动"',
           f'返回码={rc}(期望2)  快照含五个环节={"是" if ok else "否"}')
except Exception as exc:                                        # noqa: BLE001
    import traceback
    record(False, '测试1', f'{type(exc).__name__}: {exc}\n{traceback.format_exc()[-300:]}')

# ------------------------------------------------------------------ 测试 2
try:
    n = mod.Diag()
    for i in range(10):
        # 激光读数【完全不变】，但里程计在走 —— 典型卡住空转
        feed(n, '/scan', LaserScan([1.5, 2.5, 3.5]))
        feed(n, '/odom', Odometry(x=i * 0.1, y=0.0))
    feed(n, '/map', OccupancyGrid([100] * 10))   # SLAM 正常，排除掉那一类
    rc = n.verdict()
    record(rc == 3, '测试2 里程计在变但激光不变 → 判定为"卡住空转"',
           f'返回码={rc}(期望3)')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试2', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 3
try:
    n = mod.Diag()
    feed(n, '/clock', Clock())
    rc = n.verdict()
    record(rc == 1, '测试3 完全没收到激光 → 判定为"仿真没起来"',
           f'返回码={rc}(期望1)')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试3', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 4
try:
    n = mod.Diag()
    for i in range(5):
        feed(n, '/scan', LaserScan([2.0, 3.0]))
        feed(n, '/odom', Odometry(x=0.0, y=0.0))
    feed(n, '/map', OccupancyGrid([100] * 10))   # SLAM 正常，排除掉那一类
    rc = n.verdict()
    record(rc == 4, '测试4 激光和里程计都不动 → 判定为"没按遥控键/卡住"',
           f'返回码={rc}(期望4)')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试4', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 5
try:
    n = mod.Diag()
    feed(n, '/scan', LaserScan([1.0, float('inf'), 3.0]))
    feed(n, '/odom', Odometry(x=1.0, y=2.0))
    feed(n, '/cmd_vel', Twist(lx=0.2))
    feed(n, '/map', OccupancyGrid([0, 100, 0]))
    snap = n.snapshot(2.0)
    need = ['/clock', '/scan', '/odom', '/cmd_vel', '/map', '激光指纹', '地图占据栅格']
    miss = [k for k in need if k not in snap]
    record(not miss, '测试5 快照包含全部关键指标', f'缺少={miss if miss else "无"}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试5', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 6
try:
    # fingerprint 应忽略 inf 和过小的值
    fp = mod.fingerprint(LaserScan([1.0, 2.0, float('inf'), 0.001, 3.0]))
    ok = fp == (3, 6.0)
    record(ok, '测试6 激光指纹忽略 inf 与过小值', f'指纹={fp}(期望(3, 6.0))')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试6', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 7
try:
    # --spin：应注册定时器，并发出指定角速度的指令
    Pub = None
    import rclpy.node as _n
    Pub = _n._Pub
    Pub.PUBLISHED.clear()
    n = mod.Diag(spin_rate=0.4)
    has_timer = getattr(n, 'timer_cb', None) is not None
    n._publish_spin()
    n._publish_spin()
    vals = [m.angular.z for m in Pub.PUBLISHED]
    ok = has_timer and vals == [0.4, 0.4]
    record(ok, '测试7 --spin 注册定时器并发出旋转指令',
           f'有定时器={has_timer}  发出的角速度={vals}(期望[0.4, 0.4])')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试7', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 8
try:
    import rclpy.node as _n
    _n._Pub.PUBLISHED.clear()
    n = mod.Diag(spin_rate=0.4)
    n.stop()
    vals = [m.angular.z for m in _n._Pub.PUBLISHED]
    ok = vals == [0.0]
    record(ok, '测试8 诊断结束后会发停车指令',
           f'发出的角速度={vals}(期望[0.0])')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试8', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 9
try:
    # spin_rate=0 时不应注册定时器（改为人工遥控）
    n = mod.Diag(spin_rate=0.0)
    ok = getattr(n, 'timer_cb', None) is None
    record(ok, '测试9 --spin 0 时不自动控制（交给人工遥控）',
           f'未注册定时器={ok}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试9', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 10
try:
    # ★ 机器人确实在动，但 SLAM 一直没发地图 —— 不能误判成"RViz 显示问题"
    n = mod.Diag()
    for i in range(10):
        feed(n, '/scan', LaserScan([1.0 + i * 0.5, 2.0, 3.0]))
        feed(n, '/odom', Odometry(x=i * 0.1, y=0.0))
    # 注意：完全不给 /map
    rc = n.verdict()
    record(rc == 5, '测试10 激光在变但 /map 没发布 → 判定为"SLAM 没工作"（不误判成 RViz）',
           f'返回码={rc}(期望5)')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试10', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 11
try:
    # 地图只发过一次（且是在前面几轮），累计计数不应被重置
    n = mod.Diag()
    feed(n, '/map', OccupancyGrid([100] * 10))
    for i in range(10):
        feed(n, '/scan', LaserScan([1.0 + i * 0.5, 2.0]))
        feed(n, '/odom', Odometry(x=i * 0.1, y=0.0))
    n.snapshot(3.0)          # 这一步会重置 n_map
    rc = n.verdict()
    record(rc == 2, '测试11 地图计数不被 snapshot 重置（有历史地图则不判 5）',
           f'返回码={rc}(期望2)  累计地图数={n.map_total}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试11', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 12
try:
    # ★ /map 必须用默认（VOLATILE）QoS，否则会因 QoS 不兼容漏收地图，
    #   把"SLAM 正常"误报成"SLAM 没工作"
    n = mod.Diag()
    q = n.qos.get('/map', 'MISSING')
    ok = (q == 10)          # 默认 depth=10，而不是自定义的 QoSProfile 对象
    record(ok, '测试12 /map 订阅使用默认 QoS（避免 QoS 不兼容导致假阴性）',
           f'/map 的 QoS = {q!r}（期望默认的 10，不是自定义 QoSProfile）')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试12', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 输出
shutil.rmtree(TMP, ignore_errors=True)
try:
    os.rmdir(os.path.join(WS, '.testtmp'))
except OSError:
    pass

print()
print('=' * 76)
for ok, name, detail in results:
    print(f'[{"PASS" if ok else "FAIL"}] {name}')
    if detail:
        print(f'        {detail}')
print('=' * 76)
bad = [r for r in results if not r[0]]
print(f'{len(results) - len(bad)}/{len(results)} 通过')
sys.exit(1 if bad else 0)
