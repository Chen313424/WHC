#!/usr/bin/env python3
"""生成 worlds/smart_community.sdf（智慧社区完整场景）。

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


# ---------------------------------------------------------------------------
# 复合模型
# ---------------------------------------------------------------------------
def traffic_light(name, x, y, z=0.0):
    """红绿灯：杆 + 红/黄/绿三盏灯（visual 名称为 red/yellow/green）。"""
    pole = cyl(0.04, 2.6)
    pole_vis = visual("pole", pole, mat_amb_diff((0.2, 0.2, 0.2), (0.3, 0.3, 0.3)),
                      "0 0 1.3 0 0 0")
    pole_col = collision("pole_col", pole, "0 0 1.3 0 0 0")
    # 灯罩背板
    housing = visual("housing", box(0.24, 0.16, 0.62),
                     mat_amb_diff((0.1, 0.1, 0.1), (0.15, 0.15, 0.15)),
                     "0 0 2.0 0 0 0")
    red = visual("red", sph(0.09), mat_emissive(0.05, 0.05, 0.05), "0 0 2.3 0 0 0")
    yellow = visual("yellow", sph(0.09), mat_emissive(0.05, 0.05, 0.05), "0 0 2.0 0 0 0")
    green = visual("green", sph(0.09), mat_emissive(0.05, 0.05, 0.05), "0 0 1.7 0 0 0")
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} {z} 0 0 0</pose>
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


def building(name, x, y, lx, ly, h, wall, label_tex, door_axis="y", door_side=-1):
    """楼宇/站房：墙体 + 门 + 标签。door_axis 为门所在面轴向。"""
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
      </link>
    </model>
"""


def car(name, x, y, yaw_deg, plate_tex):
    """轿车：车体 + 车顶 + 4 轮 + 前后车牌。车长沿 Y，车牌在 ±Y 端。"""
    body = visual("body", box(1.8, 4.0, 1.0), mat_amb_diff((0.25, 0.25, 0.3), (0.55, 0.1, 0.1)),
                  "0 0 0.55 0 0 0")
    body_c = collision("body_c", box(1.8, 4.0, 1.0), "0 0 0.55 0 0 0")
    roof = visual("roof", box(1.5, 2.0, 0.6), mat_amb_diff((0.2, 0.2, 0.24), (0.5, 0.09, 0.09)),
                  "0 0 1.25 0 0 0")
    wheels = ""
    for wx in (-0.85, 0.85):
        for wy in (-1.25, 1.25):
            wheels += visual(f"wheel_{wx}_{wy}", cyl(0.32, 0.22),
                             mat_amb_diff((0.05, 0.05, 0.05), (0.1, 0.1, 0.1)),
                             f"{wx} {wy} 0.32 1.5708 0 0")
            wheels += collision(f"wheelc_{wx}_{wy}", cyl(0.32, 0.22),
                                f"{wx} {wy} 0.32 1.5708 0 0")
    plate_f = visual("plate_front", box(0.5, 0.02, 0.16), mat_tex(plate_tex),
                     "0 2.0 0.55 0 0 0")
    plate_r = visual("plate_rear", box(0.5, 0.02, 0.16), mat_tex(plate_tex),
                     "0 -2.0 0.55 0 0 0")
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} 0 0 0 {yaw_deg * 3.14159265 / 180}</pose>
      <link name="link">
{body_c}
{body}
{roof}
{wheels}
{plate_f}
{plate_r}
      </link>
    </model>
"""


def ebike(name, x, y, yaw_deg, color):
    """两轮电动车（简化为车架 + 两轮 + 车把）。"""
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
      <pose>{x} {y} 0 0 0 {yaw_deg * 3.14159265 / 180}</pose>
      <link name="link">
{body_c}
{body}
{seat}
{handle}
{wheels}
      </link>
    </model>
"""


def trash_bin(name, x, y, color, label_tex, open_lid):
    """垃圾桶：圆柱桶身 + 桶盖（open_lid=True 时盖子掀开）。"""
    body_v = visual("body", cyl(0.35, 0.8),
                    mat_amb_diff(tuple(c * 0.6 for c in color), color), "0 0 0.4 0 0 0")
    body_c = collision("body_c", cyl(0.35, 0.8), "0 0 0.4 0 0 0")
    if open_lid:
        lid = visual("lid", cyl(0.36, 0.06), mat_amb_diff((0.2, 0.2, 0.2), (0.35, 0.35, 0.35)),
                     "0 0.25 0.95 0 0 0")
    else:
        lid = visual("lid", cyl(0.36, 0.06), mat_amb_diff((0.2, 0.2, 0.2), (0.35, 0.35, 0.35)),
                     "0 0 0.83 0 0 0")
    label = visual("label", box(0.5, 0.05, 0.3), mat_tex(label_tex), "0 0.36 0.6 0 0 0")
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x} {y} 0 0 0 0</pose>
      <link name="link">
{body_c}
{body_v}
{lid}
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
      <ns_prefix>traffic_light_ns</ns_prefix>
      <ew_prefix>traffic_light_ew</ew_prefix>
      <green_time>15</green_time>
      <yellow_time>3</yellow_time>
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
          <geometry><plane><normal>0 0 1</normal><size>100 100</size></plane></geometry>
          <surface><friction><ode><mu>1.0</mu><mu2>1.0</mu2></ode></friction></surface>
        </collision>
        <visual name="visual">
          <geometry><plane><normal>0 0 1</normal><size>100 100</size></plane></geometry>
          <material><ambient>0.35 0.45 0.3 1</ambient><diffuse>0.45 0.58 0.4 1</diffuse><specular>0.05 0.05 0.05 1</specular></material>
        </visual>
      </link>
    </model>
""")

    # ----- 道路（沥青，十字交叉，无重叠） -----
    asphalt = (0.3, 0.3, 0.32)
    parts.append("    <!-- ===== 道路 ===== -->\n")
    parts.append(static_box_model("road_intersection", "0 0 0.01", 6, 6, 0.02,
                                  mat_amb_diff(asphalt, asphalt)))
    parts.append(static_box_model("road_ew_west", "-14 0 0.01", 22, 6, 0.02,
                                  mat_amb_diff(asphalt, asphalt)))
    parts.append(static_box_model("road_ew_east", "14 0 0.01", 22, 6, 0.02,
                                  mat_amb_diff(asphalt, asphalt)))
    parts.append(static_box_model("road_ns_north", "0 11.5 0.01", 6, 34, 0.02,
                                  mat_amb_diff(asphalt, asphalt)))
    parts.append(static_box_model("road_ns_south", "0 -11.5 0.01", 6, 34, 0.02,
                                  mat_amb_diff(asphalt, asphalt)))

    # ----- 车道中心虚线 -----
    white = (0.92, 0.92, 0.92)
    parts.append("    <!-- ===== 车道中心虚线 ===== -->\n")
    k = 0
    x = -24.0
    while x <= 24.0:
        parts.append(flat_patch(f"dash_ew_{k}", x, 0, 1.8, 0.12, white))
        x += 4.0
        k += 1
    k = 0
    y = -19.0
    while y <= 19.0:
        parts.append(flat_patch(f"dash_ns_{k}", 0, y, 0.12, 1.8, white))
        y += 4.0
        k += 1

    # ----- 停止线 -----
    parts.append("    <!-- ===== 停止线 ===== -->\n")
    parts.append(flat_patch("stop_west", -4.4, 0, 0.2, 6, white))
    parts.append(flat_patch("stop_east", 4.4, 0, 0.2, 6, white))
    parts.append(flat_patch("stop_north", 0, 4.4, 6, 0.2, white))
    parts.append(flat_patch("stop_south", 0, -4.4, 6, 0.2, white))

    # ----- 斑马线 -----
    parts.append("    <!-- ===== 斑马线 ===== -->\n")
    for cx, cname in ((-5.6, "ew_w"), (5.6, "ew_e")):
        for i in range(6):
            parts.append(flat_patch(f"zebra_{cname}_{i}", cx, -2.4 + i * 0.9, 2.4, 0.4, white))
    for cy, cname in ((-5.6, "ns_s"), (5.6, "ns_n")):
        for i in range(6):
            parts.append(flat_patch(f"zebra_{cname}_{i}", -2.4 + i * 0.9, cy, 0.4, 2.4, white))

    # ----- 红绿灯 -----
    parts.append("    <!-- ===== 红绿灯 ===== -->\n")
    parts.append(traffic_light("traffic_light_ns_north", 0, 3.4))
    parts.append(traffic_light("traffic_light_ns_south", 0, -3.4))
    parts.append(traffic_light("traffic_light_ew_east", 3.4, 0))
    parts.append(traffic_light("traffic_light_ew_west", -3.4, 0))

    # ----- 楼宇 A/B/C/D -----
    parts.append("    <!-- ===== 楼宇 ===== -->\n")
    parts.append(building("building_a", -15, 13, 8, 6, 8, (0.78, 0.72, 0.6),
                          f"{T}/label_building_a.png", "y", -1))
    parts.append(building("building_b", 15, 13, 8, 6, 8, (0.68, 0.74, 0.8),
                          f"{T}/label_building_b.png", "y", -1))
    parts.append(building("building_c", -15, -13, 8, 6, 8, (0.8, 0.74, 0.66),
                          f"{T}/label_building_c.png", "y", 1))
    parts.append(building("building_d", 15, -13, 8, 6, 8, (0.74, 0.7, 0.76),
                          f"{T}/label_building_d.png", "y", 1))

    # ----- 站房（含仪表） -----
    parts.append("    <!-- ===== 站房 + 仪表 ===== -->\n")
    parts.append(building("station_room", 10, -9, 4, 3, 3, (0.82, 0.8, 0.78),
                          f"{T}/label_station.png", "x", -1))
    parts.append(static_box_model("meter_pressure", "7.9 -9 1.3", 0.02, 0.5, 0.5,
                                  mat_tex(f"{T}/meter_pressure.png"), collide=False))
    parts.append(static_box_model("meter_temp", "7.9 -8.3 1.3", 0.02, 0.5, 0.5,
                                  mat_tex(f"{T}/meter_temp.png"), collide=False))

    # ----- 停车场（3 辆蓝牌车） -----
    parts.append("    <!-- ===== 停车场 ===== -->\n")
    parts.append(flat_patch("parking_label_pad", -11, 9.6, 4.5, 0.5, (0.2, 0.2, 0.2)))
    parts.append(static_box_model("parking_label", "-11 9.6 1.0", 4.4, 0.02, 0.4,
                                  mat_tex(f"{T}/label_parking.png"), collide=False))
    parts.append(car("car_a", -16, 7, 0, f"{T}/plate_A.png"))
    parts.append(car("car_b", -11, 7, 0, f"{T}/plate_B.png"))
    parts.append(car("car_c", -6, 7, 0, f"{T}/plate_C.png"))

    # ----- 电动车充电区（绿牌新能源车 + 充电桩） -----
    parts.append("    <!-- ===== 新能源充电区 ===== -->\n")
    parts.append(static_box_model("ev_label", "12 9.8 1.0", 4.4, 0.02, 0.4,
                                  mat_tex(f"{T}/label_ev.png"), collide=False))
    parts.append(car("car_ev", 12, 7, 0, f"{T}/plate_ev.png"))
    parts.append(static_box_model("charger_pile", "12 8.6 0.6", 0.3, 0.5, 1.2,
                                  mat_amb_diff((0.2, 0.5, 0.3), (0.3, 0.7, 0.4))))

    # ----- 电动车停车区（两轮，含违停 + 倒伏） -----
    parts.append("    <!-- ===== 两轮电动车（违停 / 倒伏） ===== -->\n")
    parts.append(ebike("ebike_parked_1", 14.5, -6, 0, (0.2, 0.5, 0.7)))
    parts.append(ebike("ebike_parked_2", 15.6, -6, 0, (0.7, 0.3, 0.2)))
    parts.append(ebike("ebike_illegal", 2.2, -2.2, 35, (0.8, 0.6, 0.1)))
    # 倒伏：绕 X 轴翻滚 90°，横躺在地
    parts.append(f"""    <model name="ebike_toppled">
      <static>true</static>
      <pose>16.5 -6 0 1.5708 0 0</pose>
      <link name="link">
{visual("body", box(0.4, 1.5, 0.5), mat_amb_diff((0.3, 0.18, 0.3), (0.55, 0.3, 0.55)), "0 0 0.3 0 0 0")}
{collision("body_c", box(0.4, 1.5, 0.5), "0 0 0.3 0 0 0")}
{visual("w1", cyl(0.28, 0.06), mat_amb_diff((0.05, 0.05, 0.05), (0.1, 0.1, 0.1)), "0 -0.55 0.3 0 0 0")}
{visual("w2", cyl(0.28, 0.06), mat_amb_diff((0.05, 0.05, 0.05), (0.1, 0.1, 0.1)), "0 0.55 0.3 0 0 0")}
      </link>
    </model>
""")

    # ----- 垃圾桶（4 分类，开/闭） -----
    parts.append("    <!-- ===== 垃圾分类投放点 ===== -->\n")
    parts.append(static_box_model("trash_label", "8 -4.4 1.0", 4.6, 0.02, 0.4,
                                  mat_tex(f"{T}/label_trash.png"), collide=False))
    parts.append(trash_bin("trash_recyclable", 6.5, -6, (0.1, 0.4, 0.7),
                           f"{T}/trash_recyclable.png", open_lid=True))
    parts.append(trash_bin("trash_other", 7.5, -6, (0.35, 0.35, 0.35),
                           f"{T}/trash_other.png", open_lid=False))
    parts.append(trash_bin("trash_hazardous", 8.5, -6, (0.7, 0.15, 0.15),
                           f"{T}/trash_hazardous.png", open_lid=True))
    parts.append(trash_bin("trash_kitchen", 9.5, -6, (0.1, 0.5, 0.2),
                           f"{T}/trash_kitchen.png", open_lid=False))

    # ----- 人偶立牌 -----
    parts.append("    <!-- ===== 人偶立牌 ===== -->\n")
    parts.append(standee("person_community_1", -15, 9.5, (0.1, 0.55, 0.3),
                         f"{T}/label_community.png", 0))
    parts.append(standee("person_community_2", -3, -2.5, (0.1, 0.55, 0.3),
                         f"{T}/label_community.png", 90))
    parts.append(standee("person_community_3", 8, 2.5, (0.1, 0.55, 0.3),
                         f"{T}/label_community.png", 0))
    parts.append(standee("person_community_4", 10, -10.5, (0.1, 0.55, 0.3),
                         f"{T}/label_community.png", 90))
    parts.append(standee("person_visitor_1", -12, 3.5, (0.85, 0.4, 0.15),
                         f"{T}/label_visitor.png", 0))
    parts.append(standee("person_visitor_2", 4, 8, (0.85, 0.4, 0.15),
                         f"{T}/label_visitor.png", 0))

    # ----- 指示牌 -----
    parts.append("    <!-- ===== 指示牌 ===== -->\n")
    parts.append(sign("sign_no_straight", 1.5, 8, f"{T}/sign_no_straight.png", 0))
    parts.append(sign("sign_speed", -22, 1.8, f"{T}/sign_speed.png", 90))
    parts.append(sign("sign_no_parking", 13, 4.5, f"{T}/sign_no_parking.png", 0))

    # ----- 出发区 -----
    parts.append("    <!-- ===== 出发区 ===== -->\n")
    parts.append(flat_patch("start_pad", 0, -1.2, 2.0, 1.6, (0.5, 0.85, 0.5)))
    parts.append(static_box_model("start_label", "0 -2.5 1.0", 1.8, 0.02, 0.4,
                                  mat_tex(f"{T}/label_start.png"), collide=False))

    parts.append("""
  </world>
</sdf>
""")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    print("生成完成 ->", OUT)
    print("模型总数（含子模型/标记）约", len(parts), "段")


if __name__ == "__main__":
    gen()
