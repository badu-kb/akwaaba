"""Register photos -> a draft monthly return.

The health worker sends one photo per register or tally-sheet page. For each page:
names are hidden (redact.py), then Claude reads ONLY the counts into the fields of
the report format, through a forced tool call so the answer is structured and has
nowhere to put a name. When the worker taps Done, code (not the model) adds the
pages up, runs consistency checks, compares with last month, and lays the result
out like the monthly return: as WhatsApp text and as a printable PDF.

Akwaaba never submits anything. The nurse checks every number and submits it herself.
"""

import base64
import datetime as dt
import json
import logging
from pathlib import Path

log = logging.getLogger("akwaaba")
FORMAT_DIR = Path(__file__).resolve().parent / "report_formats"
DEFAULT_FORMAT = "chps_monthly_draft"


def photos_enabled() -> bool:
    """Register photos stay off until the district and Ghana's Data Protection Commission
    approve them. Turn on only then, with REGISTER_PHOTOS=on in .env."""
    import os
    return os.getenv("REGISTER_PHOTOS", "off").strip().lower() in ("on", "1", "true", "yes")
MAX_PAGES = 12


def load_format(name: str | None = None) -> dict:
    path = FORMAT_DIR / f"{name or DEFAULT_FORMAT}.json"
    fmt = json.loads(path.read_text(encoding="utf-8"))
    fmt["fields"] = [f for sec in fmt["sections"] for f in sec["fields"]]
    return fmt


def report_month(today: dt.date | None = None) -> str:
    """Early in the month people report the month just gone; later, the current one."""
    today = today or dt.date.today()
    if today.day <= 12:
        today = today.replace(day=1) - dt.timedelta(days=1)
    return today.strftime("%Y-%m")


def month_name(ym: str) -> str:
    return dt.date(int(ym[:4]), int(ym[5:7]), 1).strftime("%B %Y")


# ------------------------------------------------------------------ reading one page

def tool_for(fmt: dict) -> dict:
    return {
        "name": "record_counts",
        "description": "Record the counts read from one register or tally-sheet page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "counts": {
                    "type": "object",
                    "description": "Total for each field on THIS page. Use null if the page has no count for it.",
                    "properties": {f["key"]: {"type": ["integer", "null"], "description": f["label"]}
                                   for f in fmt["fields"]},
                },
                "unclear": {"type": "array", "items": {"type": "string"},
                            "description": "Keys of fields that appear on the page but can't be read reliably."},
                "page_kind": {"type": "string", "description": "What kind of register or sheet this is, in a few words."},
            },
            "required": ["counts", "unclear"],
        },
    }


PAGE_PROMPT = (
    "This is one page of a Ghanaian community clinic register or tally sheet, photographed for the "
    "monthly return. Names and identifiers have been blacked out on purpose.\n"
    "Read ONLY counts. For each field, give this page's total (add up tally marks or rows if needed). "
    "Use null when the page has nothing for a field. Never guess unreadable numbers: list them as unclear. "
    "Do not record any names, ages of named people or other identifying details."
)


def read_page(claude, model: str, fmt: dict, jpeg: bytes) -> dict:
    """One redacted page -> {"counts": {key: int|None}, "unclear": [...], "page_kind": str}."""
    resp = claude.messages.create(
        model=model, max_tokens=1200, tools=[tool_for(fmt)],
        tool_choice={"type": "tool", "name": "record_counts"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpeg).decode()}},
            {"type": "text", "text": PAGE_PROMPT}]}])
    block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
    data = getattr(block, "input", None) or {}
    keys = {f["key"] for f in fmt["fields"]}
    counts = {}
    for k, v in (data.get("counts") or {}).items():
        if k in keys and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 100000:
            counts[k] = int(v)
    unclear = [k for k in (data.get("unclear") or []) if k in keys]
    return {"counts": counts, "unclear": unclear, "page_kind": str(data.get("page_kind") or "")[:60]}


# ------------------------------------------------------------------ adding up and checking (code, not the model)

def compile_return(fmt: dict, pages: list[dict], previous: dict | None = None) -> dict:
    totals, seen = {}, set()
    for p in pages:
        for k, v in p.get("counts", {}).items():
            totals[k] = totals.get(k, 0) + v
            seen.add(k)
    unclear = sorted({k for p in pages for k in p.get("unclear", [])})
    flags = []
    labels = {f["key"]: f["label"] for f in fmt["fields"]}
    for a, op, b, msg in fmt.get("checks", []):
        if a in totals and b in totals and op == "<=" and totals[a] > totals[b]:
            flags.append(f"{msg}: {labels[a]} {totals[a]}, {labels[b]} {totals[b]}")
    jump = fmt.get("jump", {})
    for k, v in totals.items():
        old = (previous or {}).get(k)
        if old is None:
            continue
        change = abs(v - old)
        if change >= jump.get("min_change", 10) and (v > old * jump.get("ratio", 1.5) or v * jump.get("ratio", 1.5) < old):
            flags.append(f"{labels[k]} changed a lot since last month: {old} to {v}")
    missing = [f["key"] for f in fmt["fields"] if f["key"] not in seen]
    return {"totals": totals, "missing": missing, "unclear": unclear, "flags": flags, "pages": len(pages)}


def as_text(fmt: dict, ret: dict, month: str, hidden: int) -> str:
    labels = {f["key"]: f["label"] for f in fmt["fields"]}
    out = [f"*\U0001F4CA Draft monthly return · {month_name(month)}*",
           "_Draft only. Check every number against your registers, then submit it yourself._", ""]
    for sec in fmt["sections"]:
        rows = [f"• {f['label']}: *{ret['totals'][f['key']]}*" for f in sec["fields"] if f["key"] in ret["totals"]]
        if rows:
            out += [f"*{sec['title']}*", *rows, ""]
    checks = list(ret["flags"])
    checks += [f"{labels[k]}: hard to read, please check" for k in ret["unclear"]]
    if ret["missing"]:
        checks.append("Not found on these pages: " + ", ".join(labels[k] for k in ret["missing"][:8])
                      + ("…" if len(ret["missing"]) > 8 else ""))
    if checks:
        out += ["*⚠️ Please check*", *[f"• {c}" for c in checks], ""]
    out.append(f"_Pages read: {ret['pages']} · names hidden before reading ({hidden} areas)_")
    if fmt.get("placeholder"):
        out.append("_Layout is a placeholder until your district's own form is added._")
    return "\n".join(out).strip()


def as_pdf(fmt: dict, ret: dict, month: str) -> bytes:
    """A4 table laid out section by section, like the paper return."""
    import media
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    media._fonts()
    buf = __import__("io").BytesIO()
    W, H = A4
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=30 * mm, bottomMargin=22 * mm, title="Draft monthly return")
    ink, ink2, muted = colors.HexColor(media.INK), colors.HexColor(media.INK2), colors.HexColor(media.MUTED)
    h1 = ParagraphStyle("h1", fontName="Br", fontSize=24, leading=28, textColor=ink)
    small = ParagraphStyle("s", fontName="Hk", fontSize=9, leading=13, textColor=muted)
    cell = ParagraphStyle("c", fontName="Hk", fontSize=10, leading=13, textColor=ink2)
    story = [Paragraph(media._fallback_tags(f"Draft monthly return, {month_name(month)}", True), h1), Spacer(1, 2 * mm),
             Paragraph("Prepared by Akwaaba from register photos. Check every number before you submit it.", small),
             Spacer(1, 7 * mm)]
    rows = [["SECTION", "ITEM", "TOTAL"]]
    style = [("FONT", (0, 0), (-1, 0), "Mono", 7.5), ("TEXTCOLOR", (0, 0), (-1, 0), muted),
             ("FONT", (0, 1), (-1, -1), "Hk", 10), ("TEXTCOLOR", (0, 1), (-1, -1), ink2),
             ("FONT", (2, 1), (2, -1), "Hk-B", 10.5), ("TEXTCOLOR", (2, 1), (2, -1), ink),
             ("LINEABOVE", (0, 0), (-1, 0), 1.2, ink), ("LINEBELOW", (0, 0), (-1, 0), .6, ink),
             ("ALIGN", (2, 0), (2, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 5.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
             ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]
    for sec in fmt["sections"]:
        first = True
        for f in sec["fields"]:
            v = ret["totals"].get(f["key"])
            rows.append([sec["title"] if first else "", f["label"], "" if v is None else str(v)])
            if first and len(rows) > 2:
                style.append(("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), .5, colors.HexColor("#D9D3C7")))
            if first:
                style.append(("FONT", (0, len(rows) - 1), (0, len(rows) - 1), "Hk-B", 10))
                style.append(("TEXTCOLOR", (0, len(rows) - 1), (0, len(rows) - 1), ink))
            if v is None:
                style.append(("BACKGROUND", (2, len(rows) - 1), (2, len(rows) - 1), colors.HexColor(media.PAPER2)))
            first = False
    style.append(("LINEBELOW", (0, -1), (-1, -1), 1.2, ink))
    story.append(Table(rows, colWidths=[42 * mm, 100 * mm, 28 * mm], style=TableStyle(style)))
    labels = {f["key"]: f["label"] for f in fmt["fields"]}
    notes = list(ret["flags"]) + [f"{labels[k]}: hard to read" for k in ret["unclear"]]
    if notes:
        warn = ParagraphStyle("w", fontName="Hk", fontSize=10, leading=14, textColor=ink)
        cells = [[Paragraph("<b>Please check before you submit</b>", warn)]] + [[Paragraph("• " + media._fallback_tags(n), warn)] for n in notes]
        box = Table(cells, colWidths=[170 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(media.PAPER)),
            ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(media.GOLD_BRIGHT)),
            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (0, 0), 9),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 9)]))
        story += [Spacer(1, 7 * mm), box]
    story += [Spacer(1, 7 * mm), Paragraph("Shaded totals weren't found on the photographed pages. "
                                            f"Pages read: {ret['pages']}. Names were hidden before reading.", small)]
    if fmt.get("placeholder"):
        story.append(Paragraph("The layout is a placeholder until the district's own monthly return form is added.", small))

    def decorate(canvas, d):
        canvas.saveState()
        media._kente_pdf(canvas, 0, H - 3.2 * mm, W, 3.2 * mm)
        canvas.setFillColor(ink); canvas.setFont("Br", 15); canvas.drawString(20 * mm, H - 15 * mm, "Akwaaba")
        canvas.setFont("Mono", 7.5); canvas.setFillColor(colors.HexColor(media.GOLD))
        canvas.drawString(20 * mm + canvas.stringWidth("Akwaaba", "Br", 15) + 4 * mm, H - 15 * mm, "MONTHLY RETURN · DRAFT")
        canvas.setStrokeColor(ink); canvas.setLineWidth(1); canvas.line(20 * mm, H - 19 * mm, W - 20 * mm, H - 19 * mm)
        canvas.setFont("Mono", 6.8); canvas.setFillColor(muted)
        canvas.drawString(20 * mm, 9.5 * mm, "DRAFT. THE NURSE CHECKS AND SUBMITS IT HERSELF.")
        canvas.drawRightString(W - 20 * mm, 9.5 * mm, f"{d.page:02d}")
        canvas.restoreState()
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()
