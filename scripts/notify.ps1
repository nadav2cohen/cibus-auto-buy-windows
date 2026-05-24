<#
.SYNOPSIS
  Cibus run notifier for Windows.

.DESCRIPTION
  Sends a Windows toast notification (via BurntToast if installed,
  otherwise falls back to a balloon tip) and, when CIBUS_TEAMS_WEBHOOK
  is set in the environment, POSTs the same text to a Teams incoming
  webhook so the user can see it on their phone.

.PARAMETER Message
  The notification body. First line is used as the toast title.

.EXAMPLE
  .\notify.ps1 "✅ Cibus Thursday: purchase complete."
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string] $Message
)

$ErrorActionPreference = 'Continue'

$lines = $Message -split "`r?`n"
$title = if ($lines.Count -gt 0 -and $lines[0].Trim()) { $lines[0] } else { 'Cibus' }
$body  = if ($lines.Count -gt 1) { ($lines | Select-Object -Skip 1) -join "`n" } else { '' }

# --- Toast ---------------------------------------------------------------
$toastSent = $false
if (Get-Module -ListAvailable -Name BurntToast) {
  try {
    Import-Module BurntToast -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace($body)) {
      New-BurntToastNotification -Text $title | Out-Null
    } else {
      New-BurntToastNotification -Text $title, $body | Out-Null
    }
    $toastSent = $true
  } catch {
    Write-Warning "BurntToast failed: $_"
  }
}

if (-not $toastSent) {
  try {
    Add-Type -AssemblyName System.Windows.Forms
    $ni = New-Object System.Windows.Forms.NotifyIcon
    $ni.Icon = [System.Drawing.SystemIcons]::Information
    $ni.BalloonTipTitle = $title
    $ni.BalloonTipText  = if ($body) { $body } else { $title }
    $ni.Visible = $true
    $ni.ShowBalloonTip(8000)
    Start-Sleep -Seconds 9
    $ni.Dispose()
  } catch {
    Write-Warning "Balloon tip failed: $_"
  }
}

# --- Teams webhook (optional) -------------------------------------------
$webhook = $env:CIBUS_TEAMS_WEBHOOK
if ($webhook) {
  try {
    $payload = @{
      '@type'    = 'MessageCard'
      '@context' = 'https://schema.org/extensions'
      summary    = $title
      title      = $title
      text       = ($Message -replace "`r?`n", "  `n")
    } | ConvertTo-Json -Depth 5
    Invoke-RestMethod -Method POST -Uri $webhook -ContentType 'application/json' -Body $payload | Out-Null
    Write-Host "[notify] Teams webhook POSTed."
  } catch {
    Write-Warning "Teams webhook failed: $_"
  }
}

Write-Host "[notify] sent: $title"
