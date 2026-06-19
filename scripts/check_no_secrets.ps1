$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

# High-signal secret patterns. Avoid storing real historical secrets in this script.
$patterns = @(
  'cf_clearance',
  'ASP\.NET_SessionId',
  '__cf_bm',
  'gho_[A-Za-z0-9_]+',
  'github_pat_[A-Za-z0-9_]+',
  '-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
  '\b1[3-9]\d{9}\b',  'USER\d{5,}'
)

$scanArgs = @(
  '-n',
  '--glob', '!frontend/node_modules/**',
  '--glob', '!frontend/dist/**',
  '--glob', '!storage/**',
  '--glob', '!config/app.toml',
  '--glob', '!config/accounts.toml',
  '--glob', '!config/proxy.toml',
  '--glob', '!scripts/check_no_secrets.ps1',
  ($patterns -join '|'),
  '.'
)

Write-Host "[1/3] scanning high-signal sensitive patterns..."
& rg @scanArgs
if ($LASTEXITCODE -eq 0) { throw "Potential sensitive content found. Review output above." }
if ($LASTEXITCODE -gt 1) { throw "ripgrep failed with code $LASTEXITCODE" }

Write-Host "[2/3] checking gitignored local secrets..."
if (Test-Path .git) {
  $mustIgnore = @('config/app.toml', 'config/accounts.toml', 'config/proxy.toml', 'storage')
  foreach ($item in $mustIgnore) {
    if (Test-Path -LiteralPath (Join-Path $Root $item)) {
      & git check-ignore -q -- $item 2>$null
      if ($LASTEXITCODE -ne 0) { throw "Not ignored: $item" }
      Write-Host "ignored: $item"
    }
  }
} else {
  Write-Host "No .git directory yet; skip git check-ignore. Run git init and rerun before push."
}

Write-Host "[3/3] git dry-run add list..."
if (Test-Path .git) {
  & git add -n .
} else {
  Write-Host "No .git directory yet; run 'git init' then rerun this script for a tracked-file dry run."
}

Write-Host "secret check passed"

