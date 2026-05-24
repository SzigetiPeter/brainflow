$ErrorActionPreference = "Stop"

Write-Host "Adding Android SDK CMake to PATH..."
$env:PATH = "C:\Users\szige\AppData\Local\Android\Sdk\cmake\3.22.1\bin;" + $env:PATH

Write-Host "Building BrainFlow AAR for VisualSnow App..."
python tools\build_android_aar.py --build-native --output "C:\Users\szige\Projects\VisualSnow\app\app\libs\brainflow-android.aar"

if ($LASTEXITCODE -eq 0) {
    Write-Host "Build successful! The AAR has been placed in your VisualSnow libs folder." -ForegroundColor Green
} else {
    Write-Host "Build failed with exit code $LASTEXITCODE." -ForegroundColor Red
}

Pause
