param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $pythonPath)) { throw 'Install backend dependencies first; see backend/README.md.' }
& $pythonPath (Join-Path $PSScriptRoot 'backend\run_server.py') --port $Port
exit $LASTEXITCODE
