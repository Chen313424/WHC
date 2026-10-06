#!/usr/bin/env bash
# =============================================================================
#  在 guest 里补装导航组需要的 Nav2 / SLAM 依赖
#
#  背景：我们最初的 provision 只装了 ros-jazzy-desktop + ros-gz 系列，
#        desktop 里【不含】Nav2 与 slam_toolbox。导航组合并进来之后，
#        community_nav 依赖：
#            nav2_bringup / nav2_map_server / nav2_amcl / nav2_bt_navigator
#            nav2_smac_planner / nav2_regulated_pure_pursuit_controller
#            slam_toolbox / rviz2
#        community_patrol 依赖 nav2_msgs。
#
#  安装 ros-jazzy-navigation2 会一次带齐 nav2_msgs 与全部 nav2_* 服务端；
#  rviz2 已随 desktop 一起装上，这里只做校验不重复装。
#
#  用法（guest 内，可 sudo 免密）：
#      bash install_nav2_deps.sh
#  幂等：已装的包会跳过。
# =============================================================================
set +u
set -o pipefail

DISTRO=jazzy

PKGS=(
  "ros-${DISTRO}-navigation2"
  "ros-${DISTRO}-nav2-bringup"
  "ros-${DISTRO}-nav2-smac-planner"
  "ros-${DISTRO}-nav2-regulated-pure-pursuit-controller"
  "ros-${DISTRO}-nav2-msgs"
  "ros-${DISTRO}-slam-toolbox"
)

echo "=============================================================="
echo "  安装 Nav2 / SLAM 依赖 (ROS 2 ${DISTRO})"
echo "=============================================================="

echo
echo "--- 1. 已装情况 ---"
missing=()
for p in "${PKGS[@]}"; do
  if dpkg -s "$p" >/dev/null 2>&1; then
    v=$(dpkg-query -W -f='${Version}' "$p" 2>/dev/null)
    printf '  [ok]   %-52s %s\n' "$p" "$v"
  else
    printf '  [miss] %-52s\n' "$p"
    missing+=("$p")
  fi
done

if [ ${#missing[@]} -eq 0 ]; then
  echo
  echo "全部已安装，无需操作。"
else
  echo
  echo "--- 2. apt-get update ---"
  sudo apt-get update -qq || { echo "  [X] apt-get update 失败"; exit 1; }

  echo
  echo "--- 3. 安装 ${#missing[@]} 个缺失包（约 1GB，耐心等待）---"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       "${missing[@]}"
  rc=$?
  echo "  apt 退出码: $rc"
  if [ $rc -ne 0 ]; then
    echo "  [X] 安装失败"
    exit $rc
  fi
fi

echo
echo "--- 4. 校验 share 目录 ---"
ROS_SHARE="/opt/ros/${DISTRO}/share"
for d in nav2_bringup nav2_map_server nav2_amcl nav2_bt_navigator \
         nav2_smac_planner nav2_regulated_pure_pursuit_controller \
         nav2_msgs slam_toolbox rviz2; do
  if [ -d "$ROS_SHARE/$d" ]; then
    echo "  [ok]   $d"
  else
    echo "  [X]    缺少 $d"
  fi
done

echo
echo "--- 5. 依赖是否可解析（rosdep 查不到的不算，只看 launch 能否找到包）---"
set +u
# shellcheck disable=SC1091
source "/opt/ros/${DISTRO}/setup.bash"
for p in nav2_bringup slam_toolbox rviz2; do
  p=$(echo "$p" | tr -d '\r')
  if ros2 pkg prefix "$p" >/dev/null 2>&1; then
    echo "  [ok]   ros2 pkg prefix $p -> $(ros2 pkg prefix "$p" 2>/dev/null)"
  else
    echo "  [X]    ros2 找不到包 $p"
  fi
done

echo
echo "完成。"
