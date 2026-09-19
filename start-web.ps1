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
Push-Location $projectRoot
try {
    Write-Host "Interview Studio: http://127.0.0.1:$Port"
    & $pythonPath -m uvicorn backend.app:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
