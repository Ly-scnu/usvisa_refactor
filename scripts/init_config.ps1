$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Pairs = @(
  @{ Example = "config\app.example.toml";      Target = "config\app.toml" },
  @{ Example = "config\accounts.example.toml"; Target = "config\accounts.toml" },
  @{ Example = "config\proxy.example.toml";    Target = "config\proxy.toml" }
)

foreach ($p in $Pairs) {
  $example = Join-Path $Root $p.Example
  $target = Join-Path $Root $p.Target
  if (-not (Test-Path -LiteralPath $example)) { throw "Missing example config: $($p.Example)" }
  if (Test-Path -LiteralPath $target) {
    Write-Host "exists: $($p.Target)"
  } else {
    Copy-Item -LiteralPath $example -Destination $target
    Write-Host "created: $($p.Target)"
  }
}

Write-Host ""
Write-Host "Next: edit config\app.toml, config\accounts.toml, config\proxy.toml with your own values."
Write-Host "These real config files are gitignored and must not be committed."
