<#
.SYNOPSIS
  Register Windows Task Scheduler jobs for the Cibus sentinel and live
  purchase.

.DESCRIPTION
  Creates two weekly tasks under the current user:

    Cibus-Sentinel  → Thursdays 08:00, runs thursday-sentinel.ps1
    Cibus-Run       → Thursdays 16:00, runs thursday-run.ps1

  Both run powershell.exe with -ExecutionPolicy Bypass and use the
  current user's account (no SYSTEM, no password). They only run when
  the user is logged on; the Cibus Playwright session also requires an
  interactive desktop.

.PARAMETER ToolsRoot
  Folder holding ``thursday-sentinel.ps1`` and ``thursday-run.ps1``.
  Defaults to ``$env:USERPROFILE\Documents\cibus-tools``.

.PARAMETER SentinelTime
  HH:mm for the Thursday morning dry run. Default 08:00.

.PARAMETER RunTime
  HH:mm for the Thursday afternoon live run. Default 16:00.
#>
[CmdletBinding()]
param(
  [string] $ToolsRoot     = (Join-Path $env:USERPROFILE 'Documents\cibus-tools'),
  [string] $SentinelTime  = '08:00',
  [string] $RunTime       = '16:00'
)

$ErrorActionPreference = 'Stop'

$sentinel = Join-Path $ToolsRoot 'thursday-sentinel.ps1'
$live     = Join-Path $ToolsRoot 'thursday-run.ps1'

foreach ($p in @($sentinel, $live)) {
  if (-not (Test-Path $p)) { throw "Missing script: $p" }
}

function Register-CibusTask {
  param(
    [string] $Name,
    [string] $Script,
    [string] $Time
  )
  $action  = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory (Split-Path $Script -Parent)

  $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Thursday -At $Time

  $settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

  $principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

  Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
  Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal | Out-Null
  Write-Host "[task] registered $Name → $Script @ $Time (Thursdays)"
}

Register-CibusTask -Name 'Cibus-Sentinel' -Script $sentinel -Time $SentinelTime
Register-CibusTask -Name 'Cibus-Run'      -Script $live     -Time $RunTime

Write-Host ""
Write-Host "Done. Inspect with:  Get-ScheduledTask Cibus-*"
Write-Host "Test now with:        Start-ScheduledTask -TaskName Cibus-Sentinel"
