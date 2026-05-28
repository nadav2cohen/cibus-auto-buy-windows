import argparse
import logging
import os
import shutil
import sys
import time
from datetime import datetime

# Force UTF-8 stdio on Windows so emoji log lines don't crash cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from playwright.sync_api import sync_playwright

from cibus_daily_buy.browser import attach_api_logger, take_screenshot
from cibus_daily_buy.config import (
    CIBUS_URL,
    LOG_DIR,
    LOG_FORMAT,
    NAVIGATION_TIMEOUT,
    PROFILE_DIR,
    log,
)
from cibus_daily_buy.login import login
from cibus_daily_buy.telegram import OTPTimeoutError, UserAbortError, check_daily_abort
from cibus_daily_buy.purchase import (
    add_to_cart,
    check_budget,
    cleanup_cart,
    compute_voucher_plan,
    confirm_order,
    discover_denominations,
    discover_menu_dishes,
    dismiss_error_modal,
    navigate_to_checkout,
    navigate_to_restaurant,
)


def _add_file_logger(path=None):
    """Attach a FileHandler to the cibus logger. Uses logs/<ts>_run.log if path not given."""
    if path is None:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(LOG_DIR, f"{ts}_run.log")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger("cibus").addHandler(handler)
    return path


def _remove_lock_files():
    """Remove stale Chrome lock files left after unclean shutdowns (e.g. cron kill)."""
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock = os.path.join(PROFILE_DIR, name)
        try:
            os.remove(lock)
        except FileNotFoundError:
            pass


def _launch_browser(p, fresh_login):
    """Launch Chromium with a persistent profile (requires xvfb on headless servers)."""
    if fresh_login:
        log.info("Fresh login requested — deleting Chrome profile")
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
    else:
        _remove_lock_files()

    try:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="he-IL",
        )
    except Exception as e:
        log.warning(f"Failed to launch with existing profile: {e}")
        log.info("Deleting corrupted profile and retrying…")
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="he-IL",
        )

    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(NAVIGATION_TIMEOUT)
    attach_api_logger(page)
    return context, page


MAX_OTP_RETRIES = 3


def run(dry_run: bool = False, fresh_login: bool = False):
    log.info("=" * 50)
    log.info(f"Cibus Daily Buy — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    log.info("=" * 50)

    check_daily_abort()

    for attempt in range(1, MAX_OTP_RETRIES + 1):
        with sync_playwright() as p:
            # Force fresh login on retries so stale session state can't interfere
            effective_fresh = fresh_login or attempt > 1
            context, page = _launch_browser(p, effective_fresh)
            try:
                # Step 1: Navigate to Cibus
                log.info(f"Navigating to {CIBUS_URL}")
                page.goto(CIBUS_URL, wait_until="domcontentloaded")
                time.sleep(2)
                take_screenshot(page, "01_homepage")

                # Step 2: Login
                login(page, context)

                # Step 3: Check budget (raw float)
                budget = check_budget(page)

                # Step 4: Navigate to restaurant — also captures the menu tree
                # XHR so we can resolve {price -> (category_id, dish_id)} for
                # the API-direct add flow.
                menu_tree = navigate_to_restaurant(page)
                dish_map = discover_menu_dishes(menu_tree)

                # Step 4b: Dismiss any stuck "1-voucher-per-order" error modal
                # from a prior failed run, then clean cart so quantities start at 0
                try:
                    dismiss_error_modal(page)
                    cleanup_cart(page, context)
                    menu_tree = navigate_to_restaurant(page)
                    if not dish_map:
                        dish_map = discover_menu_dishes(menu_tree)
                except Exception as cleanup_err:
                    log.warning(f"Pre-run cart cleanup failed (continuing): {cleanup_err}")

                # Step 5: Discover available denominations + compute the plan
                denoms = discover_denominations(page)
                max_over = int(os.environ.get("CIBUS_MAX_OVERSHOOT", "5"))
                plan = compute_voucher_plan(budget, denoms, max_overshoot=max_over)
                if not plan:
                    raise RuntimeError(
                        f"No viable voucher plan for budget=₪{budget:.2f} "
                        f"with denoms={denoms} (max_overshoot=₪{max_over})"
                    )
                total = sum(plan)
                log.info(
                    f"Voucher plan: {plan} = ₪{total} "
                    f"(budget=₪{budget:.2f}, overshoot=₪{total - budget:+.2f})"
                )

                # Cibus only allows 1 voucher per order, so each plan entry
                # is a separate full order cycle in LIVE mode. In DRY-RUN we
                # only verify the first order's flow because the server blocks
                # a 2nd add until the prior order is confirmed (which dry-run
                # never does).
                live_plan = plan if not dry_run else plan[:1]
                if dry_run and len(plan) > 1:
                    log.info(
                        f"DRY RUN — will only verify the first of {len(plan)} planned orders "
                        f"(server blocks unconfirmed 2nd adds)"
                    )

                all_ok = True
                for i, amount in enumerate(live_plan, start=1):
                    log.info(
                        f"--- Order {i}/{len(live_plan)}: ₪{amount} ---"
                    )

                    # Make sure we're on the restaurant menu with a clean cart
                    navigate_to_restaurant(page)
                    dismiss_error_modal(page)

                    # Step 6: Add the single voucher for this order (API-direct)
                    add_to_cart(page, amount, dish_map=dish_map)

                    # Step 7: Checkout
                    checkout_ok = navigate_to_checkout(page)

                    if dry_run:
                        if checkout_ok:
                            log.info(
                                f"DRY RUN — order {i}/{len(live_plan)} (₪{amount}) verified"
                            )
                        else:
                            log.warning(
                                f"DRY RUN — order {i}/{len(live_plan)} (₪{amount}) checkout FAILED"
                            )
                            all_ok = False
                        cleanup_cart(page, context)
                    else:
                        if not checkout_ok:
                            raise RuntimeError(
                                f"Order {i}/{len(live_plan)} (₪{amount}) — checkout not ready"
                            )
                        # Step 8: Confirm this order (live)
                        confirm_order(page)
                        log.info(
                            f"Order {i}/{len(live_plan)} (₪{amount}) confirmed"
                        )

                if dry_run:
                    log.info(
                        f"DRY RUN complete — first order verified, all_ok={all_ok}, "
                        f"full plan ({len(plan)} orders) deferred to live run"
                    )
                    return all_ok
                log.info(f"All {len(live_plan)} orders confirmed successfully")
                return True

            except UserAbortError:
                raise  # intentional — no screenshot, no retry

            except OTPTimeoutError:
                log.warning(f"OTP timed out (attempt {attempt}/{MAX_OTP_RETRIES})")
                if attempt < MAX_OTP_RETRIES:
                    log.info("Restarting login flow with a fresh browser…")
                    continue
                raise RuntimeError(
                    f"OTP not provided after {MAX_OTP_RETRIES} attempts"
                )

            except Exception as e:
                log.error(f"❌ Error: {e}")
                try:
                    take_screenshot(page, "error")
                except Exception:
                    pass
                raise

            finally:
                context.close()


def main():
    if sys.platform not in ("darwin", "win32") and not os.environ.get("DISPLAY"):
        sys.exit(
            "Error: DISPLAY environment variable is not set.\n"
            "Run via xvfb-run: xvfb-run python cibus_daily_buy.py"
        )

    parser = argparse.ArgumentParser(description="Cibus Pluxee Daily Auto-Buyer")
    parser.add_argument("--dry-run", action="store_true", help="Stop before actual purchase")
    parser.add_argument("--fresh-login", action="store_true", help="Ignore saved session and log in from scratch")
    parser.add_argument("--log-file", metavar="PATH", nargs="?", const=None,
                        help="Log file path (default: logs/<timestamp>_run.log). Always created.")
    args = parser.parse_args()

    log_path = _add_file_logger(args.log_file)
    log.info(f"Logging to file: {log_path}")

    try:
        success = run(dry_run=args.dry_run, fresh_login=args.fresh_login)
    except UserAbortError as e:
        log.info(f"Run aborted intentionally: {e}")
        sys.exit(0)
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
    sys.exit(0 if success else 1)
