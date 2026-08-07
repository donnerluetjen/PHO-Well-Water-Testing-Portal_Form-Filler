"""
Unit tests for water_form_logic.py. Run with: pytest test_water_form_logic.py
No browser / network needed.
"""
import datetime as dt

import pytest
from cryptography.fernet import Fernet

from water_form_logic import (
    TRIGGER_SUBJECT_PREFIX,
    confirm_match,
    format_trigger_email,
    prompt_barcode,
    prompt_date_collected,
    prompt_time_collected,
    stable_reading,
    validate_barcode,
)

# Fresh key per test run -- format_trigger_email()/parse_trigger_email()
# just need *a* valid Fernet key, not this specific one.
TEST_KEY = Fernet.generate_key().decode()


# --- validate_barcode ---------------------------------------------------

@pytest.mark.parametrize("barcode", ["12345678", "123456789"])
def test_validate_barcode_accepts_8_or_9_digits(barcode):
    assert validate_barcode(barcode)


@pytest.mark.parametrize(
    "barcode", ["1234567", "1234567890", "1234abc8", "", "  "]
)
def test_validate_barcode_rejects_bad_input(barcode):
    assert not validate_barcode(barcode)


# --- confirm_match -------------------------------------------------------

def test_confirm_match_identical():
    assert confirm_match("12345678", "12345678")


def test_confirm_match_trims_whitespace():
    assert confirm_match(" 12345678 ", "12345678")


def test_confirm_match_rejects_different_values():
    assert not confirm_match("12345678", "87654321")


# --- prompt_barcode (drives the input function, no real stdin) ----------

def _scripted_input(responses):
    it = iter(responses)
    return lambda _prompt: next(it)


def test_prompt_barcode_happy_path():
    fn = _scripted_input(["12345678", "12345678"])
    assert prompt_barcode(fn) == "12345678"


def test_prompt_barcode_retries_on_mismatch_then_succeeds():
    fn = _scripted_input(["12345678", "87654321", "12345678", "12345678"])
    assert prompt_barcode(fn) == "12345678"


def test_prompt_barcode_retries_on_invalid_format():
    fn = _scripted_input(["abc", "12345678", "12345678"])
    assert prompt_barcode(fn) == "12345678"


# --- prompt_date_collected -----------------------------------------------

def test_prompt_date_collected_defaults_to_today_on_empty_input():
    today = dt.date(2026, 8, 5)
    fn = _scripted_input([""])
    assert prompt_date_collected(fn, today=today) == today


def test_prompt_date_collected_parses_explicit_date():
    fn = _scripted_input(["2026-08-01"])
    assert prompt_date_collected(fn, today=dt.date(2026, 8, 5)) == dt.date(2026, 8, 1)


def test_prompt_date_collected_retries_on_bad_format():
    fn = _scripted_input(["not-a-date", "2026-08-01"])
    assert prompt_date_collected(fn, today=dt.date(2026, 8, 5)) == dt.date(2026, 8, 1)


# --- prompt_time_collected -------------------------------------------------

def test_prompt_time_collected_defaults_to_now_on_empty_input():
    now = dt.time(14, 30)
    fn = _scripted_input([""])
    assert prompt_time_collected(fn, now=now) == now


def test_prompt_time_collected_parses_explicit_time():
    fn = _scripted_input(["09:15"])
    assert prompt_time_collected(fn, now=dt.time(14, 30)) == dt.time(9, 15)


def test_prompt_time_collected_retries_on_bad_format():
    fn = _scripted_input(["25:99", "09:15"])
    assert prompt_time_collected(fn, now=dt.time(14, 30)) == dt.time(9, 15)


# --- stable_reading (camera scan debouncing) ------------------------------

def test_stable_reading_needs_enough_readings():
    assert stable_reading(["12345678", "12345678"], required_count=3) is None


def test_stable_reading_accepts_matching_valid_readings():
    assert stable_reading(["12345678", "12345678", "12345678"], required_count=3) == "12345678"


def test_stable_reading_rejects_disagreeing_readings():
    assert stable_reading(["12345678", "87654321", "12345678"], required_count=3) is None


def test_stable_reading_rejects_invalid_barcode_even_if_stable():
    assert stable_reading(["notabarcode", "notabarcode", "notabarcode"], required_count=3) is None


def test_stable_reading_only_looks_at_most_recent_readings():
    # An earlier disagreement shouldn't block acceptance once the most
    # recent `required_count` readings agree.
    readings = ["11111111", "22222222", "22222222", "22222222"]
    assert stable_reading(readings, required_count=3) == "22222222"


# --- format_trigger_email (result-fetcher intake trigger) ------------------
# Mirrored by parse_trigger_email() in
# docker_result-fetcher/water_result_logic.py -- the two files aren't
# importable across that boundary (different machines), so this only
# checks the shape this side produces, not an actual round-trip through
# the other module.
#
# Deliberately does NOT include last_name: it doesn't change between
# samples, so instead of emailing it every time, the result-fetcher side
# keeps its own fixed copy (RESULT_LAST_NAME in
# docker_result-fetcher/.env) -- one less
# piece of personal data traveling through a mailbox. The subject is
# also just the bare prefix, not "prefix + barcode", so that even a
# glance at the inbox list (or a phone's notification preview) doesn't
# show which barcode/property a message is about.
#
# The body is symmetrically encrypted (Fernet) with a shared key both
# machines hold -- so barcode + submitted_at aren't readable in plain
# text to anyone with mailbox access, or to the mail provider's own
# automated scanning.

def test_format_trigger_email_subject_is_bare_prefix():
    subject, _ = format_trigger_email("123456789", "2026-08-05T14:30:00", TEST_KEY)
    assert subject == TRIGGER_SUBJECT_PREFIX
    assert "123456789" not in subject


def test_format_trigger_email_body_is_encrypted_not_plaintext():
    _, body = format_trigger_email("123456789", "2026-08-05T14:30:00", TEST_KEY)
    assert "123456789" not in body
    assert "barcode" not in body.lower()
    assert "2026-08-05" not in body


def test_format_trigger_email_body_decrypts_to_barcode_and_timestamp_only():
    _, body = format_trigger_email("123456789", "2026-08-05T14:30:00", TEST_KEY)
    plaintext = Fernet(TEST_KEY.encode()).decrypt(body.encode()).decode()
    lines = plaintext.splitlines()
    assert "barcode: 123456789" in lines
    assert "submitted_at: 2026-08-05T14:30:00" in lines
    assert not any(line.lower().startswith("last_name") for line in lines)


def test_format_trigger_email_uses_a_fresh_token_each_time():
    # Fernet tokens include random data, so the same input encrypted
    # twice must not produce identical ciphertext (a basic sanity check
    # that we're not accidentally doing something deterministic/unsafe).
    _, body1 = format_trigger_email("123456789", "2026-08-05T14:30:00", TEST_KEY)
    _, body2 = format_trigger_email("123456789", "2026-08-05T14:30:00", TEST_KEY)
    assert body1 != body2
