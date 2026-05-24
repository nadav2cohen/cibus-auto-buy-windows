# cibus-auto-buy-windows

Fully unattended weekly **Cibus / Pluxee Israel** voucher purchases on **Windows 10/11**.

Windows port of [`Acohengadol/cibus-auto-buy`](https://github.com/Acohengadol/cibus-auto-buy), which is itself a patched fork of [`AdirTuval/cibus-daily-buy`](https://github.com/AdirTuval/cibus-daily-buy).

Packaged as a **Clawpilot skill** so any Microsoft user running Clawpilot on Windows can drop it in and schedule it.

---

## What it does

1. **Thursday 08:00** — `thursday-sentinel.ps1` does a dry-run: logs in, reads the remaining weekly Cibus budget, computes a voucher plan, and exits without buying. Toast + optional Teams webhook to your phone.
2. **Thursday 16:00** — `thursday-run.ps1` does it for real: greedily splits the budget into voucher denominations and checks out at a configured restaurant page.
3. 2FA OTP is read automatically via a pluggable **Windows OTP backend** (no macOS Messages.app, no Telegram bot).

## Quick start

```powershell
# 1) Clone upstream + this repo
mkdir $env:USERPROFILE\Documents\cibus-tools
cd    $env:USERPROFILE\Documents\cibus-tools
git clone https://github.com/AdirTuval/cibus-daily-buy.git
git clone https://github.com/nadav2cohen/cibus-auto-buy-windows.git

# 2) Python venv + Playwright
cd cibus-daily-buy
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium

# 3) Apply patches: copy windows_otp.py into the package, edit login.py
#    and run.py per SKILL.md.
Copy-Item ..\cibus-auto-buy-windows\patches\windows_otp.py .\cibus_daily_buy\

# 4) Copy wrapper scripts to the tools root
Copy-Item ..\cibus-auto-buy-windows\scripts\*.ps1 ..\

# 5) Configure .env (chmod-style ACL hardening recommended)
@"
CIBUS_USERNAME=you@example.com
CIBUS_PASSWORD=your_password
RESTAURANT_URL=https://consumers.pluxee.co.il/restaurants/pickup/restaurant/33208
CIBUS_OTP_SOURCE=prompt      # prompt | file | phone_link
# CIBUS_OTP_FILE=$env:USERPROFILE\cibus_otp.txt
# CIBUS_TEAMS_WEBHOOK=https://outlook.office.com/webhook/...
"@ | Out-File -Encoding ASCII .env

# 6) (Optional but recommended) BurntToast for native toasts
Install-Module BurntToast -Scope CurrentUser

# 7) Register weekly Task Scheduler jobs
..\setup-scheduled-tasks.ps1
```

Read **[SKILL.md](./SKILL.md)** for the full setup, OTP backend trade-offs, and Clawpilot integration.

## Disclaimer

> ⚠️ LIVE mode **actually charges your weekly Cibus budget**. Always run the sentinel dry-run first. Tested on Windows 11 24H2 with Python 3.11. Israel-only Pluxee tenant.

## License

MIT — see [LICENSE](./LICENSE).
