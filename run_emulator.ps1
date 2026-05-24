$ErrorActionPreference = "Stop"

Write-Host "Checking and installing Python dependencies (flask, winrt modules)..." -ForegroundColor Cyan
python -m pip install flask winrt-runtime winrt-Windows.Devices.Bluetooth winrt-Windows.Storage

Write-Host "Starting Muse S Athena EEG BLE Emulator..." -ForegroundColor Green
Write-Host "Press Ctrl+C in this terminal to stop the emulator." -ForegroundColor Yellow
python tools\emulate_muse.py
