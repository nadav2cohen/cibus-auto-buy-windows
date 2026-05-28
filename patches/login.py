import time

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from cibus_daily_buy.browser import is_authenticated, save_session, take_screenshot
from cibus_daily_buy.config import USERNAME, log
from cibus_daily_buy.windows_otp import read_otp


# OneTrust cookie banner blocks form rendering on first load
COOKIE_DISMISS_SELECTORS = [
    "#onetrust-accept-btn-handler",
    'button:has-text("אשר הבחירות שלי")',
    'button.save-preference-btn-handler',
]

# "קוד חד פעמי" = "one-time code" tab — the OTP login flow
OTP_TAB_SELECTORS = [
    'text=קוד חד פעמי',
    'text=חד פעמי',
    '[role="tab"]:has-text("קוד")',
]

# Email input on the OTP tab. #firstInput is the Material/Angular field;
# legacy pages used #user. Try both, prefer the visible one.
EMAIL_FIELD_SELECTORS = [
    '#firstInput:visible',
    '#user:visible',
    'input[type="text"]:visible',
    'input[type="email"]:visible',
]

# "שנמשיך?" — the continue button after the email is filled
CONTINUE_BUTTON_SELECTORS = [
    'button.cib-pink-grad:has-text("שנמשיך?"):visible',
    'button:has-text("שנמשיך?"):visible',
    'button:has-text("המשך"):visible',
    'button[type="submit"]:visible',
]


def _dismiss_cookie_banner(page) -> None:
    """OneTrust consent dialog blocks the login form on first visit."""
    for sel in COOKIE_DISMISS_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click()
                log.info(f"Dismissed cookie banner via {sel}")
                time.sleep(1)
                return
        except Exception:
            continue


def _click_otp_tab(page) -> bool:
    """Switch to the one-time-code login tab. Returns True if a tab was clicked.

    On some site variants the OTP form is the default and no tab exists —
    in that case we still succeed because #firstInput is already visible.
    """
    for sel in OTP_TAB_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click()
                log.info(f"Clicked OTP tab via {sel}")
                time.sleep(1)
                return True
        except Exception:
            continue
    log.info("No OTP tab found (assuming default tab is OTP)")
    return False


def _fill_email(page, email: str) -> None:
    """Fill the email field. Clears any prefilled value first (Remember Me)."""
    for sel in EMAIL_FIELD_SELECTORS:
        try:
            field = page.locator(sel).first
            if not field.count() or not field.is_visible():
                continue
            field.click()
            field.fill("")
            field.type(email, delay=30)
            log.info(f"Typed email into {sel}")
            return
        except Exception:
            continue
    raise RuntimeError(
        "Could not find the email input field on the OTP login form"
    )


def _click_continue(page) -> None:
    for sel in CONTINUE_BUTTON_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_visible():
                btn.click()
                log.info(f"Clicked continue via {sel}")
                return
        except Exception:
            continue
    raise RuntimeError("Could not find the continue button after email")


def _find_otp_field(page):
    """Try multiple selectors defensively — the site's HTML varies across sessions."""
    otp_selectors = [
        'input[type="number"][maxlength]',
        'input[placeholder*="קוד"]',
        'input[placeholder*="code" i]',
        'input[name*="otp"]',
        'input[name*="code"]',
    ]
    for sel in otp_selectors:
        try:
            otp_field = page.wait_for_selector(sel, timeout=4000)
            log.info(f"OTP field found: {sel}")
            return otp_field
        except PlaywrightTimeout:
            continue
    return None


def _handle_otp(page, context, otp_field) -> None:
    """Ask for OTP via the configured Windows backend, fill, and submit.

    Tolerant of the user typing the OTP directly into the visible browser
    window instead of into the configured backend — in that case the
    field is already detached / login is already complete by the time
    ``read_otp`` returns. We swallow the detached-element error and
    verify auth instead.
    """
    code = read_otp(timeout=180)
    try:
        otp_field.fill(code)
    except Exception as e:
        log.warning(f"Could not fill OTP field (probably already submitted in browser): {e}")
        if is_authenticated(page):
            log.info("Already authenticated — assuming OTP was entered in the browser")
            save_session(context)
            return
        raise
    try:
        page.locator(
            'button:has-text("שנמשיך?"):visible, '
            'button[type="submit"]:visible, button:has-text("אשר"):visible, '
            'button:has-text("כניסה"):visible'
        ).first.click()
    except Exception:
        page.keyboard.press("Enter")
    time.sleep(2)
    take_screenshot(page, "03b_after_otp")
    save_session(context)


def login(page, context) -> None:
    """Full OTP-tab login flow.

    1. Dismiss the OneTrust cookie banner so the form renders.
    2. Switch to the "קוד חד פעמי" (one-time code) tab.
    3. Fill email into #firstInput.
    4. Click "שנמשיך?" to request the OTP.
    5. Read the OTP via the configured Windows backend and submit.

    The legacy password-tab path is no longer used: it required a "company
    name" field that automation does not fill, and Cibus rate-limits the
    password endpoint anyway.
    """
    if is_authenticated(page):
        log.info("Session still valid — skipping login")
        return

    log.info("Not authenticated — starting OTP-tab login flow")
    take_screenshot(page, "02a_login_initial")

    _dismiss_cookie_banner(page)
    _click_otp_tab(page)
    _fill_email(page, USERNAME)
    take_screenshot(page, "02b_email_filled")
    _click_continue(page)

    time.sleep(2)
    take_screenshot(page, "03_after_continue_click")

    otp_field = _find_otp_field(page)
    if otp_field:
        _handle_otp(page, context, otp_field)
    else:
        log.warning(
            "No OTP field appeared after clicking continue — "
            "checking whether login already succeeded"
        )
        if is_authenticated(page):
            save_session(context)
        else:
            raise RuntimeError(
                "Login flow did not produce an OTP prompt or an authenticated "
                "session. Check the latest screenshot for details."
            )

    take_screenshot(page, "03_after_login")
    log.info("Login complete")
