#!/usr/bin/env python3
"""生成 worlds/smart_community.sdf（复赛 4.2m×4.2m 场地，严格复现任务示意图）。

回字形闭合单向环线（起点/终点合并于右上角）：
  起点(右上) → 最上方水平道路(←) → 左侧竖直道路(↓) → 中部水平道路(→)
  → 中央竖直道路(↓) → 最下方水平道路(→) → 最右侧竖直道路(↑) → 回到右上终点。

布局要点（对齐参考图）：
  - 6 段道路形成闭环，蓝色大箭头标注主行驶方向
  - 2 组红绿灯：上方为竖向(红上/黄中/绿下)，下方为横向(绿左/黄中/红右)
  - A临区(左上) 5 人、B临区(左下) 5 人，均为社区人员
  - 右侧停车场：3/2/1 号停车位从上到下，各停 1 辆蓝绿色汽车
  - 起点/终点标注、三处 60cm 尺寸标注、橙黄色局部朝向箭头

纯 SDF 基本体 + 本地 PNG 纹理，不依赖外部 mesh / Fuel 下载，离线可复现。
运行：python3 scripts/generate_world.py
产物：worlds/smart_community.sdf
"""
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.normpath(os.path.join(HERE, ".."))
OUT = os.path.join(PKG, "worlds", "smart_community.sdf")

# 纹理用 model:// URI 引用，避免相对路径随 Gazebo 进程 cwd 解析失败（渲染成黑）。
# launch 会设置 GZ_SIM_RESOURCE_PATH 指向包的 share 目录，使
# model://smart_community_sim/textures/x.png 解析到 <share>/smart_community_sim/textures/x.png。
T = "model://smart_community_sim/textures"

PI = math.pi


# ---------------------------------------------------------------------------
# 场地 / 道路几何（单位 m，原点场地中心，+x 东、+y 北）
# ---------------------------------------------------------------------------
HALF = 2.1          # 场地半边长
WALL_T = 0.005      # 墙厚 0.5cm
WALL_H = 0.5        # 墙高 50cm
ROAD_W = 0.4        # 路宽 40cm

# 六段道路中心线（回字闭环）
LEFT_X = -1.4       # 左侧竖直道路（↓）
CENTRAL_X = -0.2    # 中央竖直道路（↓）
RIGHT_X = 0.6       # 最右侧竖直道路（↑）
TOP_Y = 1.3         # 最上方水平道路（←）
MID_Y = 0.0         # 中部水平道路（→）
BOT_Y = -1.3        # 最下方水平道路（→）

ASPHALT = (0.30, 0.30, 0.32)
WHITE = (0.92, 0.92, 0.92)
YELLOW = (0.9, 0.75, 0.1)
BLUE = (0.16, 0.38, 0.82)          # 蓝色主行驶方向箭头
ORANGE = (0.93, 0.60, 0.06)        # 橙黄色局部朝向箭头


# ---------------------------------------------------------------------------
# 基础几何 / 材质 helpers
# ---------------------------------------------------------------------------
def box(sx, sy, sz):
    return f"<box><size>{sx} {sy} {sz}</size></box>"


def sph(r):
    return f"<sphere><radius>{r}</radius></sphere>"


def mat_amb_diff(amb, diff):
    r1, g1, b1 = amb
    r2, g2, b2 = diff
    return (f"<material><ambient>{r1} {g1} {b1} 1</ambient>"
            f"<diffuse>{r2} {g2} {b2} 1</diffuse></material>")


def mat_tex(path):
    return (f"<material><pbr><metal>"
            f"<albedo_map>{path}</albedo_map>"
            f"<metalness>0.0</metalness>"
            f"<roughness>1.0</roughness>"
            f"</metal></pbr></material>")


def mat_emissive(r, g, b, a=1.0, dr=None, dg=None, db=None):
    d = (dr, dg, db) if dr is not None else (0.12, 0.12, 0.12)
    return (f"<material><emissive>{r} {g} {b} {a}</emissive>"
            f"<diffuse>{d[0]} {d[1]} {d[2]} 1</diffuse></material>")


def visual(name, geom, mat, pose=""):
    p = f"<pose>{pose6(pose)}</pose>" if pose else ""
    return f"<visual name='{name}'>{p}<geometry>{geom}</geometry>{mat}</visual>"


def collision(name, geom, pose=""):
    p = f"<pose>{pose6(pose)}</pose>" if pose else ""
    return f"<collision name='{name}'>{p}<geometry>{geom}</geometry></collision>"


def pose6(p):
    parts = p.split()
    return p if len(parts) == 6 else (p + " 0 0 0")


def yaw(deg):
    return deg * PI / 180


def model(name, x, y, z, yaw_deg, body, static=True):
    return f"""    <model name="{name}">
      <static>{'true' if static else 'false'}</static>
      <pose>{x} {y} {z} 0 0 {yaw(yaw_deg)}</pose>
      <link name="link">
{body}
      </link>
    </model>
"""


def flat_patch(name, x, y, sx, sy, color, z=0.021):
    """地面色块（车道线 / 停止线 / 斑马线 / 区域边框，无碰撞）。"""
    return model(name, x, y, z, 0,
                 visual("visual", box(sx, sy, 0.01),
                        mat_amb_diff(tuple(c * 0.9 for c in color), color)))


def flat_tex(name, x, y, sx, sy, tex, z=0.022, yaw_deg=0):
    """地面贴图色块（文字标注等，无碰撞）。"""
    return model(name, x, y, z, yaw_deg,
                 visual("visual", box(sx, sy, 0.01), mat_tex(tex)))


# ---------------------------------------------------------------------------
# 车道线 helpers
# ---------------------------------------------------------------------------
def solid_line(axis, fixed, lo, hi, prefix, color, w=0.02):
    """沿一条轴画连续实线（axis='x' 沿 X 且 fixed=y；axis='y' 沿 Y 且 fixed=x）。"""
    length = hi - lo
    if axis == "x":
        return flat_patch(prefix, (lo + hi) / 2, fixed, length, w, color)
    return flat_patch(prefix, fixed, (lo + hi) / 2, w, length, color)


def dash_line(axis, fixed, lo, hi, prefix, color=YELLOW, dash=0.15, gap=0.15, w=0.02):
    """沿一条轴画中心虚线。"""
    parts = []
    v = lo
    k = 0
    while v + dash <= hi:
        c = v + dash / 2
        if axis == "x":
            parts.append(flat_patch(f"{prefix}_{k}", c, fixed, dash, w, color))
        else:
            parts.append(flat_patch(f"{prefix}_{k}", fixed, c, w, dash, color))
        v += dash + gap
        k += 1
    return "".join(parts)


def zebra(name, x, y, across_x, stripes, stripe_w=0.07):
    """斑马线：across_x=True 横跨 X 方向（横条，条纹沿 Y 排列）。"""
    parts = []
    for i in range(stripes):
        off = (i - (stripes - 1) / 2) * (stripe_w * 1.7)
        if across_x:
            parts.append(flat_patch(f"{name}_{i}", x, y + off, 0.4, stripe_w, WHITE))
        else:
            parts.append(flat_patch(f"{name}_{i}", x + off, y, stripe_w, 0.4, WHITE))
    return "".join(parts)


# ---------------------------------------------------------------------------
# 道路段（路面 + 两侧白实线 + 中心黄虚线）
# ---------------------------------------------------------------------------
def road_seg_h(name, y, x0, x1):
    """水平道路：y 固定，x 从 x0 到 x1。"""
    cx, length = (x0 + x1) / 2, x1 - x0
    parts = model(name, cx, y, 0.01, 0,
                  collision("c", box(length, ROAD_W, 0.02)) +
                  visual("v", box(length, ROAD_W, 0.02), mat_amb_diff(ASPHALT, ASPHALT)))
    parts += solid_line("x", y + ROAD_W / 2, x0, x1, f"{name}_edge_n", WHITE)
    parts += solid_line("x", y - ROAD_W / 2, x0, x1, f"{name}_edge_s", WHITE)
    parts += dash_line("x", y, x0, x1, f"{name}_dash")
    return parts


def road_seg_v(name, x, y0, y1):
    """竖直道路：x 固定，y 从 y0 到 y1。"""
    cy, length = (y0 + y1) / 2, y1 - y0
    parts = model(name, x, cy, 0.01, 0,
                  collision("c", box(ROAD_W, length, 0.02)) +
                  visual("v", box(ROAD_W, length, 0.02), mat_amb_diff(ASPHALT, ASPHALT)))
    parts += solid_line("y", x + ROAD_W / 2, y0, y1, f"{name}_edge_e", WHITE)
    parts += solid_line("y", x - ROAD_W / 2, y0, y1, f"{name}_edge_w", WHITE)
    parts += dash_line("y", x, y0, y1, f"{name}_dash")
    return parts


# ---------------------------------------------------------------------------
# 复合模型
# ---------------------------------------------------------------------------
def standee(name, x, y, yaw_deg, tex):
    """人偶立牌：高 15cm × 宽 5cm × 厚 0.5cm（贴人物纹理）。"""
    b = box(0.05, 0.005, 0.15)
    return model(name, x, y, 0.075, yaw_deg,
                 collision("collision", b) +
                 visual("visual", b, mat_tex(tex)))


def plate(name, x, y, yaw_deg, tex):
    """车牌立牌：高 3cm × 宽 9.5cm × 厚 0.1cm。"""
    b = box(0.095, 0.001, 0.03)
    return model(name, x, y, 0.015, yaw_deg,
                 collision("collision", b) +
                 visual("visual", b, mat_tex(tex)))


def car_board(name, x, y, yaw_deg, plate_tex=None):
    """车背景立牌：高 25cm × 宽 34.5cm × 厚 0.5cm（竖立，贴蓝绿色车背景纹理）。

    plate_tex 非空时，在车背景正面低处加一块车牌（9.5×3cm）。
    """
    main = box(0.345, 0.005, 0.25)
    body = collision("collision", main) + visual("visual", main,
                                                 mat_tex(f"{T}/car_background.png"))
    if plate_tex:
        pb = box(0.095, 0.002, 0.03)
        body += visual("plate", pb, mat_tex(plate_tex), "-0.08 0.0035 -0.09 0 0 0")
    return model(name, x, y, 0.125, yaw_deg, body)


def traffic_light(name, x, y, yaw_deg, orientation="horizontal",
                  order=("red", "yellow", "green")):
    """省赛红绿灯。

    orientation='horizontal'：三灯横向并排（箱体 59×14×5cm 挂双腿）。
    orientation='vertical'  ：三灯竖向排列（箱体 14×59×5cm）。
    order 依次为 (左/上, 中, 右/下) 三盏灯，灯名固定 red/yellow/green 供插件识别。
    默认 yaw=0 时正面朝北（+y）。
    """
    grey = mat_amb_diff((0.10, 0.10, 0.10), (0.18, 0.18, 0.18))
    lamp_mat = mat_emissive(0.05, 0.05, 0.05)  # 初始熄灭，插件控制
    if orientation == "horizontal":
        legs = (visual("leg_l", box(0.025, 0.025, 0.48), grey, "-0.3075 0 0.24 0 0 0") +
                visual("leg_r", box(0.025, 0.025, 0.48), grey, "0.3075 0 0.24 0 0 0"))
        housing = visual("housing", box(0.59, 0.05, 0.14), grey, "0 0 0.41 0 0 0")
        xs = [-0.19, 0.0, 0.19]
        lamps = "".join(visual(c, sph(0.045), lamp_mat, f"{lx} 0.026 0.41 0 0 0")
                        for c, lx in zip(order, xs))
    else:  # vertical
        legs = (visual("leg_l", box(0.025, 0.025, 0.30), grey, "-0.05 0 0.15 0 0 0") +
                visual("leg_r", box(0.025, 0.025, 0.30), grey, "0.05 0 0.15 0 0 0"))
        housing = visual("housing", box(0.14, 0.05, 0.59), grey, "0 0 0.42 0 0 0")
        zs = [0.19, 0.0, -0.19]
        lamps = "".join(visual(c, sph(0.045), lamp_mat, f"0 0.026 {0.42 + lz} 0 0 0")
                        for c, lz in zip(order, zs))
    return model(name, x, y, 0, yaw_deg, legs + housing + lamps)


def arrow(name, x, y, yaw_deg, length=0.45, width=0.09, color=BLUE):
    """行驶方向箭头（地面标线）：杆 + 前端箭头，指向 +x（经 yaw 旋转）。

    默认蓝色（主行驶方向）；color=ORANGE 时用于局部朝向箭头。
    """
    m = mat_amb_diff(tuple(c * 0.85 for c in color), color)
    L, W = length, width
    Lh = L * 0.5
    off = Lh * 0.3535  # 前端箭头两根斜杆中心的偏移量
    shaft = visual("shaft", box(L, W, 0.012), m)
    h1 = visual("head1", box(Lh, W, 0.012), m, f"{L/2 - off} {-off} 0 0 0 45")
    h2 = visual("head2", box(Lh, W, 0.012), m, f"{L/2 - off} {off} 0 0 0 -45")
    return model(name, x, y, 0.024, yaw_deg, shaft + h1 + h2)


def zone_rect(name, x0, x1, y0, y1):
    """矩形封闭区域边框（A/B临区），深灰色细线。"""
    border = (0.25, 0.25, 0.28)
    w = 0.02
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    lx, ly = x1 - x0, y1 - y0
    return (flat_patch(f"{name}_n", cx, y1, lx, w, border) +
            flat_patch(f"{name}_s", cx, y0, lx, w, border) +
            flat_patch(f"{name}_w", x0, cy, w, ly, border) +
            flat_patch(f"{name}_e", x1, cy, w, ly, border))


def label(name, x, y, height, yaw_deg=0, z=0.023, tex_name=None):
    """地面文字标注。height 为标注高度(m)，宽度按 PNG 宽高比自动计算。

    tex_name 默认等于 name；当多个位置复用同一张纹理时，用唯一 name +
    相同 tex_name，避免模型重名。
    """
    tex_name = tex_name or name
    width = height * 4
    try:
        from PIL import Image
        p = os.path.join(PKG, "textures", f"{tex_name}.png")
        if os.path.exists(p):
            with Image.open(p) as im:
                w, h = im.size
            width = height * w / h
    except Exception:
        pass
    return flat_tex(name, x, y, width, height, f"{T}/{tex_name}.png", z=z, yaw_deg=yaw_deg)


def parking_spot(name, x, y, lx, ly):
    """停车位白线框（4 条白边）。lx 沿 X，ly 沿 Y。"""
    w = 0.03
    white = (0.95, 0.95, 0.95)
    return (flat_patch(f"{name}_top", x, y + ly / 2, lx, w, white) +
            flat_patch(f"{name}_bot", x, y - ly / 2, lx, w, white) +
            flat_patch(f"{name}_left", x - lx / 2, y, w, ly, white) +
            flat_patch(f"{name}_right", x + lx / 2, y, w, ly, white))


# ---------------------------------------------------------------------------
# 组装世界
# ---------------------------------------------------------------------------
def gen():
    parts = []
    parts.append(f"""<?xml version="1.0" ?>
<!-- 智慧社区复赛仿真世界（4.2m×4.2m，由 scripts/generate_world.py 生成，勿手改，改脚本后重跑） -->
<sdf version="1.8">
  <world name="smart_community">

    <physics type="ode">
      <max_step_size>0.004</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>250</real_time_update_rate>
    </physics>

    <plugin name="gz::sim::systems::Physics" filename="gz-sim-physics-system"/>
    <plugin name="gz::sim::systems::UserCommands" filename="gz-sim-user-commands-system"/>
    <plugin name="gz::sim::systems::SceneBroadcaster" filename="gz-sim-scene-broadcaster-system"/>
    <plugin name="gz::sim::systems::Contact" filename="gz-sim-contact-system"/>
    <plugin name="gz::sim::systems::Sensors" filename="gz-sim-sensors-system">
      <render_engine>ogre2</render_engine>
    </plugin>
    <plugin name="gz::sim::systems::TrafficLight" filename="libTrafficLightSystem.so">
      <prefix>traffic_light</prefix>
      <green_time>15</green_time>
      <yellow_time>5</yellow_time>
      <red_time>10</red_time>
    </plugin>

    <gravity>0 0 -9.8</gravity>

    <scene>
      <grid>false</grid>
      <ambient>0.45 0.45 0.45 1</ambient>
      <background>0.6 0.72 0.85 1</background>
      <shadows>true</shadows>
    </scene>

    <light name="sun" type="directional">
      <pose>0 0 8 0 0 0</pose>
      <cast_shadows>true</cast_shadows>
      <intensity>1</intensity>
      <direction>-0.3 0.1 -0.95</direction>
      <diffuse>0.9 0.9 0.9 1</diffuse>
      <specular>0.25 0.25 0.25 1</specular>
      <attenuation>
        <range>100</range>
        <constant>0.9</constant>
        <linear>0.01</linear>
        <quadratic>0.001</quadratic>
      </attenuation>
    </light>

    <!-- ===== 地面（场地底板） ===== -->
    <model name="ground_plane">
      <static>true</static>
      <link name="link">
        <collision name="collision">
          <geometry><plane><normal>0 0 1</normal><size>6 6</size></plane></geometry>
          <surface><friction><ode><mu>1.0</mu><mu2>1.0</mu2></ode></friction></surface>
        </collision>
        <visual name="visual">
          <geometry><plane><normal>0 0 1</normal><size>6 6</size></plane></geometry>
          <material><ambient>0.72 0.72 0.74 1</ambient><diffuse>0.82 0.82 0.84 1</diffuse><specular>0.05 0.05 0.05 1</specular></material>
        </visual>
      </link>
    </model>
""")

    # ----- 围墙（4.2m 方形，厚 0.5cm、高 50cm） -----
    parts.append("    <!-- ===== 围墙 ===== -->\n")
    gray_wall = mat_amb_diff((0.55, 0.55, 0.58), (0.72, 0.72, 0.75))
    wall_outer = HALF + WALL_T / 2
    parts.append(model("wall_n", 0, wall_outer, WALL_H / 2, 0,
                       collision("c", box(4.2 + WALL_T, WALL_T, WALL_H)) +
                       visual("v", box(4.2 + WALL_T, WALL_T, WALL_H), gray_wall)))
    parts.append(model("wall_s", 0, -wall_outer, WALL_H / 2, 0,
                       collision("c", box(4.2 + WALL_T, WALL_T, WALL_H)) +
                       visual("v", box(4.2 + WALL_T, WALL_T, WALL_H), gray_wall)))
    parts.append(model("wall_e", wall_outer, 0, WALL_H / 2, 0,
                       collision("c", box(WALL_T, 4.2 + WALL_T, WALL_H)) +
                       visual("v", box(WALL_T, 4.2 + WALL_T, WALL_H), gray_wall)))
    parts.append(model("wall_w", -wall_outer, 0, WALL_H / 2, 0,
                       collision("c", box(WALL_T, 4.2 + WALL_T, WALL_H)) +
                       visual("v", box(WALL_T, 4.2 + WALL_T, WALL_H), gray_wall)))

    # ----- 回字形道路（6 段，单向闭环） -----
    parts.append("    <!-- ===== 回字形道路（6 段闭环） ===== -->\n")
    # 1. 最上方水平道路（←）：x 从 LEFT 到 RIGHT
    parts.append(road_seg_h("road_top", TOP_Y, LEFT_X, RIGHT_X))
    # 2. 左侧竖直道路（↓）：y 从 TOP 到 MID
    parts.append(road_seg_v("road_left", LEFT_X, MID_Y, TOP_Y))
    # 3. 中部水平道路（→）：x 从 LEFT 到 CENTRAL
    parts.append(road_seg_h("road_mid", MID_Y, LEFT_X, CENTRAL_X))
    # 4. 中央竖直道路（↓）：y 从 MID 到 BOT
    parts.append(road_seg_v("road_central", CENTRAL_X, BOT_Y, MID_Y))
    # 5. 最下方水平道路（→）：x 从 CENTRAL 到 RIGHT
    parts.append(road_seg_h("road_bottom", BOT_Y, CENTRAL_X, RIGHT_X))
    # 6. 最右侧竖直道路（↑）：y 从 BOT 到 TOP
    parts.append(road_seg_v("road_right", RIGHT_X, BOT_Y, TOP_Y))

    # ----- 蓝色主行驶方向箭头（6 段，逐一对应） -----
    parts.append("    <!-- ===== 蓝色主行驶方向箭头 ===== -->\n")
    parts.append(arrow("arrow_top", RIGHT_X - 0.5, TOP_Y, 180))       # 上方 ←（西）
    parts.append(arrow("arrow_left", LEFT_X, TOP_Y - 0.5, -90))        # 左侧 ↓（南）
    parts.append(arrow("arrow_mid", CENTRAL_X - 0.5, MID_Y, 0))        # 中部 →（东）
    parts.append(arrow("arrow_central", CENTRAL_X, MID_Y - 0.5, -90))  # 中央 ↓（南）
    parts.append(arrow("arrow_bottom", RIGHT_X - 0.4, BOT_Y, 0))       # 下方 →（东）
    parts.append(arrow("arrow_right", RIGHT_X, TOP_Y - 0.5, 90))       # 右侧 ↑（北）

    # ----- 红绿灯 + 停止线 + 斑马线 -----
    parts.append("    <!-- ===== 红绿灯 + 停止线 + 斑马线 ===== -->\n")
    # 上方灯：竖向排列（红上/黄中/绿下），位于顶部道路北侧（对向来车）
    parts.append(traffic_light("traffic_light_1", -0.6, TOP_Y + 0.25, 0,
                               orientation="vertical", order=("red", "yellow", "green")))
    # 顶部停止线：竖向短横线（跨道路），位于灯东侧（车自东向西，先停后过）
    parts.append(solid_line("y", -0.3, TOP_Y - ROAD_W / 2, TOP_Y + ROAD_W / 2,
                            "stop_top", WHITE, w=0.05))
    # 顶部斑马线：横向，位于灯下方道路处
    parts.append(zebra("zebra_top", -0.6, TOP_Y, across_x=True, stripes=5))
    # 下方灯：横向排列（绿左/黄中/红右），位于中央道路与下方道路交汇处
    parts.append(traffic_light("traffic_light_2", CENTRAL_X - 0.2, BOT_Y - 0.25, 180,
                               orientation="horizontal", order=("green", "yellow", "red")))
    # 下方停止线：横向，位于中央道路（车自北向南）
    parts.append(solid_line("x", BOT_Y + 0.3, CENTRAL_X - ROAD_W / 2, CENTRAL_X + ROAD_W / 2,
                            "stop_bottom", WHITE, w=0.05))
    # 下方斑马线：横向，位于中央道路与下方道路交汇处
    parts.append(zebra("zebra_bottom", CENTRAL_X, BOT_Y, across_x=True, stripes=5))

    # ----- A临区（左上矩形，5 社区人员） -----
    parts.append("    <!-- ===== A临区（左上，5 社区人员） ===== -->\n")
    a_x0, a_x1, a_y0, a_y1 = -1.2, -0.5, 0.2, 1.05
    parts.append(zone_rect("zone_a", a_x0, a_x1, a_y0, a_y1))
    parts.append(label("label_a", a_x0 + 0.25, a_y1 - 0.08, 0.06))
    a_people = ["person_community_01.png", "person_community_02.png",
                "person_community_03.png", "person_community_04.png",
                "person_community_05.png"]
    for i, tex in enumerate(a_people):
        parts.append(standee(f"person_a{i + 1}", a_x0 + 0.12 + i * 0.13,
                             (a_y0 + a_y1) / 2, 0, f"{T}/{tex}"))
    # A区下方橙色小箭头（局部朝向）
    parts.append(arrow("arrow_a", (a_x0 + a_x1) / 2 + 0.15, a_y0 - 0.05, 0,
                       length=0.22, width=0.06, color=ORANGE))

    # ----- B临区（左下矩形，5 社区人员） -----
    parts.append("    <!-- ===== B临区（左下，5 社区人员） ===== -->\n")
    b_x0, b_x1, b_y0, b_y1 = -1.2, -0.5, -1.05, -0.2
    parts.append(zone_rect("zone_b", b_x0, b_x1, b_y0, b_y1))
    parts.append(label("label_b", b_x0 + 0.25, b_y1 - 0.08, 0.06))
    b_people = ["person_community_06.png", "person_community_07.png",
                "person_community_08.png", "person_community_09.png",
                "person_community_10.png"]
    for i, tex in enumerate(b_people):
        parts.append(standee(f"person_b{i + 1}", b_x0 + 0.12 + i * 0.13,
                             (b_y0 + b_y1) / 2, 0, f"{T}/{tex}"))
    # B区橙色方向箭头：一个向上、一个向右
    parts.append(arrow("arrow_b_up", b_x0 + 0.2, b_y0 + 0.15, 90,
                       length=0.22, width=0.06, color=ORANGE))
    parts.append(arrow("arrow_b_right", b_x0 + 0.2, b_y0 + 0.03, 0,
                       length=0.22, width=0.06, color=ORANGE))

    # ----- 其余 8 人（6 社区 + 2 非社区），沿道路/场地分布 -----
    parts.append("    <!-- ===== 其余人员（6 社区 + 2 非社区） ===== -->\n")
    others = [
        ("person_s1", -0.6, 1.7, 180, "person_community_11.png"),
        ("person_s2", -1.0, 1.7, 180, "person_community_12.png"),
        ("person_s3", -1.7, 0.9, 90, "person_community_13.png"),
        ("person_s4", -1.7, -0.2, 90, "person_community_14.png"),
        ("person_s5", -0.6, -1.7, 0, "person_community_15.png"),
        ("person_s6", -1.0, -1.7, 0, "person_community_16.png"),
        ("person_f1", 0.9, 1.7, 180, "person_noncommunity_01.png"),
        ("person_f2", 1.0, -1.7, 0, "person_noncommunity_02.png"),
    ]
    for name, px, py, yw, tex in others:
        parts.append(standee(name, px, py, yw, f"{T}/{tex}"))

    # ----- 右侧停车场（3/2/1 号，从上到下，各停 1 蓝绿色汽车） -----
    parts.append("    <!-- ===== 右侧停车场（3 车位，从上到下 3/2/1） ===== -->\n")
    spots = [("3", 0.6, f"{T}/plate_3.png", "label_spot_3"),
             ("2", 0.0, f"{T}/plate_2.png", "label_spot_2"),
             ("1", -0.6, f"{T}/plate_1.png", "label_spot_1")]
    for num, sy, plate_tex, lab in spots:
        parts.append(parking_spot(f"spot_{num}", 1.7, sy, 0.6, 0.6))
        parts.append(car_board(f"car_{num}", 1.7, sy, 90, plate_tex))
        parts.append(label(lab, 2.05, sy, 0.045, yaw_deg=90))

    # ----- 起点/终点（右上角，中文标注） -----
    parts.append("    <!-- ===== 起点/终点（右上角） ===== -->\n")
    parts.append(label("label_start", 0.95, 1.75, 0.07))
    parts.append(label("label_end", 0.95, 1.55, 0.07))

    # ----- 三处 60cm 尺寸标注 -----
    parts.append("    <!-- ===== 60cm 尺寸标注（三处） ===== -->\n")
    parts.append(label("label_60cm_a", a_x1 + 0.16, 1.75, 0.05, tex_name="label_60cm"))       # A区右侧
    parts.append(label("label_60cm_parking", 1.0, 0.35, 0.05, tex_name="label_60cm"))        # 道路与停车区之间
    parts.append(label("label_60cm_b", (b_x0 + b_x1) / 2, -1.75, 0.05, tex_name="label_60cm"))  # B区下方

    parts.append("""
  </world>
</sdf>
""")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    print("生成完成 ->", OUT)


if __name__ == "__main__":
    gen()
