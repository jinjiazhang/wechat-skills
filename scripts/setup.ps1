$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & python -m venv (Join-Path $repoRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python virtual environment' }
}
& $venvPython -m pip install -e "${repoRoot}[media,send]"
if ($LASTEXITCODE -ne 0) { throw 'Failed to install dependencies' }
& $venvPython (Join-Path $PSScriptRoot 'check_runtime.py')
if ($LASTEXITCODE -ne 0) { throw 'Runtime check failed' }
Write-Host "Ready: $repoRoot/.venv/Scripts/wechat-records.exe"
