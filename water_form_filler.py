"""
Fill (but never submit) the PHO Well Water Testing Portal requisition form.

    https://www.publichealthontario.ca/laboratory-services/well-water-testing/portal?tab=0

Setup (once):
    pip install playwright
    playwright install chromium

Optional (to scan the barcode with a webcam instead of typing it, see
water_form_camera.py for details):
    pip install opencv-python zxing-cpp

Usage:
    python water_form_filler.py

What it does:
    1. Tries to open your webcam and scan the barcode on the sample
       bottle (Code 128, confirmed against a real photo of one). Once
       the same value has been read a few frames in a row, that moment
       is used as the collection date/time -- since you scan right after
       collecting. If no camera/libraries are available, or you press
       ESC/q, it falls back to typing the barcode and collection
       date/time by hand.
    2. Opens a real, visible browser window and fills in the form using
       the fixed data from water_form_config.py plus the barcode/date/time
       from step 1.
    3. Stops. It does NOT tick the "I agree to the Terms and Conditions"
       box and does NOT click Submit -- you review the filled form
       yourself and submit it manually if everything looks right.

This version was written against the live page's actual DOM (inspected
directly, id by id -- not guessed from rendered text), so it targets
fields by their HTML `id` rather than by label text. Notes from that
inspection:

  - All dropdowns (Street Type, Street Direction, Township/Municipality,
    County/District, Public Health Unit) are Semantic UI "search
    selection dropdown" widgets: the real <select> is hidden
    (display:none) and a jQuery-driven custom widget sits in front of
    it. Playwright's select_option() can't interact with a hidden
    element, so these are set via the page's own jQuery/Semantic UI API
    (`$(wrapper).dropdown('set selected', text)`), which is also what
    the site's own code uses. The option text must match the dropdown's
    visible text *exactly*, including case.
  - "Address of water source is the same as the mailing address" starts
    disabled and only becomes clickable once the mailing address's
    required fields (street no./name, city, state) are filled -- so the
    water-source section is only handled after the mailing section.
  - The phone field has no <label> at all (not even aria-label); it's
    filled by id like everything else. It has a JS input mask, but a
    bulk fill() reformats it correctly (verified live).
  - Postal Code also has a JS input mask, but -- unlike Phone -- a bulk
    fill() confuses it into silently clearing the field (verified live).
    It's filled by simulating real keystrokes (press_sequentially)
    instead, which formatted it correctly in isolated testing, though
    this mask seemed sensitive to page state in some of my tests. Every
    fill (not just Postal Code) is read back and verified afterwards --
    a wrong or empty value prints [FAILED] instead of failing silently,
    so if this one still needs a manual fix you'll know immediately.

Still, a government site can change its markup at any time -- if a
field comes back [FAILED], the printed exception will show what
Playwright actually saw, which is the fastest way to fix the selector.
"""
from __future__ import annotations

import sys

from playwright.sync_api import Page, sync_playwright

import water_form_config as cfg
from water_form_camera import scan_barcode
from water_form_logic import prompt_barcode, prompt_date_collected, prompt_time_collected

FORM_URL = "https://www.publichealthontario.ca/laboratory-services/well-water-testing/portal?tab=0"

_SET_DROPDOWN_JS = """([id, text]) => {
    const $ = window.jQuery;
    const select = document.getElementById(id);
    if (!select) return null;
    const wrapper = select.closest('.ui.dropdown');
    $(wrapper).dropdown('set selected', text);
    return select.value;
}"""


def _normalize(s: str) -> str:
    """Ignore whitespace/case so a mask inserting e.g. a space doesn't
    look like a mismatch."""
    return "".join(s.split()).upper()


def _verify(page: Page, field_id: str, value: str) -> bool:
    """Read a field back and confirm it actually holds what we meant to
    put there. Some fields have JS input masks that can silently reject
    or clear a value depending on *how* it was written -- this catches
    that instead of trusting the fill call blindly."""
    try:
        actual = page.locator(f"#{field_id}").input_value(timeout=5000)
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAILED] #{field_id}: could not read back value after filling: {exc}")
        return False
    if _normalize(actual) != _normalize(value):
        print(f"  [FAILED] #{field_id}: expected '{value}', field actually shows '{actual}'")
        return False
    return True


def _fill(page: Page, field_id: str, value: str) -> bool:
    """Fill a plain text input by its HTML id (bulk value assignment),
    then verify the field actually holds what was intended."""
    if not value:
        return True  # nothing to fill, not an error
    try:
        page.locator(f"#{field_id}").fill(value, timeout=5000)
    except Exception as exc:  # noqa: BLE001 - keep going either way
        print(f"  [FAILED] Could not fill #{field_id} with '{value}': {exc}")
        return False
    return _verify(page, field_id, value)


def _fill_typed(page: Page, field_id: str, value: str) -> bool:
    """Fill a text input by simulating real keystrokes rather than a bulk
    value assignment, then verify the result. The Postal Code field has
    a JS input mask that a bulk fill() confuses badly enough that it
    silently clears the field (verified live) -- typing character by
    character is what the mask actually expects and is what a human
    would do anyway. If this still doesn't stick, the [FAILED] message
    below will say so rather than leaving a wrong value unnoticed."""
    if not value:
        return True
    try:
        locator = page.locator(f"#{field_id}")
        locator.click(timeout=5000)
        locator.press_sequentially(value, delay=20, timeout=10000)
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAILED] Could not type #{field_id} = '{value}': {exc}")
        return False
    return _verify(page, field_id, value)


def _fill_native(page: Page, field_id: str, value: str) -> bool:
    """Fill a native <input type=date>/<input type=time> by its HTML id.
    These expect a single ISO-formatted value via fill(), not
    character-by-character typing."""
    if not value:
        return True
    try:
        page.locator(f"#{field_id}").fill(value, timeout=5000)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAILED] Could not fill #{field_id} with '{value}': {exc}")
        return False


def _check(page: Page, field_id: str, checked: bool = True) -> bool:
    """Set a checkbox/radio to a known state, idempotently."""
    try:
        loc = page.locator(f"#{field_id}")
        if checked:
            loc.check(timeout=5000)
        else:
            loc.uncheck(timeout=5000)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAILED] Could not {'check' if checked else 'uncheck'} #{field_id}: {exc}")
        return False


def _select_dropdown(page: Page, field_id: str, option_text: str) -> bool:
    """Choose an option in one of the page's Semantic UI dropdowns. These
    wrap a hidden native <select>, so this uses the page's own jQuery
    API instead of Playwright's select_option()."""
    if not option_text:
        return True
    try:
        result = page.evaluate(_SET_DROPDOWN_JS, [field_id, option_text])
        if not result:
            print(
                f"  [FAILED] #{field_id}: no option matching '{option_text}' "
                "(must match the dropdown's visible text exactly, including case)"
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAILED] Could not select '{option_text}' for #{field_id}: {exc}")
        return False


def fill_form(page: Page, barcode: str, date_iso: str, time_hm: str) -> None:
    print("Filling per-sample fields...")
    _fill(page, "barcode", barcode)
    _fill(page, "confirm-barcode", barcode)
    _fill_native(page, "date-collected", date_iso)
    _fill_native(page, "time-collected", time_hm)

    print("Filling purification system...")
    _check(page, "purification-yes" if cfg.PURIFICATION_SYSTEM_USED else "purification-no")

    print("Filling mailing address / contact info...")
    _fill(page, "last-name", cfg.LAST_NAME)
    _fill(page, "confirm-last-name", cfg.LAST_NAME)
    _fill(page, "first-name", cfg.FIRST_NAME)
    _fill(page, "phone", cfg.PHONE_NUMBER)

    # Reveal the manual address fields instead of fighting the address
    # autocomplete widget.
    _check(page, "address-not-listed", checked=True)
    _fill(page, "unit-number", cfg.MAILING_UNIT_NO)
    _fill(page, "street-number", cfg.MAILING_STREET_NO)
    _fill(page, "street-name", cfg.MAILING_STREET_NAME)
    _select_dropdown(page, "street-type", cfg.MAILING_STREET_TYPE)
    _select_dropdown(page, "street-direction", cfg.MAILING_STREET_DIRECTION)
    _fill(page, "rural-road", cfg.MAILING_RURAL_ROUTE)
    _fill(page, "po-box", cfg.MAILING_PO_BOX)
    _fill(page, "city", cfg.MAILING_CITY_TOWN)
    _fill_typed(page, "postal-code", cfg.MAILING_POSTAL_CODE)
    _fill(page, "state", cfg.MAILING_PROVINCE_STATE)
    _fill(page, "country", cfg.MAILING_COUNTRY)

    print("Handling water source address...")
    if cfg.WATER_SOURCE_SAME_AS_MAILING:
        # Only becomes enabled once the required mailing fields above are
        # filled -- which is why this section runs after the mailing one.
        _check(page, "same-water-source-address", checked=True)
    else:
        _check(page, "ws-address-not-listed", checked=True)
        _fill(page, "ws-unit-number", cfg.WATER_UNIT_NO)
        _fill(page, "ws-street-number", cfg.WATER_STREET_NO)
        _fill(page, "ws-street-name", cfg.WATER_STREET_NAME)
        _select_dropdown(page, "ws-street-type", cfg.WATER_STREET_TYPE)
        _select_dropdown(page, "ws-street-direction", cfg.WATER_STREET_DIRECTION)
        _fill(page, "ws-lot-number", cfg.WATER_LOT_NO)
        _fill(page, "ws-concession", cfg.WATER_CONCESSION)
        _fill(page, "ws-emergency-loc-number", cfg.WATER_EMERGENCY_LOCATOR)
        _select_dropdown(page, "ws-municipality", cfg.WATER_TOWNSHIP_MUNICIPALITY)
        _select_dropdown(page, "ws-county", cfg.WATER_COUNTY_DISTRICT)
        _fill_typed(page, "ws-postal-code", cfg.WATER_POSTAL_CODE)

    print("Selecting Public Health Unit...")
    _select_dropdown(page, "health-unit", cfg.PUBLIC_HEALTH_UNIT)


def main() -> None:
    missing = [f for f in cfg.REQUIRED_FIELDS if not getattr(cfg, f, "")]
    if missing:
        sys.exit(
            "Missing required value(s) in .env: " + ", ".join(missing) + "\n"
            "Copy .env.example to .env and fill them in before running this script."
        )

    print("=== Per-sample data ===")
    scanned = scan_barcode()
    if scanned:
        barcode, scanned_at = scanned
        date_collected = scanned_at.date()
        time_collected = scanned_at.time().replace(second=0, microsecond=0)
        print(f"Scanned barcode {barcode} at {scanned_at.strftime('%Y-%m-%d %H:%M')}.")
    else:
        barcode = prompt_barcode()
        date_collected = prompt_date_collected()
        time_collected = prompt_time_collected()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        print(f"\nOpening {FORM_URL} ...")
        page.goto(FORM_URL)

        fill_form(page, barcode, date_collected.isoformat(), time_collected.strftime("%H:%M"))

        print(
            "\nDone filling. The form has NOT been submitted.\n"
            "Please review every field yourself, tick the Terms and Conditions\n"
            "checkbox, and click Submit manually if everything is correct.\n"
            "Any [FAILED] lines above need a manual fix in this browser window.\n"
            "Press Enter here once you're done (this keeps the browser open)."
        )
        input()
        browser.close()


if __name__ == "__main__":
    main()
