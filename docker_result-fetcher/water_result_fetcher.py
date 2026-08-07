"""
Two jobs, run one after the other each time this script is invoked:

  1. Intake: log into a dedicated IMAP mailbox, read any new "sample
     submitted" trigger emails sent by water_form_filler.py (see
     format_trigger_email() in the root water_form_logic.py), and add
     them to the pending-samples list. This is an outbound connection
     this container initiates -- nothing needs to be reachable from the
     internet, unlike the webhook approach this replaced.
  2. Fetch: check every pending sample against the PHO "Get Results" tab
     and email the PDF report for any that are ready.

Runs in a plain Docker container on any Docker host. Meant to be
invoked periodically (e.g. 3x/day) by whatever scheduler you use (DSM's
Task Scheduler, cron, etc.), via `docker compose run --rm water-tester
python water_result_fetcher.py` -- NOT run as a
long-lived loop. Each invocation processes new trigger emails once and
checks every pending sample once: samples with a result get emailed and
removed from the list; everything else stays queued for the next
scheduled run. That's what gives you "keep checking until the result
arrives" without a fragile always-on process, and without any inbound
port needed at all.

Setup:
    pip install playwright
    playwright install chromium
    # (already done in the Docker image -- see Dockerfile)

Configuration (environment variables -- see .env.example):
    IMAP_HOST, IMAP_PORT (default 993), IMAP_USERNAME, IMAP_PASSWORD
                             the dedicated trigger mailbox. If IMAP_HOST
                             is unset, intake is skipped entirely (you'd
                             have to add samples to the pending file some
                             other way).
    IMAP_MAILBOX             default "INBOX"
    RESULT_LAST_NAME         your last name, exactly as used on the
                             requisition form -- needed to query the Get
                             Results tab. Trigger emails deliberately
                             don't carry it (see format_trigger_email()
                             on the Mac side), so it's required here
                             whenever IMAP_HOST is set.
    TRIGGER_ENCRYPTION_KEY   shared Fernet key that decrypts the trigger
                             email body -- must exactly match
                             TRIGGER_ENCRYPTION_KEY in the root .env on
                             the Mac side. Required whenever IMAP_HOST
                             is set.
    PENDING_SAMPLES_FILE     default /data/pending_samples.json
    DOWNLOAD_DIR             default /data/downloads (accepts "~/..." --
                             expanded automatically)
    RESULT_DOWNLOAD_TIMEOUT_MS   default 15000 -- how long to wait for
                             the "Download Report" button to appear
                             after submitting before concluding "not
                             ready yet", and separately how long to wait
                             for the actual file download once that
                             button is clicked
    RESULT_OVERDUE_DAYS      default 10 -- send a warning email listing
                             samples still unresolved after this many days
    SMTP_HOST, SMTP_PORT (default 587), SMTP_USERNAME, SMTP_PASSWORD,
    SMTP_FROM (defaults to SMTP_USERNAME), RESULT_EMAIL_TO

Usage:
    python water_result_fetcher.py
        Normal run: pull new trigger emails, then check every pending
        sample once, headless.

    python water_result_fetcher.py --debug BARCODE LAST_NAME
        Check exactly one *real* sample with a visible browser, for
        verifying the site's actual behaviour before relying on this
        unattended. Does not touch the pending-samples file or IMAP.

How result-checking works (confirmed live, 2026-08-06):
    Submitting the Get Results form doesn't download anything by
    itself -- it loads a results panel. Only if a result actually
    exists does a "Download Report" button show up:

        <button type="button" class="ui secondary button"
                data-barcode="013193373">
            Download Report <i class="small download icon"></i>
        </button>

    Its data-barcode attribute matches the barcode you searched for, so
    check_one() targets it exactly rather than guessing by position or
    text. Clicking it starts an immediate browser download (no
    intermediate confirmation page) -- confirmed in both Chrome and
    Safari, though Safari itself asks a one-time "allow this download?"
    question that's a browser-level prompt, not something the page/
    script controls. If that button never appears within the timeout,
    there's no result yet.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email
import imaplib
import os
import smtplib
import sys
from email.header import decode_header
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from water_result_logic import add_sample, bump_attempts, is_overdue, load_pending, parse_trigger_email, remove_sample, save_pending

RESULT_URL = "https://www.publichealthontario.ca/laboratory-services/well-water-testing/portal?tab=1"
PENDING_FILE = Path(os.environ.get("PENDING_SAMPLES_FILE", "/data/pending_samples.json")).expanduser()
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/data/downloads")).expanduser()
DOWNLOAD_TIMEOUT_MS = int(os.environ.get("RESULT_DOWNLOAD_TIMEOUT_MS", "15000"))
OVERDUE_DAYS = int(os.environ.get("RESULT_OVERDUE_DAYS", "10"))

IMAP_HOST = os.environ.get("IMAP_HOST", "")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USERNAME = os.environ.get("IMAP_USERNAME", "")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")
IMAP_MAILBOX = os.environ.get("IMAP_MAILBOX", "INBOX")

# Trigger emails don't carry a last name (see water_form_logic.py's
# format_trigger_email() on the Mac side) -- it doesn't change between
# samples, so it's configured here once instead of traveling through a
# mailbox on every submission. Needed to query the Get Results form.
RESULT_LAST_NAME = os.environ.get("RESULT_LAST_NAME", "")

# Shared secret that decrypts the trigger email body (barcode +
# timestamp) -- must exactly match TRIGGER_ENCRYPTION_KEY in the root
# .env on the Mac side. See parse_trigger_email() in water_result_logic.py.
TRIGGER_ENCRYPTION_KEY = os.environ.get("TRIGGER_ENCRYPTION_KEY", "")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM") or SMTP_USERNAME
RESULT_EMAIL_TO = os.environ.get("RESULT_EMAIL_TO", "")


def _decode_header_value(raw: str) -> str:
    """Subjects can be MIME-encoded (e.g. "=?utf-8?q?...?="); decode to
    plain text so parse_trigger_email() sees the real prefix/barcode."""
    parts = decode_header(raw or "")
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _extract_body_text(msg: email.message.Message) -> str:
    """Get the plain-text body, whether the message is a simple
    text/plain email or multipart (e.g. text/plain + text/html)."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def fetch_new_trigger_samples() -> list[dict[str, str]]:
    """Log into the dedicated trigger mailbox via IMAP, look at every
    UNSEEN message, and return the ones that parse as valid "sample
    submitted" triggers. Every UNSEEN message examined gets marked
    \\Seen regardless of whether it parsed -- so a stray/malformed email
    that ends up in the same inbox doesn't get retried forever. Returns
    an empty list (rather than raising) if IMAP_HOST isn't configured,
    since the trigger mailbox is optional -- you could add samples to
    the pending file some other way."""
    if not IMAP_HOST:
        return []

    found: list[dict[str, str]] = []
    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
        imap.login(IMAP_USERNAME, IMAP_PASSWORD)
        imap.select(IMAP_MAILBOX)
        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            return found
        message_numbers = data[0].split() if data and data[0] else []
        for num in message_numbers:
            status, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            subject = _decode_header_value(msg.get("Subject", ""))
            body = _extract_body_text(msg)
            parsed = parse_trigger_email(subject, body, TRIGGER_ENCRYPTION_KEY)
            if parsed:
                found.append(parsed)
            imap.store(num, "+FLAGS", "\\Seen")
    return found


def check_one(page: Page, barcode: str, last_name: str) -> Path | None:
    """Fill and submit the Get Results form for one sample, then look
    for the "Download Report" button that only appears once a result
    actually exists (see module docstring for the confirmed markup).
    Returns the path to a downloaded PDF if one appeared, otherwise
    None -- meaning "not ready yet, or didn't match", which is safe to
    just retry later either way."""
    page.goto(RESULT_URL)
    page.locator("#result-barcode").fill(barcode)
    page.locator("#result-last-name").fill(last_name)
    page.locator("#water-requisition-test-result-form-submit-button").click()

    download_button = page.locator(f'button[data-barcode="{barcode}"]')
    try:
        download_button.wait_for(state="visible", timeout=DOWNLOAD_TIMEOUT_MS)
    except Exception:
        return None  # no "Download Report" button showed up -- not ready yet

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            download_button.click()
        download = download_info.value
        dest = DOWNLOAD_DIR / f"{barcode}.pdf"
        download.save_as(dest)
        return dest
    except Exception:
        return None


def _send_email(subject: str, body: str, attachment: Path | None = None) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = RESULT_EMAIL_TO
    msg.set_content(body)
    if attachment:
        msg.add_attachment(
            attachment.read_bytes(), maintype="application", subtype="pdf", filename=attachment.name
        )
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(msg)


def send_result_email(barcode: str, last_name: str, pdf_path: Path) -> None:
    _send_email(
        subject=f"Well water test result ready -- barcode {barcode}",
        body=(
            f"Your well water test result is ready.\n\n"
            f"Barcode: {barcode}\nLast name used: {last_name}\n\n"
            f"The report is attached as a PDF."
        ),
        attachment=pdf_path,
    )


def send_overdue_warning(overdue: list[dict]) -> None:
    if not overdue:
        return
    lines = "\n".join(
        f"  - barcode {i['barcode']} (last name {i['last_name']}), submitted {i['submitted_at']}"
        for i in overdue
    )
    _send_email(
        subject=f"Well water tester: {len(overdue)} sample(s) still pending after {OVERDUE_DAYS} days",
        body=(
            "These samples have been checked repeatedly but no result has "
            f"shown up yet after {OVERDUE_DAYS} days (PHO's own estimate is "
            "2-4 business days). Worth checking manually on the portal or "
            "with PHO's lab customer service:\n\n" + lines
        ),
    )


def run(headless: bool = True) -> None:
    items = load_pending(PENDING_FILE)

    new_triggers = fetch_new_trigger_samples()
    for t in new_triggers:
        print(f"New trigger email: barcode {t['barcode']}")
        items = add_sample(items, t["barcode"], RESULT_LAST_NAME, t["submitted_at"])
    if new_triggers:
        save_pending(PENDING_FILE, items)

    if not items:
        print("No pending samples.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        for item in list(items):
            barcode, last_name = item["barcode"], item["last_name"]
            print(f"Checking {barcode}...")
            pdf_path = check_one(page, barcode, last_name)
            if pdf_path:
                print(f"  -> result ready, emailing {pdf_path}")
                send_result_email(barcode, last_name, pdf_path)
                items = remove_sample(items, barcode)
            else:
                print("  -> not ready yet")
                items = bump_attempts(items, barcode)

        browser.close()

    save_pending(PENDING_FILE, items)

    # UTC, not local time: this container and whatever machine sent the
    # trigger email can easily be in different timezones -- see
    # is_overdue()'s docstring in water_result_logic.py for why that
    # matters here.
    now = dt.datetime.now(dt.timezone.utc)
    overdue = [i for i in items if is_overdue(i["submitted_at"], now, OVERDUE_DAYS)]
    send_overdue_warning(overdue)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--debug", nargs=2, metavar=("BARCODE", "LAST_NAME"),
        help="Check a single, real sample with a visible browser -- for verifying the site's behaviour.",
    )
    args = parser.parse_args()

    if args.debug:
        # No SMTP/IMAP needed here -- --debug never sends mail or touches
        # the trigger inbox, it only drives the browser and reports what
        # happened, so it works with no .env at all.
        barcode, last_name = args.debug
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            pdf_path = check_one(page, barcode, last_name)
            print(f"Result: {pdf_path if pdf_path else 'no PDF downloaded within the timeout'}")
            input("Press Enter to close the browser...")
            browser.close()
        return

    missing_smtp = [n for n in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "RESULT_EMAIL_TO") if not os.environ.get(n)]
    if missing_smtp:
        sys.exit(f"Missing required env var(s): {', '.join(missing_smtp)}")
    if IMAP_HOST and not RESULT_LAST_NAME:
        sys.exit(
            "IMAP_HOST is set (trigger intake enabled) but RESULT_LAST_NAME is "
            "empty -- trigger emails no longer carry a last name, so it must be "
            "configured here to query the Get Results form. Set it in .env."
        )
    if IMAP_HOST and not TRIGGER_ENCRYPTION_KEY:
        sys.exit(
            "IMAP_HOST is set (trigger intake enabled) but TRIGGER_ENCRYPTION_KEY "
            "is empty -- trigger emails are encrypted, so this must match "
            "TRIGGER_ENCRYPTION_KEY in the root .env on the Mac side. Generate one "
            "with: python3 -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )

    run(headless=True)


if __name__ == "__main__":
    main()
