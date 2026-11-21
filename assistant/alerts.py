"""Disease alerts: signs of an immediately notifiable disease, turned into a draft
report for the District Disease Control Officer. Rules are code, not the model,
and live in diseases/idsr.json so the district can check them."""

import datetime as dt
import json
import re
from pathlib import Path

RULES = json.loads((Path(__file__).resolve().parent / "diseases" / "idsr.json").read_text(encoding="utf-8"))["diseases"]
_COMPILED = [(d, [re.compile("|".join(g), re.I) for g in d["all"]]) for d in RULES]
_COUNT = re.compile(r"\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|several|many)\s+"
                    r"(people|persons|children|adults|cases|patients|babies|households|families|pupils)\b", re.I)


_AGES = re.compile(r"\b(?:age[sd]?(?: range)?:?\s*|aged\s+|between\s+)?(\d{1,3})\s*(?:-|–|to|and)\s*(\d{1,3})\s*(?:years?|yrs?)?(?: old)?\b", re.I)


def signs_list(text: str) -> str:
    """The signs as a tidy comma-separated list: one per line or phrase, without the
    head count or age range (those have their own lines), and without repeats."""
    parts = re.split(r"[\n;,]+|\s+and\s+(?=[a-z])", text or "")
    out, seen = [], set()
    for p in parts:
        p = _COUNT.sub("", p)
        p = _AGES.sub("", p)
        p = re.sub(r"(?i)\b(age range|ages?|number affected|signs?( reported)?)\s*:?", "", p)
        p = re.sub(r"\s+", " ", p).strip(" .:-")
        p = re.sub(r"(?i)^(in|at|from) (the same |the |one |a |an )?[\w' ]{1,25}? with\s+", "", p)
        p = re.sub(r"(?i)^(with|has|have|having|showing|presenting with)\s+", "", p)
        p = " ".join(w if (w.isupper() and len(w) > 1) else w.lower() for w in p.split())  # keep TB, HIV
        if len(p) > 1 and p not in seen:
            seen.add(p)
            out.append(p)
    return ", ".join(out)[:220]


def match(text: str) -> list[dict]:
    """All rules whose every group matches. The cluster rule only counts when nothing more specific did."""
    if not text:
        return []
    hits = [d for d, groups in _COMPILED if all(g.search(text) for g in groups)]
    specific = [d for d in hits if d["key"] != "cluster"]
    return specific or hits


def draft(hits: list[dict], text: str, facility: str = "", when: dt.date | None = None) -> str:
    when = when or dt.date.today()
    names = " and ".join(h["name"] for h in hits)
    count = _COUNT.search(text)
    ages = _AGES.search(text)
    lines = [f"*\U0001F6A8 Possible {names}* · report *immediately*", ""]
    lines += [f"• {h['action']}" for h in hits]
    lines += ["", "*Draft alert for the District Disease Control Officer*",
              f"Date: {when.day} {when.strftime('%B %Y')}",
              f"From: {facility or 'CHPS compound (add name)'}",
              f"Suspected: {names}",
              f"Number affected: {count.group(0) if count else 'one (add if more)'}",
              *([f"Ages: {ages.group(1)} to {ages.group(2)}"] if ages else []),
              "Signs reported: " + (signs_list(text) or "(add)"),
              "Action taken: (add)", "",
              "_Check every line before sending. Keep names in your register, not in this chat. "
              "This is a prompt to report, not a diagnosis._"]
    return "\n".join(lines)
