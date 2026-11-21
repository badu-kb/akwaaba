"""The district surveillance desk: the receiving end of the disease alerts health
workers raise in the field.

It keeps a line list of signals (a suspected condition, how many people, which
zone, which week: never a name), applies the district's thresholds in ordinary
code, and drafts the weekly bulletin for the District Disease Control Officer to
check and send. Nothing here diagnoses anything, and nothing is sent on anyone's
behalf. The thresholds live in diseases/thresholds.json so the district can
correct them.

A signal goes through the same three states an officer would use:
new -> verified (someone has checked it is real) or discarded (it was not).
"""

import datetime as dt
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

import alerts

_HERE = Path(__file__).resolve().parent
THRESHOLDS = json.loads((_HERE / "diseases" / "thresholds.json").read_text(encoding="utf-8"))
NAMES = {d["key"]: d["name"] for d in alerts.RULES}
STATES = ("new", "verified", "discarded")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "several": 3, "many": 5}


def head_count(text: str) -> int:
    """How many people the report mentions: the stated number, or 1. 'Several' and 'many'
    become cautious minimums (3 and 5) so they are never lost."""
    m = alerts._COUNT.search(text or "")
    if not m:
        return 1
    n = m.group(1).lower()
    return min(999, int(n)) if n.isdigit() else _WORDS.get(n, 1)


def zone_of(profile: dict | None) -> str:
    """Where the reporter works (their facility or town), never where a patient lives."""
    p = profile or {}
    z = re.sub(r"\s+", " ", str(p.get("facility") or p.get("town") or "")).strip()
    return z[:60] or "Zone not set"


def epi_week(day: dt.date) -> tuple[int, int]:
    """(year, week): weeks run Monday to Sunday, as in IDSR weekly reporting."""
    y, w, _ = day.isocalendar()
    return y, w


def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def _day(sig: dict) -> dt.date:
    return dt.date.fromtimestamp(sig["ts"])


def flags(signals: list[dict], today: dt.date | None = None, now: int | None = None) -> list[dict]:
    """Everything the officer should look at, most urgent first. Discarded signals never count.

    verify : a signal nobody has checked yet (overdue once it is older than the guideline's 48 hours)
    action : an epidemic or outbreak threshold has been reached by verified signals
    alert  : an alert threshold has been reached by signals that have been verified
    watch  : Akwaaba's own prompt, not a guideline threshold
    """
    today = today or dt.date.today()
    T = THRESHOLDS
    single = T["alert_single_case"]
    live = [s for s in signals if s.get("status") != "discarded" and _day(s) <= today]
    start = week_start(today)
    this_week = [s for s in live if start <= _day(s)]
    out: list[dict] = []

    def name(d):
        return NAMES.get(d, d)

    # 1. Anything unverified from the last two weeks, with the clock running
    end_of_today = int(dt.datetime.combine(today, dt.time(23, 59, 59)).timestamp())
    clock = min(now, end_of_today) if now else end_of_today
    for s in live:
        if s.get("status") == "new" and _day(s) >= today - dt.timedelta(days=13):
            hours = max(0, (clock - s["ts"]) // 3600)
            late = hours > T["verify_hours"]
            out.append({"level": "verify", "disease": s["disease"], "zone": s["zone"], "cases": s["count"], "overdue": late,
                        "say": (f"Overdue: reported {hours // 24} days ago and still not verified. " if late else
                                f"Suspected {name(s['disease'])} reported from {s['zone']}. Verify it and tell the district. ")
                               + T["verify_say"]})

    # 2. Thresholds, counted over this week's signals by zone
    by_zone: dict = defaultdict(int)
    verified_zone: dict = defaultdict(int)
    zones_for: dict = defaultdict(set)
    for s in this_week:
        by_zone[(s["disease"], s["zone"])] += s["count"]
        zones_for[s["disease"]].add(s["zone"])
        if s.get("status") == "verified":
            verified_zone[(s["disease"], s["zone"])] += s["count"]
    best: dict = {}
    for rule in T["zone_week"]:                     # thresholds count verified signals only
        for (disease, zone), total in verified_zone.items():
            if disease == rule["disease"] and total >= rule["cases"]:
                prev = best.get((disease, zone))
                if not prev or rule["cases"] > prev[0]:
                    best[(disease, zone)] = (rule["cases"], rule, total)
    for (disease, zone), (_, rule, total) in sorted(best.items()):
        out.append({"level": rule["level"], "disease": disease, "zone": zone, "cases": total, "say": rule["say"]})
    for (disease, zone), total in sorted(verified_zone.items()):
        if (disease, zone) in best:
            continue
        if disease in single["sourced"]:
            out.append({"level": "alert", "disease": disease, "zone": zone, "cases": total, "say": single["sourced_say"]})
        elif disease == "cluster":
            out.append({"level": "alert", "disease": disease, "zone": zone, "cases": total, "say": single["cluster_say"]})
        elif disease in single["unsourced"]:
            out.append({"level": "alert", "disease": disease, "zone": zone, "cases": total, "say": single["unsourced_say"]})

    # 3. Akwaaba's own prompt: the same condition in more than one zone
    spread = T["own"]["spread"]
    for disease, zones in sorted(zones_for.items()):
        if len(zones) >= spread["zones"]:
            out.append({"level": "watch", "disease": disease, "zone": ", ".join(sorted(zones)),
                        "cases": sum(by_zone[(disease, z)] for z in zones), "say": spread["say"]})

    order = {"action": 0, "verify": 1, "alert": 2, "watch": 3}
    return sorted(out, key=lambda f: (order[f["level"]], not f.get("overdue", False), f["disease"], f["zone"]))


def weekly(signals: list[dict], today: dt.date | None = None, weeks: int = 8) -> dict:
    """Suspected cases per disease for each of the last `weeks` weeks (oldest first)."""
    today = today or dt.date.today()
    starts = [week_start(today) - dt.timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    table: dict = {d: [0] * weeks for d in sorted({s["disease"] for s in signals if s.get("status") != "discarded"})}
    for s in signals:
        if s.get("status") == "discarded":
            continue
        ws = week_start(_day(s))
        if ws in starts:
            table[s["disease"]][starts.index(ws)] += s["count"]
    return {"weeks": [epi_week(d) for d in starts], "starts": starts, "cases": {d: v for d, v in table.items() if any(v)}}


def bulletin(signals: list[dict], district: str = "", today: dt.date | None = None, zones_expected: int | None = None,
             last_complete: bool = False) -> str:
    """The weekly bulletin as plain text, built entirely from counts. A draft for the
    officer to check: it reports what was signalled, not what was confirmed.
    With last_complete, it covers the most recent full Monday-to-Sunday week."""
    today = today or dt.date.today()
    if last_complete:
        end = week_start(today) - dt.timedelta(days=1)
        start, label = week_start(end), ""
    else:
        start, end = week_start(today), today
        label = "" if today.weekday() == 6 else " (week to date)"
    year, week = epi_week(start)
    live = [s for s in signals if s.get("status") != "discarded" and start <= _day(s) <= end]
    last = [s for s in signals if s.get("status") != "discarded"
            and start - dt.timedelta(days=7) <= _day(s) < start]
    now_c, last_c = Counter(), Counter()
    for s in live:
        now_c[s["disease"]] += s["count"]
    for s in last:
        last_c[s["disease"]] += s["count"]
    zones = sorted({s["zone"] for s in live})
    due = start + dt.timedelta(days=7 + 3)            # the Thursday after the week ends
    lines = [f"WEEKLY SURVEILLANCE BULLETIN (DRAFT) · Week {week}, {year}{label}",
             f"{district or 'District (add name)'} · {start.day} {start.strftime('%B')} to {end.day} {end.strftime('%B %Y')}",
             f"Due at the region by Thursday {due.day} {due.strftime('%B')}.", ""]
    heard = f"{len(zones)} of {zones_expected}" if zones_expected else str(len(zones))
    lines += [f"Signals received: {len(live)} from {heard} reporting zones.",
              f"Verified so far: {sum(1 for s in live if s.get('status') == 'verified')}. "
              f"Awaiting verification: {sum(1 for s in live if s.get('status') == 'new')}."]
    if zones_expected and len(zones) < zones_expected:
        lines.append(f"{zones_expected - len(zones)} zones sent nothing. Confirm with each whether that is a true zero report.")
    lines.append("")
    if now_c:
        lines.append("SUSPECTED CASES THIS WEEK (last week in brackets)")
        for d, n in sorted(now_c.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- {NAMES.get(d, d).capitalize()}: {n} ({last_c.get(d, 0)})")
    else:
        lines.append("No signals were received this week. Please confirm whether this is a true zero report.")
    fl = [f for f in flags(signals, end) if f["level"] != "verify"]
    words = {"action": "EPIDEMIC OR OUTBREAK THRESHOLD", "alert": "ALERT THRESHOLD", "watch": "WATCH"}
    lines += ["", "THRESHOLDS REACHED" if fl else "No thresholds reached this week."]
    for f in fl:
        lines.append(f"- {words[f['level']]}: {NAMES.get(f['disease'], f['disease'])}, {f['zone']} ({f['cases']}). {f['say']}")
    lines += ["", "ACTIONS THIS WEEK: (add)", "",
              "Drafted by Akwaaba from community signals. These are suspected cases reported by health workers, "
              "not confirmed diagnoses. Check every line against the register before sending."]
    return "\n".join(lines)


def sample(today: dt.date | None = None) -> list[dict]:
    """Invented signals for a fictional district, for demonstrations and training only."""
    today = today or dt.date.today()
    rng = random.Random(2026)
    zones = [f"Zone {c} (sample)" for c in "ABCDEF"]
    out, n = [], 0

    def add(days_ago, disease, count, zone, status="verified"):
        nonlocal n
        n += 1
        day = today - dt.timedelta(days=days_ago)
        ts = int(dt.datetime(day.year, day.month, day.day, 9 + n % 8).timestamp())
        out.append({"id": n, "ts": ts, "disease": disease, "count": count, "zone": zone, "status": status,
                    "reporter": f"Volunteer {1 + n % 9}"})

    for wk in range(7, 0, -1):                       # a quiet background over the earlier weeks
        for _ in range(rng.randint(0, 2)):
            add(wk * 7 + rng.randint(0, 6), rng.choice(["measles", "cluster", "measles"]), 1, rng.choice(zones),
                rng.choice(["verified", "verified", "discarded"]))
    monday = today.weekday()
    add(min(monday, 9) + 9, "measles", 1, zones[1])
    add(min(monday, 9) + 6, "measles", 2, zones[1])   # measles building in one zone, then a second
    add(max(monday - 1, 0), "measles", 2, zones[1], "verified")
    add(max(monday - 2, 0), "measles", 1, zones[2], "new")
    add(0, "cholera", 3, zones[4], "new")             # a new cluster of watery diarrhoea today
    add(max(monday - 1, 0), "cluster", 4, zones[4], "new")
    return sorted(out, key=lambda s: -s["ts"])
