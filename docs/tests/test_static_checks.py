#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工程静态检查 —— 不需要 ROS 环境，几秒内跑完
================================================================================
用法：

    cd <工作区根目录>
    python3 docs/tests/test_static_checks.py

检查四类问题：

  1. Python 语法错误（所有 .py）
  2. XML 格式错误（package.xml）
  3. YAML 解析错误（所有配置文件）
  4. ★ "函数内先用后定义/导入" —— 这类错误会在运行时才暴露，
     而且报错信息（NameError / UnboundLocalError）不容易对应到源码

【第 4 项的价值】
    项目开发中真实踩到过：launch 文件里把
        from launch.conditions import IfCondition
    写在了使用 IfCondition 的那一行【之后】，
    运行时直接抛 NameError。语法检查查不出来，只有真跑才会炸。

    本脚本用 AST 分析把它提前抓出来，并且**自带一个自测**，
    确保这个检查器本身不会退化成"永远通过"的摆设。

【注意一个容易误判的地方】
    Python 的 ast.walk 是【广度优先】，遍历顺序不等于行号顺序。
    所以判断"是否在使用前定义"时，必须取该名字所有绑定位置中
    【最小的行号】，否则会误报。
================================================================================
"""

import ast
import builtins
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(_HERE))
# 只扫描源码包（src/ 下的三个 ROS 包），不把 docs/tests 里的测试脚本本身卷进来，
# 否则检查器会对测试文件里合法的"使用顺序"误报。
ROS_WS = os.path.join(WS, 'src')

try:
    import yaml
except ImportError:
    print('缺少 PyYAML。请执行： sudo apt install -y python3-yaml', file=sys.stderr)
    sys.exit(1)


def find(pat, root=ROS_WS):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ('__pycache__', 'build', 'install', 'log')]
        for fn in filenames:
            if fn.endswith(pat):
                hits.append(os.path.join(dirpath, fn))
    return hits


# ==============================================================================
#  核心检查：函数内"先用后定义"
# ==============================================================================
def _target_names(node):
    """从赋值/循环目标里取出所有被绑定的名字（支持元组解包）"""
    names = []
    if isinstance(node, ast.Name):
        names.append(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            names.extend(_target_names(e))
    return names


def check_use_before_binding(src):
    """返回 [(名字, 使用行, 定义行), ...]"""
    hits = []
    tree = ast.parse(src)

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        bind_line = {}

        def bind(name, lineno):
            prev = bind_line.get(name)
            bind_line[name] = lineno if prev is None else min(prev, lineno)

        # ---- ★ 先绑定函数形参 ----
        # 形参在函数入口（fn.lineno）就已经是绑定状态，
        # 函数体内"先用参数、再重绑定参数"是合法写法：
        #     def f(x, y):
        #         z = x + y      # 使用形参
        #         x = x / 2      # 重绑定
        # 早期版本漏掉形参，会对此类代码误报"先用后定义"。
        for _arg in (list(fn.args.posonlyargs) + list(fn.args.args)
                     + list(fn.args.kwonlyargs)):
            bind(_arg.arg, fn.lineno)
        if fn.args.vararg is not None:
            bind(fn.args.vararg.arg, fn.lineno)
        if fn.args.kwarg is not None:
            bind(fn.args.kwarg.arg, fn.lineno)

        # ---- ★ 先处理【推导式】的目标变量 ----
        # 形如
        #     x = "".join(f(a) for a, b in items)
        # 里 a / b 是由【同一个表达式】的 for 子句绑定的。
        # 从文本行号看"使用"在"绑定"之前，但语义上完全合法。
        #
        # 早期版本没处理这一点，对合法代码误报（实测踩到过：
        # 仿真组的 generate_world.py 里一句生成器表达式被判成 NameError）。
        # 修法：把推导式的目标变量绑定到【整个推导式表达式的起始行】，
        #      这样推导式内部的任何使用都不会被判为"先用后定义"。
        for comp in ast.walk(fn):
            if isinstance(comp, (ast.ListComp, ast.SetComp,
                                 ast.DictComp, ast.GeneratorExp)):
                for gen in comp.generators:
                    for name in _target_names(gen.target):
                        bind(name, comp.lineno)

        for stmt in ast.walk(fn):
            if isinstance(stmt, ast.Import):
                for al in stmt.names:
                    bind(al.asname or al.name.split('.')[0], stmt.lineno)
            elif isinstance(stmt, ast.ImportFrom):
                for al in stmt.names:
                    bind(al.asname or al.name, stmt.lineno)
            elif isinstance(stmt, ast.Name) and isinstance(stmt.ctx, ast.Store):
                bind(stmt.id, stmt.lineno)

        for stmt in ast.walk(fn):
            if isinstance(stmt, ast.Name) and isinstance(stmt.ctx, ast.Load):
                bl = bind_line.get(stmt.id)
                if bl is not None and stmt.lineno < bl:
                    hits.append((stmt.id, stmt.lineno, bl))

    return hits


# ==============================================================================
#  检查器自测（防止它退化成"永远通过"）
# ==============================================================================
BAD_SAMPLE = '''
def generate_launch_description():
    cond = IfCondition(use_rviz)
    from launch.conditions import IfCondition
    return cond
'''

GOOD_SAMPLE = '''
from launch.conditions import IfCondition


def generate_launch_description():
    return IfCondition(True)
'''


def selftest_checker():
    bad_hits = check_use_before_binding(BAD_SAMPLE)
    good_hits = check_use_before_binding(GOOD_SAMPLE)
    return len(bad_hits) >= 1 and len(good_hits) == 0, bad_hits


# ==============================================================================
#  主流程
# ==============================================================================
def main():
    problems = []
    counts = {'py': 0, 'xml': 0, 'yaml': 0}

    print('=' * 74)
    print('  工程静态检查')
    print('=' * 74)
    print(f'  检查目录: {ROS_WS}')

    if not os.path.isdir(ROS_WS):
        print(f'  ❌ 找不到 {ROS_WS}')
        return 1

    # ---- 1/4 Python ----
    for f in find('.py'):
        counts['py'] += 1
        rel = os.path.relpath(f, WS)
        with open(f, encoding='utf-8') as fh:
            src = fh.read()
        try:
            compile(src, f, 'exec')
        except SyntaxError as e:
            problems.append(f'[语法错误] {rel}\n    第 {e.lineno} 行: {e.msg}')
            continue
        for name, use_at, bind_at in check_use_before_binding(src):
            problems.append(
                f'[使用顺序] {rel}\n'
                f'    第 {use_at} 行使用了 `{name}`，但它到第 {bind_at} 行才被定义/导入\n'
                f'    → 运行时会抛 NameError / UnboundLocalError')

    # ---- 2/4 XML ----
    import xml.etree.ElementTree as ET
    for f in find('.xml'):
        counts['xml'] += 1
        try:
            ET.parse(f)
        except Exception as e:                                  # noqa: BLE001
            problems.append(f'[XML 错误] {os.path.relpath(f, WS)}\n    {e}')

    # ---- 3/4 YAML ----
    for f in find('.yaml') + find('.yml'):
        counts['yaml'] += 1
        try:
            with open(f, encoding='utf-8') as fh:
                yaml.safe_load(fh)
        except Exception as e:                                  # noqa: BLE001
            problems.append(f'[YAML 错误] {os.path.relpath(f, WS)}\n    {e}')

    # ---- 4/4 检查器自测 ----
    print()
    print(f'  检查了 {counts["py"]} 个 .py、{counts["xml"]} 个 .xml、'
          f'{counts["yaml"]} 个 .yaml')
    print('-' * 74)

    ok, bad_hits = selftest_checker()
    if ok:
        print(f'  ✅ 使用顺序检查器自测通过（坏样例抓到 {len(bad_hits)} 处）')
    else:
        problems.append('[检查器失效] 使用顺序检查器没能抓到故意写错的样例，'
                        '它可能已经退化成"永远通过"')
        print('  ❌ 使用顺序检查器自测失败')

    print('-' * 74)
    if problems:
        for p in problems:
            print(p)
            print()
        print(f'❌ 发现 {len(problems)} 个问题')
        return 1

    print('✅ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
