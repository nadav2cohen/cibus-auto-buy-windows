<#
.SYNOPSIS
  Cibus run notifier for Windows (Outlook email + optional toast).

.DESCRIPTION
  Sends the run summary as an Outlook email to the current user via
  Outlook desktop COM automation. This path works on Microsoft-managed
  tenants where the Teams "incoming webhook" trigger is blocked by DLP.

  Also fires a Windows toast (BurntToast if available, otherwise a
  classic balloon tip) for visibility when the user is at the PC.

.PARAMETER Message
  The notification body. First line is used as the toast title and the
  email subject.

.NOTES
  Requires Outlook desktop (any Microsoft 365 / Office 2016+) signed in
  as the user. No app registration, no Graph token, no DLP-restricted
  connectors involved. If Outlook is closed, COM will launch it.

  To override the recipient (default: the Outlook profile's own SMTP),
  set $env:CIBUS_NOTIFY_EMAIL.
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

# --- Toast (best effort, never blocks email) ----------------------------
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

# --- Outlook email (the actual phone push) ------------------------------
try {
  $olOk = $false
  try {
    $outlook = New-Object -ComObject Outlook.Application
    $olOk = $true
  } catch {
    Write-Warning "Could not start Outlook COM: $_"
  }

  if ($olOk) {
    $ns = $outlook.GetNamespace('MAPI')
    # Determine sender (and default recipient) from the active Outlook profile.
    $defaultAddr = $null
    try {
      $defaultAddr = $ns.Accounts.Item(1).SmtpAddress
    } catch {
      try { $defaultAddr = $ns.CurrentUser.AddressEntry.GetExchangeUser().PrimarySmtpAddress } catch { }
    }

    $to = if ($env:CIBUS_NOTIFY_EMAIL) { $env:CIBUS_NOTIFY_EMAIL } else { $defaultAddr }
    if (-not $to) {
      Write-Warning "Could not resolve a recipient from Outlook; set CIBUS_NOTIFY_EMAIL in .env"
    } else {
      $mail = $outlook.CreateItem(0)   # olMailItem
      $mail.Subject  = "[Cibus] $title"
      $mail.To       = $to
      $htmlBody  = "<pre style='font-family:Consolas,monospace;font-size:13px;white-space:pre-wrap'>"
      $htmlBody += [System.Net.WebUtility]::HtmlEncode($Message)
      $htmlBody += "</pre>"
      $mail.HTMLBody = $htmlBody
      $mail.Send()
      Write-Host "[notify] Email sent to $to."
    }
  }
} catch {
  Write-Warning "Outlook send failed: $_"
}

Write-Host "[notify] sent: $title"

