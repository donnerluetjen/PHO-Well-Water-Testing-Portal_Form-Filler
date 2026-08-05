"""
Pure logic for collecting per-sample data (barcode, collection date/time).

Kept separate from water_form_filler.py (which needs Playwright/a browser)
so it can be unit-tested without launching anything.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable

InputFunc = Callable[[str], str]


class MismatchError(ValueError):
    """Raised when a value and its confirmation don't match."""


def confirm_match(value: str, confirmation: str) -> bool:
    """Barcode and its confirmation must match exactly (whitespace-trimmed)."""
    return value.strip() == confirmation.strip()


def validate_barcode(barcode: str) -> bool:
    """PHO barcodes are 8 or 9 digits."""
    b = barcode.strip()
    return b.isdigit() and len(b) in (8, 9)


def stable_reading(recent: list[str], required_count: int = 3) -> str | None:
    """Given the last few barcode strings decoded from camera frames (most
    recent last), return the value if the most recent `required_count`
    readings are identical and look like a valid barcode -- otherwise
    None. Used to avoid accepting a single misread frame."""
    if len(recent) < required_count:
        return None
    tail = recent[-required_count:]
    if len(set(tail)) == 1 and validate_barcode(tail[0]):
        return tail[0]
    return None


def prompt_barcode(input_func: InputFunc = input) -> str:
    """Ask for the barcode twice (mirrors the form's own confirm field)
    and keep retrying until both entries match and look like a valid
    8-9 digit barcode."""
    while True:
        barcode = input_func("Barcode (8 or 9 digits): ").strip()
        if not validate_barcode(barcode):
            print("  -> Doesn't look like an 8-9 digit barcode, try again.")
            continue
        confirm = input_func("Confirm barcode: ").strip()
        if not confirm_match(barcode, confirm):
            print("  -> Barcodes don't match, try again.")
            continue
        return barcode


def prompt_date_collected(
    input_func: InputFunc = input, today: dt.date | None = None
) -> dt.date:
    """Ask for the collection date. Empty input defaults to today.
    Accepts YYYY-MM-DD."""
    today = today or dt.date.today()
    while True:
        raw = input_func(
            f"Date collected [YYYY-MM-DD, default {today.isoformat()}]: "
        ).strip()
        if not raw:
            return today
        try:
            return dt.date.fromisoformat(raw)
        except ValueError:
            print("  -> Use YYYY-MM-DD format.")


def prompt_time_collected(
    input_func: InputFunc = input, now: dt.time | None = None
) -> dt.time:
    """Ask for the collection time (24h). Empty input defaults to now."""
    now = now or dt.datetime.now().time().replace(second=0, microsecond=0)
    while True:
        raw = input_func(
            f"Time collected [HH:MM 24h, default {now.strftime('%H:%M')}]: "
        ).strip()
        if not raw:
            return now
        try:
            return dt.datetime.strptime(raw, "%H:%M").time()
        except ValueError:
            print("  -> Use HH:MM 24h format, e.g. 14:30.")
