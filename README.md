# PHO Well Water Testing Portal — Automation

Two parts:

1. **Form filler** (`water_form_filler.py`, run on your own machine) — fills
   out the [PHO Online Water Testing Portal](https://www.publichealthontario.ca/laboratory-services/well-water-testing/portal?tab=0)
   requisition form automatically. **It never submits the form** — it opens
   a real, visible browser window, fills in every field, and stops so you
   can review and submit yourself.
2. **Result fetcher** (`docker_result-fetcher/water_result_fetcher.py`, run
   as a plain Docker container on any Docker host) — once you've submitted
   a sample, checks periodically whether the result is ready and emails
   you the PDF when it is. It finds out a sample was submitted by reading
   a dedicated trigger mailbox over IMAP — an outbound connection it
   initiates, so the host never needs to be reachable from outside its
   own network.

## Files

Everything for Part 1 lives at the repo root; everything for Part 2 lives
in `docker_result-fetcher/`, so the two deployments don't mix.

| File | Runs on | Purpose |
|---|---|---|
| `water_form_filler.py` | your machine | Main form-filling script. |
| `water_form_config.py` | your machine | Loads your fixed data from `.env`. |
| `.env.example` | — | Template for `.env`. Copy it, fill in your data. |
| `water_form_logic.py` | your machine | Pure logic (barcode validation, prompts, camera debounce). Unit-tested. |
| `water_form_camera.py` | your machine | Optional webcam barcode scanner. |
| `test_water_form_logic.py` | — | Tests for `water_form_logic.py`. |
| `requirements.txt` | your machine | Dependencies for Part 1. |
| `docker_result-fetcher/water_result_fetcher.py` | Docker container | Reads new "sample submitted" trigger emails via IMAP, queues them, checks queued samples, emails the PDF when ready. |
| `docker_result-fetcher/water_result_logic.py` | Docker container | Pure logic (pending-list management, trigger-email parsing). Unit-tested. |
| `docker_result-fetcher/test_water_result_logic.py` | — | Tests for `water_result_logic.py`. |
| `docker_result-fetcher/requirements.txt` | Docker container | Dependencies for Part 2. |
| `docker_result-fetcher/Dockerfile`, `docker_result-fetcher/docker-compose.yml` | — | Container definition. |
| `docker_result-fetcher/.env.example` | — | Template for `docker_result-fetcher/.env`. |

`.env` (root) and `docker_result-fetcher/.env` hold your personal
data/secrets and are both gitignored — never committed, which is what
makes this repo safe to keep public.

---

## Part 1: Form filler (your machine)

### Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

`opencv-python`, `zxing-cpp`, and `pytest` in `requirements.txt` are
optional (camera scanning and testing, respectively) — skip installing
either and that feature is silently disabled, everything else still
works. Emailing the trigger to the result-fetcher (below) uses the
standard library's `smtplib` to send, plus `cryptography` to encrypt
the trigger body — both are required, not optional.

### Configure your fixed data

```bash
cp .env.example .env
```

Open `.env` and fill in your real values once: last name, address, Public
Health Unit, etc. Leave a value empty if it's optional and doesn't apply to
you. Fields marked "(dropdown option text)" must match the on-page
dropdown's visible text **exactly**, including capitalization (e.g.
`Street`, not `street`). No quotes needed around multi-word values.

The script refuses to run until the required fields (`LAST_NAME`,
`MAILING_STREET_NO`, `MAILING_STREET_NAME`, `MAILING_CITY_TOWN`,
`PUBLIC_HEALTH_UNIT`) are non-empty in `.env`.

### Usage

```bash
python water_form_filler.py
```

1. **Barcode + collection time.** Tries to open your webcam and read the
   barcode on the sample bottle (Code 128, confirmed against a real sample
   photo). Once the same value is read a few frames in a row, it's
   accepted, and *that moment* is used as the collection date/time.
   - `ESC`/`q` in the camera window cancels and falls back to typing the
     barcode by hand.
   - No camera/libraries available -> falls back automatically, prompting
     for the barcode (typed twice) and collection date/time (Enter =
     "now").
2. **Browser fills the form.** A visible Chromium window fills in every
   field using your `.env` data plus the barcode/date/time from step 1.
3. **You review and submit.** `[FAILED]` is printed for any field that
   couldn't be filled (with the underlying error). The Terms and
   Conditions checkbox and Submit button are left untouched — check the
   browser, fix anything flagged, and submit manually.
4. **Confirm submission.** You're asked whether you submitted the form. If
   yes, and `TRIGGER_EMAIL_TO` is set in `.env`, it emails a "sample
   submitted" notice (barcode + timestamp, encrypted with
   `TRIGGER_ENCRYPTION_KEY` — see Part 2) to that address. The
   result-fetcher (Part 2) reads that inbox via IMAP and starts checking
   for that sample — no inbound connection to it involved, so this works
   from any network, not just home.

### Testing

```bash
pytest test_water_form_logic.py
```

---

## Part 2: Result fetcher (Docker)

`docker_result-fetcher/` is a plain Docker container and works the same
on any Docker host — a NAS, a Raspberry Pi, a VPS with cron, whatever
you've got. The instructions below are written generically; the one
place a specific product comes up is scheduling, where DSM's Task
Scheduler is used as the example (swap in cron or whatever your host
uses instead).

### Why it's built this way

The result-fetcher doesn't run as one long process that waits until a
result arrives — on a NAS, anything that has to survive reboots, DSM
updates, and power blips for days at a time is fragile. It's also not a
webhook receiver: an earlier version was, but that required the host to
be reachable from your Mac, which in practice meant either being on the
same LAN (fine at home, useless anywhere else) or exposing a port to the
internet (not something to do for a personal NAS). Instead:

- `water_result_fetcher.py` is a plain script, **not a server** —
  invoked periodically (e.g. 3x/day) by a scheduler (DSM's Task
  Scheduler, cron, etc.). Each run does two things: it logs into a
  dedicated mailbox via IMAP
  (an *outbound* connection the script itself initiates — nothing needs
  to be reachable from outside) to pick up any new "sample submitted"
  emails from `water_form_filler.py`, then checks every pending sample
  once against the PHO site. Results that are ready get emailed and
  removed from the queue; everything else stays queued for the next
  scheduled run. That's what gives you "keep checking until the result
  shows up" without an always-on process or an open port.

### How result-checking actually works

Submitting the Get Results form doesn't download anything by itself —
it loads a results panel, and only if a result actually exists does a
"Download Report" button show up:

```html
<button type="button" class="ui secondary button" data-barcode="...">
    Download Report <i class="small download icon"></i>
</button>
```

Its `data-barcode` attribute matches the barcode you searched for, so
`check_one()` targets it exactly. Clicking it starts an immediate
browser download with no intermediate confirmation step (verified
against a real, previously-submitted sample, both via `--debug` and the
full scheduled pipeline end-to-end). If that button never appears
within the timeout, there's no result yet — safe to just retry on the
next scheduled run.

**Debugging tip**: if PHO ever changes this page and results stop
downloading, `--debug` runs a single check with a visible browser
instead of the scheduled/headless version, so you can see exactly what
the page is doing:

```bash
cd docker_result-fetcher
python water_result_fetcher.py --debug 123456789 YourLastName
```

### Setup

First, create the dedicated trigger mailbox — any provider is fine
(a free Gmail/Outlook/etc. account works well), as long as it's
**separate from** the account you use to send the trigger email from
your Mac and separate from wherever you want result emails delivered.
Its only job is to sit there and receive one short email per sample.
Most providers require an app-specific password for IMAP login rather
than your normal account password.

On the Docker host, with this repo cloned, everything below happens
inside the `docker_result-fetcher/` folder:

```bash
cd docker_result-fetcher
cp .env.example .env
```

Fill in `docker_result-fetcher/.env`:
- `IMAP_HOST` / `IMAP_PORT` / `IMAP_USERNAME` / `IMAP_PASSWORD` — the
  dedicated trigger mailbox from above.
- `RESULT_LAST_NAME` — your last name, exactly as used on the form.
  Trigger emails deliberately don't carry this (see below), so it's
  configured here once instead.
- `TRIGGER_ENCRYPTION_KEY` — a shared secret that must exactly match
  the one in the root `.env` on your Mac (see below). Generate **one**
  key and copy the same value into both files:
  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` /
  `RESULT_EMAIL_TO` — for sending you the result PDF. If you're running
  this on a Synology, you can reuse the same SMTP account already
  configured under DSM's Control Panel → Notification → Email — DSM's
  own notifications don't support custom recipients or PDF attachments,
  but the SMTP account behind them works fine here via Python's
  `smtplib`.

**What the trigger email actually contains**, and why: just the
barcode and a submission timestamp — no last name, no address, nothing
that identifies you or the property. On top of that, the body is
symmetrically encrypted (via the `cryptography` package's Fernet) with
`TRIGGER_ENCRYPTION_KEY`, so even that minimal content isn't plain text
to anyone with mailbox access or to whatever automated scanning your
mail provider does — only something holding the shared key can read
it. The subject stays a generic `[WaterSample]` (not encrypted, since
IMAP search needs to read it to recognize a trigger email at all — but
it carries no barcode or other identifying detail either way). Set
`TRIGGER_ENCRYPTION_KEY` on both sides before sending your first sample
after upgrading: a message encrypted with a key the other side doesn't
have just fails to decrypt and is silently skipped, same as any other
malformed email — it won't error loudly, it'll just never turn into a
pending sample.

**That timestamp is UTC, not local time** — deliberately. The machine
submitting a sample and the machine running the result-fetcher can
easily be in different timezones (this isn't hypothetical: submitting
from Canada and checking from a Docker host in Germany is exactly the
setup this was built for). Comparing naive local timestamps across
that gap doesn't error out, it just silently produces a wrong "days
since submission" number for `RESULT_OVERDUE_DAYS`, which is worse than
a crash. So `water_form_filler.py` writes `submitted_at` as
`datetime.now(dt.timezone.utc)`, and `is_overdue()` in
`water_result_logic.py` compares it against the fetcher's own UTC
`now` — correct regardless of what local timezone either machine is
set to. (This only applies to the internal `submitted_at` bookkeeping
timestamp; the sample's actual collection date/time on the requisition
form itself stays in local time, since that's genuinely when and where
you collected it.)

On your Mac, set `TRIGGER_EMAIL_TO` in the root `.env` to that same
dedicated mailbox's address, plus `SMTP_HOST` / `SMTP_PORT` /
`SMTP_USERNAME` / `SMTP_PASSWORD` for whatever account you want to send
*from* (a different account than the trigger mailbox itself).

Then, still inside `docker_result-fetcher/` on the Docker host, build
the image once:

```bash
docker compose build
```

There's nothing to start and no port to expose — the container only
runs when invoked (see Scheduling below) and exits when done.

### Scheduling the fetcher

On a Synology, in DSM: **Control Panel → Task Scheduler → Create →
Scheduled Task → User-defined script**. Set it to run e.g. 3x/day during
business hours, with a script like:

```bash
cd /volume1/docker/<path-to-this-repo>/docker_result-fetcher
docker compose run --rm water-tester python water_result_fetcher.py
```

(Adjust the `cd` path to wherever you cloned the repo — `docker
compose` needs to run from the `docker_result-fetcher/` folder so it
picks up `docker-compose.yml` and `.env`.)

### Testing

```bash
cd docker_result-fetcher
pip install pytest
pytest test_water_result_logic.py
```

No Playwright/imaplib/network needed — this only tests the pure
pending-list management and trigger-email parsing logic.

---

## Notes on how the requisition form works (in case the site changes)

- Fields are targeted by their HTML `id`, not by visible label text — more
  robust against wording changes, and several labels on this page aren't
  reliably associated with their inputs anyway.
- The dropdowns (Street Type, Street Direction, Township/Municipality,
  County/District, Public Health Unit) are Semantic UI widgets with a
  hidden native `<select>`. They're set via the page's own
  `jQuery(...).dropdown('set selected', text)` API rather than
  Playwright's `select_option()`, which can't reach a hidden element.
- "Address of water source is the same as the mailing address" only
  becomes clickable once the mailing address's required fields are filled,
  so the mailing section always runs first.
- The Postal Code field has a JS input mask that silently clears itself on
  a bulk value assignment; it's filled by simulating real keystrokes
  instead. Every field is read back after filling and verified — a wrong
  or empty value shows up as `[FAILED]` rather than failing silently.

If the site's markup changes and a field starts failing, the printed
exception under `[FAILED]` shows exactly what Playwright saw, which is the
fastest way to find the new selector.
