"""
测试 make_keepout_mask.py：
  1. PGM 读写往返
  2. 黑名单模式（keepout_rects）
  3. 白名单模式（allow_rects）
  4. 多边形禁区
  5. 坐标映射正确性（世界坐标 -> 像素）
  6. 输出的 yaml 与源地图分辨率/原点一致
  7. --init 生成模板
  8. 没有定义任何区域时应报错退出
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(_HERE))
SCRIPT = os.path.join(WS, 'src', 'community_nav', 'scripts', 'make_keepout_mask.py')

sys.path.insert(0, os.path.join(WS, '.tools'))
import yaml  # noqa: E402


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


TMP = _make_tmpdir('keepout_test_')

spec = importlib.util.spec_from_file_location('keepout_mod', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

results = []


def record(ok, name, detail=''):
    results.append((bool(ok), name, detail))


# ------------------------------------------------------------------ 造合成地图
W, H, RES, OX, OY = 100, 100, 0.05, 0.0, 0.0     # 覆盖 5m x 5m
MAP_PGM = os.path.join(TMP, 'test_map.pgm')
MAP_YAML = os.path.join(TMP, 'test_map.yaml')

mod.write_pgm(MAP_PGM, W, H, bytearray([254] * (W * H)))
with open(MAP_YAML, 'w', encoding='utf-8') as f:
    f.write('image: test_map.pgm\nmode: trinary\nresolution: 0.05\n'
            'origin: [0.0, 0.0, 0.0]\nnegate: 0\n'
            'occupied_thresh: 0.65\nfree_thresh: 0.196\n')


def pixel_at(pixels, wx, wy):
    """按生成器使用的同一套公式反推像素位置"""
    col = int((wx - OX) / RES - 0.5 + 0.5 - 0.5) if False else int(wx / RES - 0.5 + 1e-9)
    row = H - 1 - int(wy / RES - 0.5 + 1e-9)
    col = max(0, min(W - 1, col))
    row = max(0, min(H - 1, row))
    return pixels[row * W + col]


def run_gen(cfg_dict, name):
    cfg_path = os.path.join(TMP, name + '.yaml')
    out_base = os.path.join(TMP, name + '_mask')
    cfg = dict(cfg_dict)
    cfg['map_yaml'] = MAP_YAML
    cfg['output'] = out_base
    with open(cfg_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)
    p = subprocess.run([sys.executable, SCRIPT, '--config', cfg_path],
                       capture_output=True, text=True)
    pgm = out_base + '.pgm'
    pixels = None
    if os.path.isfile(pgm):
        _, _, pixels = mod.read_pgm(pgm)
    return p, pixels, out_base


# ------------------------------------------------------------------ 测试 1
try:
    px = bytearray(range(256)) * 40
    px = px[:W * H]
    p1 = os.path.join(TMP, 'rt.pgm')
    mod.write_pgm(p1, W, H, px)
    w2, h2, px2 = mod.read_pgm(p1)
    ok = (w2 == W and h2 == H and bytes(px2) == bytes(px))
    record(ok, '测试1 PGM 写入后读回一致', f'{w2}x{h2}, 数据一致={bytes(px2)==bytes(px)}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试1 PGM 往返', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 2 黑名单
try:
    p, pixels, _ = run_gen({'keepout_rects': [[1.0, 1.0, 2.0, 2.0, 'block']]}, 'black')
    inside = pixel_at(pixels, 1.5, 1.5)      # 禁区中心 -> 应为 0
    out1 = pixel_at(pixels, 0.5, 0.5)        # 界外 -> 254
    out2 = pixel_at(pixels, 3.0, 3.0)        # 界外 -> 254
    n_zero = sum(1 for v in pixels if v == 0)
    # 1m x 1m 区域，0.05 分辨率 -> 约 400 像素
    ok = (inside == 0 and out1 == 254 and out2 == 254 and 350 <= n_zero <= 450)
    record(ok, '测试2 黑名单模式',
           f'禁区中心={inside}(期望0) 界外={out1},{out2}(期望254) 禁行像素={n_zero}(期望约400)')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试2 黑名单模式', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 3 白名单
try:
    p, pixels, _ = run_gen({'allow_rects': [[1.0, 1.0, 2.0, 2.0, 'lane']]}, 'white')
    inside = pixel_at(pixels, 1.5, 1.5)      # 允许区 -> 254
    out1 = pixel_at(pixels, 0.5, 0.5)        # 界外 -> 0
    out2 = pixel_at(pixels, 3.0, 3.0)        # 界外 -> 0
    n_zero = sum(1 for v in pixels if v == 0)
    ok = (inside == 254 and out1 == 0 and out2 == 0 and n_zero >= W * H - 450)
    record(ok, '测试3 白名单模式（推荐用法）',
           f'允许区中心={inside}(期望254) 界外={out1},{out2}(期望0) 禁行像素={n_zero}/{W*H}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试3 白名单模式', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 4 边界
try:
    p, pixels, _ = run_gen({'allow_rects': [[1.0, 1.0, 2.0, 2.0, 'lane']]}, 'edge')
    just_in = pixel_at(pixels, 1.05, 1.05)
    just_out = pixel_at(pixels, 0.90, 0.90)
    ok = (just_in == 254 and just_out == 0)
    record(ok, '测试4 矩形边界判定',
           f'(1.05,1.05)={just_in}(期望254)  (0.90,0.90)={just_out}(期望0)')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试4 边界判定', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 5 多边形
try:
    poly = {'name': 'tri', 'points': [[1.0, 1.0], [3.0, 1.0], [2.0, 3.0]]}
    p, pixels, _ = run_gen({'keepout_polygons': [poly]}, 'poly')
    inside = pixel_at(pixels, 2.0, 1.5)      # 三角形内部 -> 0
    outside = pixel_at(pixels, 1.2, 2.8)     # 三角形外部 -> 254
    ok = (inside == 0 and outside == 254)
    record(ok, '测试5 多边形禁区',
           f'形内(2.0,1.5)={inside}(期望0)  形外(1.2,2.8)={outside}(期望254)')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试5 多边形禁区', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 6 输出 yaml
try:
    p, pixels, base = run_gen({'allow_rects': [[0.5, 0.5, 4.5, 4.5, 'all']]}, 'yamlchk')
    with open(base + '.yaml', encoding='utf-8') as f:
        out = yaml.safe_load(f)
    with open(MAP_YAML, encoding='utf-8') as f:
        src = yaml.safe_load(f)
    same_res = abs(float(out['resolution']) - float(src['resolution'])) < 1e-9
    same_org = all(abs(float(a) - float(b)) < 1e-9
                   for a, b in zip(out['origin'], src['origin']))
    same_img = out['image'] == os.path.basename(base + '.pgm')
    record(same_res and same_org and same_img, '测试6 输出 yaml 与源地图一致',
           f'分辨率一致={same_res} 原点一致={same_org} image字段={same_img}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试6 输出 yaml', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 7 --init
try:
    cfgfile = os.path.join(TMP, 'init_cfg.yaml')
    p = subprocess.run([sys.executable, SCRIPT, '--init', '--config', cfgfile],
                       capture_output=True, text=True)
    ok = p.returncode == 0 and os.path.isfile(cfgfile)
    content_ok = False
    if ok:
        with open(cfgfile, encoding='utf-8') as f:
            c = yaml.safe_load(f)
        content_ok = isinstance(c, dict) and 'allow_rects' in c and 'map_yaml' in c
    record(ok and content_ok, '测试7 --init 生成配置模板',
           f'退出码={p.returncode} 文件存在={ok} 内容含关键字段={content_ok}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试7 --init', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 8 空配置
try:
    p, pixels, _ = run_gen({}, 'empty')
    ok = p.returncode == 1 and '没有任何区域' in (p.stdout + p.stderr)
    record(ok, '测试8 未定义任何区域时报错退出',
           f'退出码={p.returncode}(期望1) 提示明确={"没有任何区域" in (p.stdout+p.stderr)}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试8 空配置', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 测试 9 地图不存在
try:
    cfg = {'map_yaml': os.path.join(TMP, 'nope.yaml'),
           'output': os.path.join(TMP, 'x'), 'allow_rects': [[0, 0, 1, 1]]}
    cfgfile = os.path.join(TMP, 'nofile.yaml')
    with open(cfgfile, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f)
    p = subprocess.run([sys.executable, SCRIPT, '--config', cfgfile],
                       capture_output=True, text=True)
    ok = p.returncode == 1 and '找不到地图文件' in (p.stdout + p.stderr)
    record(ok, '测试9 地图不存在时给出可操作提示',
           f'退出码={p.returncode} 提示={"找不到地图文件" in (p.stdout+p.stderr)}')
except Exception as exc:                                        # noqa: BLE001
    record(False, '测试9 地图缺失处理', f'{type(exc).__name__}: {exc}')

# ------------------------------------------------------------------ 输出
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
