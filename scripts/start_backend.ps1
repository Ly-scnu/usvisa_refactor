$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Test-Path config\app.toml) -or -not (Test-Path config\accounts.toml) -or -not (Test-Path config\proxy.toml)) {
  Write-Host "Local config files are missing; creating them from examples..."
  & powershell -ExecutionPolicy Bypass -File .\scripts\init_config.ps1
}

$env:PYTHONPATH = Join-Path $Root "backend"
python .\backend\run_api.py
