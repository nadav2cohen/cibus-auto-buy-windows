<#
.SYNOPSIS
  Thursday morning Cibus sentinel (Windows).

.DESCRIPTION
  Dry-run sanity check before the live purchase. Logs in, reads the
  balance, computes the voucher plan, then exits without buying. Sends
  a toast + Teams webhook (if configured) with the result so the user
  has time to fix anything before the 16:00 live run.
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
$log = Join-Path $LogDir "${ts}_sentinel.log"

if (-not (Test-Path $Repo)) {
  $msg = "🔴 Cibus sentinel: repo not found at $Repo"
  if (Test-Path $Notify) { & $Notify $msg }
  Write-Error $msg
  exit 2
}

Set-Location $Repo

$envFile = Join-Path $Repo '.env'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*#') { return }
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
      [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"').Trim("'"), 'Process')
    }
  }
}

$python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
  $msg = "🔴 Cibus sentinel: venv python missing at $python — run setup."
  if (Test-Path $Notify) { & $Notify $msg }
  Write-Error $msg
  exit 3
}

& $python 'cibus_daily_buy.py' '--dry-run' '--log-file' $log *>&1 | Tee-Object -FilePath $log -Append
$exit = $LASTEXITCODE

$budget = ''
$plan   = ''
if (Test-Path $log) {
  $b = Select-String -Path $log -Pattern 'Remaining budget' | Select-Object -Last 1
  $p = Select-String -Path $log -Pattern 'Voucher plan'    | Select-Object -Last 1
  if ($b) { $budget = $b.Line.Trim() }
  if ($p) { $plan   = $p.Line.Trim() }
}

if ($exit -eq 0) {
  $msg = @"
🟢 Cibus sentinel OK (Thu 08:00)
$budget
$plan
Live purchase scheduled 16:00.
"@
} else {
  $tail = if (Test-Path $log) { (Get-Content $log -Tail 15) -join "`n" } else { '(no log)' }
  $msg = @"
🔴 Cibus sentinel FAILED (Thu 08:00, exit=$exit)
Login or balance read broke. Manual check needed before 16:00.
Tail of log:
$tail
"@
}

if (Test-Path $Notify) { & $Notify $msg }
Write-Host $msg
exit $exit
