#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Keepout 掩膜生成器 —— 车道线合规的第三层防护
================================================================================
用法：

    # 1) 先生成一份配置模板
    python3 make_keepout_mask.py --init

    # 2) 按实际场地修改 keepout_config.yaml

    # 3) 生成掩膜
    python3 make_keepout_mask.py --config keepout_config.yaml

【它解决什么问题】
    仿真场地里的车道线是画在地板纹理上的图像。
    2D 激光雷达的扫描平面在地面上方，**只能感知立体障碍物，看不到地面标线**。
    因此代价地图里不存在车道线约束，规划器会直接抄近路压线。

    本脚本读取 SLAM 建出的地图（拿到分辨率与原点），
    再按你指定的区域生成一张同尺寸的掩膜图：
        像素值 0   = 禁行区（keepout）
        像素值 254 = 可通行
    把这层掩膜接入 Nav2 的 KeepoutFilter，规划器就会绕开禁行区。

【两种用法】
    A. 黑名单模式：keepout_rects —— 把指定矩形标为禁区，其余可走
    B. 白名单模式：allow_rects   —— 只允许指定区域可走，其余全部禁区（推荐用于车道合规）

    模式 B 更适合本赛题：车道的可行驶区域是明确的窄带，
    把"非车道"整片划为禁区，规划器就只能贴着车道走。
================================================================================
"""

import argparse
import math
import os
import sys

try:
    import yaml
except ImportError:
    print('缺少 PyYAML，请先执行： pip3 install pyyaml', file=sys.stderr)
    sys.exit(1)


# ==============================================================================
#  PGM 读写（P5 二进制格式，ROS map_server 使用的格式）
# ==============================================================================
def read_pgm(path):
    """读取 P5 PGM，返回 (width, height, bytearray)"""
    with open(path, 'rb') as f:
        data = f.read()

    # 解析头部：P5 / 宽 高 / 最大值，中间可能夹注释行
    tokens = []
    i = 0
    while len(tokens) < 4:
        # 跳过空白
        while i < len(data) and data[i:i + 1].isspace():
            i += 1
        # 跳过注释
        if i < len(data) and data[i:i + 1] == b'#':
            while i < len(data) and data[i:i + 1] != b'\n':
                i += 1
            continue
        j = i
        while j < len(data) and not data[j:j + 1].isspace():
            j += 1
        tokens.append(data[i:j])
        i = j

    if tokens[0] != b'P5':
        raise ValueError(f'{path} 不是 P5 格式的 PGM（读到 {tokens[0]!r}）')

    width = int(tokens[1])
    height = int(tokens[2])
    maxval = int(tokens[3])
    if maxval != 255:
        raise ValueError(f'只支持 maxval=255 的 PGM，当前是 {maxval}')

    i += 1                                   # 头部结束后恰好一个空白字符
    pixels = bytearray(data[i:i + width * height])
    if len(pixels) != width * height:
        raise ValueError(f'像素数据长度不符：期望 {width*height}，实际 {len(pixels)}')
    return width, height, pixels


def write_pgm(path, width, height, pixels):
    with open(path, 'wb') as f:
        f.write(b'P5\n')
        f.write(f'{width} {height}\n'.encode())
        f.write(b'255\n')
        f.write(bytes(pixels))


# ==============================================================================
#  几何工具
# ==============================================================================
def point_in_rect(x, y, rect):
    x1, y1, x2, y2 = rect[0], rect[1], rect[2], rect[3]
    return (min(x1, x2) <= x <= max(x1, x2)) and (min(y1, y2) <= y <= max(y1, y2))


def point_in_polygon(x, y, pts):
    """射线法判断点是否在多边形内"""
    n = len(pts)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i][0], pts[i][1]
        xj, yj = pts[j][0], pts[j][1]
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


# ==============================================================================
#  主流程
# ==============================================================================
TEMPLATE = '''# Keepout 掩膜配置
# ---------------------------------------------------------------------------
# 坐标使用【地图坐标系】（与 RViz 里显示的坐标一致，单位：米）
# 怎么取坐标：在 RViz 里把鼠标移到车道边界上，看左下角显示的 x / y
# ---------------------------------------------------------------------------

# SLAM 建出的地图（脚本从这里读分辨率与原点）
map_yaml: ~/smart_community_ws/src/community_nav/map/community_map.yaml

# 输出的掩膜文件（不要写扩展名，脚本会自动加 .pgm / .yaml）
output: ~/smart_community_ws/src/community_nav/map/keepout_mask

# ---------------------------------------------------------------------------
# 模式 A（黑名单）：把这些矩形标为禁行区，其余地方可以走
#   每行格式：[x1, y1, x2, y2, "说明"]
#   注释掉就不生效
# ---------------------------------------------------------------------------
keepout_rects: []
# keepout_rects:
#   - [1.0, 1.0, 1.5, 2.0, "示例：某个不该进的角落"]

# ---------------------------------------------------------------------------
# 模式 B（白名单）：★ 推荐用于车道合规
#   只允许这些矩形内通行，其余全部标为禁行区
#   按车道一段一段填，相邻矩形要【互相重叠一点】，否则接缝处会出现禁行缝
# ---------------------------------------------------------------------------
allow_rects: []
# allow_rects:
#   - [0.2, 3.4, 3.8, 4.0, "顶部横向车道"]
#   - [3.6, 0.2, 4.0, 4.0, "右侧纵向车道"]
#   - [0.2, 0.2, 3.8, 0.6, "底部横向车道"]

# ---------------------------------------------------------------------------
# 多边形禁区（可选，用于不规则形状）
# ---------------------------------------------------------------------------
keepout_polygons: []
# keepout_polygons:
#   - name: "三角形禁行区"
#     points: [[1.0, 1.0], [2.0, 1.0], [1.5, 2.0]]
'''


def build_mask(width, height, res, ox, oy, cfg):
    """生成掩膜像素。

    坐标约定（与 ROS map_server 一致）：
      · 地图 yaml 的 origin 是【左下角】在地图坐标系中的位置
      · PGM 的【第一行】是地图的【最上面】，即 y 最大处
    所以：
      world_x = ox + (col + 0.5) * res
      world_y = oy + (height - row - 0.5) * res
    """
    keepout_rects = cfg.get('keepout_rects') or []
    allow_rects = cfg.get('allow_rects') or []
    keepout_polys = cfg.get('keepout_polygons') or []

    use_whitelist = len(allow_rects) > 0

    pixels = bytearray(width * height)
    n_keepout = 0

    for row in range(height):
        wy = oy + (height - row - 0.5) * res
        for col in range(width):
            wx = ox + (col + 0.5) * res

            if use_whitelist:
                # 白名单：默认禁行，落在任一允许矩形内才可通行
                free = any(point_in_rect(wx, wy, r) for r in allow_rects)
            else:
                # 黑名单：默认可通行，落在任一禁行矩形内则禁行
                free = not any(point_in_rect(wx, wy, r) for r in keepout_rects)

            if free:
                # 再叠加多边形禁区
                for poly in keepout_polys:
                    pts = poly.get('points') or []
                    if len(pts) >= 3 and point_in_polygon(wx, wy, pts):
                        free = False
                        break

            if free:
                pixels[row * width + col] = 254
            else:
                pixels[row * width + col] = 0
                n_keepout += 1

    return pixels, n_keepout


def print_nav2_snippet(mask_yaml):
    print()
    print('=' * 74)
    print('  接入 Nav2 的步骤')
    print('=' * 74)
    print(f'''
  ① 编辑 nav2_params.yaml，在 global_costmap 里启用 KeepoutFilter：

     global_costmap:
       global_costmap:
         ros__parameters:
           plugins: ["static_layer", "obstacle_layer", "inflation_layer", "keepout_filter"]
           keepout_filter:
             plugin: "nav2_costmap_2d::KeepoutFilter"
             enabled: True
             filter_info_topic: "/costmap_filter_info"

  ② 在 navigation.launch.py 里增加两个节点和一个生命周期管理器：

     Node(package='nav2_map_server', executable='map_server',
          name='filter_mask_server',
          parameters=[{{'yaml_filename': '{mask_yaml}',
                       'topic_name': '/keepout_mask',
                       'use_sim_time': True}}],
          output='screen'),

     Node(package='nav2_map_server', executable='costmap_filter_info_server',
          name='costmap_filter_info_server',
          parameters=[{{'use_sim_time': True,
                       'type': 0,
                       'filter_info_topic': '/costmap_filter_info',
                       'mask_topic': '/keepout_mask',
                       'base': 0.0,
                       'multiplier': 1.0}}],
          output='screen'),

     Node(package='nav2_lifecycle_manager', executable='lifecycle_manager',
          name='lifecycle_manager_costmap_filters',
          parameters=[{{'use_sim_time': True,
                       'autostart': True,
                       'node_names': ['filter_mask_server',
                                      'costmap_filter_info_server']}}],
          output='screen'),

  ③ 重跑导航，在 RViz 里打开 "Filter Mask" 或 "Costmap" 显示，
     确认禁行区显示出来了、且规划路径不再穿越它。
''')


def resolve(p):
    return os.path.expanduser(os.path.expandvars(p))


def main():
    ap = argparse.ArgumentParser(description='生成 Nav2 Keepout 掩膜图')
    ap.add_argument('--init', action='store_true', help='生成配置模板文件')
    ap.add_argument('--config', default='keepout_config.yaml', help='配置文件路径')
    args = ap.parse_args()

    if args.init:
        out = resolve(args.config)
        if os.path.exists(out):
            print(f'配置文件已存在，不覆盖：{out}')
            return 0
        with open(out, 'w', encoding='utf-8') as f:
            f.write(TEMPLATE)
        print(f'已生成配置模板：{out}')
        print('请按实际场地填写 allow_rects 或 keepout_rects，然后运行：')
        print(f'  python3 {os.path.basename(__file__)} --config {args.config}')
        return 0

    cfg_path = resolve(args.config)
    if not os.path.isfile(cfg_path):
        print(f'找不到配置文件：{cfg_path}')
        print(f'先生成模板：python3 {os.path.basename(__file__)} --init')
        return 1

    with open(cfg_path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}

    map_yaml = resolve(cfg.get('map_yaml', ''))
    output = resolve(cfg.get('output', ''))

    if not map_yaml or not os.path.isfile(map_yaml):
        print(f'找不到地图文件：{map_yaml}')
        print('请先完成建图（见 smart_community_ws/README.md 第 3.2 节）')
        return 1
    if not output:
        print('配置里缺少 output')
        return 1

    # ---- 读地图元信息 ----
    with open(map_yaml, encoding='utf-8') as f:
        meta = yaml.safe_load(f)

    res = float(meta['resolution'])
    origin = meta.get('origin', [0.0, 0.0, 0.0])
    ox, oy, oyaw = float(origin[0]), float(origin[1]), float(origin[2])

    img = meta['image']
    if not os.path.isabs(img):
        img = os.path.join(os.path.dirname(map_yaml), img)

    if abs(oyaw) > 1e-6:
        print(f'⚠️ 地图 origin 的偏航角是 {oyaw}，本脚本假设为 0。')
        print('   map_saver 生成的地图通常 yaw=0，若你的不是，请告知导航组。')

    width, height, _ = read_pgm(img)

    print('=' * 74)
    print('  Keepout 掩膜生成')
    print('=' * 74)
    print(f'  源地图    : {img}')
    print(f'  尺寸      : {width} x {height} 像素')
    print(f'  分辨率    : {res} m/像素')
    print(f'  原点      : ({ox:.3f}, {oy:.3f})')
    print(f'  覆盖范围  : x ∈ [{ox:.2f}, {ox + width*res:.2f}]'
          f'   y ∈ [{oy:.2f}, {oy + height*res:.2f}]')

    allow = cfg.get('allow_rects') or []
    keep = cfg.get('keepout_rects') or []
    polys = cfg.get('keepout_polygons') or []

    if not allow and not keep and not polys:
        print()
        print('  ⚠️ 配置里没有任何区域定义，生成的掩膜将全是"可通行"，等于没起作用。')
        print('     请在配置文件里填写 allow_rects（推荐）或 keepout_rects。')
        return 1

    mode = '白名单（只允许 allow_rects 内通行）' if allow else '黑名单（只禁用 keepout_rects）'
    print(f'  模式      : {mode}')
    print(f'  允许矩形  : {len(allow)} 个')
    print(f'  禁行矩形  : {len(keep)} 个')
    print(f'  禁行多边形: {len(polys)} 个')
    print()

    pixels, n_keepout = build_mask(width, height, res, ox, oy, cfg)

    ratio = n_keepout / (width * height) * 100
    print(f'  禁行像素  : {n_keepout} / {width*height}  ({ratio:.1f}%)')
    if ratio > 95:
        print('  ⚠️ 超过 95% 都是禁行区，可能坐标填错了（检查坐标系与范围）')
    if ratio == 0:
        print('  ⚠️ 没有任何禁行区，掩膜不起作用')

    os.makedirs(os.path.dirname(output) or '.', exist_ok=True)
    pgm_out = output + '.pgm'
    yaml_out = output + '.yaml'

    write_pgm(pgm_out, width, height, pixels)

    with open(yaml_out, 'w', encoding='utf-8') as f:
        f.write(f'image: {os.path.basename(pgm_out)}\n')
        f.write('mode: trinary\n')
        f.write(f'resolution: {res}\n')
        f.write(f'origin: [{ox}, {oy}, 0.0]\n')
        f.write('negate: 0\n')
        f.write('occupied_thresh: 0.65\n')
        f.write('free_thresh: 0.196\n')

    print()
    print(f'  ✅ 已生成：')
    print(f'     {pgm_out}')
    print(f'     {yaml_out}')
    print()
    print('  ⚠️ 掩膜的分辨率与原点必须和源地图完全一致，本脚本已自动保证。')

    print_nav2_snippet(yaml_out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
