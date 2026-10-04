#!/usr/bin/env bash
# =============================================================================
#  修改 Gazebo GUI 的初始相机位姿，让它开机就正对小车
#
#  gui.config 是 Gazebo 自己维护的文件，**退出时会回写**，所以必须在
#  仿真停止的状态下修改，否则改动会被覆盖。
#
#  camera_pose 格式: "x y z roll pitch yaw"
#  相机沿自身 +X 方向看；yaw 绕 Z 轴。默认值 "-6 0 6 0 0.5 0" 就是
#  从 x=-6 朝 +X（原点）看，pitch=0.5 俯视。
#
#  小车 spawn 在 (32, 14)。这里把相机放到 (32, 4, 5)，yaw=90° 朝 +Y，
#  于是正对小车，距离 10m、高 5m、略俯视。
# =============================================================================
set +u

CFG="$HOME/.gz/sim/8/gui.config"
POSE="${1:-32 4 5 0 0.4 1.5708}"

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
