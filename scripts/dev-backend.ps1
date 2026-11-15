[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Run setup-local.ps1 first.' }
Push-Location (Join-Path $root 'backend')
try {
    & $python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
    if ($LASTEXITCODE -ne 0) { throw 'Local API stopped with an error.' }
} finally {
    Pop-Location
}
