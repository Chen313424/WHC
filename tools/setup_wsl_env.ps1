#Requires -RunAsAdministrator
<#
.SYNOPSIS
    配置符合本项目要求的虚拟化环境：WSL2 + Ubuntu 24.04 (Noble)。

.DESCRIPTION
    为什么是 WSL2 而不是 VirtualBox
    --------------------------------
    1. 项目 README 的环境要求原文就是「Ubuntu 24.04 (Noble) / WSL2」
       （ROS 2 Jazzy 只支持 24.04；本机现有 VirtualBox VM 是 22.04）。
    2. 本机已开启 VBS / 内存完整性(HVCI) / HvHost，Hyper-V 虚拟机监控程序在跑。
       VirtualBox 拿不到独占 VT-x，VM 会以 "不能为电脑打开一个新任务" 失败；
       重装 VirtualBox 也修不好这个冲突。
    3. WSL2 本身就构建在 Hyper-V 之上，与 VBS 天然共存，不抢 VT-x。
    4. WSLg 自带 X/Wayland，Gazebo GUI 可直接显示，无需额外配 X Server。

    本脚本只负责宿主机侧（启用功能 + 安装发行版）。
    发行版内的 ROS 2 / Gazebo 安装由同目录的 setup_ros_jazzy.sh 完成。

.PARAMETER Distro
    要安装的发行版名，默认 Ubuntu-24.04。

.PARAMETER SkipFeatures
    跳过 Windows 功能启用（功能已开时可用，避免无谓重启）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\setup_wsl_env.ps1
#>
param(
    [string]$Distro = "Ubuntu-24.04",
    [switch]$SkipFeatures
)

$ErrorActionPreference = "Stop"

function Write-Step($n, $t) { Write-Host "`n=== $n. $t ===" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [OK] $m"   -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  [!]  $m"   -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [X]  $m"   -ForegroundColor Red }

# ---------------------------------------------------------------- 0. 权限
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Err "请用【管理员】身份运行 PowerShell。"
    Write-Host "    右键开始菜单 -> 终端(管理员) / Windows PowerShell(管理员)"
    exit 1
}

Write-Host "WSL2 环境配置 —— 目标: $Distro" -ForegroundColor White

# ---------------------------------------------------------------- 1. 现状
Write-Step 1 "检查当前状态"

$os = Get-CimInstance Win32_OperatingSystem
Write-Host ("  Windows: {0} (Build {1})" -f $os.Caption, $os.BuildNumber)
if ([int]$os.BuildNumber -lt 19041) {
    Write-Err "Windows 版本过低（需要 Build 19041+，即 Win10 2004 以上）"
    exit 1
}
Write-Ok "Windows 版本满足要求"

Write-Host "  可用内存: $([math]::Round($os.FreePhysicalMemory/1MB,2)) GB / $([math]::Round($os.TotalVisibleMemorySize/1MB,2)) GB"
if ($os.FreePhysicalMemory / 1MB -lt 4) {
    Write-Warn2 "空闲内存不足 4GB，Gazebo 可能吃力。建议先关掉一些程序。"
}

$wslExe = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($wslExe) {
    Write-Ok "wsl.exe 存在: $($wslExe.Source)"
    try { & wsl.exe --version 2>&1 | Select-Object -First 3 | ForEach-Object { Write-Host "    $_" } } catch {}
    Write-Host "  已安装的发行版:"
    try { & wsl.exe -l -v 2>&1 | ForEach-Object { Write-Host "    $_" } } catch { Write-Host "    (无)" }
} else {
    Write-Warn2 "wsl.exe 不存在，将随功能启用一起安装"
}

# ---------------------------------------------------------------- 2. 功能
if (-not $SkipFeatures) {
    Write-Step 2 "启用 Windows 功能（适用于 Linux 的子系统 + 虚拟机平台）"

    $needReboot = $false
    foreach ($f in @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")) {
        $state = (Get-WindowsOptionalFeature -Online -FeatureName $f -ErrorAction SilentlyContinue).State
        if ($state -eq "Enabled") {
            Write-Ok "$f 已启用"
        } else {
            Write-Host "  正在启用 $f ..."
            $r = Enable-WindowsOptionalFeature -Online -FeatureName $f -All -NoRestart -ErrorAction Stop
            if ($r.RestartNeeded) { $needReboot = $true }
            Write-Ok "$f 已启用（需要重启生效）"
        }
    }
    if ($needReboot) {
        Write-Warn2 "有功能需要重启才能生效。"
        Write-Host "    请重启电脑后【重新运行本脚本】继续。" -ForegroundColor Yellow
        exit 0
    }
} else {
    Write-Step 2 "跳过功能启用（-SkipFeatures）"
}

# ---------------------------------------------------------------- 3. WSL 内核
Write-Step 3 "更新 WSL 内核"
try {
    & wsl.exe --update
    Write-Ok "wsl --update 完成"
} catch {
    Write-Warn2 "wsl --update 失败（离线也能继续，系统自带内核通常够用）: $_"
}
& wsl.exe --set-default-version 2 2>&1 | Out-Null
Write-Ok "默认版本设为 WSL2"

# ---------------------------------------------------------------- 4. 安装发行版
Write-Step 4 "安装 $Distro"

$existing = (& wsl.exe -l -q 2>&1) -replace "`0", "" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
if ($existing -contains $Distro) {
    Write-Ok "$Distro 已安装，跳过"
} else {
    Write-Host "  正在安装（需要从微软服务器下载约 500MB，请耐心等待）..."
    # --no-launch: 先不启动，避免卡在交互式创建用户
    & wsl.exe --install -d $Distro --no-launch
    if ($LASTEXITCODE -ne 0) {
        Write-Err "安装失败（退出码 $LASTEXITCODE）"
        Write-Host "    手动排查：" -ForegroundColor Yellow
        Write-Host "      wsl --list --online        查看可安装的发行版名"
        Write-Host "      wsl --install -d Ubuntu-24.04"
        Write-Host "    如果微软源不可达，可先执行 wsl --update 再重试。"
        exit 1
    }
    Write-Ok "$Distro 安装完成"
}

# ---------------------------------------------------------------- 5. 验证
Write-Step 5 "验证"
& wsl.exe -l -v 2>&1 | ForEach-Object { Write-Host "  $_" }

Write-Host ""
Write-Host "下一步：" -ForegroundColor White
Write-Host "  1) 首次启动并创建用户：" -ForegroundColor Gray
Write-Host "       wsl -d $Distro" -ForegroundColor Yellow
Write-Host "     （会让你输入 UNIX 用户名和密码，记牢）" -ForegroundColor Gray
Write-Host "  2) 回到本目录，把 ROS 安装脚本复制进去并执行：" -ForegroundColor Gray
Write-Host "       wsl -d $Distro -- bash -lc 'bash /mnt/d/ai-sfjs/tools/setup_ros_jazzy.sh'" -ForegroundColor Yellow
Write-Host ""
Write-Host "关于 VirtualBox：" -ForegroundColor White
Write-Host "  WSL2 开启后，VirtualBox 会因 VT-x 被 Hyper-V 占用而无法启动虚拟机。" -ForegroundColor Gray
Write-Host "  这是二选一的架构冲突，不是故障。若确认不再需要 VirtualBox，可用" -ForegroundColor Gray
Write-Host "  tools\remove_virtualbox.ps1 清理（会先列出将被删除的内容并要求确认）。" -ForegroundColor Gray
