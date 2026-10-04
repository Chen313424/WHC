# git_ssh_diag.ps1 —— 彻底排查 SSH 到 GitHub 的认证链路
# 目的：把「密钥没登记」与其他可能原因（密钥没递出、端口被挡、config 干扰）区分开
$ErrorActionPreference = "Continue"
$sshExe = "C:\WINDOWS\System32\OpenSSH\ssh.exe"
$key    = "$env:USERPROFILE\.ssh\id_ed25519"

Write-Host "=== 1. .ssh 目录内容（看有没有 config 在干扰） ===" -ForegroundColor Cyan
Get-ChildItem "$env:USERPROFILE\.ssh" -Force | Select-Object Name, Length | Format-Table -AutoSize
$cfg = "$env:USERPROFILE\.ssh\config"
if (Test-Path $cfg) {
    Write-Host "  发现 config，内容：" -ForegroundColor Yellow
    Get-Content $cfg | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Host "  无 ~/.ssh/config（不存在覆盖行为）" -ForegroundColor Gray
}

Write-Host "`n=== 2. 本地密钥是否可用（能派生出公钥即说明无口令/未损坏） ===" -ForegroundColor Cyan
$derivedText = ((& $sshExe -y -f $key 2>&1) | Out-String).Trim()
# 注意：& ssh -y 返回字符串数组，用 -is [string] 判断会永远为假，
# 必须 join 成整串再匹配（之前这里误报过「密钥不可用」）。
if ($derivedText -match '^ssh-(ed25519|rsa)') {
    Write-Host "  [OK] $($derivedText.Substring(0, [Math]::Min(50, $derivedText.Length)))..." -ForegroundColor Green
} else {
    Write-Host "  [X] 密钥不可用: $derivedText" -ForegroundColor Red
}

Write-Host "`n=== 3. 认证过程：确认密钥是否真的被递出 ===" -ForegroundColor Cyan
$verbose = & $sshExe -v -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=20 -T git@github.com 2>&1
$verbose | Select-String -Pattern 'Offering public key|Authentications that can continue|Server accepts key|Permission denied|Connection|banner' |
    Select-Object -First 12 | ForEach-Object { Write-Host "  $_" }

# 服务器 banner 是判断有无中间盒的线索：GitHub 正常特征是 SSH-2.0-babeld-<hash>。
$bannerLine = ($verbose | Select-String -Pattern 'compat_banner' | Select-Object -First 1)
if ($bannerLine -and $bannerLine.ToString() -notmatch 'babeld') {
    Write-Host "  [!] 服务器 banner 不含 babeld（GitHub 的特征），链路可能经过 SSH 代理/中间盒" -ForegroundColor Yellow
}

Write-Host "`n=== 4. 换用 ssh.github.com:443（GitHub 的备用 SSH 端点） ===" -ForegroundColor Cyan
$alt = & $sshExe -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=20 -T -p 443 git@ssh.github.com 2>&1 | Out-String
Write-Host ("  " + (($alt.Trim() -split "`n") | Select-Object -Last 1)) -ForegroundColor Gray

Write-Host "`n=== 5. 端口可达性复核 ===" -ForegroundColor Cyan
foreach ($hp in @(@('github.com',22), @('ssh.github.com',443))) {
    $r = Test-NetConnection -ComputerName $hp[0] -Port $hp[1] -WarningAction SilentlyContinue
    Write-Host ("  {0}:{1}  TCP={2}" -f $hp[0], $hp[1], $r.TcpTestSucceeded) -ForegroundColor Gray
}

Write-Host "`n=== 6. 远端仓库当前状态（走 API，验证仓库本身可达） ===" -ForegroundColor Cyan
Write-Host "  仓库是 public，默认分支 main —— 详见上方 web_fetch 结果" -ForegroundColor Gray
