"""
Optional webcam barcode scanner for water_form_filler.py.

Setup (once), in addition to Playwright:
    pip install opencv-python zxing-cpp

Tested against a real photo of a PHO sample barcode: it's Code 128,
9 digits, and zxing-cpp decodes it correctly even from a blurry phone
photo -- no system library (like zbar) needed, the pip package bundles
its own decoder.

If either package is missing, or no camera is found, scanning is skipped
automatically and water_form_filler.py falls back to typing the barcode
by hand. I couldn't test the live camera loop itself (no camera in my
sandbox) -- if a window doesn't open or nothing gets detected, check
that OpenCV can see your camera at all (index 0) before assuming the
barcode-reading part is at fault.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional, Tuple

from water_form_logic import stable_reading

try:
    import cv2
    import zxingcpp
    CAMERA_AVAILABLE = True
except ImportError:
    CAMERA_AVAILABLE = False

REQUIRED_STABLE_READS = 3  # consecutive identical reads before accepting
WINDOW_NAME = "Barcode scanner -- point at the sample barcode (ESC/q to type it instead)"


def scan_barcode() -> Optional[Tuple[str, dt.datetime]]:
    """Open the default webcam and look for the sample's barcode.

    Returns (barcode, moment_it_was_confirmed) once the same valid-looking
    8-9 digit value has been read several frames in a row -- that moment
    is what water_form_filler.py uses as the collection date/time, since
    scanning happens right at collection. Returns None if the camera/
    libraries aren't available, no camera is found, or the user presses
    ESC/q to cancel (caller should fall back to manual entry)."""
    if not CAMERA_AVAILABLE:
        print("  (camera scanning not set up -- pip install opencv-python zxing-cpp to enable it)")
        return None

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("  (no camera found -- falling back to manual entry)")
        cap.release()
        return None

    recent: list[str] = []
    result: Optional[str] = None
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("  (lost the camera feed -- falling back to manual entry)")
                break

            barcodes = zxingcpp.read_barcodes(frame, formats=zxingcpp.BarcodeFormat.Code128)
            texts = [b.text.strip() for b in barcodes if b.valid]

            if texts:
                recent.append(texts[0])
                candidate = stable_reading(recent, REQUIRED_STABLE_READS)
                colour = (0, 200, 0) if candidate else (0, 165, 255)
                cv2.putText(frame, texts[0], (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, colour, 2)
                if candidate:
                    result = candidate
            else:
                recent = []

            cv2.putText(
                frame, "ESC/q to type it manually instead",
                (20, frame.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
            )
            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF
            if result or key in (27, ord("q")):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if result:
        return result, dt.datetime.now()
    return None
