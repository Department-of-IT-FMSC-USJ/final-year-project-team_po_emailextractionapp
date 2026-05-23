# Start the API and Streamlit frontend in two separate PowerShell windows.
#
# Usage:  .\run.ps1
# Stop:   close the two windows, or Ctrl+C in each.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Python venv not found at $python. Create it first: python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -e '.[dev]'"
    exit 1
}

Write-Host "Starting API on http://127.0.0.1:8000 ..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$root'; & '$python' -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload"
) -WindowStyle Normal

Start-Sleep -Seconds 2

Write-Host "Starting Streamlit on http://127.0.0.1:8501 ..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$root'; & '$python' -m streamlit run apps/streamlit_app/main.py --server.port 8501 --server.address 127.0.0.1"
) -WindowStyle Normal

Write-Host ""
Write-Host "Both servers starting. Open http://127.0.0.1:8501 in your browser."
Write-Host "To stop them, close the two PowerShell windows or press Ctrl+C in each."
