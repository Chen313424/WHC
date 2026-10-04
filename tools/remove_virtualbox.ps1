#Requires -RunAsAdministrator
<#
.SYNOPSIS
    清理 VirtualBox 及其虚拟机（可选操作，不可逆）。

.DESCRIPTION
    什么时候需要跑这个脚本
    ----------------------
    **不是必须的。** WSL2 与 VirtualBox 可以共存于同一台机器（只是不能同时
    运行各自的虚拟机：Hyper-V 会占用 VT-x，VirtualBox 拿不到硬件虚拟化）。

    跑它的理由只有两个：
      * 想回收磁盘空间（现有 VM 磁盘 21GB + 6.8GB 安装目录）
      * 确定不再需要 VirtualBox

    安全设计
    --------
    * 默认 **只列出** 会删除什么（-DryRun 是默认行为）
    * 删除任何东西都需要 -Confirm 且手动输入确认短语
    * VM 磁盘另需 -DeleteDisks，避免手滑丢数据

    重要提醒
    --------
    D:\Program Files\vbox1\qks\Ubuntu\Ubuntu.vdi 有 21GB，里面可能有你之前的
    工作成果。**删之前请先确认不需要，或者先备份该文件。**

.PARAMETER DryRun
    只显示计划，不执行。默认行为。

.PARAMETER Confirm
    实际执行卸载/删除。

.PARAMETER DeleteDisks
    连同 VM 磁盘文件一起删除（危险，不可恢复）。

.PARAMETER KeepVBox
    只删 VM，保留 VirtualBox 程序。

.EXAMPLE
    # 第一步：看看会删什么（安全）
    powershell -ExecutionPolicy Bypass -File tools\remove_virtualbox.ps1

.EXAMPLE
    # 第二步：确认后执行
    powershell -ExecutionPolicy Bypass -File tools\remove_virtualbox.ps1 -Confirm
#>
param(
    [switch]$DryRun,
    [switch]$Confirm,
    [switch]$DeleteDisks,
    [switch]$KeepVBox
)

$ErrorActionPreference = "Stop"

function Write-Step($n, $t) { Write-Host "`n=== $n. $t ===" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [X]  $m" -ForegroundColor Red }

# 默认就是 dry-run：除非显式给了 -Confirm
$doIt = $Confirm -and -not $DryRun

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Err "请用【管理员】身份运行。"; exit 1 }

Write-Host "VirtualBox 清理 —— $(if ($doIt) { '执行模式' } else { '预览模式（不会改动任何东西）' })" `
    -ForegroundColor White

# ---------------------------------------------------------------- 1. 现状
Write-Step 1 "当前 VirtualBox 状态"

$vbm = @(
    'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe',
    'C:\Program Files (x86)\Oracle\VirtualBox\VBoxManage.exe',
    'D:\vbox\VBoxManage.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($vbm) {
    Write-Ok "VBoxManage: $vbm"
    Write-Host "  已注册的虚拟机:"
    & $vbm list vms 2>&1 | ForEach-Object { Write-Host "    $_" }
    Write-Host "  运行中的虚拟机:"
    & $vbm list runningvms 2>&1 | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Warn2 "未找到 VBoxManage.exe"
}

Write-Step 2 "将被处理的文件"
$targets = @()
$vmRoots = @(
    "$env:USERPROFILE\VirtualBox VMs",
    'D:\Program Files\vbox1',
    'D:\Program Files\Ubuntu'
)
foreach ($r in $vmRoots) {
    if (Test-Path $r) {
        $size = (Get-ChildItem $r -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object -Property Length -Sum).Sum
        $targets += [pscustomobject]@{
            Path = $r
            SizeGB = [math]::Round($size / 1GB, 2)
            Kind = '虚拟机文件'
        }
    }
}

# VirtualBox 程序目录
if (-not $KeepVBox) {
    foreach ($p in @('C:\Program Files\Oracle\VirtualBox', 'D:\vbox')) {
        if (Test-Path $p) {
            $size = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue |
                     Measure-Object -Property Length -Sum).Sum
            $targets += [pscustomobject]@{
                Path = $p
                SizeGB = [math]::Round($size / 1GB, 2)
                Kind = '程序目录'
            }
        }
    }
}

$targets | Format-Table -AutoSize
$totalGB = [math]::Round(($targets | Measure-Object -Property SizeGB -Sum).Sum, 2)
Write-Host "  合计约 $totalGB GB" -ForegroundColor White

# ---------------------------------------------------------------- 3. 数据保护
Write-Step 3 "磁盘文件（默认保留）"
$disks = Get-ChildItem -Path @("$env:USERPROFILE\VirtualBox VMs", 'D:\Program Files\vbox1') `
    -Recurse -Filter '*.vdi' -ErrorAction SilentlyContinue
if ($disks) {
    foreach ($d in $disks) {
        Write-Host ("    {0}  ({1} GB)" -f $d.FullName, [math]::Round($d.Length / 1GB, 2))
    }
    if (-not $DeleteDisks) {
        Write-Warn2 "上面这些磁盘【不会】被删除（未指定 -DeleteDisks）。"
        Write-Host "        其中可能包含你之前的工作。确认不需要后，再决定是否加 -DeleteDisks。" -ForegroundColor Gray
    } else {
        Write-Err "已指定 -DeleteDisks：这些磁盘将被永久删除！"
    }
} else {
    Write-Host "    未找到 .vdi 文件"
}

# ---------------------------------------------------------------- 4. 执行
if (-not $doIt) {
    Write-Host ""
    Write-Host "以上为预览。确认无误后执行：" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File tools\remove_virtualbox.ps1 -Confirm" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "建议顺序：" -ForegroundColor White
    Write-Host "  1. 先跑 tools\setup_wsl_env.ps1 把 WSL2 装好并确认 Gazebo 能跑" -ForegroundColor Gray
    Write-Host "  2. 确认新环境可用后，再回来清理 VirtualBox" -ForegroundColor Gray
    exit 0
}

Write-Step 4 "确认"
Write-Host "  即将卸载 VirtualBox 并删除上表中的虚拟机文件。" -ForegroundColor Yellow
if ($DeleteDisks) { Write-Host "  并且会【永久删除所有 .vdi 磁盘】。" -ForegroundColor Red }
$answer = Read-Host "  输入 DELETE 以继续（其它任何输入都会取消）"
if ($answer -ne 'DELETE') {
    Write-Host "  已取消，未做任何改动。" -ForegroundColor Green
    exit 0
}

Write-Step 5 "停止相关进程"
Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -match 'VirtualBox|VBox' } |
    ForEach-Object { Write-Host "  结束 $($_.ProcessName) (pid=$($_.Id))"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
Write-Ok "已停止"

Write-Step 6 "卸载 VirtualBox 程序"
if ($KeepVBox) {
    Write-Ok "按要求保留 VirtualBox 程序（-KeepVBox）"
} else {
    $uninstalled = $false
    $keys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    foreach ($k in $keys) {
        $entries = Get-ItemProperty $k -ErrorAction SilentlyContinue |
                   Where-Object { $_.DisplayName -like '*VirtualBox*' }
        foreach ($e in $entries) {
            Write-Host "  发现: $($e.DisplayName) $($e.DisplayVersion)"
            if ($e.UninstallString) {
                Write-Host "  执行: $($e.UninstallString) --silent"
                $exe = ($e.UninstallString -replace '"', '') -replace '\s+--.*$', ''
                if (Test-Path $exe) {
                    Start-Process -FilePath $exe -ArgumentList '--silent' -Wait
                    $uninstalled = $true
                } else {
                    Write-Warn2 "卸载程序不存在: $exe"
                }
            }
        }
    }
    if ($uninstalled) { Write-Ok "卸载完成" } else { Write-Warn2 "没找到可用的卸载入口，可能需要手动在「设置 → 应用」里卸载" }
}

Write-Step 7 "删除残留文件"
foreach ($t in $targets) {
    if (-not (Test-Path $t.Path)) { continue }
    if (-not $DeleteDisks) {
        # 保留磁盘：只删非 .vdi 的内容
        Write-Host "  清理 $($t.Path)（保留 .vdi）"
        Get-ChildItem $t.Path -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -ne '.vdi' } |
            ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
    } else {
        Write-Host "  删除 $($t.Path)"
        Remove-Item $t.Path -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Write-Ok "清理完成"

if (-not $DeleteDisks -and $disks) {
    Write-Host ""
    Write-Warn2 "磁盘文件仍保留在原位置。确认不再需要可手动删除，或加 -DeleteDisks 重跑。"
}

Write-Host ""
Write-Host "完成。现在用 tools\setup_wsl_env.ps1 建立 WSL2 环境。" -ForegroundColor Green
