param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Missing .venv. Run: python -m venv .venv; then install requirements-web.txt.'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) {
    throw 'Frontend not built. Run npm install and npm run build inside frontend.'
}
# 语音识别首次使用要从 Hugging Face 下载模型；国内直连通常会卡到超时，看起来像卡死。
# 默认走镜像，想用官方源就自己先设 HF_ENDPOINT。
if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = 'https://hf-mirror.com' }

Push-Location $projectRoot
try {
    Write-Host "Interview Studio: http://127.0.0.1:$Port"
    & $pythonPath -m uvicorn backend.app:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
