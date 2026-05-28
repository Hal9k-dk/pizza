#!/usr/bin/env python3
"""
Automate placing pizza orders on skalborgpizza.dk based on the Google Spreadsheet order list.
Opens a visible browser, fills in all items, then leaves it open for the user to complete checkout.
"""

import argparse
import os
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

from extract_orders import extract_orders

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def normalize_menu_url(pizza_url: str) -> str:
    """
    Turn the configured pizza URL into the actual menu page URL.

    Users tend to paste either the site root, the real menu URL, or some other
    ordering/landing page path copied from the browser. The automation only
    works on the legacy menu page, so normalize everything to /menukort at the
    site root.
    """
    parsed = urlsplit(pizza_url.strip())
    base_path = parsed.path.rstrip("/")

    if base_path.endswith("/menukort"):
        menu_path = base_path
    else:
        menu_path = "/menukort"

    return urlunsplit((parsed.scheme, parsed.netloc, menu_path, "", ""))


# ---------------------------------------------------------------------------
# Menu scraping
# ---------------------------------------------------------------------------


def scrape_menu(page: Page) -> dict:
    """
    Build a lookup map from the menu page.

    Returns a dict keyed by the identifier used in the spreadsheet's "Nr" field:
      - "9"        → numbered pizza  (e.g. "9 - Rose")
      - "12A"      → alphanumeric    (e.g. "12A - Freyas Pizza")
      - "Børnepizza 5" → named item  (e.g. "Børnepizza 5 - Børnepizza 5")
      - "132"      → sub-variant     (e.g. "132 - Pommes Frites Stor")

    Each value is a dict:
      {
        "item_id":        str,   # argument to showItemDetails()
        "select_prefix":  str,   # text prefix to match in <select> (or None = first option)
      }
    """
    menu = {}
    items = page.query_selector_all("li[onclick*='showItemDetails']")

    for item in items:
        onclick = item.get_attribute("onclick") or ""
        m = re.search(r'showItemDetails\((\d+)\)', onclick)
        if not m:
            continue
        item_id = m.group(1)

        name_els = item.query_selector_all(".itemname")
        names = [el.inner_text().strip() for el in name_els if el.inner_text().strip()]
        if not names:
            continue
        full_name = names[-1]  # last .itemname is the actual pizza name

        # --- numbered items: "3. Pepperoni", "12A. Freyas Pizza", …
        nm = re.match(r'^(\d+[A-Z]?)\.\s+', full_name)
        if nm:
            num = nm.group(1)
            menu[num] = {"item_id": item_id, "select_prefix": None}
            continue

        # --- named items without a number prefix (Børnepizza X, Pommes Frites, …)
        menu[full_name] = {"item_id": item_id, "select_prefix": None}

        # Check description for numbered variants like "Valg: 131. Lille, 132. Stor"
        desc_el = item.query_selector(".itemdescription")
        if desc_el:
            desc = desc_el.inner_text()
            for vm in re.finditer(r'(\d+)\.\s+(\S+)', desc):
                variant_num = vm.group(1)
                menu[variant_num] = {
                    "item_id": item_id,
                    "select_prefix": variant_num,  # match option text that starts with this
                }

    return menu


# ---------------------------------------------------------------------------
# Helpers for the item detail modal
# ---------------------------------------------------------------------------

def select_variant(page: Page, select_prefix: str | None) -> None:
    """Select the right size/variant in the <select id='spacial_itm'>.

    show_item_details.php already triggers showItemToppings() for the default
    variant via an inline <script>, so we only call select_option() when a
    *different* variant is needed — calling it unnecessarily would fire
    showItemToppings() a second time and overwrite any checkboxes we already
    ticked.  Either way we wait for checkboxes to appear in #divToppings.

    select_prefix matching:
      - None          → pick the first option whose text starts with "alm" (normal size)
      - "132"         → numbered variant: pick option whose text starts with "132"
      - "Fuldkorn"    → named variant: startswith tried first, then case-insensitive
                        contains (matches "Alm. Fuldkorn - 75.00")
    """
    select = page.query_selector("#spacial_itm")
    if not select:
        # No size select — toppings load automatically from show_item_details.php
        pass
    else:
        options = select.query_selector_all("option")
        if options:
            if select_prefix is None:
                target = next(
                    (opt for opt in options
                     if opt.inner_text().strip().lower().startswith("alm")),
                    options[0]
                )
            else:
                # Try startswith first (numeric variants like "132")
                target = next(
                    (opt for opt in options
                     if opt.inner_text().strip().startswith(select_prefix)),
                    None
                )
                # Fall back to case-insensitive contains (named variants like "Fuldkorn")
                if target is None:
                    target = next(
                        (opt for opt in options
                         if select_prefix.lower() in opt.inner_text().strip().lower()),
                        None
                    )
                if target is None:
                    print(f"    ⚠ variant '{select_prefix}' not found; using first option")
                    target = options[0]

            target_value = target.get_attribute("value")
            current_value = select.evaluate("el => el.value")
            if current_value != target_value:
                # Only switch if needed — avoids a redundant showItemToppings call
                select.select_option(value=target_value)

    # Wait for toppings to finish loading (triggered either by show_item_details.php
    # inline script or by the select_option onchange above).
    # Use _toppingsDone flag (set by our showItemToppings override) so this works
    # for items with no toppings too (which never get any checkboxes).
    page.wait_for_function("() => window._toppingsDone === true", timeout=8000)


def apply_modifications(page: Page, mods_text: str) -> None:
    """
    Tick the extra-topping checkboxes that match the comma-separated
    modifications string from the spreadsheet (e.g. "chili, hvidløg").
    Uses case-insensitive keyword matching.

    Must be called AFTER select_variant(), since toppings are loaded
    dynamically via showItemToppings() when the variant select changes.
    Operates via page.evaluate to avoid stale element handle issues.
    """
    if not mods_text:
        return

    mods = [m.strip().lower() for m in mods_text.split(",") if m.strip()]
    if not mods:
        return

    # Wait for toppings to be present
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('#cboxLoadedContent input[type=checkbox]').length > 0",
            timeout=5000
        )
    except Exception:
        print("    ⚠ no checkboxes appeared — skipping modifications")
        return

    # Do matching and checking entirely in JS to avoid stale element handle issues
    results = page.evaluate(
        """
        (mods) => {
            const results = [];
            const checkboxes = document.querySelectorAll(
                '#cboxLoadedContent input[type=checkbox]'
            );
            for (const cb of checkboxes) {
                const labelText = cb
                    .closest('label')
                    ?.querySelector('.poptext')
                    ?.innerText
                    ?.trim() ?? '';
                const labelLower = labelText.toLowerCase();
                for (const mod of mods) {
                    const words = mod.split(' ');
                    if (words.every(w => labelLower.includes(w))) {
                        if (!cb.checked) {
                            cb.checked = true;
                            cb.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                        results.push({ mod, label: labelText, found: true });
                        break;
                    }
                }
            }
            // Report unmatched mods
            const matchedMods = new Set(results.map(r => r.mod));
            for (const mod of mods) {
                if (!matchedMods.has(mod)) results.push({ mod, label: null, found: false });
            }
            return results;
        }
        """,
        mods,
    )

    for r in results:
        if r["found"]:
            print(f"    ✓ modification: {r['label']!r}")
        else:
            print(f"    ⚠ no checkbox found for modification: {r['mod']!r}")


def add_to_cart(page: Page) -> None:
    """Click the Tilføj (add to cart) button and wait for the modal to close."""
    page.click("input[value='Tilføj']")
    # With $.fx.off=true the overlay disappears as soon as the AJAX completes.
    page.wait_for_selector("#cboxOverlay", state="hidden", timeout=10000)


def wait_for_menu_ready(page: Page) -> None:
    """Wait until the actual menu page is interactive."""
    page.wait_for_function(
        """
        () => {
            const items = document.querySelectorAll("li[onclick*='showItemDetails']");
            const controls = Array.from(document.querySelectorAll('a, button, span, div, input[type=button], input[type=submit]'));
            const pickupSelected = controls.some(el => /afhentning/i.test((el.innerText || el.value || '').trim())
                && /orderTypeBtnSelected|delpick|cartdelpick/.test(el.className || ''));
            const pickupAvailable = controls.some(el => /afhentning/i.test((el.innerText || el.value || '').trim()));
            return items.length > 0
                && typeof window.showItemDetails === 'function'
                && (pickupSelected || pickupAvailable);
        }
        """,
        timeout=20000,
    )


def choose_pickup(page: Page) -> None:
    """Ensure Afhentning is selected in the order-type UI."""
    already_pickup = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a, button, span, div'))
            .some(el => /afhentning/i.test((el.innerText || '').trim())
                && /orderTypeBtnSelected|delpick|cartdelpick/.test(el.className || ''))
        """
    )
    if already_pickup:
        print("✓ 'Afhentning' already selected")
        return

    pickup_popup_button = "#colorbox input[value='Afhentning']"
    pickup_page_link_candidates = [
        "a[onclick='changemenucard(2);']",
        "a:has-text('Afhentning')",
        "button:has-text('Afhentning')",
        "input[type=button][value='Afhentning']",
        "input[type=submit][value='Afhentning']",
    ]

    if page.locator(pickup_popup_button).count():
        page.click(pickup_popup_button, timeout=5000)
    else:
        clicked = False
        for selector in pickup_page_link_candidates:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible(timeout=1000):
                    locator.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            raise RuntimeError("Could not find an Afhentning control on the menu page")

    try:
        page.wait_for_selector("#cboxOverlay", state="hidden", timeout=15000)
    except Exception:
        pass


def debug_cookie_controls(page: Page) -> None:
    """Print visible cookie-related controls so we can debug the exact banner behavior."""
    frames = [page.main_frame] + list(page.frames)
    seen = set()

    for frame in frames:
        try:
            controls = frame.evaluate(
                """
                () => {
                    const nodes = Array.from(document.querySelectorAll('button, a, input[type=button], input[type=submit]'));
                    return nodes
                        .map((el, idx) => {
                            const text = (el.innerText || el.value || '').trim();
                            const attrs = [
                                el.id ? `#${el.id}` : '',
                                el.className ? `.${String(el.className).trim().replace(/\\s+/g, '.')}` : '',
                                el.getAttribute('aria-label') || '',
                                el.getAttribute('title') || '',
                            ].filter(Boolean).join(' ');
                            const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                            return { idx, text, attrs, visible };
                        })
                        .filter(x => x.visible)
                        .filter(x => {
                            const hay = `${x.text} ${x.attrs}`.toLowerCase();
                            return hay.includes('cookie')
                                || hay.includes('accept')
                                || hay.includes('accepter')
                                || hay.includes('tillad')
                                || hay.includes('consent')
                                || hay.includes('privacy')
                                || hay.includes('indstillinger')
                                || hay.includes('settings');
                        })
                        .slice(0, 20);
                }
                """
            )
        except Exception:
            continue

        for control in controls:
            key = (frame.url, control["text"], control["attrs"])
            if key in seen:
                continue
            seen.add(key)
            frame_name = "main-frame" if frame == page.main_frame else f"frame:{frame.url}"
            print(
                f"COOKIE DEBUG [{frame_name}] text={control['text']!r} attrs={control['attrs']!r}"
            )


def dismiss_cookie_banner(page: Page) -> bool:
    """Dismiss only explicit accept-all cookie banners; avoid broad clicks that open settings."""
    selectors = [
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "#CybotCookiebotDialogBodyButtonAccept",
        "button#onetrust-accept-btn-handler",
        "button:has-text('Jeg accepterer')",
        "button:has-text('Accepter alle')",
        "button:has-text('Accept all')",
        "button:has-text('Tillad alle')",
        "input[type=button][value='Jeg accepterer']",
        "input[type=button][value='Accepter alle']",
        "input[type=button][value='Accept all']",
        "input[type=button][value='Tillad alle']",
    ]

    debug_cookie_controls(page)

    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        for selector in selectors:
            try:
                locator = frame.locator(selector).first
                if locator.count() and locator.is_visible(timeout=1000):
                    label = (locator.inner_text(timeout=1000) or "").strip() if 'input[' not in selector else selector
                    frame_name = "main-frame" if frame == page.main_frame else frame.url
                    print(f"COOKIE DEBUG clicking {selector!r} on {frame_name}: {label!r}")
                    locator.click(timeout=3000)
                    time.sleep(1)
                    return True
            except Exception:
                continue

    return False


def dismiss_cookie_banners(page: Page, attempts: int = 2) -> int:
    """Dismiss cookie banners a couple of times in case the site shows a second real consent layer."""
    dismissed = 0
    for _ in range(attempts):
        if page.is_closed():
            break
        if not dismiss_cookie_banner(page):
            break
        dismissed += 1
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        time.sleep(1)
    return dismissed


def wait_for_real_site(page: Page, timeout_ms: int = 120000) -> bool:
    """
    Wait through the anti-bot/front-page interstitial until the actual site loads.

    Returns True once the interstitial is gone, False if it never clears within
    the timeout.
    """
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        if page.is_closed():
            raise RuntimeError("Browser page was closed while waiting for the site to load")

        try:
            body_text = (page.locator("body").inner_text(timeout=3000) or "").strip()
        except Exception:
            time.sleep(1)
            continue

        interstitial = (
            "Please wait while your request is being verified" in body_text
            or "One moment, please" in body_text
        )
        if not interstitial:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            time.sleep(1.5)
            return True

        time.sleep(1)

    return False


def prompt_manual_site_ready(page: Page) -> None:
    """Let the user manually clear the verification/front page in the visible browser."""
    print()
    print("⚠ The site is still showing its verification/front page.")
    print("  Please use the opened browser window to complete any manual step needed")
    print("  until the real menu page is visible, then press Enter here to continue.")
    print("  If needed, navigate the browser to the menu page manually.")
    input()
    if page.is_closed():
        raise RuntimeError("Browser page was closed before the real menu page became available")


def open_working_menu_page(page: Page, pizza_url: str) -> None:
    """
    Open the pizza site robustly.

    The configured URL may land on a front page/interstitial first, and the real
    ordering UI may only become available after a delay/reload or after manually
    navigating to /menukort.
    """
    menu_url = normalize_menu_url(pizza_url)
    candidates = []
    for url in [pizza_url.strip(), menu_url]:
        if url and url not in candidates:
            candidates.append(url)

    def menu_ready() -> bool:
        if page.is_closed():
            return False
        try:
            return page.evaluate(
                """
                () => {
                    const items = document.querySelectorAll("li[onclick*='showItemDetails']");
                    const controls = Array.from(document.querySelectorAll('a, button, span, div, input[type=button], input[type=submit]'));
                    const pickupSelected = controls.some(el => /afhentning/i.test((el.innerText || el.value || '').trim())
                        && /orderTypeBtnSelected|delpick|cartdelpick/.test(el.className || ''));
                    const pickupAvailable = controls.some(el => /afhentning/i.test((el.innerText || el.value || '').trim()));
                    return items.length > 0
                        && typeof window.showItemDetails === 'function'
                        && (pickupSelected || pickupAvailable);
                }
                """
            )
        except Exception:
            return False

    for url in candidates:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        dismiss_cookie_banners(page)
        if not wait_for_real_site(page):
            prompt_manual_site_ready(page)
        dismiss_cookie_banners(page)

        for _ in range(3):
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            dismiss_cookie_banners(page)

            if menu_ready():
                return

            if "/menukort" not in page.url.rstrip("/"):
                page.goto(menu_url, wait_until="domcontentloaded", timeout=60000)
                dismiss_cookie_banners(page)
                if not wait_for_real_site(page, timeout_ms=30000):
                    prompt_manual_site_ready(page)
                dismiss_cookie_banners(page)
            else:
                time.sleep(2)
                dismiss_cookie_banners(page)

            if menu_ready():
                return

    raise RuntimeError(
        "Configured pizza URL did not open the expected menu page. "
        f"Opened: {page.url}"
    )


# ---------------------------------------------------------------------------
# Main ordering flow
# ---------------------------------------------------------------------------

def place_orders(orders: list[dict], pizza_url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 960},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            ),
            locale="da-DK",
        )
        page = context.new_page()
        page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            """
        )
        open_working_menu_page(page, pizza_url)

        # 1. Dismiss cookie consent if present
        cookie_dismissals = dismiss_cookie_banners(page)
        if cookie_dismissals:
            print(f"✓ Dismissed cookie banner {cookie_dismissals} time(s)")

        wait_for_menu_ready(page)

        # 2. Dismiss the "Udbringning eller hent selv?" chooser — choose Afhentning
        choose_pickup(page)
        cookie_dismissals = dismiss_cookie_banners(page)
        if cookie_dismissals:
            print(f"✓ Dismissed cookie banner {cookie_dismissals} time(s) after pickup selection")
        else:
            print("COOKIE DEBUG no explicit accept-all cookie button found after pickup selection")
        wait_for_menu_ready(page)
        print("✓ Selected 'Afhentning' (pickup)\n")

        # 3. Scrape the menu
        menu = scrape_menu(page)
        print(f"✓ Menu scraped: {len(menu)} entries\n")

        # 4. Kill animations so each order processes ~800 ms faster
        page.evaluate("""
            () => {
                // Disable all jQuery duration-based animations ($.animate, $.fadeTo, etc.)
                // Callbacks still fire — they just fire instantly.
                $.fx.off = true;

                // Replace showItemToppings to:
                //  a) remove the 200 ms setTimeout so load_toppings AJAX fires immediately
                //  b) avoid .promise().done() which fires the callback TWICE in jQuery 1.8
                //     when $.fx.off=true (a known jQuery 1.8 bug), causing two AJAX calls
                //  c) abort any in-flight toppings request before starting a new one, so
                //     the inline <script> in show_item_details.php and our select_option()
                //     onchange can never race each other
                //  d) set window._toppingsDone=true when the AJAX completes, so Python can
                //     reliably wait for toppings to load even on items with no checkboxes
                window._toppingXHR = null;
                window._toppingsDone = false;
                window.showItemToppings = function(proId, childProductId) {
                    if (window._toppingXHR) {
                        window._toppingXHR.abort();
                        window._toppingXHR = null;
                    }
                    window._toppingsDone = false;
                    const loader = '<div class="popupshow" style="margin:0 auto;text-align:center">'
                                 + '<img src="assets/images/loader/loader-2_food.gif" '
                                 + 'style="margin:0;height:200px"/></div>';
                    jQuery('#divToppings').html(loader);
                    colorboxResize(true);
                    window._toppingXHR = jQuery.post(
                        'view/combined-theme-1/includes/ajax/load_toppings.php',
                        { proId: proId, childProductId: childProductId },
                        function(data) {
                            window._toppingXHR = null;
                            window._toppingsDone = true;
                            jQuery('#divToppings').html(data);
                            colorboxResize(true);
                            calculateRuntimePrice();
                        }
                    );
                };
            }
        """)

        # 5. Process each order
        for idx, order in enumerate(orders, 1):
            navn   = order.get("Navn", "?")
            nr     = order.get("Nr", "")
            mods   = order.get("Tilbehør") or ""
            price  = order.get("Pris (tekst)", "")

            print(f"[{idx}/{len(orders)}] {navn}: {nr}  {price}")
            if mods:
                print(f"    modifications: {mods}")

            # --- Resolve the item ----------------------------------------
            # Nr format: "9 - Rose", "12A - Freyas Pizza",
            #            "Børnepizza 5 - Børnepizza 5", "132 - Pommes Frites Stor"
            # Fuldkorn format: "71 - Durum Kebab - Fuldkorn"
            #   → key="71", select_prefix overridden to "Fuldkorn"
            nr_parts = nr.split(" - ")
            key = nr_parts[0].strip()  # "9", "12A", "Børnepizza 5", "132"
            # A third segment (e.g. "Fuldkorn") overrides the menu's select_prefix
            nr_variant = nr_parts[2].strip() if len(nr_parts) > 2 else None

            if key not in menu:
                print(f"    ✗ Item {key!r} not found in menu — skipping\n")
                continue

            entry         = menu[key]
            item_id       = entry["item_id"]
            select_prefix = nr_variant if nr_variant else entry["select_prefix"]

            # --- Open item detail modal ------------------------------------
            # Clear #product_id first so wait_for_function below cannot resolve
            # with stale content left over from the previous modal (same item_id
            # would otherwise match immediately before show_item_details.php responds).
            page.evaluate(
                """
                () => {
                    const el = document.querySelector('#product_id');
                    if (el) el.value = '';
                }
                """
            )
            page.click(f"li[onclick='showItemDetails({item_id})']")
            # Now wait for show_item_details.php to respond and populate fresh content.
            page.wait_for_function(
                f"() => document.querySelector('#product_id')?.value === '{item_id}'",
                timeout=10000
            )

            # --- Select variant (size) ------------------------------------
            select_variant(page, select_prefix)

            # --- Tick modification checkboxes ----------------------------
            apply_modifications(page, mods)

            # --- Add to cart ---------------------------------------------
            add_to_cart(page)
            print("    ✓ Added to cart\n")

        print("=" * 60)
        print(f"✓ All {len(orders)} orders added to cart.")
        print("  The browser is open — complete and submit the order.")
        print("  Press Enter here when done to close the browser.")
        print("=" * 60)
        try:
            input()
        except EOFError:
            print("No interactive stdin available; closing browser.")
        context.close()
        browser.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Place pizza orders on skalborgpizza.dk"
    )
    parser.add_argument("--pizza-url", "-p", default="https://skalborgpizza.dk/", help="Pizza place URL (default: https://skalborgpizza.dk/)")
    parser.add_argument("--sheet-url", "-s", help="Google Sheets URL (overrides .env)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress order summary output")

    args = parser.parse_args()

    load_dotenv()
    pizza_url = args.pizza_url or os.getenv("PIZZA_PLACE")
    if not pizza_url:
        print("✗ PIZZA_PLACE not set in .env and --pizza-url not provided")
        sys.exit(1)

    print("Fetching orders from Google Spreadsheet…")
    orders = extract_orders(url=args.sheet_url, output_format=None)  # silent, returns list
    if not orders:
        print("No orders found.")
        sys.exit(1)

    if not args.quiet:
        print(f"Found {len(orders)} orders:\n")
        for o in orders:
            mods = f"  [{o['Tilbehør']}]" if o.get("Tilbehør") else ""
            print(f"  {o['Navn']}: {o['Nr']}{mods}")
        print()

    place_orders(orders, pizza_url)


if __name__ == "__main__":
    main()
