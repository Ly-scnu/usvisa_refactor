$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\start_backend.ps1") -WorkingDirectory $Root
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\start_frontend.ps1") -WorkingDirectory $Root

Write-Host "Backend:  http://127.0.0.1:18890/docs"
Write-Host "Frontend: http://127.0.0.1:18891"
