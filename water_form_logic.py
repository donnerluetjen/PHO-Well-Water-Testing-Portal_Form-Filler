"""
Pure logic for collecting per-sample data (barcode, collection date/time).

Kept separate from water_form_filler.py (which needs Playwright/a browser)
so it can be unit-tested without launching anything.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable

from cryptography.fernet import Fernet

InputFunc = Callable[[str], str]

# Marks trigger emails so the result-fetcher's intake can find them by
# subject and ignore anything else that might land in that inbox. Must
# match TRIGGER_SUBJECT_PREFIX in docker_result-fetcher/water_result_logic.py.
TRIGGER_SUBJECT_PREFIX = "[WaterSample]"


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


def format_trigger_email(barcode: str, submitted_at: str, encryption_key: str) -> tuple[str, str]:
    """Build the (subject, body) of the email that tells the
    result-fetcher a sample was just submitted. See parse_trigger_email()
    in docker_result-fetcher/water_result_logic.py, which must stay in
    sync with this format.

    submitted_at should be a UTC-aware ISO timestamp (i.e. produced by
    dt.datetime.now(dt.timezone.utc).isoformat(), not plain .now()) --
    this machine and whatever's running the result-fetcher can easily
    be in different local timezones, and the overdue check on the
    other end needs an unambiguous instant, not a naive wall-clock
    string. See is_overdue() in docker_result-fetcher/water_result_logic.py.

    Deliberately carries only the barcode and timestamp, not your last
    name -- that's fixed and doesn't need to travel through a mailbox
    on every submission (see RESULT_LAST_NAME in
    docker_result-fetcher/.env.example).
    The subject is the bare prefix, with no barcode in it, so a glance
    at the inbox (or a notification preview) doesn't reveal which
    sample/property a message is about.

    The body itself is symmetrically encrypted with `encryption_key`
    (a Fernet key, generate one with:
    `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
    -- barcode and timestamp aren't readable in plain text to anyone
    with access to the mailbox, or to the mail provider's own automated
    scanning. The same key must be configured on the result-fetcher
    side (TRIGGER_ENCRYPTION_KEY in docker_result-fetcher/.env) to
    decrypt it; see parse_trigger_email()."""
    subject = TRIGGER_SUBJECT_PREFIX
    plaintext = (
        f"barcode: {barcode}\n"
        f"submitted_at: {submitted_at}\n"
    )
    token = Fernet(encryption_key.encode()).encrypt(plaintext.encode())
    body = token.decode()
    return subject, body


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
