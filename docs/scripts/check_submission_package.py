#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提交包预检 —— 打包前跑一遍，确认符合比赛对压缩包的硬性要求
================================================================================
用法（Windows 或 WSL 里都能跑）：

    python3 "/mnt/d/project（ai/docs/scripts/check_submission_package.py"

【为什么需要它】
    比赛对压缩包有明确的硬性要求：
      · 包含完整的 ROS 工作空间（src 目录即可）
      · 需包含地图文件、模型文件、启动脚本及算法源码
      · 根目录下必须包含 README.md，说明编译和运行步骤
      · 大小 ≤ 150 MB

    这些东西缺任何一项都会影响评审。**清单容易忘，脚本不会。**
    本脚本不打包，只检查"如果现在打包，会是什么样"。

【重要】"待办"和"错误"是两回事：
    · 错误  —— 结构不对，必须修
    · 待办  —— 现在还没有，但等对应阶段完成就会有（例如地图文件要建图后才有）
================================================================================
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(_HERE))
ROS_WS = WS
SRC = os.path.join(ROS_WS, 'src')

MAX_ZIP_MB = 150.0

errors = []      # 结构性问题
todos = []       # 现在没有、后面会有的
oks = []


def add_ok(msg):
    oks.append(msg)


def add_err(msg):
    errors.append(msg)


def add_todo(msg):
    todos.append(msg)


def find_all(root, *names):
    """在 root 下递归查找指定文件名，返回路径列表"""
    hits = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn in names:
                hits.append(os.path.join(dirpath, fn))
    return hits


def find_ext(root, exts):
    hits = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in exts:
                hits.append(os.path.join(dirpath, fn))
    return hits


print('=' * 74)
print('  智慧社区 · 复赛提交包预检')
print('=' * 74)
print(f'  工作空间: {ROS_WS}')

if not os.path.isdir(SRC):
    print(f'  ❌ 找不到 {SRC}')
    sys.exit(1)

# ---------------------------------------------------------------- 1. 根目录 README
readme_root = os.path.join(ROS_WS, 'README.md')
if os.path.isfile(readme_root) and os.path.getsize(readme_root) > 500:
    add_ok(f'根目录 README.md 存在（{os.path.getsize(readme_root)} 字节）')
elif os.path.isfile(readme_root):
    add_err('根目录 README.md 内容过少（< 500 字节），评审要求"说明编译和运行步骤"')
else:
    add_err('根目录缺少 README.md —— 规则明确要求"根目录下必须包含 README.md"')

# ---------------------------------------------------------------- 2. src 结构
pkgs = []
for name in sorted(os.listdir(SRC)):
    p = os.path.join(SRC, name)
    if os.path.isdir(p) and os.path.isfile(os.path.join(p, 'package.xml')):
        pkgs.append(name)
if len(pkgs) >= 2:
    add_ok(f'src 下有 {len(pkgs)} 个 ROS 包：{", ".join(pkgs)}')
elif pkgs:
    add_todo(f'src 下只有 {len(pkgs)} 个包（{pkgs[0]}），另有包待建模组交付后加入')
else:
    add_err('src 下没有找到任何含 package.xml 的 ROS 包')

# ---------------------------------------------------------------- 3. 启动脚本
launches = find_ext(SRC, {'.launch.py'}) + [p for p in find_all(SRC) if 'launch' in p]
launches = sorted(set(p for p in find_ext(SRC, {'.py'}) if p.endswith('.launch.py')))
if launches:
    add_ok(f'启动脚本 {len(launches)} 个')
    for p in launches:
        print(f'        · {os.path.relpath(p, ROS_WS)}')
else:
    add_err('没有找到任何 *.launch.py 启动脚本')

# ---------------------------------------------------------------- 4. 算法源码
algo = [p for p in find_ext(SRC, {'.py'})
        if not p.endswith('.launch.py') and 'setup.py' not in p]
if algo:
    total_kb = sum(os.path.getsize(p) for p in algo) / 1024
    add_ok(f'算法源码 {len(algo)} 个 .py（合计 {total_kb:.1f} KB）')
else:
    add_err('没有找到算法源码（.py）')

# ---------------------------------------------------------------- 5. ★ 地图文件
maps = find_ext(SRC, {'.pgm'})
map_yamls = [p for p in find_ext(SRC, {'.yaml'})
             if os.path.basename(p).startswith(('community_map', 'map'))]
if maps and map_yamls:
    for p in maps:
        add_ok(f'地图文件 {os.path.relpath(p, ROS_WS)}（{os.path.getsize(p)} 字节）')
    for p in map_yamls:
        add_ok(f'地图描述 {os.path.relpath(p, ROS_WS)}')
else:
    add_todo('地图文件（.pgm + .yaml）—— 规则明确要求包含。**完成建图后才会生成**，'
             '见 README.md §3.2')

# ---------------------------------------------------------------- 6. ★ 模型文件
model_exts = {'.world', '.sdf', '.urdf', '.xacro', '.dae', '.stl'}
models = find_ext(SRC, model_exts)
if models:
    add_ok(f'模型文件 {len(models)} 个')
else:
    add_todo('模型文件（.world / .urdf / .sdf 等）—— 规则明确要求包含。'
             '**等建模组交付场地与机器人模型后加入**')

# ---------------------------------------------------------------- 7. 不该打进包的东西
bad_dirs = []
for name in ('build', 'install', 'log'):
    p = os.path.join(ROS_WS, name)
    if os.path.isdir(p):
        bad_dirs.append(name)
pycache = []
for dirpath, dirnames, _f in os.walk(SRC):
    if '__pycache__' in dirnames:
        pycache.append(os.path.relpath(os.path.join(dirpath, '__pycache__'), ROS_WS))
if bad_dirs:
    add_todo(f'存在 {", ".join(bad_dirs)} 目录 —— 打包时必须用 -x 排除（否则极易超 150MB）')
else:
    add_ok('没有 build/install/log 目录（尚未编译，或已清理）')
if pycache:
    add_todo(f'有 {len(pycache)} 个 __pycache__ 目录，打包时建议排除')
else:
    add_ok('没有 __pycache__ 残留')

# ---------------------------------------------------------------- 8. 体积估算
total = 0
n_files = 0
for dirpath, _d, filenames in os.walk(ROS_WS):
    # 模拟打包时的排除规则
    parts = os.path.relpath(dirpath, ROS_WS).split(os.sep)
    if any(x in ('build', 'install', 'log', '__pycache__') for x in parts):
        continue
    for fn in filenames:
        if fn.endswith(('.pyc', '.pyo')):
            continue
        total += os.path.getsize(os.path.join(dirpath, fn))
        n_files += 1

mb = total / 1024 / 1024
est_zip = mb * 0.35          # 文本和 PGM 压缩率较高，粗略按 35% 估
print()
print(f'  将要打包：{n_files} 个文件，原始大小 {mb:.2f} MB')
print(f'  压缩后估算：约 {est_zip:.2f} MB（限 {MAX_ZIP_MB:.0f} MB）')
if est_zip > MAX_ZIP_MB:
    add_err(f'估算体积 {est_zip:.1f} MB 超过 {MAX_ZIP_MB:.0f} MB 限制')
elif est_zip > MAX_ZIP_MB * 0.6:
    add_todo(f'估算体积已接近限制的 60%，打包后请务必用 ls -lh 实测')
else:
    add_ok(f'体积充裕（估算 {est_zip:.1f} MB / 限 {MAX_ZIP_MB:.0f} MB）')

# ---------------------------------------------------------------- 输出
print()
print('=' * 74)
print('  检查结果')
print('=' * 74)

for m in oks:
    print(f'  [ OK ] {m}')

if todos:
    print()
    print('  ── 待办（现在还没有，等对应阶段完成后会补上）──')
    for m in todos:
        print(f'  [待办] {m}')

if errors:
    print()
    print('  ── 错误（结构问题，必须修）──')
    for m in errors:
        print(f'  [错误] {m}')

print()
print('=' * 74)
if errors:
    print(f'  ❌ 有 {len(errors)} 处结构性问题需要修正')
    sys.exit(1)

if todos:
    print(f'  ⚠️  结构正确，但有 {len(todos)} 项待办（等后续阶段补齐）')
    print('     这是正常的 —— 建图和建模还没做，地图与模型文件自然还没有。')
    sys.exit(0)

print('  ✅ 提交包结构完整，可以打包')
print()
print('  打包命令（在 WSL 里执行）：')
print('    cd ~/smart_community_ws')
print('    zip -r "【队伍名称】-智慧社区复赛工程代码.zip" src README.md \\')
print('        -x "*/build/*" "*/install/*" "*/log/*" "*.pyc" "*/__pycache__/*"')
print('    ls -lh "【队伍名称】-智慧社区复赛工程代码.zip"')
sys.exit(0)
