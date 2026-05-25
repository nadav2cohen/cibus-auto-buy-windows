<#
.SYNOPSIS
  Cibus run notifier for Windows (v1.2 - ntfy.sh).

.DESCRIPTION
  Sends a phone push via ntfy.sh and a Windows toast.

  On Microsoft-managed tenants every "send email/Teams as the user"
  channel is blocked:
    - Power Automate "TeamsWebhookRequestReceived" trigger -> DLP block.
    - Outlook COM -> Monarch "new Outlook" has no COM interface.
    - Microsoft Graph PowerShell SDK -> AADSTS90094 admin consent block.
    - Az.Accounts -> Conditional Access forces interactive MFA per
      Graph token request, incompatible with scheduled tasks.

  ntfy.sh sidesteps all of that: it's an external push service, no
  account, no auth, no Microsoft tenant surface.

.PARAMETER Message
  The notification body. First line becomes the toast title and the
  ntfy "Title" header.

.NOTES
  Requires CIBUS_NTFY_TOPIC in the environment (loaded from .env by
  thursday-*.ps1). Topic is a "secret URL" - anyone with the topic
  name can read your messages, so keep it long and random.

  Optional:
    CIBUS_NTFY_SERVER   override base URL (default https://ntfy.sh)
    CIBUS_NTFY_PRIORITY ntfy priority 1-5 (default 3)
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

# --- Toast (best effort, never blocks the push) ------------------------
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

# --- ntfy.sh push -------------------------------------------------------
$topic = $env:CIBUS_NTFY_TOPIC
if (-not $topic) {
  Write-Warning "CIBUS_NTFY_TOPIC not set in environment - skipping phone push."
} else {
  $server = if ($env:CIBUS_NTFY_SERVER) { $env:CIBUS_NTFY_SERVER.TrimEnd('/') } else { 'https://ntfy.sh' }
  $priority = if ($env:CIBUS_NTFY_PRIORITY) { $env:CIBUS_NTFY_PRIORITY } else { '3' }

  # Pick an emoji tag based on the first character of the title.
  $tag = switch -Regex ($title) {
    '(✅|🟢)' { 'white_check_mark'; break }
    '(⚠️|🟡)' { 'warning'; break }
    '(❌|🔴)' { 'x'; break }
    default { 'shopping_cart' }
  }

  # ntfy requires HTTP headers to be pure ASCII. Strip emoji / non-ASCII
  # from the Title header but keep the original UTF-8 text in the body.
  $titleAscii = ($title -replace '[^\x20-\x7E]', '').Trim()
  $titleAscii = ($titleAscii -replace '\s+', ' ')
  if (-not $titleAscii) { $titleAscii = 'Cibus' }
  if ($titleAscii.Length -gt 250) { $titleAscii = $titleAscii.Substring(0, 250) }

  try {
    Invoke-RestMethod -Method POST -Uri "$server/$topic" `
      -Body $Message -ContentType 'text/plain; charset=utf-8' `
      -Headers @{
        Title    = $titleAscii
        Priority = $priority
        Tags     = $tag
      } | Out-Null
    Write-Host "[notify] ntfy push sent (topic=$topic, tag=$tag, title='$titleAscii')."
  } catch {
    Write-Warning "ntfy push failed: $_"
  }
}

Write-Host "[notify] sent: $title"


