# One-command launch of Local Production Mode (Windows PowerShell).
#   powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1 --state-dir D:\secure\wash_sync_state
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$python = if ($env:PYTHON) { $env:PYTHON } else { "py" }
$pyArgs = if ($python -eq "py") { @("-3.12") } else { @() }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment (.venv)..."
    & $python @pyArgs -m venv .venv
}
$venvPy = ".venv\Scripts\python.exe"
& $venvPy -c "import wash_sync, fastapi" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing the engine..."
    & $venvPy -m pip install --quiet --upgrade pip
    & $venvPy -m pip install --quiet -e ".[server]"
}
if (-not (Test-Path "apps\web\dist-local\index.html")) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "Building the local web interface (one time)..."
        npm --prefix apps/web ci --no-audit --no-fund
        npm --prefix apps/web run build:local
    } else {
        Write-Warning "npm not found - the engine and CLI work, but the web interface is not built."
    }
}
& $venvPy -m wash_sync serve @args
