#!/usr/bin/env bash
# =============================================================================
#  guest 环境加固（在 ROS 2 安装之前跑）
#
#  修两个已经实际踩到的坑：
#
#  1. 没有 swap
#     Ubuntu autoinstall 模板里写的是 `storage.swap.size: 0`，即不建交换分区。
#     结果：4GB 内存 + GNOME 桌面 + apt 解包 ros-jazzy-desktop 时内存耗尽，
#     内核既无法换出也没有可回收页，进程进入活锁 —— CPU 满载但零磁盘进展，
#     SSH / Guest Additions 全部失去响应。这里加一个 4GB swapfile 兜底。
#
#  2. NetworkManager-wait-online.service 卡启动
#     装 ubuntu-desktop-minimal 时引入了 NetworkManager，与服务器版原有的
#     systemd-networkd 抢同一块网卡。该服务等待"网络就绪"且 TimeoutStartSec
#     为无限，导致启动长期停在
#         A start job is running for NetworkManager-wait-online.service (no limit)
#     sshd 迟迟起不来。禁用它对本地开发无影响。
# =============================================================================
set -u

c_ok()   { printf '  \033[32m[OK]\033[0m %s\n' "$*"; }
c_warn() { printf '  \033[33m[!]\033[0m %s\n' "$*"; }

echo "=== 1. 配置 4GB swap ==="
if swapon --show 2>/dev/null | grep -q '/swapfile'; then
  c_ok "swap 已启用，跳过"
else
  if [ ! -f /swapfile ]; then
    sudo fallocate -l 4G /swapfile 2>/dev/null \
      || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none
  fi
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null 2>&1
  sudo swapon /swapfile 2>/dev/null
  if ! grep -q '^/swapfile' /etc/fstab 2>/dev/null; then
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  fi
  if swapon --show 2>/dev/null | grep -q '/swapfile'; then
    c_ok "swap 已创建并启用"
  else
    c_warn "swap 启用失败，继续（但大安装有内存风险）"
  fi
fi

echo "=== 2. 禁用 NetworkManager-wait-online ==="
if systemctl is-enabled NetworkManager-wait-online.service >/dev/null 2>&1; then
  sudo systemctl disable --now NetworkManager-wait-online.service 2>&1 | tail -n 2
  c_ok "已禁用（避免启动无限等待网络）"
else
  c_ok "已是禁用状态"
fi

echo "=== 3. 当前状态 ==="
free -h
echo "--- swap ---"
swapon --show 2>/dev/null || echo "(无)"
echo "--- 关键服务 ---"
for s in ssh NetworkManager systemd-networkd; do
  printf '  %-20s %s\n' "$s" "$(systemctl is-active $s 2>&1)"
done
