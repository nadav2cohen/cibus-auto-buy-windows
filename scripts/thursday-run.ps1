<#
.SYNOPSIS
  Thursday Cibus live purchase (Windows).

.DESCRIPTION
  Activates the local venv, runs cibus_daily_buy in LIVE mode, captures
  a one-line summary from the log, and dispatches a toast + optional
  Teams webhook via notify.ps1. Intended to be called by Windows Task
  Scheduler.

.PARAMETER ToolsRoot
  Root folder containing both ``cibus-daily-buy`` and ``notify.ps1``.
  Defaults to ``$env:USERPROFILE\Documents\cibus-tools``.
#>
[CmdletBinding()]
param(
  [string] $ToolsRoot = (Join-Path $env:USERPROFILE 'Documents\cibus-tools')
)

$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false

$Repo   = Join-Path $ToolsRoot 'cibus-daily-buy'
$Notify = Join-Path $ToolsRoot 'notify.ps1'
$LogDir = Join-Path $Repo 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$ts  = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = Join-Path $LogDir "${ts}_thursday.log"

if (-not (Test-Path $Repo)) {
  $msg = "⚠️ Cibus Thursday: repo not found at $Repo"
  if (Test-Path $Notify) { & $Notify $msg }
  Write-Error $msg
  exit 2
}

Set-Location $Repo

# Load .env into the process environment so CIBUS_* vars reach Python.
$envFile = Join-Path $Repo '.env'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*#') { return }
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
      $name  = $matches[1]
      $value = $matches[2].Trim('"').Trim("'")
      [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
  }
}

$python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
  $msg = "⚠️ Cibus Thursday: venv python missing at $python — run setup."
  if (Test-Path $Notify) { & $Notify $msg }
  Write-Error $msg
  exit 3
}

& $python 'cibus_daily_buy.py' '--log-file' $log *>&1 | Tee-Object -FilePath $log -Append
$exit = $LASTEXITCODE

$summary = ''
if (Test-Path $log) {
  $summary = (Select-String -Path $log -Pattern 'Remaining budget|Voucher plan|Added .|Purchase completed|Fatal error|Budget too low' |
              Select-Object -Last 10 |
              ForEach-Object { $_.Line }) -join "`n"
}

if ($exit -eq 0) {
  $msg = "✅ Cibus Thursday: purchase complete.`n$summary"
} else {
  $msg = "⚠️ Cibus Thursday: failed (exit=$exit).`n$summary"
}

if (Test-Path $Notify) { & $Notify $msg }
Write-Host $msg
exit $exit
