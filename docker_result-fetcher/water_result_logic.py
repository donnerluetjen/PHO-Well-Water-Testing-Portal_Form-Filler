"""
Pure logic used by water_result_fetcher.py: managing the "pending
samples" list and parsing incoming trigger emails.

Kept dependency-free (no imaplib/email wiring, no Playwright) so it can
be unit-tested in isolation -- the actual IMAP connection and message
parsing live in water_result_fetcher.py's fetch_new_trigger_samples(),
which calls parse_trigger_email() here for the pure part. water_form_logic.py
(used by the filler script on your own machine) has its own copy of
validate_barcode() and its own TRIGGER_SUBJECT_PREFIX -- this module
deliberately doesn't import it, since the filler runs on a different
machine than the fetcher (which runs in this Docker container) and sharing a
package across that boundary isn't worth the complexity for a few small
functions/constants. The two copies of TRIGGER_SUBJECT_PREFIX must stay
in sync.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

TRIGGER_SUBJECT_PREFIX = "[WaterSample]"


def validate_barcode(barcode: str) -> bool:
    """PHO barcodes are 8 or 9 digits."""
    b = barcode.strip()
    return b.isdigit() and len(b) in (8, 9)


def parse_trigger_email(subject: str, body: str, encryption_key: str) -> dict[str, str] | None:
    """Parse a "sample submitted" email from water_form_filler.py (see
    format_trigger_email() in the root water_form_logic.py). Returns
    None -- rather than raising -- for anything that doesn't look like
    a well-formed trigger email, so the caller can just skip it (e.g.
    stray mail that ended up in the same inbox, or one encrypted with a
    stale/wrong key) instead of crashing the whole intake run over one
    bad message.

    The body is encrypted (Fernet, symmetric) with `encryption_key` --
    must exactly match TRIGGER_ENCRYPTION_KEY used to send it. A wrong
    key, a corrupted token, or a plain-text body that was never
    encrypted at all (e.g. a stray unrelated email) all just fail to
    decrypt and return None here, same as any other malformed message.

    Only barcode + submitted_at are extracted -- there's no last name
    in these emails by design (see RESULT_LAST_NAME in
    water_result_fetcher.py, which supplies it locally instead), and
    the subject is just checked for the bare TRIGGER_SUBJECT_PREFIX, not
    matched against any particular barcode. Any other fields present in
    the decrypted body (e.g. a stray "last_name:" line from an older
    sender) are simply ignored rather than rejected."""
    if not subject.strip().startswith(TRIGGER_SUBJECT_PREFIX):
        return None

    try:
        plaintext = Fernet(encryption_key.encode()).decrypt(body.strip().encode()).decode()
    except (InvalidToken, ValueError):
        return None

    fields: dict[str, str] = {}
    for line in plaintext.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()

    barcode = fields.get("barcode", "")
    submitted_at = fields.get("submitted_at", "")

    if not validate_barcode(barcode) or not submitted_at:
        return None
    try:
        dt.datetime.fromisoformat(submitted_at)
    except ValueError:
        return None

    return {"barcode": barcode, "submitted_at": submitted_at}


def load_pending(path: Path) -> list[dict[str, Any]]:
    """Read the pending-samples list. Missing/empty file -> empty list,
    rather than an error -- there's nothing pending yet on first run."""
    if not path.exists():
        return []
    text = path.read_text().strip()
    if not text:
        return []
    return json.loads(text)


def save_pending(path: Path, items: list[dict[str, Any]]) -> None:
    """Write the pending-samples list atomically (write to a temp file,
    then rename) so a crash mid-write can't corrupt the list."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(items, indent=2))
    os.replace(tmp_path, path)


def add_sample(
    items: list[dict[str, Any]], barcode: str, last_name: str, submitted_at: str
) -> list[dict[str, Any]]:
    """Add a newly-submitted sample to the list. Idempotent: submitting
    the same barcode again just refreshes submitted_at/attempts rather
    than creating a duplicate entry."""
    barcode = barcode.strip()
    last_name = last_name.strip()
    others = [i for i in items if i["barcode"] != barcode]
    return others + [
        {
            "barcode": barcode,
            "last_name": last_name,
            "submitted_at": submitted_at,
            "attempts": 0,
        }
    ]


def remove_sample(items: list[dict[str, Any]], barcode: str) -> list[dict[str, Any]]:
    """Remove a sample once its result has been emailed."""
    return [i for i in items if i["barcode"] != barcode]


def bump_attempts(items: list[dict[str, Any]], barcode: str) -> list[dict[str, Any]]:
    """Record that a check was made for this sample without a result yet."""
    return [
        {**i, "attempts": i["attempts"] + 1} if i["barcode"] == barcode else i
        for i in items
    ]


def is_overdue(submitted_at: str, now: dt.datetime, max_days: int = 10) -> bool:
    """PHO says results are typically ready in 2-4 business days; flag
    anything still unresolved after `max_days` as worth a human look,
    rather than checking it forever.

    submitted_at is written on whatever machine you submit a sample
    from, and `now` is computed on whatever machine runs this check --
    two different machines that can easily be in different local
    timezones (this bit Ansgar in practice: submitting from Canada,
    checking from a Docker host in Germany). Comparing naive wall-clock
    strings as if they were the same timezone silently produces a wrong
    answer instead of an error, which is worse than a crash -- so both
    sides of this repo now generate timestamps as UTC-aware
    (datetime.now(dt.timezone.utc)) rather than naive local time.

    A naive value (no timezone offset) is still accepted here, treated
    as if it were already UTC, purely so this doesn't blow up on
    pending_samples.json entries written before this fix, or on a
    caller that hasn't been updated yet. It's a compatibility fallback,
    not the intended steady-state input."""
    submitted = dt.datetime.fromisoformat(submitted_at)
    if submitted.tzinfo is None:
        submitted = submitted.replace(tzinfo=dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return (now - submitted) > dt.timedelta(days=max_days)
