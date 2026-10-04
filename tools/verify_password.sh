#!/usr/bin/env bash
# =============================================================================
#  确定性验证 guest 用户密码（真实 PAM 认证，不依赖 python crypt）
#
#  为什么不用 python 的 crypt 模块：
#    Ubuntu 24.04 默认是 yescrypt（哈希以 $y$ 开头），而 Python 的 crypt 模块
#    (libxcrypt) 对 $y$ 的支持并不一致 —— 实测用它比对 123456 和旧密码
#    都返回 NO，是因为拿不到有效结果而不是密码真错了。这种"假阴性"会误导判断。
#
#  改用 `script` 分配伪终端后调 su，走完整 PAM 栈，这才是真的认证测试。
#
#  用法: bash verify_password.sh <用户名> <候选密码> [更多候选...]
# =============================================================================
set -u

USER_NAME="${1:-whc}"
shift || true
CANDIDATES=("$@")
if [ ${#CANDIDATES[@]} -eq 0 ]; then
  CANDIDATES=("123456")
fi

echo "用户: $USER_NAME"
HASH="$(sudo getent shadow "$USER_NAME" | cut -d: -f2)"
echo "算法: $(printf '%s' "$HASH" | cut -c1-3)   (y\$ = yescrypt, \$6\$ = sha512)"
echo "哈希: $(printf '%s' "$HASH" | cut -c1-28)..."
echo ""
echo "真实 PAM 认证测试："

for pw in "${CANDIDATES[@]}"; do
  # script -qec 会给命令分配 pty，su 于是能从 pty 读密码
  OUT="$(printf '%s\n' "$pw" \
        | script -qec "su - $USER_NAME -c 'echo AUTH_OK'" /dev/null 2>&1 || true)"
  if printf '%s' "$OUT" | grep -q 'AUTH_OK'; then
    VERDICT="✅ 认证成功"
  elif printf '%s' "$OUT" | grep -qi 'Authentication failure'; then
    VERDICT="❌ 认证失败"
  else
    VERDICT="? 结果不明: $(printf '%s' "$OUT" | tr -d '\r' | tail -n 1)"
  fi
  printf '  %-12s -> %s\n' "$pw" "$VERDICT"
done
