$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Api = 'http://127.0.0.1:18890'
$Py = (Get-Command python).Source
$LogDir = Join-Path $Root 'storage\logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir 'keep_10_slots.log'
$BadRouteChecks = 0
$BadConfigChecks = 0
$ApiFailChecks = 0
function Log($m) { Add-Content -Path $Log -Value ("{0} {1}" -f (Get-Date -Format s), $m) }
function ApiProcessRunning {
  $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match [regex]::Escape($Root) -and $_.CommandLine -match 'backend\\run_api.py|backend/run_api.py|run_api.py' }
  return [bool]$procs
}
function StartApi {
  if (ApiProcessRunning) { return }
  $out = Join-Path $LogDir 'api_18890_stdout.log'
  $err = Join-Path $LogDir 'api_18890_stderr.log'
  Log 'API down confirmed; starting run_api.py'
  Start-Process -FilePath $Py -ArgumentList '.\backend\run_api.py' -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
}
function AccountGuardActive {
  try {
    $guardPath = Join-Path $Root 'storage\runtime\account_guard.json'
    if (-not (Test-Path $guardPath)) { return $false }
    $g = Get-Content -LiteralPath $guardPath -Raw | ConvertFrom-Json
    if (-not [bool]$g.active) { return $false }
    if ($g.block_until) {
      $until = [datetimeoffset]::Parse([string]$g.block_until)
      if ($until -le [datetimeoffset]::Now) { return $false }
    }
    return $true
  } catch {
    return $false
  }
}
Log 'keep_10_slots watchdog started; consecutive-check mode enabled'
while ($true) {
  try {
    if (AccountGuardActive) {
      $BadRouteChecks = 0; $BadConfigChecks = 0; $ApiFailChecks = 0
      Log 'account guard active; skip 10-slot restart checks'
      Start-Sleep -Seconds 15
      continue
    }
    $s = Invoke-RestMethod -Uri "$Api/api/system/status" -TimeoutSec 8
    $ApiFailChecks = 0
    $running = [bool]$s.system.pipeline.running
    $desired = [int]$s.sla_orchestrator.decision.desired_active_slots
    $min = [int]$s.sla_orchestrator.decision.min_slots
    $max = [int]$s.sla_orchestrator.decision.max_slots
    $routes = @($s.slots | Where-Object { $_.route -or $_.proxy_display }).Count

    if (-not $running) {
      Log "pipeline not running; immediate restart"
      try { Invoke-RestMethod -Method Post -Uri "$Api/api/config/reload" -TimeoutSec 15 | Out-Null } catch { Log "reload failed: $($_.Exception.Message)" }
      try { Invoke-RestMethod -Method Post -Uri "$Api/api/pipeline/restart" -TimeoutSec 35 | Out-Null; Log 'pipeline restart requested' } catch { Log "pipeline restart failed: $($_.Exception.Message)" }
      $BadRouteChecks = 0; $BadConfigChecks = 0
    } else {
      if ($desired -ne 10 -or $min -ne 10 -or $max -ne 10) { $BadConfigChecks++ } else { $BadConfigChecks = 0 }
      # route count can be transiently 0 while snapshots are being rewritten or all slots rotate rounds;
      # require 3 consecutive bad checks (~45s) before restarting to avoid killing hot sessions.
      if ($routes -lt 10) { $BadRouteChecks++ } else { $BadRouteChecks = 0 }
      if ($BadConfigChecks -ge 2 -or $BadRouteChecks -ge 3) {
        Log "restart condition confirmed: running=$running desired=$desired min=$min max=$max routes=$routes badConfig=$BadConfigChecks badRoutes=$BadRouteChecks"
        try { Invoke-RestMethod -Method Post -Uri "$Api/api/config/reload" -TimeoutSec 15 | Out-Null } catch { Log "reload failed: $($_.Exception.Message)" }
        try { Invoke-RestMethod -Method Post -Uri "$Api/api/pipeline/restart" -TimeoutSec 35 | Out-Null; Log 'pipeline restart requested' } catch { Log "pipeline restart failed: $($_.Exception.Message)" }
        $BadRouteChecks = 0; $BadConfigChecks = 0
      } elseif ($BadConfigChecks -gt 0 -or $BadRouteChecks -gt 0) {
        Log "soft anomaly only: desired=$desired min=$min max=$max routes=$routes badConfig=$BadConfigChecks badRoutes=$BadRouteChecks; no restart yet"
      }
    }
  } catch {
    $ApiFailChecks++
    Log "API status failed#${ApiFailChecks}: $($_.Exception.Message)"
    if ($ApiFailChecks -ge 3) { StartApi; $ApiFailChecks = 0 }
  }
  Start-Sleep -Seconds 15
}



