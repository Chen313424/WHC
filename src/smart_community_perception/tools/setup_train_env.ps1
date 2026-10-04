# 一键配置 YOLO 训练环境（Windows / PowerShell）
#
# 解决三个本机特有的坑：
#   1. RTX 5060 是 Blackwell (sm_120)，必须 CUDA 12.8+ 的 torch，
#      否则运行时报 "no kernel image is available for execution on the device"
#   2. github.com 在本机被重置，Ultralytics 默认的权重下载会失败
#      -> 改从 hf-mirror.com 拉 yolo11n.pt
#   3. PyPI 直连慢 -> 用清华镜像
#
# 用法（在 src/smart_community_perception 目录下）：
#   powershell -ExecutionPolicy Bypass -File tools\setup_train_env.ps1
#
# 可选参数：
#   -VenvDir  <路径>     虚拟环境位置（默认 ..\.venv-train）
#   -PythonExe python    指定 Python 解释器（建议 3.11/3.12；3.13 也可但 wheel 较少）
#   -TorchIndex <url>    自定义 torch wheel 源

param(
    [string]$VenvDir = "",
    [string]$PythonExe = "python",
    [string]$TorchIndex = "https://download.pytorch.org/whl/cu128",
    [switch]$SkipWeights
)

$ErrorActionPreference = "Stop"

if (-not $VenvDir) {
    $VenvDir = Join-Path (Split-Path -Parent $PSScriptRoot) ".venv-train"
}
$PipMirror = "https://pypi.tuna.tsinghua.edu.cn/simple"
$WeightUrl = "https://hf-mirror.com/Ultralytics/YOLO11/resolve/main/yolo11n.pt"
$ModelsDir = Join-Path (Split-Path -Parent $PSScriptRoot) "models"

Write-Host "=== 1/5 检查 Python ===" -ForegroundColor Cyan
& $PythonExe --version
if ($LASTEXITCODE -ne 0) { throw "找不到 Python，请先安装并确保在 PATH 中" }

Write-Host "`n=== 2/5 创建虚拟环境: $VenvDir ===" -ForegroundColor Cyan
if (-not (Test-Path $VenvDir)) {
    & $PythonExe -m venv $VenvDir
}
$Vpy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $Vpy)) { throw "虚拟环境创建失败：$Vpy 不存在" }

Write-Host "`n=== 3/5 升级 pip（清华镜像）===" -ForegroundColor Cyan
& $Vpy -m pip install -U pip -i $PipMirror

Write-Host "`n=== 4/5 安装 PyTorch (CUDA 12.8 / Blackwell) ===" -ForegroundColor Cyan
Write-Host "源: $TorchIndex"
& $Vpy -m pip install torch torchvision --index-url $TorchIndex
if ($LASTEXITCODE -ne 0) {
    Write-Host "[警告] 主源安装失败，尝试国内镜像 mirrors.aliyun.com" -ForegroundColor Yellow
    & $Vpy -m pip install torch torchvision `
        --index-url "https://mirrors.aliyun.com/pytorch-wheels/cu128/"
}
if ($LASTEXITCODE -ne 0) {
    throw "PyTorch 安装失败。请手动安装 CUDA 12.8+ 版本的 torch 后重跑本脚本。"
}

Write-Host "`n=== 5/5 安装 Ultralytics 等依赖 ===" -ForegroundColor Cyan
& $Vpy -m pip install -U -r (Join-Path $PSScriptRoot "requirements-train.txt") -i $PipMirror

Write-Host "`n=== 验证 CUDA ===" -ForegroundColor Cyan
& $Vpy -c "import torch; print('torch', torch.__version__); print('cuda available:', torch.cuda.is_available()); print('device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only'); print('capability:', torch.cuda.get_device_capability(0) if torch.cuda.is_available() else '-')"

if (-not $SkipWeights) {
    Write-Host "`n=== 下载预训练权重（hf-mirror，绕开被阻断的 github.com）===" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
    $dst = Join-Path $ModelsDir "yolo11n.pt"
    if (Test-Path $dst) {
        Write-Host "已存在，跳过: $dst"
    } else {
        try {
            Invoke-WebRequest -Uri $WeightUrl -OutFile $dst -TimeoutSec 300
            $sizeMB = [math]::Round((Get-Item $dst).Length / 1MB, 2)
            Write-Host "已下载 yolo11n.pt ($sizeMB MB) -> $dst" -ForegroundColor Green
        } catch {
            Write-Host "[警告] 权重下载失败：$_" -ForegroundColor Yellow
            Write-Host "       不影响训练：tools/train_yolo.py 会自动回退到从零训练。" -ForegroundColor Yellow
        }
    }
}

Write-Host "`n配置完成。" -ForegroundColor Green
Write-Host "激活环境： & '$VenvDir\Scripts\Activate.ps1'"
Write-Host "开始训练： & '$Vpy' tools\train_yolo.py --data <数据集>\data.yaml --weights '$ModelsDir\yolo11n.pt'"
