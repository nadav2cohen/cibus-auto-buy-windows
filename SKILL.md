# cibus-auto-buy-windows — Clawpilot skill

Automates weekly **Cibus / Pluxee Israel** voucher purchases on **Windows**. Reads the remaining weekly budget, greedily splits it into voucher denominations, and checks out at a chosen restaurant page — fully unattended, including 2FA OTP via a pluggable Windows backend.

Windows port of [`Acohengadol/cibus-auto-buy`](https://github.com/Acohengadol/cibus-auto-buy) (macOS), which patches [`AdirTuval/cibus-daily-buy`](https://github.com/AdirTuval/cibus-daily-buy).

## v1.3 changes (May 2026)

The React `+` button on the restaurant page is silently gated by hidden state and swallows automated clicks. The previous patches drove the UI; v1.3 drops the UI driver and talks to `/api/main.py` directly.

| Concern | v1.2 | v1.3 |
|---|---|---|
| Add to cart | React `+` click via Playwright | `prx_add_prod_to_cart` POST (HAR-derived) |
| SPA state | Implicit | Mirrored via `localStorage['cibus-cart_he']` |
| Login | Permanent-password tab (`#user` + `#password`) | OTP tab (`#firstInput` + `שנמשיך?`) |
| Required env | `CIBUS_PASSWORD` | (none — OTP only) |
| Default restaurant | `33208` / `25923` (time-limited) | `153348` Shufersal Vouchers (always open) |

The hidden `application-id: E5D5FEF5-A05E-4C64-AEBA-BA0CECA0E402` header on every `/api/main.py` POST is the missing piece that makes the direct API path work — without it the server returns `code: 726 "Empty values is not permitted"` even with valid cookies.

The final confirm-order POST is still unobserved (not present in the captured HAR) and will be validated on the next live Thursday run.

## Architecture

```
┌─ Windows Task Scheduler ─────────────────────┐
│  Thu 08:00 → thursday-sentinel.ps1 (dry)     │
│  Thu 16:00 → thursday-run.ps1     (live buy) │
└──────────────────────────────────────────────┘
                    │
                    ▼
        cibus-daily-buy (patched upstream — v1.3)
            ├── login.py        — OTP-tab flow, no password
            ├── purchase.py     — API-direct cart + localStorage sync
            ├── run.py          — threads dish_map through add_to_cart
            └── windows_otp.py  — pluggable: prompt | file | phone_link
                    │
                    ▼
            notify.ps1 → ntfy.sh phone push + Windows toast
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
6. **Cibus account** + a phone number on file that receives SMS OTPs (no password is used in v1.3).
7. **"Remember this device"** ticked on first 2FA login — Playwright reuses the persistent profile after that.

## Repo layout (created under `%USERPROFILE%\Documents\cibus-tools\`)

```
cibus-tools\
├── cibus-daily-buy\             # cloned + patched upstream
│   ├── .venv\                   # python venv (chromium + deps installed)
│   ├── .env                     # CIBUS_USERNAME / RESTAURANT_URL / OTP_SOURCE
│   ├── cibus_daily_buy\
│   │   ├── login.py             # patched: OTP-tab flow (v1.3)
│   │   ├── purchase.py          # patched: API-direct cart + localStorage sync (v1.3)
│   │   ├── run.py               # patched: threads dish_map, skips DISPLAY check
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

Starting with **v1.3**, all four patched files are shipped as drop-in replacements under [`patches/`](./patches/). After cloning upstream, just copy them in:

```powershell
$src  = "$env:USERPROFILE\Documents\cibus-tools\cibus-auto-buy-windows\patches"
$dest = "$env:USERPROFILE\Documents\cibus-tools\cibus-daily-buy\cibus_daily_buy"
Copy-Item "$src\login.py"       $dest -Force
Copy-Item "$src\purchase.py"    $dest -Force
Copy-Item "$src\run.py"         $dest -Force
Copy-Item "$src\windows_otp.py" $dest -Force
```

| File | Purpose (v1.3) |
|---|---|
| `windows_otp.py` (new) | Pluggable Windows OTP backends — `prompt` / `file` / `phone_link`. |
| `login.py` | OTP-tab login flow: dismisses OneTrust cookie banner, clicks the **קוד חד פעמי** (one-time code) tab, types email into `#firstInput`, clicks the **שנמשיך?** continue button, and reads the OTP via `windows_otp.read_otp()`. Tolerates the "element detached" race that fires when the OTP input is replaced mid-fill. |
| `purchase.py` | API-direct cart: POSTs straight to `https://api.consumers.pluxee.co.il/api/main.py` (`prx_add_prod_to_cart`) with the hidden `application-id` header, then mirrors the resulting cart into `localStorage['cibus-cart_he']` so the React SPA's `/preorder` page renders the confirm button. Falls back to UI clicks only if the API path returns a non-OK code. |
| `run.py` | Captures the menu tree once, threads the resulting `dish_map` through `add_to_cart_via_api`, and skips the Linux `DISPLAY` guard on Windows. |

No manual code edits are needed — the four files in `patches/` are the full, ready-to-drop replacements.

## Wrapper scripts

Copy [`scripts\notify.ps1`](./scripts/notify.ps1), [`scripts\thursday-run.ps1`](./scripts/thursday-run.ps1), [`scripts\thursday-sentinel.ps1`](./scripts/thursday-sentinel.ps1), and [`scripts\setup-scheduled-tasks.ps1`](./scripts/setup-scheduled-tasks.ps1) to `%USERPROFILE%\Documents\cibus-tools\`.

To enable phone push, the notifier POSTs to a private **[ntfy.sh](https://ntfy.sh)** topic. Install the free ntfy app on your phone, subscribe to a long random topic name (e.g. `cibus-<your-name>-<random>`), and set the same topic in `.env`:

```
CIBUS_NTFY_TOPIC=cibus-your-name-7q4mz-r8xk2p
# CIBUS_NTFY_SERVER=https://ntfy.sh           # override if self-hosting
# CIBUS_NTFY_PRIORITY=3                       # 1=min, 3=default, 5=urgent
```

> **Why not Teams or Outlook?** On a Microsoft tenant every "send-as-you" automation path is blocked: Teams Incoming Webhooks are killed by the CISO DLP policy (`TeamsWebhookRequestReceived` restricted), the new "Monarch" Outlook has no COM interface, Microsoft Graph PowerShell SDK hits `AADSTS90094 admin approval required`, and Az.Accounts → Graph token requests are blocked by Conditional Access requiring interactive MFA per token (incompatible with scheduled tasks). ntfy.sh has no Microsoft tenant surface area at all.
>
> ntfy.sh topics are "secret URLs" — anyone with the topic name can read your messages. Keep the topic long and random, and never send sensitive content through it.

Optional fallback: install [BurntToast](https://github.com/Windos/BurntToast) for a richer Windows desktop toast on top of the phone push.

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
