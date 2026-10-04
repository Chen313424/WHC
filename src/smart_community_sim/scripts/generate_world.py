#!/usr/bin/env python3
"""生成 worlds/smart_community.sdf（省赛 4.2m×4.2m 场地）。

场景对齐「9.24 智慧社区培训」省赛规格与用户提供的示意图文字布局：
  - 4.2m×4.2m 方形场地，四周围墙（厚 0.5cm、高 50cm）
  - 环形闭合车道（边线 + 中心线），道路侧边留白 60cm，路宽 40cm
  - 车道上橙黄色行驶方向箭头（顺时针：顶→东、右→南、底→西、左→北）
  - 2 组红绿灯（顶部直道 / 底部直道，各带停止线），下方灯北侧斑马线
  - 18 个人偶立牌（高15 宽5 厚0.5cm）：A街区 6 + B街区 6 + 人行道 6（含 2 非社区）
  - 右侧 3 个停车位（3/2/1 号，各停 1 车背景立牌 + 车牌）+ 额外 1 车背景
  - 起点/终点合并（右上角）

纯 SDF 基本体 + 本地 PNG 纹理，不依赖外部 mesh / Fuel 下载，离线可复现。
运行：python3 scripts/generate_world.py
产物：worlds/smart_community.sdf
"""
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.normpath(os.path.join(HERE, ".."))
OUT = os.path.join(PKG, "worlds", "smart_community.sdf")

# 纹理相对 world 文件的路径（world 在 worlds/ 下，纹理在 textures/ 下）
T = "../textures"

PI = math.pi


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
    """地面色块（车道线 / 停止线 / 斑马线 / 出发区 / 停车位框，无碰撞）。"""
    return model(name, x, y, z, 0,
                 visual("visual", box(sx, sy, 0.01),
                        mat_amb_diff(tuple(c * 0.9 for c in color), color)))


# ---------------------------------------------------------------------------
# 复合模型（省赛尺寸）
# ---------------------------------------------------------------------------
def standee(name, x, y, yaw_deg, tex):
    """人偶立牌：高 15cm × 宽 5cm × 厚 0.5cm（底面贴地，贴人物纹理）。"""
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
    """车背景立牌：高 25cm × 宽 34.5cm × 厚 0.5cm（竖立，贴车背景纹理）。

    plate_tex 非空时，在车背景正面（+y 方向）低处加一块车牌（9.5×3cm）。
    朝向由 yaw 决定；纹理贴在 ±y 两个面，便于从道路两侧看到。
    """
    main = box(0.345, 0.005, 0.25)
    body = collision("collision", main) + visual("visual", main,
                                                 mat_tex(f"{T}/car_background.png"))
    if plate_tex:
        # 车牌放在车背景正面，前保险杠位置（横向偏左、贴地）
        pb = box(0.095, 0.002, 0.03)
        body += visual("plate", pb, mat_tex(plate_tex), "-0.08 0.0035 -0.09 0 0 0")
    return model(name, x, y, 0.125, yaw_deg, body)


def traffic_light(name, x, y, yaw_deg):
    """省赛红绿灯：整体高 48cm、宽 64cm、厚 5cm。

    箱体 59×14×5cm 挂在两根 48×2.5×2.5cm 腿上，箱体正面（+y）并排
    红 / 黄 / 绿 三盏灯泡（visual 名固定 red/yellow/green，供插件识别）。
    yaw 决定正面朝向；默认 yaw=0 时正面朝北（+y）。
    """
    grey = mat_amb_diff((0.10, 0.10, 0.10), (0.18, 0.18, 0.18))
    legs = (visual("leg_l", box(0.025, 0.025, 0.48), grey, "-0.3075 0 0.24 0 0 0") +
            visual("leg_r", box(0.025, 0.025, 0.48), grey, "0.3075 0 0.24 0 0 0"))
    housing = visual("housing", box(0.59, 0.05, 0.14), grey, "0 0 0.41 0 0 0")
    # 三盏灯：红左、黄中、绿右（横向并排），初始熄灭（插件控制）
    red = visual("red", sph(0.045), mat_emissive(0.05, 0.05, 0.05), "-0.19 0.026 0.41 0 0 0")
    yellow = visual("yellow", sph(0.045), mat_emissive(0.05, 0.05, 0.05), "0 0.026 0.41 0 0 0")
    green = visual("green", sph(0.045), mat_emissive(0.05, 0.05, 0.05), "0.19 0.026 0.41 0 0 0")
    return model(name, x, y, 0, yaw_deg,
                 legs + housing + red + yellow + green)


def arrow(name, x, y, yaw_deg, length=0.45, width=0.09):
    """橙黄色行驶方向箭头（地面标线）：杆 + 前端箭头，指向 +x（经 yaw 旋转）。"""
    color = (0.93, 0.60, 0.06)
    m = mat_amb_diff(tuple(c * 0.85 for c in color), color)
    L, W = length, width
    Lh = L * 0.5
    off = Lh * 0.3535  # 前端箭头两根斜杆中心的偏移量
    shaft = visual("shaft", box(L, W, 0.012), m)
    h1 = visual("head1", box(Lh, W, 0.012), m, f"{L/2 - off} {-off} 0 0 0 45")
    h2 = visual("head2", box(Lh, W, 0.012), m, f"{L/2 - off} {off} 0 0 0 -45")
    return model(name, x, y, 0.024, yaw_deg, shaft + h1 + h2)


def parking_spot(name, x, y, lx, ly):
    """停车位白线框（4 条白边）。lx 沿 X，ly 沿 Y。"""
    w = 0.03
    white = (0.95, 0.95, 0.95)
    return (flat_patch(f"{name}_top", x, y + ly / 2, lx, w, white) +
            flat_patch(f"{name}_bot", x, y - ly / 2, lx, w, white) +
            flat_patch(f"{name}_left", x - lx / 2, y, w, ly, white) +
            flat_patch(f"{name}_right", x + lx / 2, y, w, ly, white))


# ---------------------------------------------------------------------------
# 车道线 helpers
# ---------------------------------------------------------------------------
WHITE = (0.92, 0.92, 0.92)
YELLOW = (0.9, 0.75, 0.1)


def solid_line(axis, fixed, lo, hi, prefix, color, w=0.02):
    """沿一条轴画连续实线（axis='x' 时沿 X 且 fixed=y，axis='y' 时沿 Y 且 fixed=x）。"""
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
    """斑马线：across_x=True 时横跨 X 方向（条纹沿 Y 排列）的横条。"""
    parts = []
    for i in range(stripes):
        off = (i - (stripes - 1) / 2) * (stripe_w * 1.7)
        if across_x:
            parts.append(flat_patch(f"{name}_{i}", x, y + off, 0.4, stripe_w, WHITE))
        else:
            parts.append(flat_patch(f"{name}_{i}", x + off, y, stripe_w, 0.4, WHITE))
    return "".join(parts)


# ---------------------------------------------------------------------------
# 组装世界
# ---------------------------------------------------------------------------
# 场地 4.2m×4.2m，坐标原点在中心，+x 东（右）、+y 北（上）。
HALF = 2.1          # 半边长
WALL_T = 0.005      # 墙厚 0.5cm
WALL_H = 0.5        # 墙高 50cm
MARGIN = 0.6        # 道路侧边留白 60cm
ROAD_W = 0.4        # 路宽 40cm
ROAD_O = HALF - MARGIN        # 道路外缘 1.5m
ROAD_I = ROAD_O - ROAD_W      # 道路内缘 1.1m
ROAD_C = (ROAD_O + ROAD_I) / 2  # 道路中心线 1.3m


def gen():
    parts = []
    parts.append(f"""<?xml version="1.0" ?>
<!-- 智慧社区省赛仿真世界（4.2m×4.2m，由 scripts/generate_world.py 生成，勿手改，改脚本后重跑） -->
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

    # ----- 道路（环形闭合，4 段） -----
    parts.append("    <!-- ===== 环形道路 ===== -->\n")
    asphalt = (0.30, 0.30, 0.32)
    parts.append(model("road_top", 0, ROAD_C, 0.01, 0,
                       collision("c", box(ROAD_O * 2, ROAD_W, 0.02)) +
                       visual("v", box(ROAD_O * 2, ROAD_W, 0.02), mat_amb_diff(asphalt, asphalt))))
    parts.append(model("road_bottom", 0, -ROAD_C, 0.01, 0,
                       collision("c", box(ROAD_O * 2, ROAD_W, 0.02)) +
                       visual("v", box(ROAD_O * 2, ROAD_W, 0.02), mat_amb_diff(asphalt, asphalt))))
    parts.append(model("road_left", -ROAD_C, 0, 0.01, 0,
                       collision("c", box(ROAD_W, ROAD_I * 2, 0.02)) +
                       visual("v", box(ROAD_W, ROAD_I * 2, 0.02), mat_amb_diff(asphalt, asphalt))))
    parts.append(model("road_right", ROAD_C, 0, 0.01, 0,
                       collision("c", box(ROAD_W, ROAD_I * 2, 0.02)) +
                       visual("v", box(ROAD_W, ROAD_I * 2, 0.02), mat_amb_diff(asphalt, asphalt))))

    # ----- 车道边线（外侧 + 内侧，白色实线） -----
    parts.append("    <!-- ===== 车道边线 ===== -->\n")
    parts.append(solid_line("x", ROAD_O, -ROAD_O, ROAD_O, "edge_out_top", WHITE))
    parts.append(solid_line("x", -ROAD_O, -ROAD_O, ROAD_O, "edge_out_bot", WHITE))
    parts.append(solid_line("y", ROAD_O, -ROAD_O, ROAD_O, "edge_out_right", WHITE))
    parts.append(solid_line("y", -ROAD_O, -ROAD_O, ROAD_O, "edge_out_left", WHITE))
    parts.append(solid_line("x", ROAD_I, -ROAD_I, ROAD_I, "edge_in_top", WHITE))
    parts.append(solid_line("x", -ROAD_I, -ROAD_I, ROAD_I, "edge_in_bot", WHITE))
    parts.append(solid_line("y", ROAD_I, -ROAD_I, ROAD_I, "edge_in_right", WHITE))
    parts.append(solid_line("y", -ROAD_I, -ROAD_I, ROAD_I, "edge_in_left", WHITE))

    # ----- 道路中心线（黄色虚线） -----
    parts.append("    <!-- ===== 道路中心虚线 ===== -->\n")
    parts.append(dash_line("x", ROAD_C, -ROAD_C, ROAD_C, "dash_top"))
    parts.append(dash_line("x", -ROAD_C, -ROAD_C, ROAD_C, "dash_bot"))
    parts.append(dash_line("y", ROAD_C, -ROAD_I, ROAD_I, "dash_right"))
    parts.append(dash_line("y", -ROAD_C, -ROAD_I, ROAD_I, "dash_left"))

    # ----- 行驶方向箭头（顺时针：顶→东、右→南、底→西、左→北） -----
    parts.append("    <!-- ===== 行驶方向箭头（橙黄） ===== -->\n")
    parts.append(arrow("arrow_top", -0.6, ROAD_C, 0, 0.45))       # 顶：→ 东
    parts.append(arrow("arrow_right", ROAD_C, 0.6, -90, 0.45))    # 右：↓ 南
    parts.append(arrow("arrow_bottom", 0.6, -ROAD_C, 180, 0.45))  # 底：← 西
    parts.append(arrow("arrow_left", -ROAD_C, -0.6, 90, 0.45))    # 左：↑ 北

    # ----- 红绿灯（2 组） + 停止线 + 斑马线 -----
    parts.append("    <!-- ===== 红绿灯 + 停止线 + 斑马线 ===== -->\n")
    # 上方灯：右侧直道（南向车流）旁，正面向北（对向来车）
    parts.append(traffic_light("traffic_light_1", 1.75, 0.9, 0))
    parts.append(solid_line("x", 0.9, ROAD_I, ROAD_O, "stop_top", WHITE, w=0.05))
    # 下方灯：左侧直道（北向车流）旁，正面向南（对向来车）
    parts.append(traffic_light("traffic_light_2", -1.75, -0.9, 180))
    parts.append(solid_line("x", -0.9, -ROAD_O, -ROAD_I, "stop_bottom", WHITE, w=0.05))
    # 斑马线：下方灯北侧，横跨左侧直道
    parts.append(zebra("zebra_bottom", -ROAD_C, -0.72, across_x=False, stripes=5))

    # ----- A街区（左上矩形区域，6 社区） -----
    parts.append("    <!-- ===== A街区（左上，6 社区人员） ===== -->\n")
    a_people = ["person_community_01.png", "person_community_02.png",
                "person_community_03.png", "person_community_04.png",
                "person_community_05.png", "person_community_06.png"]
    a_pos = [(-0.80, 0.35, 0), (-0.45, 0.35, 0), (-0.10, 0.35, 0),
             (-0.80, 0.85, 0), (-0.45, 0.85, 0), (-0.10, 0.85, 0)]
    for i, (px, py, yw) in enumerate(a_pos):
        parts.append(standee(f"person_a{i + 1}", px, py, yw, f"{T}/{a_people[i]}"))

    # ----- B街区（左下矩形区域，6 社区） -----
    parts.append("    <!-- ===== B街区（左下，6 社区人员） ===== -->\n")
    b_people = ["person_community_07.png", "person_community_08.png",
                "person_community_09.png", "person_community_10.png",
                "person_community_11.png", "person_community_12.png"]
    b_pos = [(-0.80, -0.35, 0), (-0.45, -0.35, 0), (-0.10, -0.35, 0),
             (-0.80, -0.85, 0), (-0.45, -0.85, 0), (-0.10, -0.85, 0)]
    for i, (px, py, yw) in enumerate(b_pos):
        parts.append(standee(f"person_b{i + 1}", px, py, yw, f"{T}/{b_people[i]}"))

    # ----- 人行道区域（6 人，含 2 非社区 F1/F2） -----
    parts.append("    <!-- ===== 人行道区域（4 社区 + 2 非社区） ===== -->\n")
    parts.append(standee("person_s1", -0.5, 1.8, 180, f"{T}/person_community_13.png"))
    parts.append(standee("person_s2", -0.5, -1.8, 0, f"{T}/person_community_14.png"))
    parts.append(standee("person_s3", 0.5, -1.8, 0, f"{T}/person_community_15.png"))
    parts.append(standee("person_s4", -1.8, -0.5, 90, f"{T}/person_community_16.png"))
    parts.append(standee("person_f1", 0.5, 1.8, 180, f"{T}/person_noncommunity_01.png"))
    parts.append(standee("person_f2", -1.8, 0.5, 90, f"{T}/person_noncommunity_02.png"))

    # ----- 右侧停车场（3 号 / 2 号 / 1 号，从上到下，各停 1 车背景 + 车牌） -----
    parts.append("    <!-- ===== 右侧停车场（3 车位） ===== -->\n")
    spots = [("3", 1.0, f"{T}/plate_3.png"),
             ("2", 0.0, f"{T}/plate_2.png"),
             ("1", -1.0, f"{T}/plate_1.png")]
    for num, sy, plate_tex in spots:
        parts.append(parking_spot(f"spot_{num}", 1.8, sy, 0.6, 0.6))
        parts.append(car_board(f"car_{num}", 1.8, sy, 90, plate_tex))

    # ----- 额外 1 台车辆背景模型（放在顶部直道北侧人行道） -----
    parts.append("    <!-- ===== 额外车辆背景模型 ===== -->\n")
    parts.append(car_board("car_background_extra", 0.0, 1.8, 0))

    # ----- 起点/终点（右上角，合并） -----
    parts.append("    <!-- ===== 起点/终点（右上角） ===== -->\n")
    parts.append(flat_patch("start_pad", ROAD_C, ROAD_C, 0.5, 0.5, (0.4, 0.8, 0.4)))
    parts.append(flat_patch("start_border", ROAD_C, ROAD_C, 0.52, 0.52, WHITE, z=0.018))

    parts.append("""
  </world>
</sdf>
""")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    print("生成完成 ->", OUT)


if __name__ == "__main__":
    gen()
