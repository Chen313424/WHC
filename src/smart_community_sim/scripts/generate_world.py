#!/usr/bin/env python3
"""生成 worlds/smart_community.sdf（智慧社区完整场景，对齐总决赛场地示意图）。

道路：闭合街区路网（环形路）+ 中央南北主路，形成上下两个红绿灯十字路口；
A街区（上方）/ B街区（下方）两个封闭行人区。
纯 SDF 基本体 + 本地 PNG 纹理，不依赖外部 mesh / Fuel 下载，离线可复现。
运行：python3 scripts/generate_world.py
产物：worlds/smart_community.sdf
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.normpath(os.path.join(HERE, ".."))
OUT = os.path.join(PKG, "worlds", "smart_community.sdf")

# 纹理相对 world 文件的路径（world 在 worlds/ 下，纹理在 textures/ 下）
T = "../textures"


# ---------------------------------------------------------------------------
# 基础几何/材质 helpers
# ---------------------------------------------------------------------------
def box(sx, sy, sz):
    return f"<box><size>{sx} {sy} {sz}</size></box>"


def cyl(r, l):
    return f"<cylinder><radius>{r}</radius><length>{l}</length></cylinder>"


def sph(r):
    return f"<sphere><radius>{r}</radius></sphere>"


def mat_diff(r, g, b, a=1.0):
    return f'<material><diffuse>{r} {g} {b} {a}</diffuse></material>'


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


def static_box_model(name, pose, sx, sy, sz, mat, collide=True, extra_visuals=""):
    pose = pose6(pose)
    v = visual("visual", box(sx, sy, sz), mat)
    c = collision("collision", box(sx, sy, sz)) if collide else ""
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{pose}</pose>
      <link name="link">
{c}
{v}
{extra_visuals}
      </link>
    </model>
"""


def flat_patch(name, x, y, sx, sy, color, z=0.021):
    """地面色块（车道线/斑马线/出发区等，无碰撞）。"""
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} {z} 0 0 0</pose>
      <link name="link">
{visual("visual", box(sx, sy, 0.01), mat_amb_diff(tuple(c * 0.9 for c in color), color))}
      </link>
    </model>
"""


# ---------------------------------------------------------------------------
# 复合模型
# ---------------------------------------------------------------------------
def traffic_light(name, x, y, yaw_deg, horizontal=False):
    """红绿灯：杆 + 红/黄/绿三盏灯（visual 名固定为 red/yellow/green）。
    horizontal=False 为竖向（红上、黄中、绿下）；
    horizontal=True 为横向（绿、黄、红）。"""
    pole = cyl(0.04, 2.6)
    pole_vis = visual("pole", pole, mat_amb_diff((0.2, 0.2, 0.2), (0.3, 0.3, 0.3)),
                      "0 0 1.3 0 0 0")
    pole_col = collision("pole_col", pole, "0 0 1.3 0 0 0")
    if not horizontal:
        housing = visual("housing", box(0.24, 0.16, 0.62),
                         mat_amb_diff((0.1, 0.1, 0.1), (0.15, 0.15, 0.15)),
                         "0 0 2.0 0 0 0")
        red = visual("red", sph(0.09), mat_emissive(0.05, 0.05, 0.05), "0 0 2.3 0 0 0")
        yellow = visual("yellow", sph(0.09), mat_emissive(0.05, 0.05, 0.05), "0 0 2.0 0 0 0")
        green = visual("green", sph(0.09), mat_emissive(0.05, 0.05, 0.05), "0 0 1.7 0 0 0")
    else:
        housing = visual("housing", box(0.16, 0.62, 0.24),
                         mat_amb_diff((0.1, 0.1, 0.1), (0.15, 0.15, 0.15)),
                         "0 0 2.0 0 0 0")
        red = visual("red", sph(0.09), mat_emissive(0.05, 0.05, 0.05), "0 0.28 2.0 0 0 0")
        yellow = visual("yellow", sph(0.09), mat_emissive(0.05, 0.05, 0.05), "0 0 2.0 0 0 0")
        green = visual("green", sph(0.09), mat_emissive(0.05, 0.05, 0.05), "0 -0.28 2.0 0 0 0")
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} 0 0 0 {yaw_deg * 3.14159265 / 180}</pose>
      <link name="link">
{pole_col}
{pole_vis}
{housing}
{red}
{yellow}
{green}
      </link>
    </model>
"""


def flames(name, px, py, pz, scale=1.0):
    """火焰/高温可视化标记（橙红 emissive 发光球）。name 保证同楼宇内唯一。"""
    return (visual(f"{name}_out", sph(0.40 * scale),
                   mat_emissive(1.0, 0.45, 0.05, 1.0, 0.9, 0.35, 0.05),
                   f"{px} {py} {pz} 0 0 0") +
            visual(f"{name}_core", sph(0.24 * scale),
                   mat_emissive(1.0, 0.80, 0.10, 1.0, 1.0, 0.60, 0.10),
                   f"{px} {py} {pz} 0 0 0"))


def building(name, x, y, lx, ly, h, wall, label_tex, door_axis="y", door_side=-1,
             fire=False):
    """楼宇/站房：墙体 + 门 + 标签（fire=True 时在正面加火焰标记）。"""
    zc = h / 2.0
    wall_mat = mat_amb_diff(tuple(c * 0.6 for c in wall), wall)
    door_pos = f"0 {door_side * (ly / 2 - 0.01)} {0.5}" if door_axis == "y" \
        else f"{door_side * (lx / 2 - 0.01)} 0 {0.5}"
    door = visual("door", box(0.9, 0.05, 2.0), mat_amb_diff((0.15, 0.1, 0.05), (0.25, 0.16, 0.08)),
                  door_pos) if door_axis == "y" else \
        visual("door", box(0.05, 0.9, 2.0), mat_amb_diff((0.15, 0.1, 0.05), (0.25, 0.16, 0.08)),
               door_pos)
    label_pos = f"0 {door_side * (ly / 2 - 0.01)} {h - 0.5}" if door_axis == "y" \
        else f"{door_side * (lx / 2 - 0.01)} 0 {h - 0.5}"
    label = visual("label", box(1.6, 0.05, 0.5), mat_tex(label_tex), label_pos) \
        if door_axis == "y" else \
        visual("label", box(0.05, 1.6, 0.5), mat_tex(label_tex), label_pos)

    fire_vis = ""
    if fire:
        # 火焰挂在正面（门所在面）上部，模拟高处火情
        fz = h - 1.2
        if door_axis == "y":
            fy = door_side * (ly / 2 - 0.01)
            fire_vis = (flames("fire_l", -1.6, fy, fz) +
                        flames("fire_m", 0.0, fy, fz + 0.4) +
                        flames("fire_r", 1.6, fy, fz))
        else:
            fx = door_side * (lx / 2 - 0.01)
            fire_vis = (flames("fire_l", fx, -1.6, fz) +
                        flames("fire_m", fx, 0.0, fz + 0.4) +
                        flames("fire_r", fx, 1.6, fz))

    wall_v = visual("visual", box(lx, ly, h), wall_mat)
    wall_c = collision("collision", box(lx, ly, h))
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} {zc} 0 0 0</pose>
      <link name="link">
{wall_c}
{wall_v}
{door}
{label}
{fire_vis}
      </link>
    </model>
"""


def car(name, x, y, yaw_deg, plate_tex=None, color=(0.55, 0.1, 0.1)):
    """轿车：车体 + 车顶 + 4 轮 + 前后车牌（plate_tex=None 则无牌）。
    车长沿 Y，车牌在 ±Y 端。"""
    body = visual("body", box(1.8, 4.0, 1.0),
                  mat_amb_diff(tuple(c * 0.6 for c in color), color),
                  "0 0 0.55 0 0 0")
    body_c = collision("body_c", box(1.8, 4.0, 1.0), "0 0 0.55 0 0 0")
    roof = visual("roof", box(1.5, 2.0, 0.6),
                  mat_amb_diff(tuple(c * 0.55 for c in color), color),
                  "0 0 1.25 0 0 0")
    wheels = ""
    for wx in (-0.85, 0.85):
        for wy in (-1.25, 1.25):
            wheels += visual(f"wheel_{wx}_{wy}", cyl(0.32, 0.22),
                             mat_amb_diff((0.05, 0.05, 0.05), (0.1, 0.1, 0.1)),
                             f"{wx} {wy} 0.32 1.5708 0 0")
            wheels += collision(f"wheelc_{wx}_{wy}", cyl(0.32, 0.22),
                                f"{wx} {wy} 0.32 1.5708 0 0")
    plates = ""
    if plate_tex:
        plates = (visual("plate_front", box(0.5, 0.02, 0.16), mat_tex(plate_tex),
                         "0 2.0 0.55 0 0 0") +
                  visual("plate_rear", box(0.5, 0.02, 0.16), mat_tex(plate_tex),
                         "0 -2.0 0.55 0 0 0"))
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} 0 0 0 {yaw_deg * 3.14159265 / 180}</pose>
      <link name="link">
{body_c}
{body}
{roof}
{wheels}
{plates}
      </link>
    </model>
"""


def ebike(name, x, y, yaw_deg, color, toppled=False):
    """两轮电动车（车架 + 两轮 + 车把 + 座垫）。toppled=True 时整体侧倒。"""
    roll = "1.5708" if toppled else "0"
    body = visual("body", box(0.4, 1.5, 0.5), mat_amb_diff(tuple(c * 0.6 for c in color), color),
                  "0 0 0.55 0 0 0")
    body_c = collision("body_c", box(0.4, 1.5, 0.5), "0 0 0.55 0 0 0")
    seat = visual("seat", box(0.3, 0.5, 0.15), mat_amb_diff((0.1, 0.1, 0.1), (0.2, 0.2, 0.2)),
                  "0 -0.2 0.85 0 0 0")
    handle = visual("handle", box(0.5, 0.05, 0.05), mat_amb_diff((0.1, 0.1, 0.1), (0.2, 0.2, 0.2)),
                    "0 0.7 0.85 0 0 0")
    wheels = ""
    for wy in (-0.55, 0.55):
        wheels += visual(f"wheel_{wy}", cyl(0.28, 0.06),
                         mat_amb_diff((0.05, 0.05, 0.05), (0.1, 0.1, 0.1)),
                         f"0 {wy} 0.28 1.5708 0 0")
        wheels += collision(f"wheelc_{wy}", cyl(0.28, 0.06),
                            f"0 {wy} 0.28 1.5708 0 0")
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} 0 {roll} 0 {yaw_deg * 3.14159265 / 180}</pose>
      <link name="link">
{body_c}
{body}
{seat}
{handle}
{wheels}
      </link>
    </model>
"""


def trash_bin(name, x, y, color, label_tex, open_lid, contents_color=None):
    """垃圾桶：圆柱桶身 + 桶盖（open_lid=True 掀开）。
    contents_color 非空时，在桶口加一坨垃圾（代表投放内容）。"""
    body_v = visual("body", cyl(0.35, 0.8),
                    mat_amb_diff(tuple(c * 0.6 for c in color), color), "0 0 0.4 0 0 0")
    body_c = collision("body_c", cyl(0.35, 0.8), "0 0 0.4 0 0 0")
    if open_lid:
        lid = visual("lid", cyl(0.36, 0.06), mat_amb_diff((0.2, 0.2, 0.2), (0.35, 0.35, 0.35)),
                     "0 0.25 0.95 0 0 0")
    else:
        lid = visual("lid", cyl(0.36, 0.06), mat_amb_diff((0.2, 0.2, 0.2), (0.35, 0.35, 0.35)),
                     "0 0 0.83 0 0 0")
    content = ""
    if contents_color is not None:
        content = visual("content", sph(0.22),
                         mat_amb_diff(tuple(c * 0.6 for c in contents_color), contents_color),
                         "0 0 0.98 0 0 0")
    label = visual("label", box(0.5, 0.05, 0.3), mat_tex(label_tex), "0 0.36 0.6 0 0 0")
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} 0 0 0 0</pose>
      <link name="link">
{body_c}
{body_v}
{lid}
{content}
{label}
      </link>
    </model>
"""


def standee(name, x, y, vest, label_tex, yaw_deg=0):
    """人偶立牌：立板 + 头 + 身份标签。"""
    board = visual("board", box(0.05, 0.6, 1.6),
                   mat_amb_diff(tuple(c * 0.6 for c in vest), vest), "0 0 0.8 0 0 0")
    board_c = collision("board_c", box(0.05, 0.6, 1.6), "0 0 0.8 0 0 0")
    head = visual("head", sph(0.18), mat_amb_diff((0.9, 0.75, 0.6), (0.96, 0.84, 0.7)),
                  "0 0 1.65 0 0 0")
    label = visual("label", box(0.02, 0.6, 0.3), mat_tex(label_tex), "0 0.03 1.0 0 0 0")
    base = visual("base", box(0.5, 0.5, 0.04), mat_amb_diff((0.3, 0.3, 0.3), (0.4, 0.4, 0.4)),
                  "0 0 0.02 0 0 0")
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} 0 0 0 {yaw_deg * 3.14159265 / 180}</pose>
      <link name="link">
{board_c}
{board}
{head}
{label}
{base}
      </link>
    </model>
"""


def sign(name, x, y, tex, yaw_deg=0):
    """指示牌：杆 + 方形牌面（贴纹理）。"""
    pole = visual("pole", cyl(0.03, 2.4), mat_amb_diff((0.2, 0.2, 0.2), (0.3, 0.3, 0.3)),
                  "0 0 1.2 0 0 0")
    pole_c = collision("pole_c", cyl(0.03, 2.4), "0 0 1.2 0 0 0")
    board = visual("board", box(0.02, 0.7, 0.7), mat_tex(tex), "0 0 1.7 0 0 0")
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} 0 0 0 {yaw_deg * 3.14159265 / 180}</pose>
      <link name="link">
{pole_c}
{pole}
{board}
      </link>
    </model>
"""


def fence_box(name, x, y, lx, ly, h=0.6):
    """封闭方框：4 面矮墙（视觉 + 碰撞），围出 A/B 街区行人区。"""
    w = 0.1
    wall = mat_amb_diff((0.55, 0.55, 0.6), (0.75, 0.75, 0.8))
    top = visual("fence_top", box(lx, w, h), wall, f"0 {ly / 2} {h / 2} 0 0 0")
    bot = visual("fence_bot", box(lx, w, h), wall, f"0 {-ly / 2} {h / 2} 0 0 0")
    left = visual("fence_left", box(w, ly, h), wall, f"{-lx / 2} 0 {h / 2} 0 0 0")
    right = visual("fence_right", box(w, ly, h), wall, f"{lx / 2} 0 {h / 2} 0 0 0")
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} 0 0 0 0</pose>
      <link name="link">
{top}
{bot}
{left}
{right}
      </link>
    </model>
"""


def parking_spot(name, x, y, spot_lx, spot_ly):
    """停车位白线框（4 条白边）。spot_lx 沿 X，spot_ly 沿 Y。"""
    w = 0.08
    white = (0.95, 0.95, 0.95)
    parts = [
        flat_patch(f"{name}_top", x, y + spot_ly / 2, spot_lx, w, white),
        flat_patch(f"{name}_bot", x, y - spot_ly / 2, spot_lx, w, white),
        flat_patch(f"{name}_left", x - spot_lx / 2, y, w, spot_ly, white),
        flat_patch(f"{name}_right", x + spot_lx / 2, y, w, spot_ly, white),
    ]
    return "".join(parts)


# ---------------------------------------------------------------------------
# 车道线 / 停止线 / 斑马线
# ---------------------------------------------------------------------------
WHITE = (0.92, 0.92, 0.92)


def dashes(axis, fixed, lo, hi, step, prefix, skip_centers=()):
    """沿某条路画中心虚线。axis='x' 时沿 X（fixed=y），axis='y' 时沿 Y。
    skip_centers 为需避让的路口中心坐标列表（虚线不画在路口内）。"""
    parts = []
    k = 0
    v = lo
    while v <= hi:
        if any(abs(v - c) < 3.5 for c in skip_centers):  # 路口处跳过
            v += step
            continue
        if axis == "x":
            parts.append(flat_patch(f"{prefix}_{k}", v, fixed, 1.8, 0.12, WHITE))
        else:
            parts.append(flat_patch(f"{prefix}_{k}", fixed, v, 0.12, 1.8, WHITE))
        v += step
        k += 1
    return "".join(parts)


def intersection_markings(cx, cy, prefix):
    """十字路口四周：4 条停止线 + 4 组斑马线。"""
    parts = []
    parts.append(flat_patch(f"stop_{prefix}_n", cx, cy + 3.2, 6, 0.2, WHITE))
    parts.append(flat_patch(f"stop_{prefix}_s", cx, cy - 3.2, 6, 0.2, WHITE))
    parts.append(flat_patch(f"stop_{prefix}_e", cx + 3.2, cy, 0.2, 6, WHITE))
    parts.append(flat_patch(f"stop_{prefix}_w", cx - 3.2, cy, 0.2, 6, WHITE))
    for i in range(6):
        off = -2.7 + i * 0.9
        parts.append(flat_patch(f"zebra_{prefix}_n_{i}", cx + off, cy + 4.4, 0.4, 2.4, WHITE))
        parts.append(flat_patch(f"zebra_{prefix}_s_{i}", cx + off, cy - 4.4, 0.4, 2.4, WHITE))
        parts.append(flat_patch(f"zebra_{prefix}_e_{i}", cx + 4.4, cy + off, 2.4, 0.4, WHITE))
        parts.append(flat_patch(f"zebra_{prefix}_w_{i}", cx - 4.4, cy + off, 2.4, 0.4, WHITE))
    return "".join(parts)


# ---------------------------------------------------------------------------
# 组装世界
# ---------------------------------------------------------------------------
def gen():
    parts = []
    parts.append(f"""<?xml version="1.0" ?>
<!-- 智慧社区仿真世界（由 scripts/generate_world.py 生成，勿手改，改脚本后重跑） -->
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
      <yellow_time>3</yellow_time>
      <red_time>10</red_time>
    </plugin>

    <gravity>0 0 -9.8</gravity>

    <scene>
      <grid>false</grid>
      <ambient>0.4 0.4 0.4 1</ambient>
      <background>0.6 0.72 0.85 1</background>
      <shadows>true</shadows>
    </scene>

    <light name="sun" type="directional">
      <pose>0 0 20 0 0 0</pose>
      <cast_shadows>true</cast_shadows>
      <intensity>1</intensity>
      <direction>-0.4 0.2 -0.9</direction>
      <diffuse>0.9 0.9 0.9 1</diffuse>
      <specular>0.25 0.25 0.25 1</specular>
      <attenuation>
        <range>1000</range>
        <constant>0.9</constant>
        <linear>0.01</linear>
        <quadratic>0.001</quadratic>
      </attenuation>
    </light>

    <!-- ===== 地面（草地） ===== -->
    <model name="ground_plane">
      <static>true</static>
      <link name="link">
        <collision name="collision">
          <geometry><plane><normal>0 0 1</normal><size>120 120</size></plane></geometry>
          <surface><friction><ode><mu>1.0</mu><mu2>1.0</mu2></ode></friction></surface>
        </collision>
        <visual name="visual">
          <geometry><plane><normal>0 0 1</normal><size>120 120</size></plane></geometry>
          <material><ambient>0.35 0.45 0.3 1</ambient><diffuse>0.45 0.58 0.4 1</diffuse><specular>0.05 0.05 0.05 1</specular></material>
        </visual>
      </link>
    </model>
""")

    # ----- 道路（环形闭合 + 中央南北主路） -----
    asphalt = (0.3, 0.3, 0.32)
    parts.append("    <!-- ===== 道路（闭合街区路网） ===== -->\n")
    parts.append(static_box_model("road_top", "0 16 0.01", 88, 6, 0.02, mat_amb_diff(asphalt, asphalt)))
    parts.append(static_box_model("road_bottom", "0 -16 0.01", 88, 6, 0.02, mat_amb_diff(asphalt, asphalt)))
    parts.append(static_box_model("road_left", "-44 0 0.01", 6, 32, 0.02, mat_amb_diff(asphalt, asphalt)))
    parts.append(static_box_model("road_right", "44 0 0.01", 6, 32, 0.02, mat_amb_diff(asphalt, asphalt)))
    parts.append(static_box_model("road_center", "0 0 0.01", 6, 44, 0.02, mat_amb_diff(asphalt, asphalt)))

    # ----- 车道中心虚线 -----
    parts.append("    <!-- ===== 车道中心虚线 ===== -->\n")
    parts.append(dashes("x", 16, -44, 44, 4.0, "dash_top", skip_centers=(0,)))
    parts.append(dashes("x", -16, -44, 44, 4.0, "dash_bottom", skip_centers=(0,)))
    parts.append(dashes("y", 0, -22, 22, 4.0, "dash_center", skip_centers=(-16, 16)))

    # ----- 停止线 + 斑马线（两个红绿灯十字路口） -----
    parts.append("    <!-- ===== 停止线 / 斑马线 ===== -->\n")
    parts.append(intersection_markings(0, 16, "upper"))
    parts.append(intersection_markings(0, -16, "lower"))

    # ----- 红绿灯（2 组：上方竖向 / 下方横向） -----
    parts.append("    <!-- ===== 红绿灯 ===== -->\n")
    parts.append(traffic_light("traffic_light_1", 0, 19.5, 180, horizontal=False))
    parts.append(traffic_light("traffic_light_2", 0, -19.5, 0, horizontal=True))

    # ----- A街区（上方行人区，封闭方框，5 人） -----
    parts.append("    <!-- ===== A街区（上方行人区） ===== -->\n")
    parts.append(fence_box("block_a_fence", -18, 6.5, 24, 11))
    parts.append(static_box_model("block_a_label", "-18 13 1.0", 3.0, 0.02, 0.5,
                                  mat_tex(f"{T}/label_street_a.png"), collide=False))
    parts.append(standee("person_a1", -22, 5.0, (0.1, 0.55, 0.3), f"{T}/label_community.png", 0))
    parts.append(standee("person_a2", -18, 8.0, (0.1, 0.55, 0.3), f"{T}/label_community.png", 90))
    parts.append(standee("person_a3", -14, 5.0, (0.1, 0.55, 0.3), f"{T}/label_community.png", 180))
    parts.append(standee("person_a4", -24, 9.0, (0.85, 0.4, 0.15), f"{T}/label_visitor.png", 0))
    parts.append(standee("person_a5", -12, 9.0, (0.85, 0.4, 0.15), f"{T}/label_visitor.png", 0))

    # ----- B街区（下方行人区，封闭方框，5 人 + 左上角指示牌） -----
    parts.append("    <!-- ===== B街区（下方行人区） ===== -->\n")
    parts.append(fence_box("block_b_fence", -17, -6, 24, 11))
    parts.append(static_box_model("block_b_label", "-17 -13.5 1.0", 3.0, 0.02, 0.5,
                                  mat_tex(f"{T}/label_street_b.png"), collide=False))
    parts.append(sign("sign_no_straight", -26, -1.5, f"{T}/sign_no_straight.png", 0))
    parts.append(standee("person_b1", -20, -5.0, (0.1, 0.55, 0.3), f"{T}/label_community.png", 0))
    parts.append(standee("person_b2", -16, -8.0, (0.1, 0.55, 0.3), f"{T}/label_community.png", 90))
    parts.append(standee("person_b3", -12, -5.0, (0.1, 0.55, 0.3), f"{T}/label_community.png", 180))
    parts.append(standee("person_b4", -23, -8.0, (0.1, 0.55, 0.3), f"{T}/label_community.png", 0))
    parts.append(standee("person_b5", -10, -8.0, (0.1, 0.55, 0.3), f"{T}/label_community.png", 0))

    # ----- 楼宇 A/B/C（纵向并排，右侧）+ 楼宇 D + 站房 -----
    parts.append("    <!-- ===== 楼宇 ===== -->\n")
    parts.append(building("building_a", 16, 11, 6, 5, 8, (0.78, 0.72, 0.6),
                          f"{T}/label_building_a.png", "y", -1, fire=True))
    parts.append(building("building_b", 16, 2, 6, 5, 8, (0.68, 0.74, 0.8),
                          f"{T}/label_building_b.png", "y", -1))
    parts.append(building("building_c", 16, -7, 6, 5, 8, (0.8, 0.74, 0.66),
                          f"{T}/label_building_c.png", "y", -1))
    parts.append(building("building_d", -41, -13, 5, 4, 7, (0.74, 0.7, 0.76),
                          f"{T}/label_building_d.png", "x", 1))
    parts.append(building("station_room", -32, -13, 4, 3, 3, (0.82, 0.8, 0.78),
                          f"{T}/label_station.png", "x", 1))

    # ----- 站房仪表（压力表 + 温度表） -----
    parts.append("    <!-- ===== 站房仪表 ===== -->\n")
    parts.append(static_box_model("meter_pressure", "-29.9 -13 1.3", 0.02, 0.5, 0.5,
                                  mat_tex(f"{T}/meter_pressure.png"), collide=False))
    parts.append(static_box_model("meter_temp", "-29.9 -12.2 1.3", 0.02, 0.5, 0.5,
                                  mat_tex(f"{T}/meter_temp.png"), collide=False))

    # ----- 右侧停车场（P 标识，3 个车位各停一辆蓝牌车） -----
    parts.append("    <!-- ===== 右侧停车场（3 车位） ===== -->\n")
    parts.append(static_box_model("parking_label", "30 14 1.0", 3.6, 0.02, 0.5,
                                  mat_tex(f"{T}/label_parking.png"), collide=False))
    for i, (spot, ly) in enumerate([("1", 11), ("2", 6), ("3", 1)]):
        parts.append(parking_spot(f"spot_{spot}", 30, ly, 4.8, 2.6))
        parts.append(static_box_model(f"spot_label_{spot}", f"30 {ly + 1.5} 0.03",
                                      0.6, 0.02, 0.3, mat_tex(f"{T}/label_spot_{spot}.png"),
                                      collide=False))
    parts.append(car("car_a", 30, 11, 0, f"{T}/plate_A.png", (0.55, 0.1, 0.1)))
    parts.append(car("car_b", 30, 6, 0, f"{T}/plate_B.png", (0.55, 0.1, 0.1)))
    parts.append(car("car_c", 30, 1, 0, f"{T}/plate_C.png", (0.55, 0.1, 0.1)))

    # ----- 楼宇 C 下方路边横向停放 4 台小车（粉/青/灰/黄） -----
    parts.append("    <!-- ===== 楼宇C下方路边 4 台车 ===== -->\n")
    parts.append(car("car_side_pink", 20, -13, 90, None, (0.9, 0.5, 0.6)))
    parts.append(car("car_side_cyan", 24.5, -13, 90, None, (0.3, 0.7, 0.75)))
    parts.append(car("car_side_grey", 29, -13, 90, None, (0.5, 0.5, 0.52)))
    parts.append(car("car_side_yellow", 33.5, -13, 90, None, (0.9, 0.75, 0.2)))

    # ----- 两轮电动车：A街区违停 2 + 停车区正常 8 + 倒伏 2 -----
    parts.append("    <!-- ===== 两轮电动车（违停/正常/倒伏） ===== -->\n")
    parts.append(static_box_model("ev_label", "-14 -12 1.0", 4.6, 0.02, 0.5,
                                  mat_tex(f"{T}/label_ev.png"), collide=False))
    # A街区违停 2 辆（街区东侧人行道）
    parts.append(ebike("ebike_illegal_1", -4, 6.0, 20, (0.8, 0.6, 0.1)))
    parts.append(ebike("ebike_illegal_2", -4, 8.0, -15, (0.8, 0.6, 0.1)))
    # 停车区 8 辆正常 + 2 辆倒伏
    normal_x = [-26, -23, -20, -17, -14, -11, -8, -5]
    colors = [(0.2, 0.5, 0.7), (0.7, 0.3, 0.2), (0.3, 0.6, 0.4), (0.6, 0.4, 0.2),
              (0.5, 0.3, 0.6), (0.2, 0.6, 0.6), (0.7, 0.6, 0.2), (0.4, 0.4, 0.5)]
    for k, xp in enumerate(normal_x):
        parts.append(ebike(f"ebike_normal_{k + 1}", xp, -13.5, 0, colors[k % len(colors)]))
    parts.append(ebike("ebike_toppled_1", -2, -13.5, 0, (0.55, 0.3, 0.55), toppled=True))
    parts.append(ebike("ebike_toppled_2", 0.5, -13.5, 0, (0.5, 0.35, 0.5), toppled=True))

    # ----- 垃圾分类投放点（4 桶 2 开 2 闭，含正确/错误投放样本） -----
    parts.append("    <!-- ===== 垃圾分类投放点 ===== -->\n")
    parts.append(static_box_model("trash_label", "10.5 -12 1.0", 4.6, 0.02, 0.5,
                                  mat_tex(f"{T}/label_trash.png"), collide=False))
    parts.append(trash_bin("trash_recyclable", 6, -13.5, (0.1, 0.4, 0.7),
                           f"{T}/trash_recyclable.png", open_lid=True, contents_color=(0.1, 0.7, 0.4)))
    parts.append(trash_bin("trash_other", 9, -13.5, (0.35, 0.35, 0.35),
                           f"{T}/trash_other.png", open_lid=False))
    parts.append(trash_bin("trash_hazardous", 12, -13.5, (0.7, 0.15, 0.15),
                           f"{T}/trash_hazardous.png", open_lid=True, contents_color=(0.1, 0.4, 0.7)))
    parts.append(trash_bin("trash_kitchen", 15, -13.5, (0.1, 0.5, 0.2),
                           f"{T}/trash_kitchen.png", open_lid=False))

    # ----- 指示牌（禁止停车 / 限速） -----
    parts.append("    <!-- ===== 指示牌 ===== -->\n")
    parts.append(sign("sign_no_parking", 13, 4.5, f"{T}/sign_no_parking.png", 0))
    parts.append(sign("sign_speed", -6, 22, f"{T}/sign_speed.png", 180))

    # ----- 起点/终点（右上角，合并） -----
    parts.append("    <!-- ===== 起点/终点（右上角） ===== -->\n")
    parts.append(flat_patch("start_pad", 32, 14, 4.0, 3.0, (0.5, 0.85, 0.5)))
    parts.append(static_box_model("start_label", "32 14 1.0", 3.0, 0.02, 0.5,
                                  mat_tex(f"{T}/label_start.png"), collide=False))

    parts.append("""
  </world>
</sdf>
""")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    print("生成完成 ->", OUT)


if __name__ == "__main__":
    gen()
