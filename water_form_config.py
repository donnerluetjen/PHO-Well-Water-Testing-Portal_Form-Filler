"""
Fixed data for the PHO Well Water Testing Portal requisition form.

Loaded from environment variables via a local ".env" file, so your real
name/address/etc. never end up committed to git.

Setup: copy .env.example to .env and fill in your real values there.
".env" is listed in .gitignore and is never committed. This module only
reads already-loaded values -- see .env.example for the full list of
keys, what's required, and formatting notes (e.g. dropdown values must
match the on-page text exactly).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- Mailing address contact info -------------------------------------
LAST_NAME = _str("LAST_NAME")          # * required
FIRST_NAME = _str("FIRST_NAME")        # optional
PHONE_NUMBER = _str("PHONE_NUMBER")    # optional, e.g. "4165551234"

# --- Purification system -------------------------------------------
# The form asks "Purification system used (e.g., UV, filtration, etc.)"
# as a plain Yes/No choice -- no free-text field.
PURIFICATION_SYSTEM_USED = _bool("PURIFICATION_SYSTEM_USED", False)

# --- Mailing address --------------------------------------------------
# The form has an address-lookup field plus a "My address is not listed"
# link that reveals plain manual fields (Unit/Street No/Street Name/...).
# This script always uses the manual fields since they're far more
# reliable to automate than an address-autocomplete widget.
MAILING_UNIT_NO = _str("MAILING_UNIT_NO")
MAILING_STREET_NO = _str("MAILING_STREET_NO")              # * required
MAILING_STREET_NAME = _str("MAILING_STREET_NAME")          # * required
MAILING_STREET_TYPE = _str("MAILING_STREET_TYPE")          # (dropdown option text)
MAILING_STREET_DIRECTION = _str("MAILING_STREET_DIRECTION")  # (dropdown option text)
MAILING_RURAL_ROUTE = _str("MAILING_RURAL_ROUTE")
MAILING_PO_BOX = _str("MAILING_PO_BOX")
MAILING_CITY_TOWN = _str("MAILING_CITY_TOWN")               # * required
MAILING_POSTAL_CODE = _str("MAILING_POSTAL_CODE")
MAILING_PROVINCE_STATE = _str("MAILING_PROVINCE_STATE", "Ontario")  # * required
MAILING_COUNTRY = _str("MAILING_COUNTRY", "Canada")

# --- Water source address ---------------------------------------------
# If the water source is at the same address as above, leave this True
# (the default) and the water-source-address section is skipped
# entirely (the form's own checkbox handles it).
WATER_SOURCE_SAME_AS_MAILING = _bool("WATER_SOURCE_SAME_AS_MAILING", True)

# Only used if WATER_SOURCE_SAME_AS_MAILING is False:
WATER_UNIT_NO = _str("WATER_UNIT_NO")
WATER_STREET_NO = _str("WATER_STREET_NO")
WATER_STREET_NAME = _str("WATER_STREET_NAME")
WATER_STREET_TYPE = _str("WATER_STREET_TYPE")
WATER_STREET_DIRECTION = _str("WATER_STREET_DIRECTION")
WATER_LOT_NO = _str("WATER_LOT_NO")
WATER_CONCESSION = _str("WATER_CONCESSION")
WATER_EMERGENCY_LOCATOR = _str("WATER_EMERGENCY_LOCATOR")
WATER_TOWNSHIP_MUNICIPALITY = _str("WATER_TOWNSHIP_MUNICIPALITY")  # * required (dropdown option text)
WATER_COUNTY_DISTRICT = _str("WATER_COUNTY_DISTRICT")              # (dropdown option text)
WATER_POSTAL_CODE = _str("WATER_POSTAL_CODE")

# --- Public Health Unit -------------------------------------------
# * required (dropdown option text), e.g. "Durham Regional",
# "City of Toronto", "Middlesex-London", etc.
PUBLIC_HEALTH_UNIT = _str("PUBLIC_HEALTH_UNIT")

# --- Result-fetcher trigger email (optional) ---------------------------
# If TRIGGER_EMAIL_TO is set, water_form_filler.py emails it once you
# confirm you've submitted the form, so
# docker_result-fetcher/water_result_fetcher.py (which reads that inbox
# via IMAP -- see docker_result-fetcher/.env.example) knows to
# start checking for that sample's result. No inbound port/webhook
# needed on that side this way. Leave TRIGGER_EMAIL_TO empty to skip
# this entirely -- the form filler works fine without it.
#
# This uses its own SMTP account to *send* the trigger email -- can be
# any account you have (Gmail with an app password, your ISP's mail,
# etc.), separate from the dedicated inbox TRIGGER_EMAIL_TO points at.
SMTP_HOST = _str("SMTP_HOST")
SMTP_PORT = int(_str("SMTP_PORT", "587") or "587")
SMTP_USERNAME = _str("SMTP_USERNAME")
SMTP_PASSWORD = _str("SMTP_PASSWORD")
SMTP_FROM = _str("SMTP_FROM") or SMTP_USERNAME
TRIGGER_EMAIL_TO = _str("TRIGGER_EMAIL_TO")  # the dedicated trigger inbox's address

# Shared secret that encrypts the trigger email's body (barcode +
# timestamp) so it isn't plain text in the mailbox. Must exactly match
# TRIGGER_ENCRYPTION_KEY in docker_result-fetcher/.env. Generate one with:
#   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TRIGGER_ENCRYPTION_KEY = _str("TRIGGER_ENCRYPTION_KEY")

# Fields that must be non-empty before water_form_filler.py will run.
REQUIRED_FIELDS = (
    "LAST_NAME",
    "MAILING_STREET_NO",
    "MAILING_STREET_NAME",
    "MAILING_CITY_TOWN",
    "PUBLIC_HEALTH_UNIT",
)
