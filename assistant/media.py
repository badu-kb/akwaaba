"""Turns an answer into things people can print, forward or keep:
  - a branded A4 PDF (health talks, checklists, register summaries, quizzes with an answer key)
  - an image card sized for WhatsApp, to forward to a volunteers' or community group
  - a quiz results card

Same look as akwaabahealth.org: Bricolage Grotesque headings, Hanken Grotesk text, JetBrains Mono
labels, warm paper and a kente strip. The site fonts lack some Twi and Ewe letters (ɛ, ɔ, ɖ, ƒ, ʋ, ɣ),
so those letters fall back to DejaVu, letter by letter. All fonts are free (OFL / DejaVu licence)
and bundled in static/fonts.
"""

import io
import re
import textwrap
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

FONTS = Path(__file__).resolve().parent / "static" / "fonts"
PAPER, PAPER2, INK, INK2, MUTED = "#F6F3EC", "#EEE9DF", "#15130F", "#4A4339", "#7A7266"
GOLD, GOLD_BRIGHT, GREEN, RED = "#B88300", "#F2B705", "#0A7B3E", "#C8102E"
# kente strip: gold cloth with ink, green and red threads, as on the site
KENTE = [(GOLD_BRIGHT, 9), (INK, 1.2), (GREEN, 5.8), (INK, 1.2), (RED, 3.8), (INK, 1.2)]
HEAD, TEXT, TEXT_B, TEXT_I, MONO, FALL, FALL_B = ("Bricolage-SemiBold.ttf", "Hanken-Regular.ttf", "Hanken-SemiBold.ttf",
                                                  "Hanken-Italic.ttf", "JetBrainsMono-Medium.ttf", "DejaVuSans.ttf",
                                                  "DejaVuSans-Bold.ttf")


def _cmap(file):
    from fontTools.ttLib import TTFont
    if file not in _CMAPS:
        _CMAPS[file] = set(TTFont(str(FONTS / file)).getBestCmap())
    return _CMAPS[file]


_CMAPS: dict = {}


def _missing(ch: str) -> bool:
    """True when a site font can't draw this letter (some Twi and Ewe letters)."""
    o = ord(ch)
    return ch.strip() != "" and (o not in _cmap(TEXT) or o not in _cmap(HEAD))

# Pictographs and flags don't exist in print fonts; letters, ✓ ✗ • and accents stay.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
                    "\U0001F1E6-\U0001F1FF️‍⃣]")
_KEEP = {"✓", "✗", "✔", "✖"}


def strip_emoji(text: str) -> str:
    return "".join(ch for ch in text if ch in _KEEP or not _EMOJI.match(ch)).replace("  ", " ")


def plain(text: str) -> str:
    """WhatsApp formatting removed: for speech and for titles."""
    t = strip_emoji(text)
    t = re.sub(r"(?<!\w)[*_~](.+?)[*_~](?!\w)", r"\1", t)
    t = re.sub(r"(?m)^\s*>\s?", "", t)
    t = re.sub(r"(?m)^\s*[•\-]\s*", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def title_of(text: str, fallback: str = "Akwaaba") -> str:
    for line in text.splitlines():
        line = plain(line).strip(" :")
        if line:
            return line[:80]
    return fallback


# ---------------------------------------------------------------------------- PDF

def _fonts():
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "Hk" in pdfmetrics.getRegisteredFontNames():
        return
    for name, file in (("Hk", TEXT), ("Hk-B", TEXT_B), ("Hk-I", TEXT_I), ("Br", HEAD), ("Mono", MONO),
                       ("Dj", "DejaVuSans.ttf"), ("Dj-B", "DejaVuSans-Bold.ttf"),
                       ("Dj-I", "DejaVuSans-Oblique.ttf"), ("Dj-BI", "DejaVuSans-BoldOblique.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(FONTS / file)))
    addMapping("Hk", 0, 0, "Hk"); addMapping("Hk", 1, 0, "Hk-B")
    addMapping("Hk", 0, 1, "Hk-I"); addMapping("Hk", 1, 1, "Hk-B")
    addMapping("Br", 0, 0, "Br"); addMapping("Br", 1, 0, "Br"); addMapping("Br", 0, 1, "Br"); addMapping("Br", 1, 1, "Br")
    addMapping("Dj", 0, 0, "Dj"); addMapping("Dj", 1, 0, "Dj-B")
    addMapping("Dj", 0, 1, "Dj-I"); addMapping("Dj", 1, 1, "Dj-BI")


def _fallback_tags(t: str, bold: bool = False) -> str:
    """Wraps letters the site fonts lack in a DejaVu <font> tag (escaped text in, Paragraph markup out)."""
    out, run = [], ""
    for ch in t:
        if _missing(ch):
            run += ch
        else:
            if run:
                out.append(f'<font name="{"Dj-B" if bold else "Dj"}">{run}</font>'); run = ""
            out.append(ch)
    if run:
        out.append(f'<font name="{"Dj-B" if bold else "Dj"}">{run}</font>')
    return "".join(out)


def _inline(text: str, bold: bool = False) -> str:
    t = escape(strip_emoji(text))
    t = _fallback_tags(t, bold)
    t = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", t)
    return t


def _kente_pdf(canvas, x, y, w, h):
    from reportlab.lib import colors
    unit, i = h * 1.4, 0
    pos = x
    while pos < x + w:
        col, size = KENTE[i % len(KENTE)]
        seg = min(size * unit, x + w - pos)
        canvas.setFillColor(colors.HexColor(col)); canvas.rect(pos, y, seg, h, stroke=0, fill=1)
        pos += seg; i += 1


def pdf(body: str, kind: str = "Notes", subtitle: str = "", footer: str = "",
        worksheet: bool = False) -> bytes:
    """A4 PDF from an answer written in WhatsApp formatting."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    _fonts()
    W, H = A4
    base = ParagraphStyle("b", fontName="Hk", fontSize=11, leading=16.5, textColor=colors.HexColor(INK2),
                          alignment=TA_LEFT, spaceAfter=4)
    h1 = ParagraphStyle("h1", parent=base, fontName="Br", fontSize=24, leading=27,
                        textColor=colors.HexColor(INK), spaceAfter=12)
    h2 = ParagraphStyle("h2", parent=base, fontName="Br", fontSize=14, leading=18,
                        textColor=colors.HexColor(GREEN), spaceBefore=10, spaceAfter=4)
    bullet = ParagraphStyle("li", parent=base, leftIndent=16, bulletIndent=3, bulletFontName="Hk-B",
                            bulletColor=colors.HexColor(GOLD))
    quote = ParagraphStyle("q", parent=base, fontName="Hk-B", textColor=colors.HexColor(INK))

    lines = body.replace("\r\n", "\n").split("\n")
    story, title_done = [], False
    if worksheet:
        story.append(Table([["Name: ______________________", "Date: ___________"]],
                           colWidths=[110 * mm, 60 * mm],
                           style=TableStyle([("FONT", (0, 0), (-1, -1), "Hk", 10),
                                             ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(MUTED)),
                                             ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                             ("BOTTOMPADDING", (0, 0), (-1, -1), 12)])))
    for raw in lines:
        line = raw.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        if re.fullmatch(r"\(?Reply MORE.*", line, re.I):
            continue
        bold_line = re.fullmatch(r"\*([^*]+)\*:?", line)
        if bold_line and re.match(r"(answers?|answer key|mmuae|ŋuɖoɖo)", plain(bold_line.group(1)), re.I) \
                and worksheet:
            story += [PageBreak(), Paragraph("Answer key", h1)]
            continue
        if bold_line and not title_done:
            story.append(Paragraph(_inline(bold_line.group(1), True), h1))
            title_done = True
        elif bold_line:
            story.append(Paragraph(_inline(bold_line.group(1), True), h2))
        elif line.startswith(("•", "- ")):
            story.append(Paragraph(_inline(line.lstrip("•- ").strip()), bullet, bulletText="•"))
        elif re.match(r"^\d+[.)]\s", line):
            n, rest = re.match(r"^(\d+)[.)]\s+(.*)", line).groups()
            story.append(Paragraph(_inline(rest), bullet, bulletText=f"{n}."))
        elif line.startswith(">"):
            box = Table([[Paragraph(_inline(line.lstrip("> ").strip(), True), quote)]], colWidths=[W - 40 * mm],
                        style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(PAPER)),
                                          ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(GOLD_BRIGHT)),
                                          ("LEFTPADDING", (0, 0), (-1, -1), 12),
                                          ("TOPPADDING", (0, 0), (-1, -1), 8),
                                          ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
            story.append(KeepTogether([Spacer(1, 4), box, Spacer(1, 4)]))
        else:
            story.append(Paragraph(_inline(line), base))

    def decorate(canvas, doc):
        canvas.saveState()
        _kente_pdf(canvas, 0, H - 3.2 * mm, W, 3.2 * mm)
        canvas.setFillColor(colors.HexColor(INK))
        canvas.setFont("Br", 15)
        canvas.drawString(20 * mm, H - 15 * mm, "Akwaaba")
        canvas.setFont("Mono", 7.5)
        canvas.setFillColor(colors.HexColor(GOLD))
        canvas.drawString(20 * mm + canvas.stringWidth("Akwaaba", "Br", 15) + 4 * mm, H - 15 * mm, plain(kind).upper())
        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.drawRightString(W - 20 * mm, H - 15 * mm, (subtitle or date.today().strftime("%d %b %Y")).upper())
        canvas.setStrokeColor(colors.HexColor(INK)); canvas.setLineWidth(1)
        canvas.line(20 * mm, H - 19 * mm, W - 20 * mm, H - 19 * mm)
        canvas.setStrokeColor(colors.HexColor("#DDD8CE")); canvas.setLineWidth(.5)
        canvas.line(20 * mm, 14 * mm, W - 20 * mm, 14 * mm)
        canvas.setFont("Mono", 6.8); canvas.setFillColor(colors.HexColor(MUTED))
        canvas.drawString(20 * mm, 9.5 * mm, (footer or "Made with Akwaaba, a free WhatsApp assistant for community health workers").upper())
        canvas.drawRightString(W - 20 * mm, 9.5 * mm, f"{doc.page:02d}")
        canvas.restoreState()

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=30 * mm,
                      bottomMargin=22 * mm, title=title_of(body), author="Akwaaba").build(
        story or [Paragraph(" ", base)], onFirstPage=decorate, onLaterPages=decorate)
    return out.getvalue()


# ---------------------------------------------------------------------------- images

def _font(file, size):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONTS / file), size)


class _Face:
    """A site font at a size, with DejaVu standing in for letters it lacks."""
    def __init__(self, file, size, fallback=FALL):
        self.main, self.fall = _font(file, size), _font(fallback, round(size * .93))

    def runs(self, text):
        out, cur, cur_fall = [], "", None
        for ch in text:
            f = _missing(ch)
            if cur and f != cur_fall:
                out.append((cur, self.fall if cur_fall else self.main)); cur = ""
            cur += ch; cur_fall = f
        if cur:
            out.append((cur, self.fall if cur_fall else self.main))
        return out

    def length(self, draw, text):
        return sum(draw.textlength(t, font=f) for t, f in self.runs(text))

    def ascent(self):
        return self.main.getmetrics()[0]


def _text(d, x, y, text, face, fill, align="left"):
    """Draws text with its top at y (baseline-aligned across fonts)."""
    if align == "center":
        x -= face.length(d, text) / 2
    elif align == "right":
        x -= face.length(d, text)
    base = y + face.ascent()
    for t, f in face.runs(text):
        d.text((x, base), t, font=f, fill=fill, anchor="ls")
        x += d.textlength(t, font=f)


def _wrap(draw, text, face, width):
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        words, line = para.split(" "), ""
        for w in words:
            trial = f"{line} {w}".strip()
            if face.length(draw, trial) <= width:
                line = trial
            else:
                if line:
                    out.append(line)
                line = w
        out.append(line)
    return out


def _kente(d, y, w, h):
    unit, i, x = h * 1.4, 0, 0.0
    while x < w:
        col, size = KENTE[i % len(KENTE)]
        d.rectangle([x, y, x + size * unit, y + h], fill=col)
        x += size * unit; i += 1


def _frame(height, label):
    from PIL import Image, ImageDraw
    W = 1080
    img = Image.new("RGB", (W, height), PAPER)
    d = ImageDraw.Draw(img)
    _kente(d, 0, W, 16)
    _text(d, 70, 62, "Akwaaba", _Face(HEAD, 44), INK)
    if label:
        _text(d, W - 70, 76, plain(label).upper(), _Face(MONO, 22), GOLD, "right")
    d.rectangle([70, 140, W - 70, 142], fill=INK)
    return img, d


def _footer(d, img, text):
    W = img.width
    d.rectangle([70, img.height - 104, W - 70, img.height - 103], fill="#DDD8CE")
    _text(d, 70, img.height - 78, text.upper(), _Face(MONO, 19), MUTED)


def card(body: str, label: str = "", footer: str = "Free WhatsApp assistant for community health workers") -> bytes:
    """A shareable image of a short answer (e.g. key points from a health talk)."""
    from PIL import ImageDraw, Image
    W, pad = 1080, 70
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    title = title_of(body)
    rest = body.split("\n", 1)[1] if "\n" in body and plain(body.split("\n", 1)[0]) == title else body
    rest = re.sub(r"\n?\(?Reply MORE.*$", "", rest, flags=re.I | re.S)
    tf, bf = _Face(HEAD, 66), _Face(TEXT, 36)
    tlines = _wrap(probe, title, tf, W - 2 * pad)[:3]
    blines = []
    for line in plain(rest).split("\n"):
        blines += _wrap(probe, line, bf, W - 2 * pad - 20)
    max_body = 28
    if len(blines) > max_body:
        blines = blines[:max_body - 1] + ["…"]
    height = max(1080, 220 + len(tlines) * 72 + 60 + len(blines) * 54 + 170)
    img, d = _frame(min(height, 1920), label)
    y = 200
    for line in tlines:
        _text(d, pad, y, line, tf, INK)
        y += 72
    d.rectangle([pad, y + 14, pad + 110, y + 22], fill=GOLD_BRIGHT)
    y += 62
    for line in blines:
        _text(d, pad, y, line, bf, INK2)
        y += 54
    _footer(d, img, footer)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def results_card(score: int, total: int, topic: str, headline: str, label: str = "Quiz") -> bytes:
    from PIL import Image, ImageDraw
    img, d = _frame(1080, label)
    cx, cy, r = 540, 560, 210
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=PAPER2, width=36)
    if total:
        d.arc([cx - r, cy - r, cx + r, cy + r], start=-90, end=-90 + 360 * score / total,
              fill=GREEN if score / total >= 0.6 else GOLD_BRIGHT, width=36)
    big = _Face(HEAD, 128)
    _text(d, cx, cy - 92, f"{score}/{total}", big, INK, "center")
    _text(d, cx, cy + 50, f"{round(100 * score / total) if total else 0}%", _Face(TEXT, 40), MUTED, "center")
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    hf = _Face(HEAD, 54)
    y = 196
    for line in _wrap(probe, strip_emoji(headline), hf, 940)[:2]:
        _text(d, 540, y, line, hf, INK, "center")
        y += 62
    tf = _Face(TEXT, 36)
    for i, line in enumerate(_wrap(probe, strip_emoji(topic), tf, 940)[:2]):
        _text(d, 540, 820 + i * 46, line, tf, INK2, "center")
    _footer(d, img, "Akwaaba · free WhatsApp assistant")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
