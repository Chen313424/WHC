#Requires -Version 5.1
<#
.SYNOPSIS
    从宿主机通过 SSH 把 VirtualBox guest 配置成可运行本项目的环境。

.DESCRIPTION
    前置条件：tools\build_vm.ps1 已跑完无人值守安装，且 guest 已重启进入系统。
    （端口转发 2222 -> guest:22 已在 VM 配置阶段建好）

    本脚本按顺序做四件事，每步都可单独跳过：

      1. 等待 guest SSH 就绪
      2. 建立免密 sudo + 设定时区
         （autoinstall 模板里的 user-data.sudo 不生效，必须在这里补）
      3. 上传 tools\ 与 src\ 到 guest 的 ~/whc_setup
         （不依赖共享文件夹，因此不要求 Guest Additions 正常工作）
      4. 运行 guest 内 setup_vm_guest.sh：装桌面 -> 装 ROS 2 Jazzy + Gazebo
         -> 编译工程

    第 4 步会跑很久（ROS 2 desktop 约 2-3GB），默认后台执行并轮询日志。

.PARAMETER Detach
    第 4 步用 nohup 后台执行，只启动不等待。适合在 agent 里跑（命令有超时上限）。

.PARAMETER WaitMinutes
    第 4 步前台等待的上限（配合 -Detach:$false 使用）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\provision_vm.ps1 -Detach
#>
param(
    [string]$User     = "whc",
    [int]   $Port     = 2222,
    [string]$Password = "whc2026",
    [string]$TimeZone = "Asia/Shanghai",
    [string]$RepoRoot = "",
    [switch]$Detach,
    [switch]$SkipUpload,
    [switch]$SkipGuestInstall
)

$ErrorActionPreference = "Continue"

function Write-Step($n, $t) { Write-Host "`n=== $n. $t ===" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Write-Err2($m) { Write-Host "  [X]  $m" -ForegroundColor Red }

if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$Target = "$User@127.0.0.1"
$SshOpts = @("-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=NUL","-o","ConnectTimeout=15","-p","$Port")

function Invoke-Guest([string]$Command, [switch]$Quiet) {
    $out = & ssh @SshOpts $Target $Command 2>&1 | Out-String
    if (-not $Quiet) { Write-Host $out.TrimEnd() }
    return $out
}

# ---------------------------------------------------------------- 1. 等待 SSH
Write-Step 1 "等待 guest SSH 就绪"
$ready = $false
for ($i = 1; $i -le 90; $i++) {
    $out = & ssh @SshOpts -o BatchMode=yes $Target 'echo WHC_READY' 2>&1 | Out-String
    if ($out -match 'WHC_READY') { $ready = $true; break }
    if ($i % 3 -eq 0) { Write-Host "  ...第 $i 次探测（$([int]($i*20/60)) 分钟）" -ForegroundColor DarkGray }
    Start-Sleep -Seconds 20
}
if (-not $ready) { Write-Err2 "30 分钟内 SSH 未就绪。检查 VM 是否在运行、是否还在安装。"; exit 1 }
Write-Ok "SSH 已就绪：$(Invoke-Guest 'uname -sr' -Quiet)"

# ---------------------------------------------------------------- 2. sudo + 时区
Write-Step 2 "配置免密 sudo 与时区"
# 必须用【单引号】here-string：双引号版本会让 PowerShell 抢先展开 $(...)，
# 结果是 timedatectl / free / df 被当成 PowerShell 命令执行并报
# "not recognized as the name of a cmdlet"。需要注入的值用占位符替换。
$bootstrap = @'
set -e
if [ ! -f /etc/sudoers.d/90-whc ]; then
  echo '__PASSWORD__' | sudo -S sh -c 'printf "%s\n" "whc ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-whc && chmod 440 /etc/sudoers.d/90-whc'
  echo "[ok] 已写入 /etc/sudoers.d/90-whc"
else
  echo "[ok] 免密 sudo 已存在"
fi
sudo timedatectl set-timezone __TZ__ 2>/dev/null || true
echo "[ok] 时区        = $(timedatectl show -p Timezone --value)"
echo "[ok] 内存        = $(free -h | awk '/^Mem:/{print $2}')"
echo "[ok] 根分区可用  = $(df -h / | awk 'NR==2{print $4}')"
echo "[ok] CPU 核心    = $(nproc)"
'@
$bootstrap = $bootstrap.Replace('__PASSWORD__', $Password).Replace('__TZ__', $TimeZone)
$bootstrap | & ssh @SshOpts $Target 'bash -s' 2>&1 | ForEach-Object { Write-Host "  $_" }

# ---------------------------------------------------------------- 3. 上传
if (-not $SkipUpload) {
    Write-Step 3 "上传 tools 与 src 到 guest:~/whc_setup"
    Invoke-Guest 'mkdir -p ~/whc_setup' -Quiet | Out-Null
    foreach ($sub in @('tools','src')) {
        $local = Join-Path $RepoRoot $sub
        if (-not (Test-Path $local)) { Write-Warn2 "跳过不存在的 $local"; continue }
        Write-Host "  上传 $sub ..." -ForegroundColor Gray
        & scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -P $Port -r $local "${Target}:~/whc_setup/" 2>&1 |
            ForEach-Object { if ($_ -notmatch '^\s*$') { Write-Host "    $_" -ForegroundColor DarkGray } }
        if ($LASTEXITCODE -ne 0) { Write-Err2 "scp $sub 失败"; exit 1 }
    }
    Invoke-Guest 'chmod +x ~/whc_setup/tools/*.sh 2>/dev/null; ls ~/whc_setup ~/whc_setup/tools | head -30'
    Write-Ok "上传完成"
} else {
    Write-Step 3 "跳过上传（-SkipUpload）"
}

# ---------------------------------------------------------------- 4. guest 安装
if ($SkipGuestInstall) { Write-Step 4 "跳过 guest 安装（-SkipGuestInstall）"; exit 0 }

Write-Step 4 "在 guest 内安装桌面 + ROS 2 Jazzy + Gazebo，并编译工程"
$runner = @'
cd ~/whc_setup
nohup sudo bash tools/setup_vm_guest.sh > ~/guest_setup.log 2>&1 &
echo "PID=$!"
'@

if ($Detach) {
    $out = $runner | & ssh @SshOpts $Target 'bash -s' 2>&1 | Out-String
    Write-Host $out.TrimEnd()
    Write-Ok "已在 guest 后台启动，日志：~/guest_setup.log"
    Write-Host ""
    Write-Host "查看进度：" -ForegroundColor White
    Write-Host "    ssh -p $Port $User@127.0.0.1 'tail -f ~/guest_setup.log'" -ForegroundColor Yellow
    exit 0
}

# 前台：启动后轮询日志
$runner | & ssh @SshOpts $Target 'bash -s' 2>&1 | Out-Null
$deadline = (Get-Date).AddMinutes(90)
$lastLine = ""
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 30
    $status = Invoke-Guest 'pgrep -f setup_vm_guest.sh >/dev/null && echo RUNNING || echo FINISHED' -Quiet
    $tail = Invoke-Guest 'tail -n 1 ~/guest_setup.log' -Quiet
    if ($tail.Trim() -ne $lastLine) { Write-Host "  $($tail.Trim())" -ForegroundColor Gray; $lastLine = $tail.Trim() }
    if ($status -match 'FINISHED') { break }
}
Invoke-Guest 'tail -n 40 ~/guest_setup.log'
