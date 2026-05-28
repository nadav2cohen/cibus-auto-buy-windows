# cibus-auto-buy-windows

Fully unattended weekly **Cibus / Pluxee Israel** voucher purchases on **Windows 10/11**.

Windows port of [`Acohengadol/cibus-auto-buy`](https://github.com/Acohengadol/cibus-auto-buy), which is itself a patched fork of [`AdirTuval/cibus-daily-buy`](https://github.com/AdirTuval/cibus-daily-buy).

Packaged as a **Clawpilot skill** so any Microsoft user running Clawpilot on Windows can drop it in and schedule it.

**v1.3 highlights**
- **API-direct cart** — bypasses the React `+` button (silently gated under automation) by POSTing `prx_add_prod_to_cart` directly to `/api/main.py`. Mirrors the cart into `localStorage['cibus-cart_he']` so the SPA stays in sync and `/preorder` loads with the confirm button visible.
- **OTP-tab login** — the "permanent password" tab now requires a "company name" field; we switched to the "קוד חד פעמי" (one-time code) tab. `CIBUS_PASSWORD` is no longer used.
- **Always-open restaurant** — default `RESTAURANT_URL` is now `153348` (Shufersal Vouchers, `is_open=1` 24/7). Store-specific restaurants like `25923` (Shufersal Petach Tikva) only accept orders during their local hours.

---

## What it does

1. **Thursday 08:00** — `thursday-sentinel.ps1` does a dry-run: logs in, reads the remaining weekly Cibus budget, computes a voucher plan, and exits without buying.
2. **Thursday 16:00** — `thursday-run.ps1` does it for real: greedily splits the budget into voucher denominations and checks out at a configured restaurant page.
3. After each run you get a **phone push via ntfy.sh** plus a Windows desktop toast. 2FA OTP is read automatically via a pluggable **Windows OTP backend** (no macOS Messages.app, no Telegram bot).

   **Why ntfy.sh and not Teams/Outlook?** Microsoft tenants block every "send-as-you" automation path: Teams Incoming Webhooks (DLP `TeamsWebhookRequestReceived`), Outlook COM (replaced by Monarch which has no COM), Microsoft Graph PowerShell SDK (`AADSTS90094` admin block), and Az.Accounts → Graph (Conditional Access forces interactive MFA per token). ntfy.sh lives entirely outside the Microsoft tenant — no auth, no DLP, no token refresh. Set `CIBUS_NTFY_TOPIC=<your-random-topic>` in `.env`.

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

# 3) Apply patches: copy the four v1.3 files into the upstream package.
#    See SKILL.md for diff-level details.
Copy-Item ..\cibus-auto-buy-windows\patches\windows_otp.py .\cibus_daily_buy\
Copy-Item ..\cibus-auto-buy-windows\patches\login.py       .\cibus_daily_buy\
Copy-Item ..\cibus-auto-buy-windows\patches\purchase.py    .\cibus_daily_buy\
Copy-Item ..\cibus-auto-buy-windows\patches\run.py         .\cibus_daily_buy\

# 4) Copy wrapper scripts to the tools root
Copy-Item ..\cibus-auto-buy-windows\scripts\*.ps1 ..\

# 5) Configure .env (chmod-style ACL hardening recommended)
@"
CIBUS_USERNAME=you@example.com
# CIBUS_PASSWORD is no longer needed — v1.3 uses the OTP-tab login flow.
# Restaurant 153348 = Shufersal Vouchers (generic, always open). Other
# voucher restaurants (e.g. 25923 Shufersal Petach Tikva) only accept
# orders during their store-specific hours.
RESTAURANT_URL=https://consumers.pluxee.co.il/restaurants/pickup/restaurant/153348
CIBUS_OTP_SOURCE=prompt      # prompt | file | phone_link
# CIBUS_OTP_FILE=$env:USERPROFILE\cibus_otp.txt
CIBUS_NTFY_TOPIC=cibus-your-name-long-random-string
# CIBUS_NTFY_SERVER=https://ntfy.sh
# CIBUS_NTFY_PRIORITY=3
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
