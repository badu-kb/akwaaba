"""Hides names on register photos BEFORE they reach Claude.

Registers list patients by name. Akwaaba only needs the counts, so every photo
taken for a monthly return goes through two layers here first:

  1. Known name columns. A report format (report_formats/*.json) can list
     "redact_bands": horizontal ranges of the page, as fractions of its width,
     where that district's register keeps names. Those bands are painted over
     completely. This is the strong layer, and it needs the district's real
     register layout, which the field team collects before the pilot.
  2. Words. Tesseract OCR finds every word on the page. Anything with letters
     is painted over unless it is a known register heading ("Age", "Sex",
     "Malaria", ...). Long digit runs (phone numbers, ID numbers) are hidden too.
     Counts, tally marks and dates survive.

It fails closed: if Tesseract is missing or the photo can't be read, the photo
is NOT sent to Claude and the health worker is told why.

OCR is weaker on handwriting, so layer 1 matters, and the district data-sharing
agreement and Data Protection Commission registration still come first.
"""

import io
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("akwaaba")

# Register headings and common health terms that are safe to keep visible.
KEEP_WORDS = {
    "age", "sex", "male", "female", "m", "f", "date", "total", "totals", "month", "week", "year", "page",
    "register", "tally", "sheet", "chps", "compound", "community", "health", "officer", "district", "sub",
    "opd", "attendance", "new", "old", "visits", "visit", "home", "referral", "referrals", "referred",
    "malaria", "suspected", "tested", "rdt", "positive", "negative", "confirmed", "treated", "act",
    "anc", "pnc", "antenatal", "postnatal", "delivery", "deliveries", "births", "birth", "registrants",
    "cwc", "weighed", "weight", "penta", "mr", "measles", "rubella", "bcg", "opv", "pcv", "rota",
    "fp", "family", "planning", "acceptors", "continuing", "users", "depo", "implant", "pills", "condoms",
    "diarrhoea", "diarrhea", "ors", "zinc", "pneumonia", "cough", "fever", "talks", "talk", "under", "over",
    "yrs", "years", "months", "no", "yes", "remarks", "outcome", "dose", "doses", "ghs", "ghana", "service",
    # column headings, common complaints and diagnoses (words, never names)
    "complaint", "complaints", "diagnosis", "diagnoses", "symptoms", "result", "results", "status", "gestation",
    "wks", "weeks", "gravida", "parity", "visit", "attendant", "number", "serial", "address", "community",
    "headache", "rash", "pain", "pains", "body", "vomiting", "cold", "catarrh", "convulsion", "convulsions",
    "anaemia", "anemia", "measles", "typhoid", "hypertension", "diabetes", "ulcer", "wound", "wounds", "injury",
    "skin", "eye", "ear", "abdominal", "chest", "dehydration", "malnutrition", "jaundice", "worms", "infection",
    # months
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}
ORDINAL = re.compile(r"^\d{1,2}(st|nd|rd|th)$")
LONG_DIGITS = re.compile(r"\d{7,}")


class RedactionUnavailable(RuntimeError):
    pass


def available() -> tuple[bool, str]:
    if not shutil.which("tesseract"):
        return False, "Tesseract OCR is not installed (brew install tesseract)"
    return True, "ready"


def _hide(word: str, keep: set[str]) -> bool:
    w = word.strip(".,:;()[]{}|/\\-_'\"").lower()
    if not w:
        return False
    if LONG_DIGITS.search(w):
        return True
    if not re.search(r"[a-zɛɔŋƒʋɖ]", w):
        return False  # numbers, tallies, dates
    if ORDINAL.match(w):
        return False  # 1st, 2nd, 3rd visit
    return w not in keep


def _words(path: Path) -> list[dict]:
    out = subprocess.run(["tesseract", str(path), "stdout", "--psm", "11", "tsv"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RedactionUnavailable(out.stderr.strip()[:200] or "OCR failed")
    rows = out.stdout.splitlines()
    words = []
    for line in rows[1:]:
        cols = line.split("\t")
        if len(cols) < 12 or not cols[11].strip():
            continue
        left, top, width, height = (int(c) for c in cols[6:10])
        words.append({"text": cols[11], "box": (left, top, left + width, top + height)})
    return words


def redact(image: bytes, fmt: dict | None = None) -> tuple[bytes, int]:
    """Returns (JPEG bytes with names painted over, number of areas hidden)."""
    ok, why = available()
    if not ok:
        raise RedactionUnavailable(why)
    from PIL import Image, ImageDraw, ImageOps
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(image))).convert("RGB")
    except Exception as e:
        raise RedactionUnavailable(f"photo unreadable: {e}")
    if im.width > 2400:  # big enough to read counts, small enough for OCR and upload
        im = im.resize((2400, int(im.height * 2400 / im.width)))
    fmt = fmt or {}
    if not fmt.get("redact_bands"):  # no approved register layout: we can't know where the name columns are
        raise RedactionUnavailable("no approved register layout (redact_bands is empty)")
    keep = KEEP_WORDS | {w.lower() for w in fmt.get("keep_words", [])}
    draw = ImageDraw.Draw(im)
    hidden = 0
    for a, b in fmt.get("redact_bands", []):  # layer 1: the district's known name columns
        draw.rectangle([int(a * im.width), 0, int(b * im.width), im.height], fill=(40, 40, 40))
        hidden += 1
    with tempfile.TemporaryDirectory(prefix="akwaaba-redact-") as d:
        path = Path(d) / "page.png"
        im.save(path)
        words = _words(path)
        if not words:  # OCR read nothing at all: handwriting may still be visible, so refuse
            raise RedactionUnavailable("couldn't read the page well enough to hide names")
        for w in words:  # layer 2: every word that isn't a known heading
            if _hide(w["text"], keep):
                x0, y0, x1, y1 = w["box"]
                pad = max(2, (y1 - y0) // 4)
                draw.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], fill=(40, 40, 40))
                hidden += 1
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue(), hidden
