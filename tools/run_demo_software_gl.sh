#!/usr/bin/env bash
# =============================================================================
#  在【没有可用 3D 加速】的虚拟机里启动巡检演示
#
#  背景（本机实测）：VM 设置里 VMSVGA + 3D Acceleration 都是开着的，
#  但 guest 内核一直报
#      [drm:vmw_msg_ioctl [vmwgfx]] *ERROR* Failed to open channel.
#  即宿主侧 3D 通道没打开（VirtualBox 在部分 Windows 宿主机上无法为
#  VMSVGA 提供 3D 后端）。Gazebo GUI 因此报
#      libEGL warning: egl: failed to create dri2 screen
#  但这只是【警告】，GUI 仍然能起来并渲染（Mesa 会自动回退到 llvmpipe）。
#
#  ★★ 血泪教训：不要设下面这两个！它们会让 Gazebo GUI 直接 abort：
#      MESA_LOADER_DRIVER_OVERRIDE=llvmpipe   # llvmpipe 不是合法的 loader 驱动名
#                                             #   → "failed to load driver: llvmpipe"
#      LIBGL_DRI3_DISABLE=1                   # 连 drisw 也建不出来
#                                             #   → "failed to create drisw screen"
#    症状是 Qt Quick 建不出 GL 上下文：
#      QSGRenderLoop::handleContextCreationFailure → X Error: GLXBadFBConfig → Aborted
#    注意 gz sim -s（纯 server）在这两个变量下【照样能跑】，
#    所以只测 server 会误判成"环境没问题"——必须看 GUI。
#
#  实测通过的组合（GUI 存活到超时）：
#      不设任何变量 / LIBGL_ALWAYS_SOFTWARE=1 / +GALLIUM_DRIVER=llvmpipe /
#      QT_QUICK_BACKEND=software
#    而 QT_QUICK_BACKEND=software 与 LIBGL_ALWAYS_SOFTWARE 同时用时反而早退，
#    所以这里只保留最确定的一对。
#
#  用法：
#      bash run_demo_software_gl.sh              # 等同 run_patrol_demo.sh
#      bash run_demo_software_gl.sh --no-rviz
# =============================================================================
set +u

export LIBGL_ALWAYS_SOFTWARE=1        # 一律走软件光栅（3D 通道不可用）
export GALLIUM_DRIVER=llvmpipe        # gallium 后端选 llvmpipe
export LP_NUM_THREADS=4               # llvmpipe 用满 4 个 vCPU

# 明确清掉已知会搞死 GUI 的两个变量（防止从别处继承进来）
unset MESA_LOADER_DRIVER_OVERRIDE
unset LIBGL_DRI3_DISABLE

DEMO="$HOME/smart_community_ws/src/community_nav/scripts/run_patrol_demo.sh"

echo "=============================================================="
echo "  软件渲染（llvmpipe）启动巡检演示"
echo "    LIBGL_ALWAYS_SOFTWARE=$LIBGL_ALWAYS_SOFTWARE"
echo "    GALLIUM_DRIVER=$GALLIUM_DRIVER  LP_NUM_THREADS=$LP_NUM_THREADS"
echo "    演示脚本: $DEMO"
echo "=============================================================="
echo

exec bash "$DEMO" "$@"
