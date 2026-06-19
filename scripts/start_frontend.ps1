$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location (Join-Path $Root "frontend")

if (-not (Test-Path node_modules)) {
  if (Test-Path package-lock.json) { npm ci } else { npm install }
}

$env:VITE_API_BASE = "http://127.0.0.1:18890"
npm run dev

