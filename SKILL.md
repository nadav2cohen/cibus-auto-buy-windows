# cibus-auto-buy-windows — Clawpilot skill

Automates weekly **Cibus / Pluxee Israel** voucher purchases on **Windows**. Reads the remaining weekly budget, greedily splits it into voucher denominations, and checks out at a chosen restaurant page — fully unattended, including 2FA OTP via a pluggable Windows backend.

Windows port of [`Acohengadol/cibus-auto-buy`](https://github.com/Acohengadol/cibus-auto-buy) (macOS), which patches [`AdirTuval/cibus-daily-buy`](https://github.com/AdirTuval/cibus-daily-buy).

## Architecture

```
┌─ Windows Task Scheduler ─────────────────────┐
│  Thu 08:00 → thursday-sentinel.ps1 (dry)     │
│  Thu 16:00 → thursday-run.ps1     (live buy) │
└──────────────────────────────────────────────┘
                    │
                    ▼
        cibus-daily-buy (patched upstream)
            ├── login.py        — uses windows_otp instead of Telegram
            ├── purchase.py     — compute_voucher_plan() greedy split
            ├── run.py          — skips DISPLAY check on Windows
            └── windows_otp.py  — pluggable: prompt | file | phone_link
                    │
                    ▼
            notify.ps1 → Outlook desktop (COM) → email + Outlook mobile push
```

The only Windows-specific surface is `windows_otp.py` + the PowerShell wrappers. Everything else is plain Python and works the same as upstream.

## Required prerequisites (one-time)

1. **Windows 10/11** with an interactive user account (Task Scheduler runs in the user's desktop session).
2. **Python 3.10+** via the official installer or `winget install Python.Python.3.12` — the Python launcher (`py -3`) must be on PATH.
3. **Git** (`winget install Git.Git`).
4. **PowerShell 5.1+** (built in) or PowerShell 7.
5. **(Recommended)** [`BurntToast`](https://github.com/Windos/BurntToast) PowerShell module for native toasts:
   ```powershell
   Install-Module BurntToast -Scope CurrentUser
   ```
6. **Cibus account credentials** + a phone number on file that receives SMS OTPs.
7. **"Remember this device"** ticked on first 2FA login — Playwright reuses the persistent profile after that.

## Repo layout (created under `%USERPROFILE%\Documents\cibus-tools\`)

```
cibus-tools\
├── cibus-daily-buy\             # cloned + patched upstream
│   ├── .venv\                   # python venv (chromium + deps installed)
│   ├── .env                     # CIBUS_USERNAME / PASSWORD / URL / OTP_SOURCE
│   ├── cibus_daily_buy\
│   │   ├── login.py             # patched: reads OTP via windows_otp
│   │   ├── purchase.py          # patched: compute_voucher_plan(), multi-voucher
│   │   ├── run.py               # patched: skips Linux DISPLAY check
│   │   └── windows_otp.py       # NEW — pluggable OTP backends
│   └── logs\                    # per-run logs
├── notify.ps1                   # toast + optional Teams webhook
├── thursday-run.ps1             # Thu 16:00 live purchase
├── thursday-sentinel.ps1        # Thu 08:00 dry-run sanity check
└── setup-scheduled-tasks.ps1    # registers Task Scheduler jobs
```

## OTP backends

Set with `CIBUS_OTP_SOURCE` in `.env`. Choose **one**:

### `prompt` (default)

Interactive console — Cibus pauses, prints `[CIBUS] Enter OTP from SMS …`, you type it. Works everywhere, but the run is not truly unattended; useful for first-time setup and debugging.

### `file` — recommended for unattended runs

`windows_otp.py` watches `%USERPROFILE%\cibus_otp.txt` (override with `CIBUS_OTP_FILE`). Any external automation can drop the OTP there. Two easy producers:

- **Microsoft Power Automate (cloud) — "When a new email arrives"** → filter on the Cibus sender → extract digits with `match()` → "Create file" connector pointing at your OneDrive-synced `~\cibus_otp.txt`. Requires Cibus to be sending OTP to your email *or* an SMS-to-email forwarding rule on your phone.
- **Phone-side automation** (Android: Tasker / iOS: Shortcuts) → detect Cibus SMS → push the digits to a shared file via OneDrive / Dropbox.

The file is consumed (deleted) after read, so the next run waits for a fresh OTP.

### `phone_link` — Android phones synced to Windows

Reads from the Microsoft **Phone Link** app's local SQLite store under `%LOCALAPPDATA%\Packages\Microsoft.YourPhone_*\LocalState\`. The schema is undocumented and changes across Phone Link updates — best-effort. Works for many Android users on Windows 11.

> iPhone users on Windows: Apple severely limits Phone Link's iMessage access; `phone_link` is unlikely to work. Use `file` with Shortcuts on the iPhone instead.

## Patches (apply on top of upstream `AdirTuval/cibus-daily-buy`)

### 1. `cibus_daily_buy/windows_otp.py` (new file)

Copy from [`patches/windows_otp.py`](./patches/windows_otp.py).

### 2. `cibus_daily_buy/login.py`

Replace the Telegram OTP call:

```python
# from cibus_daily_buy.telegram import ask_telegram
from cibus_daily_buy.windows_otp import read_otp
```

Inside `_handle_otp` (or wherever upstream calls `ask_telegram`):

```python
otp = read_otp(timeout=180)
```

### 3. `cibus_daily_buy/purchase.py`

Multi-voucher checkout — same patch as the macOS fork, unchanged. Add a greedy splitter and loop `add_to_cart` per entry:

```python
DENOMS = [200, 100, 50, 30]  # adjust to your restaurant

def compute_voucher_plan(budget: float) -> list[int]:
    plan, remaining = [], int(budget)
    for d in DENOMS:
        while remaining >= d:
            plan.append(d)
            remaining -= d
    return plan
```

Make `check_budget()` return a `float`.

### 4. `cibus_daily_buy/run.py`

Use the plan and skip the Linux `DISPLAY` guard on Windows:

```python
plan = compute_voucher_plan(budget)
log.info(f"Voucher plan for ₪{budget}: {plan}")
if not plan:
    log.warning("Budget too low — no vouchers to buy"); return
for amt in plan:
    add_to_cart(page, amt)
```

```python
if sys.platform not in ("darwin", "win32") and not os.environ.get("DISPLAY"):
    sys.exit("No DISPLAY")
```

## Wrapper scripts

Copy [`scripts\notify.ps1`](./scripts/notify.ps1), [`scripts\thursday-run.ps1`](./scripts/thursday-run.ps1), [`scripts\thursday-sentinel.ps1`](./scripts/thursday-sentinel.ps1), and [`scripts\setup-scheduled-tasks.ps1`](./scripts/setup-scheduled-tasks.ps1) to `%USERPROFILE%\Documents\cibus-tools\`.

To enable phone push, the notifier sends an **Outlook email** via Outlook desktop COM — no webhook URL, no app registration, no DLP-restricted connector. The Outlook mobile app pushes the email to your phone in seconds.

> **Why not a Teams Incoming Webhook?** Microsoft retired classic O365 connectors on 2025-12-31, and the replacement Power Automate trigger (`TeamsWebhookRequestReceived`) is **blocked by default on Microsoft tenants** (CISO Default Environment DLP policy). Outlook COM bypasses both problems because it uses the user's already-signed-in Outlook session.

Optional: set `$env:CIBUS_NOTIFY_EMAIL` in `.env` if you want the email to go to a different address than your default Outlook profile.

## Scheduling

Run once, as your normal user (no admin needed):

```powershell
$env:USERPROFILE\Documents\cibus-tools\setup-scheduled-tasks.ps1
```

Creates two weekly tasks:

| Task name        | When             | Script                  |
| ---------------- | ---------------- | ----------------------- |
| `Cibus-Sentinel` | Thursdays 08:00  | `thursday-sentinel.ps1` |
| `Cibus-Run`      | Thursdays 16:00  | `thursday-run.ps1`      |

Both run as the current user with `LogonType Interactive`. Edge cases (sleep, locked screen) — see Troubleshooting.

### Clawpilot scheduling alternative

If you prefer Clawpilot's built-in scheduler over Task Scheduler, create two automations:

```text
Name:     Cibus sentinel (Thu 08:00)
Schedule: every Thursday at 08:00
Prompt:   Run the PowerShell script at %USERPROFILE%\Documents\cibus-tools\thursday-sentinel.ps1
          and send me a Teams message with the result.

Name:     Cibus live buy (Thu 16:00)
Schedule: every Thursday at 16:00
Prompt:   Run the PowerShell script at %USERPROFILE%\Documents\cibus-tools\thursday-run.ps1
          and send me a Teams message with the result.
```

This works because Clawpilot can shell out via PowerShell and observe exit codes.

## Validation

```powershell
& "$env:USERPROFILE\Documents\cibus-tools\thursday-sentinel.ps1"
```

Expected:

- [ ] Login succeeds (browser may prompt for OTP first time → tick "Remember this device").
- [ ] OTP auto-extracted from your chosen backend within `CIBUS_OTP_TIMEOUT` (180s default).
- [ ] Balance number printed in the log.
- [ ] Voucher plan matches expectation (e.g. 360 → `[200, 100, 30, 30]`).
- [ ] Toast pops up, Teams card arrives (if webhook configured).

## Troubleshooting

| Symptom                                          | Fix                                                                                 |
| ------------------------------------------------ | ----------------------------------------------------------------------------------- |
| `playwright` cannot launch Chromium              | Re-run `playwright install chromium` inside the venv.                               |
| `windows_otp` raises `TimeoutError`              | Backend never saw the code. Bump `CIBUS_OTP_TIMEOUT`, or switch backend.            |
| `prompt` backend errors `requires an interactive terminal` | Task Scheduler hides the console — use `file` or `phone_link` for scheduled runs.    |
| `phone_link` finds no DB                         | Open the Phone Link app once and pair your phone, then re-run.                      |
| Task Scheduler job never runs                    | Edit task → "Run only when user is logged on" → also tick "Wake the computer".      |
| Session expired                                  | Delete `.playwright-profile\`, run the sentinel manually, re-tick "Remember this device". |
| Restaurant denominations changed                 | Verify via sentinel dry-run, update `DENOMS` in `purchase.py`.                      |
| BurntToast missing                               | Notifier falls back to `System.Windows.Forms.NotifyIcon` balloon — works but uglier. |

## Notes / known limits

- Windows-only. macOS users — use the original [`Acohengadol/cibus-auto-buy`](https://github.com/Acohengadol/cibus-auto-buy).
- Israel-only Pluxee tenant (`consumers.pluxee.co.il`).
- LIVE flow actually charges your weekly budget — the Thu 08:00 sentinel is the safety net; do not disable it.
- Greedy split assumes the restaurant offers all denominations in `DENOMS`; falls back gracefully (drops the remainder under the smallest denom).
- The OTP regex looks for `verification code is: ####` and bare 6-digit numbers; override with `CIBUS_OTP_REGEX` if Cibus changes the wording.
