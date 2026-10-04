#!/usr/bin/env bash
# =============================================================================
#  可靠地设置 guest 用户密码
#
#  为什么不能只用 `echo 'user:pass' | chpasswd`：
#    实测该命令打印了
#        BAD PASSWORD: The password is shorter than 8 characters
#        CHANGED_OK            <- 但退出码却是 0
#    最终 libcrypt 回验发现新密码和旧密码**都不匹配** —— 说明 pam_pwquality
#    拦下了这次修改，而 chpasswd 仍然返回了成功。只看退出码会被骗。
#
#  本例做法：自己用系统 libcrypt 生成哈希（Perl 的 crypt() 支持 yescrypt），
#  自检通过后用 `chpasswd -e` 直接写哈希，绕过密码强度策略，最后回读校验。
#
#  用法: bash set_password.sh <用户名> <新密码>
# =============================================================================
set -eu

USER_NAME="${1:-whc}"
NEW_PW="${2:-123456}"

echo "=== 1. 生成 yescrypt 哈希并自检 ==="
SALT="$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9./' | head -c 16)"
HASH="$(perl -e 'print crypt($ARGV[0], $ARGV[1])' "$NEW_PW" "\$y\$j9T\$$SALT")"
CHECK="$(perl -e 'print crypt($ARGV[0], $ARGV[1])' "$NEW_PW" "$HASH")"
if [ "$CHECK" != "$HASH" ]; then
  echo "  [X] 哈希自检失败，中止"
  exit 1
fi
echo "  [ok] 哈希: $(printf '%s' "$HASH" | cut -c1-28)..."

echo "=== 2. 用 chpasswd -e 直接写入哈希（跳过 pam_pwquality） ==="
printf '%s:%s\n' "$USER_NAME" "$HASH" | sudo chpasswd -e
echo "  [ok] 已写入 /etc/shadow"

echo "=== 3. 回读校验 ==="
CUR="$(sudo getent shadow "$USER_NAME" | cut -d: -f2)"
if [ "$CUR" = "$HASH" ]; then
  echo "  [ok] 回读一致"
else
  echo "  [X] 回读不一致！"
  exit 1
fi

echo "=== 4. 用 libcrypt 独立复核 ==="
if [ -f "$HOME/check_pw.pl" ]; then
  sudo perl "$HOME/check_pw.pl" "$USER_NAME" "$NEW_PW"
else
  perl -e 'my $h=shift; my $p=shift; print((crypt($p,$h) eq $h) ? "MATCH\n" : "NO\n")' "$CUR" "$NEW_PW"
fi

echo "=== 5. 账号状态 ==="
sudo passwd -S "$USER_NAME"
