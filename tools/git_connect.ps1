<#
.SYNOPSIS
    验证并建立 D:\ai-sfjs 与 GitHub 仓库的可推送连接。

.DESCRIPTION
    按顺序做四件事，任一步失败都会明确告诉你卡在哪、下一步该做什么：

      1. 定位 git 与 ssh（Windows 原生 OpenSSH）
      2. 测试 SSH 认证（ssh -T git@github.com）
      3. 认证通过则 fetch 远程、建立分支追踪
      4. 用 `git push --dry-run` **只验证写权限，不真的推送**

    为什么用 SSH 而不是 HTTPS
    -------------------------
    本机对 github.com 的 HTTPS 是「TCP 能连、TLS 被重置」（实测 curl exit 56
    Connection was reset），而 22 端口的 SSH 握手正常。所以只有 SSH 这条路。

    为什么显式指定 GIT_SSH
    ----------------------
    git for Windows 走 SSH 时会调 MSYS 的 sh.exe，而 sh.exe 在这种受限环境下
    会因无法创建信号管道而启动失败（fatal error - couldn't create signal pipe）。
    把 GIT_SSH 指向 Windows 原生 ssh.exe 可让 git 直接 exec、不经 shell。
    你自己的普通终端里不需要这一步，但设了也无害。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\git_connect.ps1
#>
param(
    [string]$Repo   = "D:\ai-sfjs",
    [string]$Remote = "origin",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Continue"

function Write-Step($n, $t) { Write-Host "`n=== $n. $t ===" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Write-Err2($m) { Write-Host "  [X]  $m" -ForegroundColor Red }

# ---------------------------------------------------------------- 1. 工具
Write-Step 1 "定位 git 与 ssh"

$git = @(
    "$env:LOCALAPPDATA\Programs\PortableGit\cmd\git.exe",
    "C:\Program Files\Git\cmd\git.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $git) {
    $g = Get-Command git -ErrorAction SilentlyContinue
    if ($g) { $git = $g.Source }
}
if (-not $git) { Write-Err2 "找不到 git.exe"; exit 1 }
Write-Ok "git: $git"

$sshExe = @(
    "C:\WINDOWS\System32\OpenSSH\ssh.exe",
    "$env:ProgramFiles\OpenSSH\ssh.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($sshExe) {
    # 让 git 直接 exec 原生 ssh.exe，绕开 MSYS sh.exe
    $env:GIT_SSH = $sshExe
    Write-Ok "GIT_SSH -> $sshExe"
} else {
    Write-Warn2 "没找到 Windows 原生 ssh.exe，沿用 git 自带 ssh"
}
Remove-Item Env:\GIT_SSH_COMMAND -ErrorAction SilentlyContinue
$env:GIT_TERMINAL_PROMPT = "0"

if (-not (Test-Path (Join-Path $Repo ".git"))) { Write-Err2 "$Repo 不是 git 仓库"; exit 1 }
& $git -C $Repo remote -v | ForEach-Object { Write-Host "  $_" }

# ---------------------------------------------------------------- 2. 认证
Write-Step 2 "测试 SSH 认证"
$authOut = & ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=20 -T git@github.com 2>&1 | Out-String
$authed = $authOut -match 'successfully authenticated'
if ($authed) {
    Write-Ok $authOut.Trim()
} else {
    Write-Err2 ($authOut.Trim() -split "`n" | Select-Object -Last 1)
}

if (-not $authed) {
    $pubPath = "$env:USERPROFILE\.ssh\id_ed25519.pub"
    $pub = if (Test-Path $pubPath) { (Get-Content $pubPath -Raw).Trim() } else { "(找不到 $pubPath)" }
    $fpr = if (Test-Path $pubPath) { (& ssh-keygen -l -f $pubPath 2>&1) -join "" } else { "" }

    Write-Host ""
    Write-Host "  认证未通过 —— 唯一原因是这个公钥还没登记到 GitHub。" -ForegroundColor Yellow
    Write-Host "  （网络侧没问题：github.com:22 的 TCP 与 SSH 握手都是通的）" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  ── 公钥（复制这一整行） ──" -ForegroundColor White
    Write-Host "  $pub" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  指纹供核对: $fpr" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  两种加法，取决于你和仓库的关系：" -ForegroundColor White
    Write-Host "    A) 仓库是别人的 -> 请主人加 Deploy key（勾 Allow write access）" -ForegroundColor Gray
    Write-Host "       https://github.com/Chen313424/WHC/settings/keys" -ForegroundColor Gray
    Write-Host "    B) 是你自己的账号 -> 加到你账号的 SSH keys，并确认对该仓库有写权限" -ForegroundColor Gray
    Write-Host "       https://github.com/settings/keys" -ForegroundColor Gray
    Write-Host ""
    Write-Warn2 "本机无法打开 github.com 网页（HTTPS 被重置），需换设备/网络完成这一步。"
    exit 1
}

# ---------------------------------------------------------------- 3. fetch
Write-Step 3 "拉取远程并建立分支追踪"
& $git -C $Repo fetch $Remote --prune 2>&1 | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) { Write-Err2 "fetch 失败"; exit 1 }
& $git -C $Repo branch -r 2>&1 | ForEach-Object { Write-Host "  远程分支: $_" }

$hasLocalCommits = (& $git -C $Repo rev-list --all --count 2>&1) -ne "0"
if (-not $hasLocalCommits) {
    Write-Warn2 "本地还没有任何提交。远程有内容的话，用下面命令接管远程历史："
    Write-Host "    git -C $Repo reset --hard $Remote/$Branch" -ForegroundColor Yellow
} else {
    & $git -C $Repo branch --set-upstream-to="$Remote/$Branch" $Branch 2>&1 | ForEach-Object { Write-Host "  $_" }
}

# ---------------------------------------------------------------- 4. 写权限
Write-Step 4 "验证推送权限（--dry-run，不会真的推送）"
$dry = & $git -C $Repo push --dry-run $Remote HEAD:$Branch 2>&1 | Out-String
Write-Host ($dry.Trim() -split "`n" | ForEach-Object { "  $_" } | Out-String)
if ($dry -match 'Permission denied|denied to|403|not authorized') {
    Write-Err2 "认证通过但没有写权限 —— 密钥需要以「Allow write access」的 Deploy key 添加，或账号需被设为 collaborator。"
    exit 2
}
Write-Ok "写权限验证通过，可以直接 git push"
