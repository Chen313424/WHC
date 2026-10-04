#Requires -RunAsAdministrator
<#
.SYNOPSIS
    用 VirtualBox 构建符合本项目要求的 Ubuntu 24.04 虚拟机。

.DESCRIPTION
    为什么是 live-server ISO 而不是 desktop
    ----------------------------------------
    VirtualBox 的无人值守安装模板用的是 subiquity / curtin 语法
    （`curtin in-target`、`apt.fallback: offline-install`），
    这是 Ubuntu **Server** 安装器的方言。24.04 Desktop 换成了 Flutter 新安装器，
    套这个模板会失败。所以：先用 live-server 无人值守装好，
    再通过 SSH 补装 ubuntu-desktop-minimal 得到图形界面。

    本脚本做三件事：
      1. 校验 ISO 与 SHA256
      2. 用 tools/vbox_autoinstall_user_data 作为 autoinstall 配置跑无人值守安装
         （该模板已内置：SSH + 宿主机公钥、免密 sudo、清华 apt 镜像）
      3. 校验 VirtualBox 的占位符替换是否干净（有残留说明模板不匹配，会中止）

    安装本身约 10-25 分钟。装完用 SSH 进入（端口转发 2222）：
        ssh -p 2222 whc@127.0.0.1

.PARAMETER VmName
    已存在的虚拟机名。先用 VBoxManage createvm 建好（见 docs）。

.PARAMETER IsoPath
    Ubuntu 24.04 live-server ISO 路径。

.PARAMETER StartType
    gui / headless / separate / none。默认 gui，方便肉眼确认安装过程。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\build_vm.ps1
#>
param(
    [string]$VmName   = "WHC-Ubuntu2404",
    [string]$IsoPath  = "D:\VMs\ubuntu-24.04.5-live-server-amd64.iso",
    [string]$User     = "whc",
    [string]$Password = "whc2026",
    [string]$HostName = "whc-dev",
    [string]$StartType = "gui",
    [switch]$SkipIsoCheck
)

$ErrorActionPreference = "Stop"

function Write-Step($n, $t) { Write-Host "`n=== $n. $t ===" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [X]  $m" -ForegroundColor Red }

$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo   = Split-Path -Parent $here
$vbm    = @(
    'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe',
    'C:\Program Files (x86)\Oracle\VirtualBox\VBoxManage.exe',
    'D:\vbox\VBoxManage.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $vbm) { Write-Err "找不到 VBoxManage.exe"; exit 1 }
Write-Host "VBoxManage: $vbm" -ForegroundColor Gray

$scriptTemplate = Join-Path $here 'vbox_autoinstall_user_data'
$additionsIso   = Join-Path (Split-Path $vbm -Parent) 'VBoxGuestAdditions.iso'

# ---------------------------------------------------------------- 1. VM 存在性
Write-Step 1 "检查虚拟机"
$vms = & $vbm list vms 2>&1 | Out-String
if ($vms -notmatch [regex]::Escape($VmName)) {
    Write-Err "虚拟机 '$VmName' 不存在。先创建并配置（见 docs 里的 build 步骤），或改 -VmName。"
    exit 1
}
Write-Ok "'$VmName' 已注册"

# ---------------------------------------------------------------- 2. ISO
Write-Step 2 "校验安装镜像"
if (-not (Test-Path $IsoPath)) { Write-Err "找不到 ISO: $IsoPath"; exit 1 }
$isoSizeGB = [math]::Round((Get-Item $IsoPath).Length / 1GB, 2)
Write-Host "  ISO: $IsoPath ($isoSizeGB GB)"

if (-not $SkipIsoCheck) {
    $sums = Join-Path (Split-Path $IsoPath -Parent) 'SHA256SUMS'
    if (Test-Path $sums) {
        $name = Split-Path $IsoPath -Leaf
        $line = Select-String -Path $sums -Pattern ([regex]::Escape($name) + '$') | Select-Object -First 1
        if ($line) {
            $expected = $line.Line.Split()[0].ToLower()
            Write-Host "  计算 SHA256（3.8GB，约需 10-30 秒）..."
            $actual = (Get-FileHash $IsoPath -Algorithm SHA256).Hash.ToLower()
            if ($expected -ne $actual) {
                Write-Err "SHA256 不匹配！ISO 可能损坏，请重新下载。"
                Write-Host "    期望 $expected"
                Write-Host "    实际 $actual"
                exit 1
            }
            Write-Ok "SHA256 校验通过"
        } else {
            Write-Warn2 "SHA256SUMS 里没有 $name，跳过校验"
        }
    } else {
        Write-Warn2 "没有 SHA256SUMS 文件，跳过校验"
    }
}

# ---------------------------------------------------------------- 3. 模板
Write-Step 3 "检查 autoinstall 模板"
if (-not (Test-Path $scriptTemplate)) { Write-Err "找不到模板: $scriptTemplate"; exit 1 }
Write-Ok "模板: $scriptTemplate"

# ---------------------------------------------------------------- 4. 无人值守安装
Write-Step 4 "执行无人值守安装（VBox 会自动挂载 ISO 与应答盘）"
# 注意：不要用 $args —— 那是 PowerShell 的保留自动变量
$vbArgs = @(
    'unattended', 'install', $VmName,
    "--iso=$IsoPath",
    "--user=$User",
    "--user-password=$Password",
    "--full-user-name=WHC Dev",
    "--hostname=$HostName",
    "--locale=en_US.UTF-8",
    "--country=CN",
    "--time-zone=Asia/Shanghai",
    "--script-template=$scriptTemplate",
    "--install-additions",
    "--start-vm=$StartType"
)
if (Test-Path $additionsIso) { $vbArgs += "--additions-iso=$additionsIso" }

Write-Host "  VBoxManage $($vbArgs -join ' ')" -ForegroundColor Gray
& $vbm @vbArgs 2>&1
Write-Output "exit=$LASTEXITCODE"

# ---------------------------------------------------------------- 5. 占位符校验
Write-Step 5 "校验占位符替换"
# 括号位置很关键：必须是 Split-Path -Parent (EXPR)，
# 写成 Split-Path (EXPR -Parent) 会导致语法错误
$cfgLine = & $vbm showvminfo $VmName --machinereadable 2>&1 |
    Select-String -Pattern '^CfgFile=' | Select-Object -First 1
$vmDir = $null
if ($cfgLine) { $vmDir = Split-Path -Parent ($cfgLine.Line.Split('=')[1].Trim('"')) }
$generated = if ($vmDir) {
    Get-ChildItem $vmDir -Filter 'Unattended-*-user-data' -ErrorAction SilentlyContinue |
        Select-Object -First 1
} else { $null }
if ($generated) {
    $leftover = Select-String -Path $generated.FullName -Pattern '@@VBOX_' -ErrorAction SilentlyContinue
    if ($leftover) {
        Write-Err "模板里有未替换的占位符 —— 说明模板与 VirtualBox 版本不匹配："
        $leftover | Select-Object -First 10 | ForEach-Object { Write-Host "    L$($_.LineNumber): $($_.Line.Trim())" }
        Write-Host "  安装可能已进入交互模式。请关闭 VM 后修正模板再重试。" -ForegroundColor Yellow
        exit 1
    }
    Write-Ok "占位符全部替换完毕（$($generated.Name)）"
} else {
    Write-Warn2 "没找到生成的 user-data，无法校验（不影响继续）"
}

# ---------------------------------------------------------------- 6. 后续
Write-Host ""
Write-Host "安装已启动。约 10-25 分钟后可用 SSH 登录验证：" -ForegroundColor White
Write-Host "    ssh -o StrictHostKeyChecking=no -p 2222 $User@127.0.0.1" -ForegroundColor Yellow
Write-Host ""
Write-Host "登录后补装桌面与 ROS 2：" -ForegroundColor White
Write-Host "    sudo bash /media/sf_WHC/tools/setup_vm_guest.sh    # 共享文件夹（需 Guest Additions 生效）" -ForegroundColor Gray
Write-Host "  或先 scp 过去：" -ForegroundColor Gray
Write-Host "    scp -P 2222 -r `"$repo\tools`" `"$repo\src`" ${User}@127.0.0.1:~/whc_setup/" -ForegroundColor Gray
