#!/usr/bin/env bash
# =============================================================================
#  修改 Gazebo GUI 的初始相机位姿，让它开机就正对小车
#
#  gui.config 是 Gazebo 自己维护的文件，**退出时会回写**，所以必须在
#  仿真停止的状态下修改，否则改动会被覆盖。
#
#  camera_pose 格式: "x y z roll pitch yaw"
#  相机沿自身 +X 方向看；yaw 绕 Z 轴。
#
#  ★ 2026-10 更新：建模组把场景从 88x44m 的城市场地重建成了 4.2x4.2m 的
#    省赛场地（x,y 约在 [-2.1, 2.1]，小车 spawn 在 (1.3, 1.3)）。
#    旧的默认机位 "32 4 5 0 0.4 1.5708" 是配合 (32,14) 的旧 spawn 调的，
#    在新场景下相机离场地 28m 远、什么也看不到 —— 表现为
#    **Gazebo GUI 的 3D 视口一片全黑**（而 Entity Tree 里模型都在），
#    很容易误判成"显卡/3D 加速坏了"。
#    实测就是这个原因，改成下面的俯视机位后画面正常。
#
#  当前默认：相机在场地正上方 5.5m，pitch=-90° 垂直俯视，一次看全 4.2m 场地。
#  想要立体视角可以用例如 "0 -5 4 0 -0.6 1.5708"（从南侧斜看全场）。
# =============================================================================
set +u

CFG="$HOME/.gz/sim/8/gui.config"
POSE="${1:-0 0 5.5 0 -1.5708 0}"

if [ ! -f "$CFG" ]; then
  echo "[X] 找不到 $CFG（先跑一次 Gazebo 让它生成）"
  exit 1
fi

# 停仿真（避免退出时回写覆盖我们的修改）
if systemctl --user is-active whc-sim.service >/dev/null 2>&1; then
  echo "停止 whc-sim.service ..."
  systemctl --user stop whc-sim.service
  sleep 4
fi

[ -f "$CFG.bak" ] || cp "$CFG" "$CFG.bak"
echo "已备份: $CFG.bak"

if grep -q '<camera_pose>' "$CFG"; then
  sed -i "s|<camera_pose>[^<]*</camera_pose>|<camera_pose>${POSE}</camera_pose>|" "$CFG"
  echo "已更新 camera_pose"
else
  echo "[!] 文件里没有 <camera_pose> 标签，无法修改"
  exit 1
fi

echo "当前值:"
grep -n 'camera_pose' "$CFG" | sed 's/^/  /'
