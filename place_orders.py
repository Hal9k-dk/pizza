#!/usr/bin/env python3
"""
Automate placing pizza orders on skalborgpizza.dk based on the Google Spreadsheet order list.
Opens a visible browser, fills in all items, then leaves it open for the user to complete checkout.
"""

import argparse
import csv
import os
import re
import sys
import time
from io import StringIO
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv, find_dotenv
from playwright.sync_api import Page, sync_playwright

from extract_orders import extract_orders

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Spreadsheet Menu loader
# ---------------------------------------------------------------------------


def _normalize_name(name: str) -> str:
    """Strip number prefix, punctuation, and whitespace for fuzzy matching."""
    name = re.sub(r'^\d+[A-Za-z]?\.\s*', '', name)       # "71. Durum Kebab" → "Durum Kebab"
    name = re.sub(r'\s*[-–]\s*Nyhed!?$', '', name, flags=re.IGNORECASE)  # drop " - Nyhed!"
    name = re.sub(r'[()]', '', name)                      # "Chili Burger (Hot)" → "Chili Burger Hot"
    name = re.sub(r'\s+', ' ', name).strip().lower()
    return name


def _discover_sheet_gid(sheet_url: str, sheet_name: str) -> str | None:
    """Resolve a Google Sheets tab name to its gid.

    The gid is not available via the public CSV export, but it is embedded in
    the editor page's bootstrap JSON.  This parser looks for the
    ``[n,0,"<gid>",[{"1":[[0,0,"<name>"]]`` sequence that Google Sheets emits
    for each worksheet.

    Returns ``None`` if the sheet cannot be found (e.g. the spreadsheet is
    private or the tab was renamed).
    """
    if requests is None:
        return None

    sheet_id = sheet_url.split("/d/")[1].split("/")[0]
    try:
        resp = requests.get(
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception:
        return None

    # The tab name and gid appear adjacent in the bootstrap data, with the
    # JSON quotes backslash-escaped in the HTML source.
    pattern = re.compile(
        r'\[\d+,0,\\"(\d+)\\",\[\{\\"1\\":\[\[0,0,\\"' + re.escape(sheet_name) + r'\\"\]'
    )
    match = pattern.search(resp.text)
    return match.group(1) if match else None


def load_spreadsheet_menu(sheet_url: str | None = None) -> dict[str, dict]:
    """Load the "Menu" sheet from the order spreadsheet.

    Returns a dict keyed by the identifier used in the Bestilling sheet's
    "Nr" column (just the number / code before the first `` - ``):

        {
          "86": {"name": "Kebab Sandwich", "fuldkorn_flag": False, "price": "70",
                 "price_fuldkorn": "75"},
          "71": {"name": "Durum Kebab",    "fuldkorn_flag": False, "price": "70"},
          ...
        }
    """
    if requests is None:
        raise RuntimeError("The 'requests' package is required to load the spreadsheet menu.")

    # Derive the spreadsheet ID from the ORDER_SHEET_URL.
    if sheet_url is None:
        load_dotenv(find_dotenv(usecwd=True))
        sheet_url = os.getenv("ORDER_SHEET_URL", "")
    sheet_id = sheet_url.split("/d/")[1].split("/")[0]

    gid = os.getenv("MENU_SHEET_GID") or _discover_sheet_gid(sheet_url, "Menu")
    if not gid:
        raise RuntimeError(
            "Could not determine the gid of the 'Menu' sheet. "
            "Set MENU_SHEET_GID in .env or make sure the spreadsheet is public."
        )

    csv_url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
        f"?format=csv&gid={gid}"
    )

    resp = requests.get(csv_url, timeout=15)
    resp.raise_for_status()
    csv_text = resp.content.decode("utf-8")

    menu: dict[str, dict] = {}
    reader = csv.reader(StringIO(csv_text))
    rows = list(reader)

    for row in rows:
        if not row or len(row) < 5:
            continue
        nr = row[0].strip()
        name = row[1].strip() if len(row) > 1 else ""
        fuldkorn_raw = row[2].strip() if len(row) > 2 else ""
        # Col D is the display name, col E is the price
        price = row[4].strip() if len(row) > 4 else ""

        if not nr or not name:
            continue
        # Skip the "0 / zero" placeholder row
        if nr in ("0", "zero"):
            continue
        # Skip rows that look like variant lines (e.g. "129 (10 stk)")
        if "(" in nr and nr[0].isdigit():
            continue

        fuldkorn_flag = fuldkorn_raw.upper() == "TRUE"
        entry = {"name": name, "fuldkorn_flag": fuldkorn_flag, "price": price}

        if nr not in menu:
            # First entry for this key (always the non-Fuldkorn variant)
            menu[nr] = entry
        elif fuldkorn_flag and not menu[nr].get("fuldkorn_flag"):
            # If the first entry wasn't Fuldkorn, store the Fuldkorn price
            menu[nr]["price_fuldkorn"] = price

    return menu


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def login(page: Page, email: str, password: str) -> None:
    """Log in to the pizza site with the given credentials.

    Navigates to the login page, fills in the email and password fields,
    submits the form, and waits until the navigation back to the main site
    (indicating a successful login).
    """
    print("Logging in…")
    page.goto("https://www.skalborgpizza.dk/login-opret", wait_until="domcontentloaded", timeout=30000)
    dismiss_cookie_banners(page)
    page.wait_for_selector("#username", timeout=10000)

    page.fill("#username", email)
    page.fill("#password", password)
    page.click("input.btn_login[value='LOGIND']")

    # Wait for the login form to disappear — successful login redirects away
    # from /login-opret to the main page (or /menukort).
    try:
        page.wait_for_function(
            "() => !window.location.pathname.includes('/login-opret')",
            timeout=15000,
        )
    except Exception as e:
        # Check for error messages on the login page
        error_el = page.query_selector("span.msghide")
        if error_el:
            error_text = error_el.inner_text().strip()
            if error_text:
                print(f"    ✗ Login failed: {error_text}")
            else:
                print("    ✗ Login failed (unknown reason)")
        else:
            print(f"    ✗ Login failed or timed out: {e}")
        raise RuntimeError("Login failed") from e

    print("✓ Logged in successfully\n")


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


def scrape_menu(page: Page) -> tuple[dict, dict, dict]:
    """
    Build lookup maps from the website menu page.

    Returns ``(menu, item_id_index, name_index)`` where:

    * **menu** — keyed by the menu number / named-item identifier:
      ``"9"``, ``"12A"``, ``"Børnepizza 5"``, ``"132"``
    * **item_id_index** — keyed by the raw ``showItemDetails()`` argument
      (e.g. ``"86"``).  Used as a fallback when a numeric key is not
      found in *menu*.
    * **name_index** — maps :func:`_normalize_name` → list of website
      entries, for cross-referencing against the spreadsheet Menu sheet.

    Each value is a dict:
      {
        "item_id":        str,   # argument to showItemDetails()
        "select_prefix":  str,   # text prefix to match in <select> (or None)
        "name":           str,   # raw full_name from the website
      }
    """
    menu = {}
    item_id_index = {}
    name_index: dict[str, list[dict]] = {}
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

        entry = {"item_id": item_id, "select_prefix": None, "name": full_name}

        # Always index by the raw item_id as a fallback — this handles
        # spreadsheets that use the internal item_id (e.g. "86") instead
        # of the menu number (e.g. "76").  Kept in a separate dict to
        # avoid collisions with menu numbers (e.g. item_id=4 is "Cocio"
        # while menu number "4" is "Capricciosa").
        item_id_index[item_id] = entry

        # --- numbered items: "3. Pepperoni", "12A. Freyas Pizza", …
        # Also match lowercase suffixes like "75a."
        nm = re.match(r'^(\d+[A-Za-z]?)\.\s+', full_name)
        if nm:
            num = nm.group(1)
            menu[num] = entry
        else:
            # --- named items without a number prefix (Børnepizza X, Pommes Frites, …)
            menu[full_name] = entry

        # --- name-based index for cross-referencing with the spreadsheet ---
        norm = _normalize_name(full_name)
        name_index.setdefault(norm, []).append(entry)

        # Check description for numbered variants like "Valg: 131. Lille, 132. Stor"
        desc_el = item.query_selector(".itemdescription")
        if desc_el:
            desc = desc_el.inner_text()
            for vm in re.finditer(r'(\d+)\.\s+(\S+)', desc):
                variant_num = vm.group(1)
                menu[variant_num] = {
                    "item_id": item_id,
                    "select_prefix": variant_num,  # match option text that starts with this
                    "name": full_name,
                }

    return menu, item_id_index, name_index


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


def _pickup_selected_js() -> str:
    """Return JS that checks whether the 'Afhentning' order type is selected.

    Must match the *button* itself (exact text "Afhentning" and the
    ``orderTypeBtnSelected`` class), not the cart/delivery containers that
    merely list both "Udbringning" and "Afhentning".
    """
    return """
        () => Array.from(document.querySelectorAll('a, button, span, div, input, select'))
            .some(el => (el.innerText || el.value || '').trim() === 'Afhentning'
                && el.classList
                && el.classList.contains('orderTypeBtnSelected'))
    """


def is_pickup_selected(page: Page) -> bool:
    """Whether the current page has 'Afhentning' selected."""
    return bool(page.evaluate(_pickup_selected_js()))


def wait_for_menu_ready(page: Page) -> None:
    """Wait until the actual menu page is interactive."""
    page.wait_for_function(
        """
        () => {
            const items = document.querySelectorAll("li[onclick*='showItemDetails']");
            const controls = Array.from(document.querySelectorAll('a, button, span, div, input[type=button], input[type=submit]'));
            const pickupSelected = controls.some(el => (el.innerText || el.value || '').trim() === 'Afhentning'
                && el.classList && el.classList.contains('orderTypeBtnSelected'));
            const pickupAvailable = controls.some(el => (el.innerText || el.value || '').trim() === 'Afhentning');
            return items.length > 0
                && typeof window.showItemDetails === 'function'
                && (pickupSelected || pickupAvailable);
        }
        """,
        timeout=20000,
    )


def choose_pickup(page: Page) -> None:
    """Ensure Afhentning is selected and the delivery/pickup colorbox is dismissed."""
    # Check whether the delivery/pickup colorbox is still open — this is the
    # modal that asks "Udbringning eller hent selv?".  If it's still visible
    # with the Afhentning button inside it, we MUST click that button to
    # actually select pickup and close the colorbox.  The old "already_pickup"
    # check matched the button text *inside* the still-open dialog, causing a
    # false positive that left the overlay blocking all menu interaction.
    colorbox_visible = page.evaluate(
        """
        () => {
            const overlay = document.querySelector('#cboxOverlay');
            const colorbox = document.querySelector('#colorbox');
            return {
                overlay_display: overlay ? window.getComputedStyle(overlay).display : 'missing',
                colorbox_display: colorbox ? window.getComputedStyle(colorbox).display : 'missing',
            };
        }
        """
    )
    dialog_is_open = (
        colorbox_visible.get("overlay_display") != "none"
        and colorbox_visible.get("colorbox_display") != "none"
    )

    if not dialog_is_open:
        # Colorbox is already closed — check if Afhentning is selected on the page
        if is_pickup_selected(page):
            print("✓ 'Afhentning' already selected")
            return

    # Colorbox with the delivery/pickup dialog is still open (or Afhentning
    # wasn't selected yet) — click the Afhentning button inside it.
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

    # With $.fx.off=true the colorbox should close instantly, but if the
    # overlay still lingers (e.g. the site JS closed the content but not the
    # overlay), forcibly remove it so it doesn't block further interactions.
    try:
        page.wait_for_selector("#cboxOverlay", state="hidden", timeout=8000)
    except Exception:
        print("    ⚠ overlay still visible after pickup selection — forcibly removing it")
        page.evaluate("""
            () => {
                const overlay = document.querySelector('#cboxOverlay');
                if (overlay) overlay.style.display = 'none';
                const colorbox = document.querySelector('#colorbox');
                if (colorbox) colorbox.style.display = 'none';
            }
        """)

    # Verify Afhentning actually became selected.  changemenucard() reloads
    # the page after setting the order type, so this waits for the new page
    # state to settle before confirming.
    try:
        page.wait_for_function(_pickup_selected_js(), timeout=15000)
    except Exception as e:
        raise RuntimeError(
            "Clicked 'Afhentning' but it did not become selected"
        ) from e


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
        "button.cc-nb-okagree",
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
                    const pickupSelected = controls.some(el => (el.innerText || el.value || '').trim() === 'Afhentning'
                        && el.classList && el.classList.contains('orderTypeBtnSelected'));
                    const pickupAvailable = controls.some(el => (el.innerText || el.value || '').trim() === 'Afhentning');
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

def place_orders(orders: list[dict], pizza_url: str,
                 email: str | None = None, password: str | None = None) -> None:
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

        # Login first if credentials are provided
        if email and password:
            login(page, email, password)

        open_working_menu_page(page, pizza_url)

        # 1. Dismiss cookie consent if present
        cookie_dismissals = dismiss_cookie_banners(page)
        if cookie_dismissals:
            print(f"✓ Dismissed cookie banner {cookie_dismissals} time(s)")

        wait_for_menu_ready(page)

        # 1.5. Kill jQuery animations early — this makes the colorbox close
        # animation instant when we click "Afhentning" below.  We must also
        # re-apply this AFTER choose_pickup because clicking Afhentning resets
        # the page's JavaScript context (losing $.fx.off and our overrides).
        page.evaluate("$.fx.off = true;")

        # 2. Dismiss the "Udbringning eller hent selv?" chooser — choose Afhentning
        choose_pickup(page)
        cookie_dismissals = dismiss_cookie_banners(page)
        if cookie_dismissals:
            print(f"✓ Dismissed cookie banner {cookie_dismissals} time(s) after pickup selection")
        else:
            print("COOKIE DEBUG no explicit accept-all cookie button found after pickup selection")
        wait_for_menu_ready(page)
        print("✓ Selected 'Afhentning' (pickup)\n")

        # Re-apply $.fx.off and showItemToppings override — the choose_pickup
        # click resets the page's JavaScript context, so we must re-install.
        page.evaluate("""
            () => {
                $.fx.off = true;
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
                    ).fail(function() {
                        window._toppingXHR = null;
                        window._toppingsDone = true;
                    });
                };
            }
        """)

        # 3. Load the spreadsheet Menu sheet (authoritative item list)
        try:
            ss_menu = load_spreadsheet_menu()
            print(f"✓ Spreadsheet Menu loaded: {len(ss_menu)} items\n")
        except Exception as e:
            print(f"⚠ Could not load spreadsheet Menu: {e}")
            ss_menu = {}

        # 4. Scrape the website menu
        menu, item_id_index, name_index = scrape_menu(page)
        print(f"✓ Website menu scraped: {len(menu)} entries "
              f"(+ {len(item_id_index)} by item_id, "
              f"{len(name_index)} by name)\n")

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

            entry = None

            # Does the spreadsheet Menu know about this key?
            ss_item = ss_menu.get(key)

            if ss_item is not None:
                # --- Spreadsheet-driven lookup: find the website item by
                #     matching the canonical name from the Menu sheet ---
                ss_name_norm = _normalize_name(ss_item["name"])
                name_matches = name_index.get(ss_name_norm, [])
                if len(name_matches) == 1:
                    entry = name_matches[0]
                elif len(name_matches) > 1:
                    # Multiple matches — prefer the one whose menu number
                    # matches the spreadsheet key.
                    for candidate in name_matches:
                        if menu.get(key) is candidate:
                            entry = candidate
                            break
                    if entry is None:
                        entry = name_matches[0]

                # Fallback: if name matching didn't work, try direct menu
                # lookup (handles variant numbers like "131" and spelling
                # mismatches like "Nansy" vs "Nancy" on the website).
                # Deliberately NOT using item_id_index here — that would
                # create false positives (e.g. "86" → item_id=86 → "Stærk
                # Durum" which is a different item than "Kebab Sandwich").
                if entry is None:
                    entry = menu.get(key)
            else:
                # --- Website-driven lookup (key not in spreadsheet Menu) ---
                entry = menu.get(key)
                if entry is None:
                    # Fall back to raw website item_id (some spreadsheets
                    # use the internal site ID instead of the menu number).
                    entry = item_id_index.get(key)

            if entry is None:
                if ss_item is not None:
                    print(f"    ✗ '{ss_item['name']}' (key {key!r}) "
                          f"is in the spreadsheet Menu but NOT on the website — skipping\n")
                else:
                    print(f"    ✗ Item {key!r} not found in menu — skipping\n")
                continue

            item_id       = entry["item_id"]
            select_prefix = nr_variant
            if select_prefix is None and ss_item is not None:
                if ss_item.get("fuldkorn_flag"):
                    select_prefix = "Fuldkorn"
            if select_prefix is None:
                select_prefix = entry["select_prefix"]

            # --- Open item detail modal ------------------------------------
            item_start = time.monotonic()
            try:
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
                page.click(f"li[onclick='showItemDetails({item_id})']", timeout=5000)
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
                elapsed = time.monotonic() - item_start
                print(f"    ✓ Added to cart ({elapsed:.1f}s)\n")
            except Exception as e:
                elapsed = time.monotonic() - item_start
                print(f"    ✗ FAILED after {elapsed:.1f}s: {e}")
                # Force-close any open colorbox overlay so subsequent items can proceed
                page.evaluate("""
                    () => {
                        try { if (typeof $ !== 'undefined' && $.colorbox) $.colorbox.close(); } catch(e) {}
                        const el = document.querySelector('#cboxOverlay');
                        if (el) el.style.display = 'none';
                        const cb = document.querySelector('#colorbox');
                        if (cb) cb.style.display = 'none';
                    }
                """)
                continue

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
    parser.add_argument("--email", "-e", help="Login email (overrides PIZZA_EMAIL in .env)")
    parser.add_argument("--password", help="Login password (overrides PIZZA_PASSWORD in .env)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress order summary output")

    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    pizza_url = args.pizza_url or os.getenv("PIZZA_PLACE")
    if not pizza_url:
        print("✗ PIZZA_PLACE not set in .env and --pizza-url not provided")
        sys.exit(1)

    email = args.email or os.getenv("PIZZA_EMAIL")
    password = args.password or os.getenv("PIZZA_PASSWORD")

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

    place_orders(orders, pizza_url, email=email, password=password)


if __name__ == "__main__":
    main()
