"""
Unit tests for water_form_logic.py. Run with: pytest test_water_form_logic.py
No browser / network needed.
"""
import datetime as dt

import pytest

from water_form_logic import (
    confirm_match,
    prompt_barcode,
    prompt_date_collected,
    prompt_time_collected,
    stable_reading,
    validate_barcode,
)


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
