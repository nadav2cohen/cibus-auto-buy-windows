import json
import re
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from cibus_daily_buy.browser import save_session, take_screenshot
from cibus_daily_buy.config import (
    ACTION_TIMEOUT,
    PREORDER_URL,
    RESTAURANT_URL,
    log,
)

# Cibus API constants Γאפ captured from real browser traffic (HAR).
# The API host is separate from the page origin; calls also require this
# `application-id` header or they 400 with "Empty values is not permitted".
API_URL = "https://api.consumers.pluxee.co.il/api/main.py"
APPLICATION_ID = "E5D5FEF5-A05E-4C64-AEBA-BA0CECA0E402"


def _post_api(page, body: dict) -> dict:
    """POST a JSON request to /api/main.py from the logged-in page context.

    Uses the page's own fetch + cookies, plus the `application-id` header
    that the real Cibus front-end always sends. Returns the parsed JSON
    response (raises on HTTP error or non-zero `code`).
    """
    js = """async (args) => {
      const r = await fetch(args.url, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json; charset=UTF-8',
          'application-id': args.appId,
          'Accept': 'application/json, text/plain, */*',
          'Accept-Language': 'he'
        },
        body: JSON.stringify(args.body)
      });
      const text = await r.text();
      try { return {status: r.status, json: JSON.parse(text)}; }
      catch { return {status: r.status, text: text.slice(0, 500)}; }
    }"""
    res = page.evaluate(js, {"url": API_URL, "appId": APPLICATION_ID, "body": body})
    if res.get("status") != 200:
        raise RuntimeError(f"API call failed: HTTP {res.get('status')} body={res}")
    j = res.get("json") or {}
    return j


def check_budget(page) -> float:
    """Read remaining budget from the page and return it as a float (ILS)."""
    budget_el = page.locator(".budget")
    budget_el.wait_for(state="visible", timeout=ACTION_TIMEOUT)
    text = budget_el.text_content()
    take_screenshot(page, "04_budget")

    match = re.search(r"Γג¬([\d,]+\.?\d*)", text)
    if not match:
        raise RuntimeError(f"Could not parse budget from: {text!r}")
    budget = float(match.group(1).replace(",", ""))
    log.info(f"Remaining budget: Γג¬{budget:.2f}")
    return budget


def dismiss_error_modal(page) -> bool:
    """Close any open Cibus error/info dialog.

    Step 1: try Playwright-native clicks on known close selectors
    (`a.x-icon`, `.modal-header .close`, etc.) Γאפ these dispatch real
    trusted mouse events which React state actually responds to.
    Step 2: fall back to text-based JS detection + click for unknown layouts.
    Step 3: final fallback Γאפ Escape key.
    """
    # Step 1: native Playwright click on common close selectors
    selectors = [
        'a.x-icon',
        'a.x-icon:visible',
        '.x-icon',
        '.modal a.x-icon',
        '[class*="modal"] a.x-icon',
        'button.close',
        '.modal-header .close',
        '[aria-label="Close"]',
        '[aria-label="close"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=500):
                log.info(f"Closing modal via native Playwright click on {sel!r}")
                loc.click(timeout=2000, force=True)
                time.sleep(0.5)
                return True
        except Exception:
            continue

    # Step 2: text-based JS fallback
    info = {}
    try:
        info = page.evaluate(r"""() => {
            const ERR_TEXTS = ['╫ק╫£╫פ ╫⌐╫ע╫ש╫נ╫פ', '╫⌐╫ע╫ש╫נ╫פ ╫ס╫⌐╫£╫ש╫ק╫¬', '╫₧╫⌐╫ץ╫ס╫¿ ╫נ╫ק╫ף', '╫פ╫צ╫₧╫á╫פ ╫á╫ץ╫í╫ñ╫¬'];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let textNode = null;
            while (walker.nextNode()) {
                const t = walker.currentNode.nodeValue || '';
                if (ERR_TEXTS.some(e => t.includes(e))) { textNode = walker.currentNode; break; }
            }
            if (!textNode) return { found: false };
            let el = textNode.parentElement, modal = null;
            for (let i = 0; i < 10 && el && el !== document.body; i++) {
                const cls = (el.className || '').toString().toLowerCase();
                const role = (el.getAttribute && el.getAttribute('role') || '').toLowerCase();
                const cs = getComputedStyle(el);
                if (role === 'dialog' || role === 'alertdialog' ||
                    cls.includes('modal') || cls.includes('popup') ||
                    cls.includes('dialog') || cls.includes('overlay') ||
                    cs.position === 'fixed') { modal = el; break; }
                el = el.parentElement;
            }
            if (!modal) modal = textNode.parentElement.closest('div') || textNode.parentElement;
            return {
                found: true,
                modalCls: (modal.className || '').toString(),
                modalTag: modal.tagName,
                html: modal.outerHTML.slice(0, 1500),
            };
        }""")
    except Exception as e:
        log.warning(f"Modal inspect failed: {e}")
        return False

    if not info.get("found"):
        return False

    log.warning(
        f"Modal found by text but no known close selector matched. "
        f"tag={info.get('modalTag')} cls={info.get('modalCls')} "
        f"HTML: {info.get('html','')[:1000]}"
    )
    # Step 3: Escape
    try:
        page.keyboard.press("Escape")
        time.sleep(0.3)
    except Exception:
        pass
    return False


def discover_denominations(page) -> list:
    """Scrape voucher denominations from the restaurant page.

    Returns a sorted (descending) list of available integer ILS amounts.
    """
    page.wait_for_selector('.card .card-footer label', timeout=ACTION_TIMEOUT)
    labels = page.locator('.card .card-footer label').all_text_contents()
    seen = set()
    for txt in labels:
        m = re.search(r"Γג¬(\d+(?:\.\d+)?)", txt)
        if m:
            v = int(float(m.group(1)))
            if v > 0:
                seen.add(v)
    denoms = sorted(seen, reverse=True)
    log.info(f"Available denominations: {denoms}")
    return denoms


def compute_voucher_plan(budget: float, denoms: list, max_overshoot: int = 5) -> list:
    """Find a multiset of denoms summing to ~budget using coin-change DP.

    Strategy (in order of preference):
      1. Exact fit (sum == floor(budget)).
      2. Smallest overshoot in [floor(budget)+1, floor(budget)+max_overshoot].
      3. Largest undershoot (sum < floor(budget), sum >= min(denoms)).

    Among ties, prefers fewer vouchers (= larger denoms).
    Returns the chosen multiset sorted descending. Empty list = nothing to buy.
    """
    if not denoms:
        return []
    budget_floor = int(budget)  # ignore agorot
    upper = budget_floor + max_overshoot
    if upper < min(denoms):
        return []

    # dp[s] = best multiset (list of ints) summing to exactly s, or None.
    dp = [None] * (upper + 1)
    dp[0] = []
    for s in range(1, upper + 1):
        best = None
        for d in denoms:
            if s - d >= 0 and dp[s - d] is not None:
                candidate = dp[s - d] + [d]
                if best is None or len(candidate) < len(best):
                    best = candidate
        dp[s] = best

    # Exact, then overshoot.
    for s in range(budget_floor, upper + 1):
        if dp[s] is not None:
            return sorted(dp[s], reverse=True)
    # Fall back to largest reachable undershoot.
    for s in range(budget_floor - 1, -1, -1):
        if dp[s] is not None and s >= min(denoms):
            return sorted(dp[s], reverse=True)
    return []


def navigate_to_restaurant(page) -> dict:
    """Navigate to the restaurant page and capture the menu-tree response.

    The page itself fires `rest_menu_tree.py?restaurant_id=...&comp_id=...`
    on load. We attach a one-shot response listener so we can decode the
    menu tree without having to re-construct the URL ourselves.

    Returns the parsed menu-tree JSON (or {} on failure).
    """
    log.info(f"Navigating to restaurant page: {RESTAURANT_URL}")

    captured = {"json": None}

    def _on_response(resp):
        try:
            if "rest_menu_tree" in resp.url and resp.status == 200:
                captured["json"] = resp.json()
        except Exception:
            pass

    page.on("response", _on_response)
    try:
        page.goto(RESTAURANT_URL, wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=ACTION_TIMEOUT)
        # Give the XHR a moment in case it lands just after domcontentloaded
        for _ in range(20):
            if captured["json"] is not None:
                break
            time.sleep(0.25)
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass

    take_screenshot(page, "05_restaurant_page")
    return captured["json"] or {}


def _walk_dishes(node, cat_id=None, out=None):
    """Recurse the rest_menu_tree response collecting `{price: (cat_id, dish_id)}`.

    The tree uses Cibus' nested element model:
      - element_type == 12 Γזע category (carries the category_id we need)
      - element_type == 13 Γזע dish (carries the dish_id we need)
    The fields are `element_id` and `price`, NOT `dish_id`/`dish_price`.
    """
    if out is None:
        out = {}
    if isinstance(node, dict):
        et = node.get("element_type")
        eid = node.get("element_id")
        if et == 12 and eid:
            cat_id = eid
        if et == 13 and node.get("price"):
            out[int(node["price"])] = (cat_id, eid)
        for v in node.values():
            if isinstance(v, (dict, list)):
                _walk_dishes(v, cat_id, out)
    elif isinstance(node, list):
        for v in node:
            _walk_dishes(v, cat_id, out)
    return out


def discover_menu_dishes(menu_tree_json: dict) -> dict:
    """Build `{price_int: (category_id, dish_id)}` from a rest_menu_tree payload."""
    dish_map = _walk_dishes(menu_tree_json)
    log.info(f"Discovered {len(dish_map)} dish denominations: {sorted(dish_map.keys())}")
    return dish_map


def _click_plus_for_amount(page, coupon_amount: int):
    """[Legacy] Click the + button on the card for the given coupon amount.

    Retained only as a fallback / for diagnostic purposes. The real
    `add_to_cart` flow now uses `add_to_cart_via_api` because the React
    onClick handler is silently gated on hidden state and refuses to fire
    `prx_add_prod_to_cart` from automated clicks.
    """
    card_selector = f'.card:has(.card-footer label:text("Γג¬{coupon_amount}.00"))'
    card = page.locator(card_selector).first
    plus_btn = card.locator('input[type="image"]')
    plus_btn.wait_for(state="visible", timeout=ACTION_TIMEOUT)

    log.info(f"Clicking + on Γג¬{coupon_amount} card")
    try:
        with page.expect_response("**/api/main.py", timeout=8000) as resp_info:
            plus_btn.click(force=True)
        return resp_info.value
    except PlaywrightTimeout:
        log.warning(
            "Native click did not trigger API call Γאפ "
            "retrying with JS-dispatched click"
        )
        with page.expect_response("**/api/main.py", timeout=ACTION_TIMEOUT) as resp_info:
            page.evaluate(
                """(amount) => {
                    const cards = [...document.querySelectorAll('.card')];
                    const card = cards.find(c => {
                        const lbl = c.querySelector('.card-footer label');
                        return lbl && lbl.textContent && lbl.textContent.includes(`Γג¬${amount}.00`);
                    });
                    if (!card) throw new Error(`No card for Γג¬${amount}`);
                    const btn = card.querySelector('input[type="image"]');
                    if (!btn) throw new Error(`No + button for Γג¬${amount}`);
                    btn.click();
                }""",
                coupon_amount,
            )
        return resp_info.value


def _sync_local_cart(page, coupon_amount: int, cat_id: int, dish_id: int) -> None:
    """Mirror the server cart into localStorage['cibus-cart_he'].

    The Cibus SPA reads its cart state from localStorage, NOT from
    `prx_get_cart`. Our API-direct add updates the server cart but leaves
    the local store empty Γאפ which makes /preorder redirect back to the
    restaurant list. Writing the entry into localStorage keeps the SPA's
    React state in sync.
    """
    import re
    m = re.search(r'/restaurant/(\d+)', RESTAURANT_URL)
    restaurant_id = int(m.group(1)) if m else None
    cart_entry = {
        "dish_list": [{
            "category_id": cat_id,
            "dish_id": dish_id,
            "dish_price": coupon_amount,
            "co_owner_id": -1,
            "extra_list": [],
            "restaurant_id": restaurant_id,
            "order_type": 2,
        }]
    }
    page.evaluate(
        "(payload) => localStorage.setItem('cibus-cart_he', JSON.stringify(payload))",
        cart_entry,
    )
    log.info(f"Synced localStorage cibus-cart_he with Γג¬{coupon_amount} entry")


def add_to_cart_via_api(page, coupon_amount: int, dish_map: dict) -> None:
    """Add one voucher of the given price by POSTing prx_add_prod_to_cart directly.

    The UI `+` button is gated by hidden React state and silently swallows
    automated clicks (verified across modal-dismissal strategies, fresh
    profiles, and clean carts). The underlying API call works fine Γאפ and
    is what a real click would have produced.

    After the server-side add, we mirror the cart into localStorage so the
    SPA's in-memory store matches and /preorder doesn't bounce us out.
    """
    if coupon_amount not in dish_map:
        raise RuntimeError(
            f"No dish_id known for Γג¬{coupon_amount} Γאפ "
            f"available: {sorted(dish_map.keys())}"
        )
    cat_id, dish_id = dish_map[coupon_amount]
    log.info(
        f"API-add Γג¬{coupon_amount} (category_id={cat_id}, dish_id={dish_id})"
    )
    resp = _post_api(page, {
        "type": "prx_add_prod_to_cart",
        "order_type": 2,
        "dish_list": {
            "category_id": cat_id,
            "dish_id": dish_id,
            "dish_price": coupon_amount,
            "co_owner_id": -1,
            "extra_list": [],
        },
    })
    code = resp.get("code")
    if code != 0:
        raise RuntimeError(
            f"prx_add_prod_to_cart failed: code={code}, msg={resp.get('msg')}"
        )
    log.info(f"prx_add_prod_to_cart OK Γאפ added Γג¬{coupon_amount} to cart")
    _sync_local_cart(page, coupon_amount, cat_id, dish_id)
    take_screenshot(page, f"06_after_add_{coupon_amount}")


def add_to_cart(page, coupon_amount: int, dish_map: dict = None) -> None:
    """Add a voucher to cart. API-direct when `dish_map` is provided, else
    falls back to the legacy click flow (kept for diagnostic comparison).
    """
    if dish_map:
        add_to_cart_via_api(page, coupon_amount, dish_map)
        return

    log.warning(
        "add_to_cart called without dish_map Γאפ falling back to legacy click flow"
    )
    time.sleep(1.0)
    dismiss_error_modal(page)
    take_screenshot(page, f"05b_before_click_{coupon_amount}")

    response = None
    for attempt in range(1, 4):
        try:
            response = _click_plus_for_amount(page, coupon_amount)
            break
        except PlaywrightTimeout:
            log.warning(
                f"+ click attempt {attempt} timed out Γאפ checking for error modal"
            )
            take_screenshot(page, f"05c_timeout_{coupon_amount}_{attempt}")
            closed = dismiss_error_modal(page)
            if not closed:
                log.info("No modal found via DOM Γאפ sending Escape key")
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.4)
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            time.sleep(1.0)
            if attempt == 3:
                raise

    body = response.json()
    log.info(f"prx_add_prod_to_cart response: code={body.get('code')}, msg={body.get('msg')}")
    if body.get("code") != 0:
        raise RuntimeError(f"Failed to add to cart: code={body.get('code')}, msg={body.get('msg')}")
    log.info(f"Added Γג¬{coupon_amount} to cart")
    take_screenshot(page, f"06_after_add_{coupon_amount}")


def navigate_to_checkout(page) -> bool:
    """Go to the preorder URL. Returns True if the confirm button is visible.

    After an API-direct cart add, the front-end React store is out of sync
    with the server cart. Reload the current page first so the SPA refetches
    `prx_get_cart` on init Γאפ otherwise /preorder sees empty front-end state
    and redirects to the restaurants list.
    """
    log.info("Refreshing page state to sync front-end with server cartΓאª")
    try:
        page.reload(wait_until="domcontentloaded")
        time.sleep(2)
    except Exception as e:
        log.warning(f"Page reload before checkout failed (continuing): {e}")

    log.info("Navigating to checkout...")
    page.goto(PREORDER_URL, wait_until="domcontentloaded")
    time.sleep(2)
    take_screenshot(page, "07_preorder_page")

    checkout_ok = False
    if "preorder" in page.url:
        confirm_btn = page.locator('button:has-text("╫נ╫ש╫⌐╫ץ╫¿ ╫פ╫פ╫צ╫₧╫á╫פ")')
        try:
            confirm_btn.wait_for(state="visible", timeout=ACTION_TIMEOUT)
            checkout_ok = True
            log.info("Checkout page stable Γאפ confirm button visible")
        except PlaywrightTimeout:
            log.warning("Checkout page loaded but confirm button not found")
    else:
        log.warning(f"Redirected away from checkout: {page.url}")

    return checkout_ok


def _get_next_order_time(page) -> str:
    """Next available pickup slot as HH:MM.

    Strategy:
      1. Read from the /preorder DOM (the visible slot dropdown / selected text).
      2. Fall back to computing the next 15-minute boundary from local now.

    Voucher-only orders accept any future slot Γאפ the manual HAR used the next
    quarter-hour after cart-add, so we just match that behavior.
    """
    try:
        slot = page.evaluate(
            """() => {
              const sel = document.querySelector('select[name*="time"], select.order-time');
              if (sel && sel.value && /^\\d{1,2}:\\d{2}$/.test(sel.value)) return sel.value;
              const opts = document.querySelectorAll('option, [role="option"]');
              for (const o of opts) {
                const m = (o.textContent || '').trim().match(/^\\d{1,2}:\\d{2}$/);
                if (m) return m[0];
              }
              const all = document.body.innerText || '';
              const m = all.match(/\\b([01]?\\d|2[0-3]):[0-5]\\d\\b/);
              return m ? m[0] : null;
            }"""
        )
        if slot:
            log.info(f"Next order time slot (from DOM): {slot}")
            return slot
    except Exception as e:
        log.warning(f"DOM slot probe failed: {e}")

    # Fallback: next 15-minute boundary from now (Asia/Jerusalem).
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("Asia/Jerusalem"))
    except Exception:
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
    minute = ((now.minute // 15) + 1) * 15
    hour = now.hour
    if minute >= 60:
        minute = 0
        hour = (hour + 1) % 24
    slot = f"{hour:02d}:{minute:02d}"
    log.info(f"Next order time slot (computed): {slot}")
    return slot


def submit_order_via_api(page, order_time: str = None) -> dict:
    """POST prx_simulate_order then prx_apply_order. Returns the deal record.

    This bypasses the UI confirm button entirely. The button is gated by the
    React store having seen a successful prx_simulate_order Γאפ but since we POST
    prx_apply_order directly, that gate is irrelevant.

    Captured shape (HAR-derived, manual Γג¬15 buy 2026-05-28):
      simulate: {"type":"prx_simulate_order","order_time":"12:15"} -> code:0
      apply:    {"type":"prx_apply_order","order_time":"12:15"}    -> deal_id
    """
    slot = order_time or _get_next_order_time(page)

    log.info(f"prx_simulate_order @ {slot}")
    sim = _post_api(page, {"type": "prx_simulate_order", "order_time": slot})
    if sim.get("code") != 0:
        raise RuntimeError(f"prx_simulate_order failed: {sim}")
    log.info(f"  simulate OK: {sim.get('list')}")

    log.info(f"prx_apply_order @ {slot}")
    res = _post_api(page, {"type": "prx_apply_order", "order_time": slot})
    if res.get("code") != 0:
        raise RuntimeError(f"prx_apply_order failed: {res}")
    deal_list = res.get("list") or []
    if not deal_list or "deal_id" not in deal_list[0]:
        raise RuntimeError(f"prx_apply_order returned no deal_id: {res}")
    deal = deal_list[0]
    log.info(
        f"Γ£ו Order placed Γאפ deal_id={deal['deal_id']} "
        f"restaurant_id={deal.get('restaurant_id')} "
        f"result_code={deal.get('result_code')}"
    )
    return deal


def confirm_order(page) -> None:
    """Submit the order via API. Bypasses the disabled-by-default UI button.

    The historic UI flow (click '╫נ╫ש╫⌐╫ץ╫¿ ╫פ╫פ╫צ╫₧╫á╫פ') is preserved as a fallback only Γאפ
    on the API path the button never has to enable.
    """
    log.info("Confirming order via prx_apply_order...")
    try:
        deal = submit_order_via_api(page)
        time.sleep(2)
        take_screenshot(page, "08_after_confirm")
        log.info(f"Γ£ו Purchase completed Γאפ deal_id={deal['deal_id']}")
        return
    except Exception as e:
        log.warning(f"API confirm failed, falling back to UI click: {e}")
    confirm_btn = page.locator('button:has-text("╫נ╫ש╫⌐╫ץ╫¿ ╫פ╫פ╫צ╫₧╫á╫פ")')
    confirm_btn.click()
    page.wait_for_load_state("domcontentloaded")
    time.sleep(3)
    take_screenshot(page, "08_after_confirm")
    log.info("Γ£ו Purchase completed via UI fallback")


def _confirm_deletion(page) -> None:
    """Handle the deletion confirmation dialog after trash click."""
    confirm_selectors = [
        'button:has-text("╫¢╫ƒ, ╫£╫₧╫ק╫ץ╫º")',
        'text="╫¢╫ƒ, ╫£╫₧╫ק╫ץ╫º"',
        ':text("╫£╫₧╫ק╫ץ╫º")',
    ]
    for sel in confirm_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=5000):
                log.info(f"Found confirm-delete button: {sel}")
                btn.click()
                time.sleep(2)
                take_screenshot(page, "10_after_confirm_delete")
                log.info("Confirmed cart item deletion")
                return
        except Exception:
            continue
    log.warning("Could not find confirm-delete button in dialog")
    take_screenshot(page, "10_confirm_delete_failed")


def cleanup_cart(page, context) -> None:
    """Remove ALL items from cart.

    Strategy:
      1. Try the API-direct `prx_del_cart` first Γאפ fast, reliable, server-side,
         and works even if there are no trash icons visible (e.g. cart in a
         stuck state where the preorder page doesn't render line items).
      2. Fall back to the legacy UI trash-icon flow only if the API call fails
         (e.g. the page is on a screen where fetch can't carry session cookies).
    """
    log.info("Cleaning up cart...")

    # Step 1: API-direct delete
    try:
        resp = _post_api(page, {"type": "prx_del_cart"})
        if resp.get("code") == 0:
            log.info("prx_del_cart OK Γאפ server cart cleared via API")
            try:
                page.evaluate("localStorage.setItem('cibus-cart_he', JSON.stringify({dish_list: []}))")
            except Exception as e:
                log.warning(f"Could not clear localStorage cart (continuing): {e}")
            save_session(context)
            return
        log.warning(
            f"prx_del_cart returned code={resp.get('code')} msg={resp.get('msg')} Γאפ "
            "falling back to UI trash flow"
        )
    except Exception as api_err:
        log.warning(f"prx_del_cart API call failed ({api_err}) Γאפ falling back to UI trash flow")

    # Step 2: legacy UI flow
    trash_selectors = [
        'img[src*="icon-trash"]',
        'img[src*="trash"]',
        '[class*="trash"]',
        '[class*="delete"]',
    ]
    if "preorder" not in page.url:
        page.goto(PREORDER_URL, wait_until="domcontentloaded")
        time.sleep(1)

    cleaned = 0
    for iteration in range(1, 21):  # safety cap: never iterate forever
        trash_btn = None
        used_sel = None
        for sel in trash_selectors:
            try:
                candidate = page.locator(sel).first
                if candidate.is_visible(timeout=2000):
                    trash_btn = candidate
                    used_sel = sel
                    break
            except Exception:
                continue
        if trash_btn is None:
            break
        log.info(f"Found trash button ({used_sel}) Γאפ removing item {iteration}")
        take_screenshot(page, f"08_before_trash_click_{iteration}")
        try:
            trash_btn.click()
            time.sleep(2)
            take_screenshot(page, f"09_after_trash_click_{iteration}")
            _confirm_deletion(page)
            cleaned += 1
            time.sleep(1)
        except Exception as e:
            log.warning(f"Trash click failed on iteration {iteration}: {e}")
            break

    if cleaned == 0:
        log.info("No trash buttons found Γאפ cart already empty")
    else:
        log.info(f"Removed {cleaned} cart item(s) via trash icon")

    save_session(context)
    log.info("Session saved")
