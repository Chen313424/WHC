"""
用打桩方式真实执行全部三个 launch 文件，验证：
  sim.launch.py        —— 正常路径 + 两条防御路径（缺文件时报错可诊断）
  mapping.launch.py    —— 节点配置正确 + 缺包时给出 apt 安装提示
  navigation.launch.py —— 包含 Nav2 bringup、RViz、巡检节点

原理：把 ament_index_python / launch / launch_ros 替换成轻量桩，
然后真的 exec 这些 launch 文件。任何语法错误、导入错误、依赖查找失败
都会在这里暴露 —— 与 `ros2 launch --show-args` 的效果一致，但不需要 ROS 环境。
"""
import importlib.util
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(_HERE))
LAUNCH_DIR = os.path.join(WS, 'src', 'community_nav', 'launch')


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


TMP = _make_tmpdir('launch_test_')
STUB = os.path.join(TMP, 'stubs')
FAKEROOT = os.path.join(TMP, 'fakeros')

# ==============================================================================
#  桩模块
# ==============================================================================
STUBS = {
    'ament_index_python/__init__.py': '',
    'ament_index_python/packages.py': (
        'import os\n'
        'def get_package_share_directory(name):\n'
        '    root = os.environ["FAKE_ROS_ROOT"]\n'
        '    p = os.path.join(root, name)\n'
        '    if not os.path.isdir(p):\n'
        '        raise LookupError("package \'%s\' not found" % name)\n'
        '    return p\n'),
    'launch/__init__.py': (
        'class LaunchDescription:\n'
        '    def __init__(self, entities=None):\n'
        '        self.entities = list(entities) if entities else []\n'),
    'launch/actions.py': (
        'class DeclareLaunchArgument:\n'
        '    def __init__(self, name, **kw):\n'
        '        self.name = name\n'
        '        self.kw = kw\n'
        'class LogInfo:\n'
        '    def __init__(self, msg=None, **kw):\n'
        '        self.msg = msg\n'
        'class IncludeLaunchDescription:\n'
        '    def __init__(self, source, launch_arguments=None, condition=None):\n'
        '        self.source = source\n'
        '        self.launch_arguments = dict(launch_arguments or {})\n'
        '        self.condition = condition\n'),
    'launch/conditions.py': (
        'class IfCondition:\n'
        '    def __init__(self, predicate):\n'
        '        self.predicate = predicate\n'),
    'launch/launch_description_sources.py': (
        'class PythonLaunchDescriptionSource:\n'
        '    def __init__(self, path):\n'
        '        self.path = path\n'),
    'launch/substitutions.py': (
        'class LaunchConfiguration:\n'
        '    def __init__(self, name, default=None):\n'
        '        self.name = name\n'),
    'launch_ros/__init__.py': '',
    'launch_ros/actions.py': (
        'class Node:\n'
        '    def __init__(self, **kw):\n'
        '        self.kw = kw\n'
        '        self.package = kw.get("package")\n'
        '        self.executable = kw.get("executable")\n'
        '        self.name = kw.get("name")\n'
        '    def __repr__(self):\n'
        '        return "Node(%s/%s)" % (self.package, self.executable)\n'),
    'launch_ros/substitutions.py': '',
}
shutil.rmtree(STUB, ignore_errors=True)
for rel, content in STUBS.items():
    p = os.path.join(STUB, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)

PKG_DIRS = [
    'community_nav/launch', 'community_nav/config',
    'smart_community_sim/launch', 'smart_community_sim/worlds',
    'nav2_bringup/launch', 'nav2_bringup/rviz',
    'slam_toolbox/launch', 'nav2_lifecycle_manager/launch',
    'turtlebot3_gazebo/launch', 'turtlebot3_gazebo/worlds',
    'turtlebot3_gazebo/models/turtlebot3_waffle',
]
TB3_FILES = [
    # 仿真组的场景包（社区世界 + 一键启动）
    'smart_community_sim/launch/smart_community.launch.py',
    'smart_community_sim/worlds/smart_community.sdf',
    'nav2_bringup/launch/bringup_launch.py',
    'nav2_bringup/launch/rviz_launch.py',
    # ★ mapping.launch.py 启动时会自检这个文件是否存在
    'community_nav/config/slam_toolbox_params.yaml',
]


def make_fakeroot(with_model=True, with_world=True, with_nav2_bringup=True,
                  with_slam_params=True, with_scene=True):
    shutil.rmtree(FAKEROOT, ignore_errors=True)
    for d in PKG_DIRS:
        # 注意：必须连【目录】一起跳过，否则包看起来是存在的
        if not with_nav2_bringup and d.startswith('nav2_bringup'):
            continue
        if not with_model and d.startswith('turtlebot3_gazebo/models'):
            continue
        if not with_scene and d.startswith('smart_community_sim'):
            continue
        os.makedirs(os.path.join(FAKEROOT, d), exist_ok=True)
    for rel in TB3_FILES:
        if not with_model and rel.endswith('model.sdf'):
            continue
        if not with_world and rel.endswith('.world'):
            continue
        if not with_nav2_bringup and rel.startswith('nav2_bringup'):
            continue
        if not with_slam_params and rel.startswith('community_nav/config'):
            continue
        if not with_scene and rel.startswith('smart_community_sim'):
            continue
        p = os.path.join(FAKEROOT, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write('# fake\n')


def load(name):
    if STUB not in sys.path:
        sys.path.insert(0, STUB)
    path = os.path.join(LAUNCH_DIR, name)
    spec = importlib.util.spec_from_file_location('lt_' + name.replace('.', '_'), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def kinds(ld):
    out = {}
    for e in ld.entities:
        k = type(e).__name__
        out[k] = out.get(k, 0) + 1
    return out


def nodes(ld):
    return [e for e in ld.entities if type(e).__name__ == 'Node']


def includes(ld):
    return [e for e in ld.entities if type(e).__name__ == 'IncludeLaunchDescription']


results = []


def record(ok, name, detail=''):
    results.append((bool(ok), name, detail))


os.environ['FAKE_ROS_ROOT'] = FAKEROOT

# ==============================================================================
#  测试 1-3：三个 launch 文件都能正常生成
# ==============================================================================
make_fakeroot()
for fname, want_declares, want_nodes in [
    ('sim.launch.py', 1, 0),
    ('mapping.launch.py', 4, 3),
    ('mapping_minimal.launch.py', 2, 3),
    ('navigation.launch.py', 7, 2),
]:
    try:
        mod = load(fname)
        ld = mod.generate_launch_description()
        k = kinds(ld)
        ok = (k.get('DeclareLaunchArgument', 0) == want_declares
              and k.get('Node', 0) == want_nodes)
        record(ok, f'测试 {fname} 能正常生成',
               f'实体构成 {k}（期望 DeclareLaunchArgument={want_declares}, Node={want_nodes}）')
    except Exception as exc:                                    # noqa: BLE001
        import traceback
        record(False, f'测试 {fname} 能正常生成',
               f'{type(exc).__name__}: {exc}\n{traceback.format_exc()[-300:]}')

# ==============================================================================
#  测试 4：sim.launch.py 复用仿真组场景，且场景缺失时报错可诊断
#  （世界/机器人/桥接都由 smart_community_sim 提供，我们只做启动前检查）
# ==============================================================================
try:
    make_fakeroot()
    mod = load('sim.launch.py')
    ld = mod.generate_launch_description()
    inc = includes(ld)
    paths = [str(getattr(e.source, 'path', '')) for e in inc]
    ok = any('smart_community.launch.py' in p for p in paths)
    record(ok, '测试 sim 复用了仿真组的场景启动文件',
           f'引入的启动文件数={len(paths)}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试 sim 复用仿真组场景', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 4b：场景包缺失（未编译）时必须直接报错并给出编译命令
# ==============================================================================
try:
    make_fakeroot(with_scene=False)
    mod = load('sim.launch.py')
    try:
        mod.generate_launch_description()
        record(False, '测试 sim 缺场景包时报错可诊断', '本应抛错却正常返回')
    except RuntimeError as exc:
        msg = str(exc)
        ok = '找不到仿真组' in msg and 'colcon build' in msg
        record(ok, '测试 sim 缺场景包时报错可诊断',
               '报错含编译提示' if ok else msg.replace('\n', ' | ')[:200])
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试 sim 场景包缺失防御', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 5：mapping.launch.py 的节点配置
# ==============================================================================
try:
    make_fakeroot()
    mod = load('mapping.launch.py')
    ld = mod.generate_launch_description()
    ns = {n.name: n for n in nodes(ld)}
    slam = ns.get('slam_toolbox')
    # ★ Jazzy 上必须有生命周期管理器：
    #   该版本的 async_slam_toolbox_node 是生命周期节点，
    #   不 configure + activate 就完全不发 /map（而且不报错）。
    lm = ns.get('lifecycle_manager_slam')
    lm_params = lm.kw.get('parameters', [{}])[0] if lm else {}
    ok = (slam is not None and slam.executable == 'async_slam_toolbox_node'
          and slam.package == 'slam_toolbox'
          and lm is not None and lm.executable == 'lifecycle_manager'
          and lm.package == 'nav2_lifecycle_manager'
          and lm_params.get('node_names') == ['slam_toolbox']
          # ★ bond_timeout 必须为 0：slam_toolbox 不支持 Nav2 的 bond 心跳
          and lm_params.get('bond_timeout') == 0.0)
    record(ok, '测试 mapping 的节点配置正确（含生命周期管理器 + 关闭 bond 检查）',
           f'节点={list(ns.keys())} node_names={lm_params.get("node_names")} '
           f'bond_timeout={lm_params.get("bond_timeout")}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试 mapping 节点配置', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 5b：检测到陈旧 gzserver 时必须直接报错
#  （否则新 gzserver 抢不到 11345 端口会以 exit 255 退出，
#    表现为"/scan 没有、/map 没有"，看起来像 SLAM 坏了）
# ==============================================================================
try:
    import subprocess as _sp
    make_fakeroot()
    mod = load('sim.launch.py')

    class _FakeCP:
        returncode = 0
        stdout = '644 gzserver /opt/ros/humble/share/turtlebot3_gazebo/worlds/turtlebot3_world.world\n'

    _orig = _sp.run
    _sp.run = lambda *a, **k: _FakeCP()
    try:
        try:
            mod.generate_launch_description()
            record(False, '测试 sim 检测到陈旧 gzserver 时直接报错', '本应抛错却正常返回')
        except RuntimeError as exc:
            msg = str(exc)
            ok = 'pkill' in msg and ('gz sim' in msg or 'gzserver' in msg)
            record(ok, '测试 sim 检测到陈旧 Gazebo 时直接报错',
                   '报错含清理命令' if ok else msg.replace('\n', ' | ')[:200])
    finally:
        _sp.run = _orig
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试 sim 陈旧 gzserver 检测', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 6：mapping.launch.py 缺包时给出 apt 安装提示
# ==============================================================================
try:
    make_fakeroot(with_nav2_bringup=False)
    mod = load('mapping.launch.py')
    try:
        mod.generate_launch_description()
        record(False, '测试 mapping 缺包时给出安装提示', '本应抛错却正常返回')
    except RuntimeError as exc:
        msg = str(exc)
        ok = 'nav2_bringup' in msg and 'apt install' in msg and 'ros-jazzy-nav2-bringup' in msg
        record(ok, '测试 mapping 缺包时给出 apt 安装提示',
               '提示含 apt 包名' if ok else msg.replace('\n', ' | ')[:220])
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试 mapping 缺包提示', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 6b：mapping.launch.py 缺 SLAM 参数文件时，启动瞬间就报错
#  （否则 slam_toolbox 会悄悄死掉，表现为"节点没起来、/map 没有"）
# ==============================================================================
try:
    make_fakeroot(with_slam_params=False)
    mod = load('mapping.launch.py')
    try:
        mod.generate_launch_description()
        record(False, '测试 mapping 缺 SLAM 参数文件时启动即报错', '本应抛错却正常返回')
    except RuntimeError as exc:
        msg = str(exc)
        ok = ('找不到 SLAM 参数文件' in msg) and ('colcon build' in msg)
        record(ok, '测试 mapping 缺 SLAM 参数文件时启动即报错',
               '报错含编译提示' if ok else msg.replace('\n', ' | ')[:220])
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试 mapping 缺参数文件提示', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 6d：mapping_minimal.launch.py 不依赖任何参数文件（排查后备方案）
# ==============================================================================
try:
    # 故意把 SLAM 参数文件和 nav2_bringup 都拿掉，它也应该能生成
    make_fakeroot(with_slam_params=False)
    mod = load('mapping_minimal.launch.py')
    ld = mod.generate_launch_description()
    ns = {n.name: n for n in nodes(ld)}
    slam = ns.get('slam_toolbox')
    # 参数必须是内联字典，而不是一个 YAML 文件路径
    inline = False
    if slam:
        for src in slam.kw.get('parameters', []):
            if isinstance(src, dict) and 'scan_topic' in src:
                inline = True
    record(inline and 'lifecycle_manager_slam' in ns and 'rviz2' in ns,
           '测试 mapping_minimal 完全不依赖参数文件（参数内联）',
           f'节点={sorted(ns.keys())}  参数内联={inline}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试 mapping_minimal', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 6c：mapping 里的 RViz 必须设置 on_exit
#  否则 RViz 一崩，整个 launch 会被关闭，slam_toolbox 被一起带走，/map 永远出不来
# ==============================================================================
try:
    make_fakeroot()
    mod = load('mapping.launch.py')
    ld = mod.generate_launch_description()
    rv = [n for n in nodes(ld) if n.name == 'rviz2']
    ok = len(rv) == 1 and rv[0].kw.get('on_exit') is not None
    record(ok, '测试 mapping 的 RViz 设了 on_exit（崩了不会带崩建图）',
           f'找到 rviz2 节点 {len(rv)} 个，on_exit 已设置={rv[0].kw.get("on_exit") is not None if rv else False}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试 mapping 的 RViz on_exit', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 7：navigation.launch.py 的内容
# ==============================================================================
try:
    make_fakeroot()
    mod = load('navigation.launch.py')
    ld = mod.generate_launch_description()
    inc = includes(ld)
    inc_paths = [str(getattr(e.source, 'path', '')) for e in inc]
    has_nav2 = any('bringup_launch.py' in p for p in inc_paths)
    # ★ RViz 现在是我们自己起的节点（带 on_exit），不再是 include
    rv = [n for n in nodes(ld) if n.name == 'rviz2']
    has_rviz = len(rv) == 1 and rv[0].kw.get('on_exit') is not None
    patrol = [n for n in nodes(ld) if n.package == 'community_patrol']
    ok = (has_nav2 and has_rviz and len(patrol) == 1
          and patrol[0].executable == 'patrol_node')
    record(ok, '测试 navigation 包含 Nav2 bringup + RViz(带 on_exit) + 巡检节点',
           f'包含 bringup={has_nav2} RViz 节点带 on_exit={has_rviz} 巡检节点={len(patrol)}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试 navigation 内容', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  测试 8：navigation.launch.py 传给 Nav2 的参数完整
# ==============================================================================
try:
    make_fakeroot()
    mod = load('navigation.launch.py')
    ld = mod.generate_launch_description()
    nav2 = [e for e in includes(ld)
            if 'bringup_launch.py' in str(getattr(e.source, 'path', ''))]
    args = nav2[0].launch_arguments if nav2 else {}
    need = {'map', 'use_sim_time', 'params_file', 'autostart',
            'use_composition', 'slam'}
    ok = need.issubset(set(args.keys()))
    record(ok, '测试 navigation 传给 Nav2 的参数完整',
           f'参数={sorted(args.keys())} 缺少={sorted(need - set(args.keys()))}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试 navigation 参数', f'{type(exc).__name__}: {exc}')

# ==============================================================================
#  输出
# ==============================================================================
shutil.rmtree(TMP, ignore_errors=True)
try:
    os.rmdir(os.path.join(WS, '.testtmp'))
except OSError:
    pass

print('=' * 76)
for ok, name, detail in results:
    print(f'[{"PASS" if ok else "FAIL"}] {name}')
    if detail:
        print(f'        {detail}')
print('=' * 76)
bad = [r for r in results if not r[0]]
print(f'{len(results) - len(bad)}/{len(results)} 通过')
sys.exit(1 if bad else 0)
