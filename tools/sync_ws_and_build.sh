#!/usr/bin/env bash
# =============================================================================
#  把宿主机 src/ 的【全部 4 个包】同步进 guest 工作空间并编译
#
#  宿主机 D:\ai-sfjs 通过共享文件夹挂到 guest 的 /media/sf_WHC。
#  编译必须在 Linux 文件系统里做（在 vboxsf 上跑 colcon --symlink-install 会出问题），
#  所以先 rsync 到 ~/smart_community_ws/src，再在 $HOME 下编译。
#
#  用法：
#      bash sync_ws_and_build.sh                 # 同步 + 编译全部 4 包
#      bash sync_ws_and_build.sh --full          # 额外编译整个工作空间
#      bash sync_ws_and_build.sh --clean         # 先清 build/install（软链残留时用）
# =============================================================================
set +u
set -o pipefail

WS="$HOME/smart_community_ws"
SRC="/media/sf_WHC/src"
PKGS=(smart_community_sim community_nav community_patrol smart_community_perception)

FULL=0; CLEAN=0; SYNC_ONLY=0
for a in "$@"; do
  case "$a" in
    --full)      FULL=1 ;;
    --clean)     CLEAN=1 ;;
    --sync-only) SYNC_ONLY=1 ;;
  esac
done

echo "=============================================================="
echo "  同步 + 编译工作空间"
echo "  WS  = $WS"
echo "  SRC = $SRC"
echo "=============================================================="

[ -d "$SRC" ] || { echo "[X] 共享文件夹不可用: $SRC"; exit 1; }
[ -d "$WS" ]  || { echo "[X] 工作空间不存在: $WS"; exit 1; }

echo
echo "--- 1. 同步源码 ---"
for p in "${PKGS[@]}"; do
  if [ ! -d "$SRC/$p" ]; then
    echo "  [skip] $SRC/$p 不存在"
    continue
  fi
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$SRC/$p/" "$WS/src/$p/" >/dev/null 2>&1 \
      && echo "  [ok] rsync $p" || { echo "  [X] rsync $p 失败"; exit 1; }
  else
    rm -rf "${WS:?}/src/$p"
    cp -r "$SRC/$p" "$WS/src/" && echo "  [ok] cp $p" || { echo "  [X] cp $p 失败"; exit 1; }
  fi
done

# ★ 宿主机是 Windows 检出，.sh 可能是 CRLF（core.autocrlf=true）。
#   这些脚本要在本机用 bash 执行，CRLF 会让 bash 报
#       line NN: $'\r': command not found
#       syntax error near unexpected token `$'do\r''
#   实测踩到过一次：同步覆盖后又变回 CRLF，演示脚本当场语法错误退出。
#   每次同步后统一转 LF（幂等，本来就没 CR 时 sed 不改内容）。
n_crlf=$(find "$WS/src" -name '*.sh' -print0 2>/dev/null | xargs -0 -r grep -lU $'\r' 2>/dev/null | wc -l)
find "$WS/src" -name '*.sh' -print0 2>/dev/null | xargs -0 -r sed -i 's/\r$//'
echo "  [ok] .sh 行尾统一为 LF（本次修正 $n_crlf 个）"

echo
echo "--- 2. 各包文件数 ---"
for p in "${PKGS[@]}"; do
  printf '  %-30s py=%-3s yaml=%-3s launch=%-2s\n' "$p" \
    "$(find "$WS/src/$p" -name '*.py' 2>/dev/null | wc -l)" \
    "$(find "$WS/src/$p" -name '*.yaml' 2>/dev/null | wc -l)" \
    "$(find "$WS/src/$p" -name '*.launch.py' 2>/dev/null | wc -l)"
done

if [ "$CLEAN" -eq 1 ]; then
  echo
  echo "--- 3. 清理 build/install 残留 ---"
  for p in "${PKGS[@]}"; do
    rm -rf "${WS:?}/build/$p" "${WS:?}/install/$p"
  done
  echo "  已清理"
fi

if [ "$SYNC_ONLY" -eq 1 ]; then
  echo
  echo "--sync-only：跳过编译。"
  exit 0
fi

echo
echo "--- 4. colcon build ---"
set +u
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
cd "$WS" || exit 1
if [ "$FULL" -eq 1 ]; then
  colcon build --symlink-install
else
  colcon build --symlink-install --packages-select "${PKGS[@]}"
fi
rc=$?
echo "  colcon 退出码: $rc"
[ $rc -ne 0 ] && { echo "  [X] 编译失败"; exit $rc; }

echo
echo "--- 5. 校验安装结果 ---"
# shellcheck disable=SC1091
source "$WS/install/setup.bash"

echo "  可用包:"
for p in "${PKGS[@]}"; do
  if ros2 pkg prefix "$p" >/dev/null 2>&1; then
    echo "    [ok]   $p"
  else
    echo "    [X]    $p 未注册"
  fi
done

echo "  community_nav 的 launch 文件:"
ls -1 "$WS/install/community_nav/share/community_nav/launch/" 2>/dev/null | sed 's/^/    /'
echo "  community_nav 的 map:"
ls -1 "$WS/install/community_nav/share/community_nav/map/" 2>/dev/null | sed 's/^/    /'
echo "  community_patrol 可执行:"
ros2 pkg executables community_patrol 2>/dev/null | sed 's/^/    /'

echo
echo "完成。"
