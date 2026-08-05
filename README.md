# PHO Well Water Testing Portal — Form Filler

Fills out the [PHO Online Water Testing Portal](https://www.publichealthontario.ca/laboratory-services/well-water-testing/portal?tab=0)
requisition form automatically, using a mix of data that never changes
(your name, address, Public Health Unit, ...) and data that changes with
every sample (barcode, collection date/time).

**It never submits the form.** It opens a real, visible browser window,
fills in every field, and then stops so you can review everything and
click Submit yourself.

## Files

| File | Purpose |
|---|---|
| `water_form_filler.py` | Main script. Run this one. |
| `water_form_config.py` | Loads your fixed data (name, address, Public Health Unit, ...) from `.env`. Nothing to edit here. |
| `.env.example` | Template for your fixed data. Copy to `.env` and fill in your real values. |
| `water_form_logic.py` | Pure logic (barcode validation, prompts, camera-reading debounce). No browser needed — this is what's unit-tested. |
| `water_form_camera.py` | Optional webcam barcode scanner. |
| `test_water_form_logic.py` | Unit tests for `water_form_logic.py`. |

`.env` is your personal data and is gitignored — it's never committed,
which is what makes it safe to keep this repo public.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

`opencv-python`, `zxing-cpp`, and `pytest` in `requirements.txt` are
optional (camera scanning and testing, respectively) — if you'd rather
skip them, just don't install those three and everything else still
works, minus that feature.

## Configure your fixed data

```bash
cp .env.example .env
```

Then open `.env` and fill in your real values once: last name, address,
Public Health Unit, etc. Anything you leave empty is treated as not
applicable and skipped. Fields marked "(dropdown option text)" must
match the on-page dropdown's visible text **exactly**, including
capitalization (e.g. `Street`, not `street`).

The script refuses to run until the required fields (`LAST_NAME`,
`MAILING_STREET_NO`, `MAILING_STREET_NAME`, `MAILING_CITY_TOWN`,
`PUBLIC_HEALTH_UNIT`) are non-empty in `.env`.

## Usage

```bash
python water_form_filler.py
```

1. **Barcode + collection time.** The script tries to open your webcam
   and read the barcode on the sample bottle (it's a Code 128 barcode —
   confirmed against a real sample photo). Once the same value has been
   read for a few frames in a row, it's accepted, and *that moment* is
   used as the collection date/time (since you scan right after
   collecting).
   - Press `ESC` or `q` in the camera window at any point to cancel and
     type the barcode by hand instead.
   - If the camera libraries aren't installed, or no camera is found,
     it falls back to manual entry automatically — you'll be prompted
     for the barcode (typed twice, to catch typos) and the collection
     date/time (press Enter to accept "now" as the default for either).
2. **Browser fills the form.** A visible Chromium window opens, navigates
   to the portal, and fills in every field using your config plus the
   barcode/date/time from step 1.
3. **You review and submit.** The script prints `[FAILED]` for any field
   it couldn't fill (with the underlying error, so you can see exactly
   what happened) and leaves the Terms and Conditions checkbox and the
   Submit button untouched. Check the browser window, fix anything
   flagged, tick the checkbox yourself, and submit manually. Press Enter
   in the terminal once you're done to close the browser.

## Testing

```bash
pytest test_water_form_logic.py
```

These tests cover the barcode/date/time prompts and the camera-reading
stability check — all pure logic, no browser or camera required.

## Notes on how the form works (in case the site changes)

- Fields are targeted by their HTML `id`, not by visible label text —
  more robust against wording changes, and several labels on this page
  aren't reliably associated with their inputs anyway.
- The dropdowns (Street Type, Street Direction, Township/Municipality,
  County/District, Public Health Unit) are Semantic UI widgets with a
  hidden native `<select>`. They're set via the page's own
  `jQuery(...).dropdown('set selected', text)` API rather than
  Playwright's `select_option()`, which can't reach a hidden element.
- "Address of water source is the same as the mailing address" only
  becomes clickable once the mailing address's required fields are
  filled, so the mailing section always runs first.
- The Postal Code field has a JS input mask that silently clears itself
  on a bulk value assignment; it's filled by simulating real keystrokes
  instead. Every field is read back after filling and verified — a
  wrong or empty value shows up as `[FAILED]` rather than failing
  silently.

If the site's markup changes and a field starts failing, the printed
exception under `[FAILED]` shows exactly what Playwright saw, which is
the fastest way to find the new selector.
