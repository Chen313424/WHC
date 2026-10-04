#!/usr/bin/env bash
# =============================================================================
#  把宿主机 src/ 的改动同步进 guest 工作空间并重新编译
#
#  宿主机目录是通过共享文件夹暴露的： D:\ai-sfjs -> /media/sf_WHC
#  编译必须在 Linux 文件系统里做（/mnt 或 vboxsf 上 colcon --symlink-install 会出问题）
# =============================================================================
set +u

WS="$HOME/smart_community_ws"
SRC="/media/sf_WHC/src"
PKG="${1:-smart_community_perception}"

echo "=== 1. 同步源码 ==="
if [ ! -d "$SRC" ]; then
  echo "  [X] 共享文件夹不可用: $SRC"
  exit 1
fi
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "$SRC/$PKG/" "$WS/src/$PKG/" && echo "  [ok] rsync 完成"
else
  rm -rf "$WS/src/$PKG"
  cp -r "$SRC/$PKG" "$WS/src/" && echo "  [ok] cp 完成"
fi
echo "  已同步: $(find "$WS/src/$PKG" -name '*.py' | wc -l) 个 py 文件，$(find "$WS/src/$PKG" -name '*.yaml' | wc -l) 个 yaml"

echo ""
echo "=== 2. colcon build ($PKG) ==="
source /opt/ros/jazzy/setup.bash
cd "$WS" || exit 1
colcon build --symlink-install --packages-select "$PKG"
RC=$?
echo "  colcon 退出码: $RC"
if [ $RC -ne 0 ]; then
  echo "  [X] 编译失败"
  exit $RC
fi

echo ""
echo "=== 3. 可执行是否注册 ==="
source "$WS/install/setup.bash"
for exe in yolo_detector traffic_controller autolabel_capture; do
  if ros2 pkg executables smart_community_perception 2>/dev/null | grep -q "$exe"; then
    echo "  [ok] $exe"
  else
    echo "  [X] $exe 未注册"
  fi
done

echo ""
echo "=== 4. 配置与 launch 是否安装到 share ==="
ls -1 "$WS/install/smart_community_perception/share/smart_community_perception/config/" 2>/dev/null | sed 's/^/  config\//'
ls -1 "$WS/install/smart_community_perception/share/smart_community_perception/launch/" 2>/dev/null | sed 's/^/  launch\//'

echo ""
echo "提示: 如果删过源文件，--symlink-install 会在 build/ 留下指向已删文件的软链，"
echo "      导致 install 阶段报 \"can't copy ... doesn't exist\"。此时先清掉该包的"
echo "      build/ 与 install/ 目录再编译。"
