"""
Unit tests for water_result_logic.py. Run with: pytest test_water_result_logic.py
No imaplib / Playwright / network needed.
"""
import datetime as dt

from cryptography.fernet import Fernet

from water_result_logic import (
    add_sample,
    bump_attempts,
    is_overdue,
    load_pending,
    parse_trigger_email,
    remove_sample,
    save_pending,
    validate_barcode,
)

# Fresh key per test run -- parse_trigger_email() just needs *a* valid
# Fernet key, not this specific one.
TEST_KEY = Fernet.generate_key().decode()


def _encrypt(plaintext: str, key: str = TEST_KEY) -> str:
    """Build an encrypted body the way format_trigger_email() (root
    water_form_logic.py) would, without importing across that
    machine boundary."""
    return Fernet(key.encode()).encrypt(plaintext.encode()).decode()


# --- validate_barcode ------------------------------------------------------

def test_validate_barcode_accepts_8_or_9_digits():
    assert validate_barcode("12345678")
    assert validate_barcode("123456789")


def test_validate_barcode_rejects_bad_input():
    assert not validate_barcode("1234567")
    assert not validate_barcode("abcdefgh")
    assert not validate_barcode("")


# --- parse_trigger_email -----------------------------------------------
# The trigger email deliberately carries only barcode + submitted_at, not
# a last name (that's fixed on this side via RESULT_LAST_NAME instead --
# see water_result_fetcher.py) or a barcode in the subject (kept generic
# so an inbox listing/notification preview doesn't reveal which sample
# a message is about). The body is also encrypted (Fernet, symmetric --
# see format_trigger_email() on the Mac side), so every body here goes
# through _encrypt() rather than being plain "key: value" text.

def test_parse_trigger_email_happy_path():
    subject = "[WaterSample]"
    body = _encrypt("barcode: 123456789\nsubmitted_at: 2026-08-05T14:30:00\n")
    assert parse_trigger_email(subject, body, TEST_KEY) == {
        "barcode": "123456789",
        "submitted_at": "2026-08-05T14:30:00",
    }


def test_parse_trigger_email_ignores_unrelated_subject():
    body = _encrypt("barcode: 123456789\nsubmitted_at: 2026-08-05T14:30:00")
    assert parse_trigger_email("Re: your order", body, TEST_KEY) is None


def test_parse_trigger_email_rejects_invalid_barcode():
    subject = "[WaterSample]"
    body = _encrypt("barcode: bad\nsubmitted_at: 2026-08-05T14:30:00")
    assert parse_trigger_email(subject, body, TEST_KEY) is None


def test_parse_trigger_email_rejects_missing_barcode():
    subject = "[WaterSample]"
    body = _encrypt("submitted_at: 2026-08-05T14:30:00")
    assert parse_trigger_email(subject, body, TEST_KEY) is None


def test_parse_trigger_email_rejects_unparseable_date():
    subject = "[WaterSample]"
    body = _encrypt("barcode: 123456789\nsubmitted_at: not-a-date")
    assert parse_trigger_email(subject, body, TEST_KEY) is None


def test_parse_trigger_email_tolerates_extra_whitespace_and_blank_lines():
    subject = "  [WaterSample]  "
    body = _encrypt("\nbarcode:   123456789  \n\nsubmitted_at:2026-08-05T14:30:00\n")
    assert parse_trigger_email(subject, body, TEST_KEY) == {
        "barcode": "123456789",
        "submitted_at": "2026-08-05T14:30:00",
    }


def test_parse_trigger_email_ignores_stray_extra_fields():
    # A last_name still present in the body (e.g. an older Mac-side
    # version, or manual testing) shouldn't break parsing -- extra
    # fields are just ignored, not rejected.
    subject = "[WaterSample]"
    body = _encrypt("barcode: 123456789\nlast_name: Mustermann\nsubmitted_at: 2026-08-05T14:30:00")
    assert parse_trigger_email(subject, body, TEST_KEY) == {
        "barcode": "123456789",
        "submitted_at": "2026-08-05T14:30:00",
    }


def test_parse_trigger_email_wrong_key_returns_none():
    # Simulates a misconfigured/mismatched TRIGGER_ENCRYPTION_KEY --
    # this must fail closed (None, i.e. "skip this message"), not raise
    # and crash the whole intake run over one bad key.
    subject = "[WaterSample]"
    body = _encrypt("barcode: 123456789\nsubmitted_at: 2026-08-05T14:30:00")
    wrong_key = Fernet.generate_key().decode()
    assert parse_trigger_email(subject, body, wrong_key) is None


def test_parse_trigger_email_rejects_unencrypted_body():
    # Plain "key: value" text (e.g. a stray email that happens to start
    # with the trigger prefix, or a pre-encryption sender) isn't a
    # valid Fernet token and must be skipped, not raised on.
    subject = "[WaterSample]"
    body = "barcode: 123456789\nsubmitted_at: 2026-08-05T14:30:00"
    assert parse_trigger_email(subject, body, TEST_KEY) is None


# --- add_sample / remove_sample / bump_attempts -----------------------------

def test_add_sample_to_empty_list():
    items = add_sample([], "123456789", "Mustermann", "2026-08-05T14:30:00")
    assert items == [
        {"barcode": "123456789", "last_name": "Mustermann",
         "submitted_at": "2026-08-05T14:30:00", "attempts": 0}
    ]


def test_add_sample_is_idempotent_for_same_barcode():
    items = add_sample([], "123456789", "Mustermann", "2026-08-05T14:30:00")
    items = bump_attempts(items, "123456789")
    items = add_sample(items, "123456789", "Mustermann", "2026-08-06T09:00:00")
    assert len(items) == 1
    assert items[0]["submitted_at"] == "2026-08-06T09:00:00"
    assert items[0]["attempts"] == 0  # refreshed, not accumulated


def test_add_sample_keeps_other_entries():
    items = add_sample([], "111", "A", "2026-08-01T00:00:00")
    items = add_sample(items, "222", "B", "2026-08-02T00:00:00")
    assert {i["barcode"] for i in items} == {"111", "222"}


def test_remove_sample_removes_only_matching_barcode():
    items = add_sample([], "111", "A", "2026-08-01T00:00:00")
    items = add_sample(items, "222", "B", "2026-08-02T00:00:00")
    items = remove_sample(items, "111")
    assert [i["barcode"] for i in items] == ["222"]


def test_bump_attempts_only_affects_matching_barcode():
    items = add_sample([], "111", "A", "2026-08-01T00:00:00")
    items = add_sample(items, "222", "B", "2026-08-02T00:00:00")
    items = bump_attempts(items, "111")
    by_barcode = {i["barcode"]: i["attempts"] for i in items}
    assert by_barcode == {"111": 1, "222": 0}


# --- is_overdue ----------------------------------------------------------
# submitted_at comes from a different machine (wherever you submit the
# sample) than `now` (wherever this Docker host lives) -- they can
# easily be in different local timezones, so is_overdue() must compare
# them as absolute instants, not naive wall-clock strings.

def test_is_overdue_false_within_window():
    submitted = "2026-08-01T00:00:00"
    now = dt.datetime.fromisoformat("2026-08-05T00:00:00")
    assert not is_overdue(submitted, now, max_days=10)


def test_is_overdue_true_past_window():
    submitted = "2026-08-01T00:00:00"
    now = dt.datetime.fromisoformat("2026-08-15T00:00:00")
    assert is_overdue(submitted, now, max_days=10)


def test_is_overdue_compares_correctly_across_different_utc_offsets():
    # The actual bug this guards against: submitted_at written in one
    # machine's timezone, `now` computed in another's. These two
    # timestamps represent the exact same instant (00:00 UTC), just
    # expressed with different offsets -- naive comparison would see a
    # bogus 2-hour gap; correct comparison sees zero.
    submitted = "2026-08-01T00:00:00+00:00"  # UTC
    now = dt.datetime.fromisoformat("2026-08-01T02:00:00+02:00")  # same instant, +2h zone
    assert not is_overdue(submitted, now, max_days=10)


def test_is_overdue_true_past_window_with_utc_offsets():
    submitted = "2026-08-01T00:00:00+00:00"
    now = dt.datetime.fromisoformat("2026-08-15T00:00:00+00:00")
    assert is_overdue(submitted, now, max_days=10)


def test_is_overdue_treats_naive_submitted_at_as_utc():
    # Backward compatibility: pending_samples.json entries written
    # before submitted_at became timezone-aware have no offset at all.
    # Rather than crashing (mixing naive and aware datetimes raises
    # TypeError), treat a naive submitted_at as if it were already UTC.
    submitted = "2026-08-01T00:00:00"  # no offset
    now = dt.datetime(2026, 8, 5, tzinfo=dt.timezone.utc)
    assert not is_overdue(submitted, now, max_days=10)


def test_is_overdue_treats_naive_now_as_utc():
    # Symmetric case: a caller passing a naive `now` (e.g. an older
    # call site not yet updated) shouldn't crash either.
    submitted = "2026-08-01T00:00:00+00:00"
    now = dt.datetime(2026, 8, 5)  # naive
    assert not is_overdue(submitted, now, max_days=10)


# --- load_pending / save_pending (file IO, using pytest's tmp_path) --------

def test_load_pending_missing_file_returns_empty_list(tmp_path):
    assert load_pending(tmp_path / "does_not_exist.json") == []


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "pending_samples.json"
    items = add_sample([], "123456789", "Mustermann", "2026-08-05T14:30:00")
    save_pending(path, items)
    assert load_pending(path) == items


def test_save_pending_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "pending_samples.json"
    save_pending(path, [])
    assert path.exists()
