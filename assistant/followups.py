"""Follow-ups: who is due for a vaccine, an antenatal contact or a postnatal check.

Research behind it (see the build guide): in a 2016 survey, 76% of Volta Region
facilities had no written defaulter list, only 1 to 2% of health workers listed
defaulter tracing as a duty, and 14% of Volta children who got the first measles
dose missed the second, while 78% of caregivers said they'd welcome reminders.

The nurse adds a person by REGISTER OR CARD NUMBER and one date. No names:
words that aren't numbers or register words are dropped. Code (not the model)
works out what is due from the schedules in schedules/ghana.json.
"""

import datetime as dt
import json
import re
from pathlib import Path

SCHEDULES = json.loads((Path(__file__).resolve().parent / "schedules" / "ghana.json").read_text(encoding="utf-8"))
KINDS = ("child", "pregnancy", "postnatal")
LOOKAHEAD_DAYS = 7
LABEL_WORDS = {"card", "reg", "register", "no", "anc", "cwc", "pnc", "opd", "house", "hse", "twin", "a", "b",
               "baby", "child", "mother", "mum", "#", "id", "folder", "h/no", "hno"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


class FollowupError(ValueError):
    """Something the nurse can fix; the message says how."""


def today() -> dt.date:
    return dt.date.today()


# A number token may carry a register prefix (ANC34, CWC/045) but nothing else, so
# "Ama123" is dropped rather than kept just because it contains a digit.
_NUM_TOKEN = re.compile(r"^(?:(?:anc|cwc|pnc|opd|reg|hse|h|no|card)[-/.#]?)?\d[\d/-]{0,9}[a-z]?$", re.I)


def clean_label(text: str) -> str:
    """Keeps register words and numbers only, so names never get stored."""
    keep = []
    for tok in re.split(r"[\s,;:]+", text):
        t = tok.strip(".()")
        if not t:
            continue
        if _NUM_TOKEN.match(t) or t.lower() in LABEL_WORDS:
            keep.append(t if re.search(r"\d", t) else t.upper() if t.lower() in {"anc", "cwc", "pnc", "opd"} else t.capitalize())
    label = " ".join(keep)[:24].strip()
    if not re.search(r"\d", label):
        raise FollowupError("no_number")
    return label


_ID = re.compile(r"\b(house|hse|h/no|card|reg(?:ister)?|anc|pnc|cwc|opd|folder|no\.?|#)\s*[-:#]?\s*(\d{1,6}[a-z]?)\b", re.I)


def first_label(text: str) -> str:
    """'House 14, baby 5 days old ...' -> 'House 14'. Falls back to the first phrase's numbers."""
    m = _ID.search(text)
    if m:
        word = m.group(1).lower().rstrip(".")
        word = word.upper() if word in {"anc", "pnc", "cwc", "opd"} else "No." if word in {"no", "#"} else word.capitalize()
        return f"{word} {m.group(2)}"
    return clean_label(re.split(r"[,;\n]", text, 1)[0])


def parse_date(text: str, base: dt.date | None = None) -> dt.date | None:
    base = base or today()
    t = text.lower()
    if "today" in t:
        return base
    if "yesterday" in t:
        return base - dt.timedelta(days=1)
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        y, mo, d = map(int, m.groups())
        return _safe(y, mo, d)
    m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b", t)
    if m:
        d, mo, y = map(int, m.groups())
        return _safe(y + 2000 if y < 100 else y, mo, d)
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{4})\b", t)
    if m and m.group(2)[:3] in MONTHS:
        return _safe(int(m.group(3)), MONTHS[m.group(2)[:3]], int(m.group(1)))
    return None


def _safe(y, mo, d):
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def date_part_removed(text: str) -> str:
    t = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b", " ", text)
    t = re.sub(r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}\b", " ", t)
    t = re.sub(r"\b(edd|due|born|dob|delivered|delivery|on|today|yesterday|weeks?|wks?|pregnant|gestation)\b", " ", t, flags=re.I)
    t = re.sub(r"\b\d{1,2}\s*(weeks?|wks?)\b", " ", t, flags=re.I)
    return t


def parse_entry(kind: str, text: str, base: dt.date | None = None) -> tuple[str, dt.date]:
    """Returns (label, anchor date). For pregnancy the anchor is the start of pregnancy (LMP)."""
    base = base or today()
    if kind == "pregnancy":
        weeks = re.search(r"\b(\d{1,2})\s*(?:weeks?|wks?)\b", text, re.I)
        stripped = re.sub(r"\b\d{1,2}\s*(?:weeks?|wks?)\b", " ", text, flags=re.I)
        label = clean_label(date_part_removed(stripped))
        if weeks:
            w = int(weeks.group(1))
            if not 4 <= w <= 42:
                raise FollowupError("bad_weeks")
            return label, base - dt.timedelta(weeks=w)
        edd = parse_date(text, base)
        if not edd:
            raise FollowupError("no_date")
        if not base - dt.timedelta(days=21) <= edd <= base + dt.timedelta(days=280):
            raise FollowupError("bad_date")
        return label, edd - dt.timedelta(days=280)
    when = parse_date(text, base)
    if not when:
        raise FollowupError("no_date")
    label = clean_label(date_part_removed(text))
    oldest = 760 if kind == "child" else 60
    if when > base or when < base - dt.timedelta(days=oldest):
        raise FollowupError("bad_date")
    return label, when


def past_visits(kind: str, anchor: dt.date, base: dt.date | None = None) -> list[str]:
    """Visits that were due before today. Nothing is marked done on its own: the nurse
    confirms them against the record book, and anything unconfirmed stays as catch-up."""
    base = base or today()
    return [v["key"] for v in SCHEDULES[kind]["visits"] if anchor + dt.timedelta(days=v["days"]) < base]


def visits(fu: dict) -> list[dict]:
    """Each visit's date: the schedule date, pushed later when an earlier dose in the same
    series was given late, or is still outstanding, and the schedule sets a minimum gap
    ("after" in schedules/ghana.json)."""
    anchor = dt.date.fromisoformat(fu["anchor"])
    done = set(fu.get("done") or [])
    given = fu.get("given") or {}
    out, by_key = [], {}
    for v in SCHEDULES[fu["kind"]]["visits"]:
        when = anchor + dt.timedelta(days=v["days"])
        after = v.get("after")
        if after:
            prev = given.get(after["key"])
            if prev and prev != "card":
                when = max(when, dt.date.fromisoformat(prev) + dt.timedelta(days=after["gap"]))
            elif after["key"] in by_key and not by_key[after["key"]]["done"]:
                when = max(when, by_key[after["key"]]["date"] + dt.timedelta(days=after["gap"]))
        item = {**v, "date": when, "done": v["key"] in done, "given": given.get(v["key"])}
        out.append(item)
        by_key[v["key"]] = item
    return out


def next_visit(fu: dict, base: dt.date | None = None) -> dict | None:
    """The earliest visit not yet marked done. Past antenatal contacts more than
    2 weeks old are skipped (the moment has passed); missed vaccines stay (catch-up)."""
    base = base or today()
    for v in visits(fu):
        if v["done"]:
            continue
        if fu["kind"] != "child" and v["date"] < base - dt.timedelta(days=14):
            continue
        return v
    return None


def is_finished(fu: dict, base: dt.date | None = None) -> bool:
    base = base or today()
    anchor = dt.date.fromisoformat(fu["anchor"])
    return next_visit(fu, base) is None or (base - anchor).days > SCHEDULES[fu["kind"]]["close_after_days"]


def due_list(fus: list[dict], base: dt.date | None = None) -> dict:
    """{'overdue': [(fu, visit)], 'soon': [(fu, visit)]}, soonest first."""
    base = base or today()
    out = {"overdue": [], "soon": []}
    for fu in fus:
        if is_finished(fu, base):
            continue
        v = next_visit(fu, base)
        if v["date"] < base:
            out["overdue"].append((fu, v))
        elif v["date"] <= base + dt.timedelta(days=LOOKAHEAD_DAYS):
            out["soon"].append((fu, v))
    for k in out:
        out[k].sort(key=lambda p: p[1]["date"])
    return out


def day(d: dt.date, base: dt.date | None = None) -> str:
    base = base or today()
    return f"{d.day} {d.strftime('%b')}" + (f" {d.year}" if abs((d - base).days) > 300 else "")


def due_text(due: dict, base: dt.date | None = None) -> str:
    base = base or today()
    lines = [f"*\U0001F4C5 Follow-ups due* · week of {day(base)}"]
    if due["overdue"]:
        lines += ["", "\U0001F534 *Overdue*"] + [
            f"• {fu['label']} · {v['what']} · was due {day(v['date'])}" for fu, v in due["overdue"][:15]]
    if due["soon"]:
        lines += ["", "\U0001F7E1 *Due in the next 7 days*"] + [
            f"• {fu['label']} · {v['what']} · {day(v['date'])}" for fu, v in due["soon"][:15]]
    n = len(due["overdue"]) + len(due["soon"])
    if n > 30:
        lines.append(f"_…and {n - 30} more_")
    return "\n".join(lines)


def schedule_text(fu: dict, base: dt.date | None = None) -> str:
    base = base or today()
    s = SCHEDULES[fu["kind"]]
    nv = next_visit(fu, base)
    lines = [f"*{fu['label']}* · {s['title']}"]
    for v in visits(fu):
        mark = "✅" if v["done"] else ("➡️" if nv and v["key"] == nv["key"] else "▫️")
        lines.append(f"{mark} {day(v['date'])} · {v['what']}")
    return "\n".join(lines)
