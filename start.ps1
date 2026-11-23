# Start the RT-FAAS website at http://127.0.0.1:8000
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}
& .\.venv\Scripts\python -m pip install -q -r backend\requirements.txt

Write-Host "Website  http://127.0.0.1:8000"
Write-Host "API docs http://127.0.0.1:8000/docs"
& .\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --reload --port 8000
