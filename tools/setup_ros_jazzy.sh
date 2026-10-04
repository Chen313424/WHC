#!/usr/bin/env bash
# =============================================================================
#  在 WSL2 的 Ubuntu 24.04 里安装 ROS 2 Jazzy + Gazebo Harmonic，并编译本项目
#
#  用法（在 Windows 侧执行）：
#      wsl -d Ubuntu-24.04 -- bash -lc 'bash /mnt/d/ai-sfjs/tools/setup_ros_jazzy.sh'
#
#  为什么脚本里全是国内镜像
#  ------------------------
#  本机网络对 github.com / raw.githubusercontent.com 是阻断的，而 ROS 2 官方
#  的 apt 源密钥和历史文档都托管在那里。所以这里：
#    * apt 包走清华 TUNA 镜像（已验证 /ros2/ubuntu/ 存在 noble 分支）
#    * 密钥按「多个来源依次尝试」，任一成功即可，不依赖 github 一定可达
#
#  幂等：可以重复运行，已安装的步骤会跳过。
# =============================================================================
set -euo pipefail

ROS_DISTRO_NAME="jazzy"
UBUNTU_CODENAME="noble"

# 注意：脚本常以 `sudo bash setup_ros_jazzy.sh` 运行，此时 $HOME 是 /root，
# 工程会被建到 /root/smart_community_ws，普通用户随后无权使用。
# 这里显式取调用者（SUDO_USER）的真实家目录。
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
  REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
  REAL_USER="$SUDO_USER"
else
  REAL_HOME="$HOME"
  REAL_USER="$(id -un)"
fi
WS_DIR="${REAL_HOME}/smart_community_ws"

# 源码目录自动探测，两种运行环境都支持：
#   * WSL2      -> Windows 盘挂载在 /mnt/d
#   * VirtualBox-> 共享文件夹挂载在 /media/sf_WHC（需 Guest Additions）
HOST_SRC="${1:-}"
if [ -z "$HOST_SRC" ]; then
  for cand in /mnt/d/ai-sfjs/src /media/sf_WHC/src "$HOME/whc_setup/src"; do
    [ -d "$cand" ] && HOST_SRC="$cand" && break
  done
fi

TUNA_UBUNTU="https://mirrors.tuna.tsinghua.edu.cn/ubuntu"
TUNA_ROS2="https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu"
ROS_KEY_FPR="C1CF6E31E6BADE8868B172B4F42ED6FBAB17C654"

c_info()  { printf '\n\033[36m=== %s ===\033[0m\n' "$*"; }
c_ok()    { printf '  \033[32m[OK]\033[0m %s\n' "$*"; }
c_warn()  { printf '  \033[33m[!]\033[0m %s\n' "$*"; }
c_err()   { printf '  \033[31m[X]\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------- 0. 前置检查
c_info "0. 环境检查"
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi
if [ -r /etc/os-release ]; then
  . /etc/os-release
  echo "  发行版: ${PRETTY_NAME:-unknown}"
  if [ "${VERSION_CODENAME:-}" != "$UBUNTU_CODENAME" ]; then
    c_err "需要 Ubuntu ${UBUNTU_CODENAME} (24.04)，当前是 ${VERSION_CODENAME:-unknown}。"
    c_err "ROS 2 Jazzy 只支持 24.04；22.04 请改用 Humble。"
    exit 1
  fi
  c_ok "Ubuntu 版本正确"
fi
echo "  使用 sudo: ${SUDO:-（已是 root）}"

# ---------------------------------------------------------------- 1. Ubuntu 源
c_info "1. 切换 Ubuntu apt 源到清华镜像（提速）"
if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then
  if ! grep -q "tuna.tsinghua" /etc/apt/sources.list.d/ubuntu.sources; then
    $SUDO cp /etc/apt/sources.list.d/ubuntu.sources \
             /etc/apt/sources.list.d/ubuntu.sources.bak
    $SUDO sed -i \
      -e "s|http://archive.ubuntu.com/ubuntu|${TUNA_UBUNTU}|g" \
      -e "s|http://security.ubuntu.com/ubuntu|${TUNA_UBUNTU}|g" \
      -e "s|https://archive.ubuntu.com/ubuntu|${TUNA_UBUNTU}|g" \
      -e "s|https://security.ubuntu.com/ubuntu|${TUNA_UBUNTU}|g" \
      /etc/apt/sources.list.d/ubuntu.sources
    c_ok "已切换（备份在 ubuntu.sources.bak）"
  else
    c_ok "已是清华源"
  fi
else
  c_warn "未找到 ubuntu.sources，跳过"
fi

c_info "2. 更新索引并安装基础工具"
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq \
  curl gnupg lsb-release ca-certificates locales \
  software-properties-common apt-transport-https
$SUDO locale-gen en_US en_US.UTF-8 >/dev/null 2>&1 || true
c_ok "基础工具就绪"

# ---------------------------------------------------------------- 3. ROS 2 源
c_info "3. 配置 ROS 2 apt 源（清华镜像 + 签名密钥）"

KEYRING=/usr/share/keyrings/ros-archive-keyring.gpg

# 判断 keyring 是否【真的】含公钥。
# 坑 1：gpg --recv-keys 失败时仍会创建 32 字节的空 keyring，
#       只判断 [ -s file ] 会被这个空文件骗过，后面 apt 报 NO_PUBKEY。
# 坑 2：本机实测 —— raw.githubusercontent 被重置、packages.ros.org 证书不匹配
#       （DNS 解析到 ftp.osuosl.org）、清华 /ros/ 目录下并没有 ros.key。
#       唯一稳定可用的是 keyserver 的 HTTPS pks 接口。
keyring_ok() {
  [ -s "$KEYRING" ] || return 1
  gpg --no-default-keyring --keyring "$KEYRING" --list-keys 2>/dev/null | grep -q '^pub'
}

if keyring_ok; then
  c_ok "密钥已存在且有效，跳过"
else
  $SUDO rm -f "$KEYRING"
  got_key=0

  # 来源 1（首选）：keyserver.ubuntu.com 的 HTTPS pks 接口。
  # 注意不要用 gpg 的 hkps:// —— 它依赖 dirmngr，在受限网络里常失败。
  if [ $got_key -eq 0 ]; then
    if curl -fsSL --max-time 40 \
         "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x${ROS_KEY_FPR}" \
         -o /tmp/ros.key.asc 2>/dev/null \
       && grep -q 'BEGIN PGP PUBLIC KEY BLOCK' /tmp/ros.key.asc 2>/dev/null; then
      $SUDO gpg --dearmor --yes -o "$KEYRING" < /tmp/ros.key.asc
      got_key=1
      c_ok "密钥来源 1（keyserver.ubuntu.com HTTPS）成功"
    fi
  fi

  # 来源 2：packages.ros.org 官方
  if [ $got_key -eq 0 ]; then
    if curl -fsSL --max-time 30 "https://packages.ros.org/ros.key" \
         -o /tmp/ros.key.asc 2>/dev/null \
       && grep -q 'BEGIN PGP PUBLIC KEY BLOCK' /tmp/ros.key.asc 2>/dev/null; then
      $SUDO gpg --dearmor --yes -o "$KEYRING" < /tmp/ros.key.asc
      got_key=1
      c_ok "密钥来源 2（packages.ros.org）成功"
    fi
  fi

  # 来源 3：github raw（有代理时可用）
  if [ $got_key -eq 0 ]; then
    if curl -fsSL --max-time 30 \
         "https://raw.githubusercontent.com/ros/rosdistro/master/ros.key" \
         -o /tmp/ros.key.asc 2>/dev/null \
       && grep -q 'BEGIN PGP PUBLIC KEY BLOCK' /tmp/ros.key.asc 2>/dev/null; then
      $SUDO gpg --dearmor --yes -o "$KEYRING" < /tmp/ros.key.asc
      got_key=1
      c_ok "密钥来源 3（raw.githubusercontent）成功"
    fi
  fi

  # 来源 4（兜底）：gpg 的 hkps 协议，需要 dirmngr
  if [ $got_key -eq 0 ]; then
    if $SUDO gpg --no-default-keyring --keyring "$KEYRING" \
         --keyserver hkps://keyserver.ubuntu.com \
         --recv-keys "$ROS_KEY_FPR" 2>/dev/null; then
      got_key=1
      c_ok "密钥来源 4（gpg hkps keyserver）成功"
    fi
  fi

  $SUDO chmod 644 "$KEYRING" 2>/dev/null || true
  if ! keyring_ok; then
    c_err "所有来源都拿不到有效的 ROS 2 仓库密钥。"
    c_err "手动方案：把 ASCII 公钥存成 /tmp/ros.key.asc，然后执行"
    c_err "    sudo gpg --dearmor --yes -o $KEYRING < /tmp/ros.key.asc"
    exit 1
  fi
  c_ok "密钥安装完成并已校验"
fi

ROS_LIST=/etc/apt/sources.list.d/ros2.list
echo "deb [arch=$(dpkg --print-architecture) signed-by=$KEYRING] $TUNA_ROS2 $UBUNTU_CODENAME main" \
  | $SUDO tee "$ROS_LIST" >/dev/null
c_ok "已写入 $ROS_LIST"

$SUDO apt-get update -qq
# 这里有两个坑，都踩过：
#   1. 不能用 `grep -q Candidate` —— 包取不到时输出是 "Candidate: (none)"，一样会匹配上。
#   2. 本脚本开了 `set -o pipefail`，而 `cmd | grep -q` 里 grep 一旦命中就立即退出，
#      写端可能收到 SIGPIPE 返回 141，于是「源明明可用却被判定为不可用」。
#      手动执行时没有 pipefail，所以这个 bug 只在脚本里复现。
#   正确做法：先把结果落到变量，再判断变量是否为空。
cand="$(apt-cache policy "ros-${ROS_DISTRO_NAME}-desktop" 2>/dev/null \
        | grep -E 'Candidate: [0-9]' || true)"
if [ -z "$cand" ]; then
  c_err "ROS 2 源不可用，检查 $ROS_LIST 与密钥。"
  c_err "apt-cache 原始输出："
  apt-cache policy "ros-${ROS_DISTRO_NAME}-desktop" 2>&1 | head -n 6 || true
  exit 1
fi
c_ok "ROS 2 ${ROS_DISTRO_NAME} 源可用（候选版本 ${cand#*Candidate: }）"

# ---------------------------------------------------------------- 4. 安装 ROS 2
c_info "4. 安装 ROS 2 Jazzy（约 2-3GB，耐心等待）"
$SUDO apt-get install -y \
  ros-${ROS_DISTRO_NAME}-desktop \
  ros-dev-tools

c_ok "ROS 2 Jazzy 安装完成"

# ---------------------------------------------------------------- 5. Gazebo + 桥接
c_info "5. 安装 Gazebo Harmonic 与 ros_gz 桥接"
# 关于 gz-sim8 的开发头文件：
#   `libgz-sim8-dev` 既不在 Ubuntu 归档里，也不在 ROS 2 仓库里 ——
#   它属于 OSRF 的独立仓库 (packages.osrfoundation.org)，而该域名在国内常被
#   证书不匹配/超时挡掉。ROS 2 Jazzy 的正确做法是装
#   `ros-jazzy-gz-sim-vendor`：它把 gz-sim8 连头文件和 CMake 配置一起 vendor 进来，
#   工程里的 find_package(gz-sim8 REQUIRED) 只要 source 过 ROS 环境就能找到。
$SUDO apt-get install -y \
  ros-${ROS_DISTRO_NAME}-ros-gz \
  ros-${ROS_DISTRO_NAME}-ros-gz-sim \
  ros-${ROS_DISTRO_NAME}-ros-gz-bridge \
  ros-${ROS_DISTRO_NAME}-gz-sim-vendor \
  ros-${ROS_DISTRO_NAME}-xacro \
  ros-${ROS_DISTRO_NAME}-robot-state-publisher \
  ros-${ROS_DISTRO_NAME}-teleop-twist-keyboard \
  ros-${ROS_DISTRO_NAME}-cv-bridge \
  ros-${ROS_DISTRO_NAME}-vision-msgs \
  python3-opencv python3-numpy python3-yaml python3-colcon-common-extensions

c_ok "Gazebo 与桥接组件安装完成"

# ---------------------------------------------------------------- 6. 环境变量
c_info "6. 配置 shell 环境"
if ! grep -q "ros/${ROS_DISTRO_NAME}/setup.bash" "$HOME/.bashrc" 2>/dev/null; then
  {
    echo ""
    echo "# ROS 2 ${ROS_DISTRO_NAME}"
    echo "source /opt/ros/${ROS_DISTRO_NAME}/setup.bash"
    echo "[ -f \$HOME/smart_community_ws/install/setup.bash ] && source \$HOME/smart_community_ws/install/setup.bash"
  } >> "$HOME/.bashrc"
  c_ok "已写入 ~/.bashrc"
else
  c_ok "~/.bashrc 已配置"
fi

# ---------------------------------------------------------------- 7. 工程代码
c_info "7. 把项目复制到 Linux 文件系统并编译"
# 为什么复制而不是直接在 /mnt/d 编译：
#   /mnt/d 是 Windows 盘，跨 9p 文件系统，colcon 的 --symlink-install 会失败，
#   编译速度也只有原生 ext4 的几分之一。
if [ -d "$HOST_SRC" ]; then
  mkdir -p "$WS_DIR/src"
  rsync -a --delete "$HOST_SRC/" "$WS_DIR/src/" 2>/dev/null || {
    c_warn "没有 rsync，改用 cp"
    rm -rf "$WS_DIR/src" && mkdir -p "$WS_DIR/src"
    cp -r "$HOST_SRC/." "$WS_DIR/src/"
  }
  c_ok "源码已同步到 $WS_DIR/src"
  ls -1 "$WS_DIR/src"
else
  c_warn "$HOST_SRC 不存在（Windows 侧 D:\\ai-sfjs\\src 没找到），跳过源码同步"
fi

if [ -d "$WS_DIR/src" ] && [ -n "$(ls -A "$WS_DIR/src" 2>/dev/null)" ]; then
  cd "$WS_DIR"
  # 关键：ROS 的 setup.bash 不是 nounset-clean 的 —— 它会读
  # AMENT_TRACE_SETUP_FILES 等未必存在的变量。本脚本开了 `set -u`，
  # 直接 source 会报 "AMENT_TRACE_SETUP_FILES: unbound variable" 并中止，
  # 表现为「ROS 装完了但 colcon build 根本没跑」。source 前后必须临时关掉。
  set +u
  # shellcheck disable=SC1090
  source "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
  set -u
  c_info "colcon build（首次会同时编译红绿灯 C++ 插件）"
  colcon build --symlink-install || {
    c_err "编译失败。常见原因："
    c_err "  - 缺 libgz-sim8-dev（第 5 步）"
    c_err "  - 磁盘空间不足"
    exit 1
  }
  c_ok "编译完成"
fi

# ---------------------------------------------------------------- 8. 验证
c_info "8. 验证"
set +u
# shellcheck disable=SC1090
source "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
[ -f "$WS_DIR/install/setup.bash" ] && source "$WS_DIR/install/setup.bash"
set -u

echo "  ros2:  $(command -v ros2 || echo '未找到')"
echo "  gz:    $(command -v gz || echo '未找到')"
ros2 --help >/dev/null 2>&1 && c_ok "ros2 可用" || c_warn "ros2 异常"
gz sim --versions 2>/dev/null | head -2 || true

cat <<EOF

$(printf '\033[32m全部完成。\033[0m')

启动仿真：
  cd $WS_DIR
  source install/setup.bash
  ros2 launch smart_community_sim smart_community.launch.py

如果 Gazebo 窗口没出来（WSLg 未生效）：
  echo \$DISPLAY        # 应为 :0 之类
  ls /mnt/wslg          # 目录存在说明 WSLg 正常
无 GPU 时可用软件渲染：
  export LIBGL_ALWAYS_SOFTWARE=1
  export GALLIUM_DRIVER=llvmpipe

感知与控制（另一个终端）：
  source /opt/ros/${ROS_DISTRO_NAME}/setup.bash && source $WS_DIR/install/setup.bash
  ros2 launch smart_community_perception perception.launch.py
EOF
