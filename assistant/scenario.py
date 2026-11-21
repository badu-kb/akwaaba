"""Exercise Clear Water: one invented week in a fictional district, played through
the real code. Each step is a message from the field or a decision at the
district; after each one the desk is recomputed by surveillance.py and
district.py, so what the demonstration shows is what the software does.

Everything here is invented. No real place, person or event is described.
"""

import datetime as dt
import html
import re

import alerts
import dhis2_export
import district
import prompts
import safety
import surveillance as sv

MONDAY = dt.date(2026, 11, 9)
DISTRICT = "Sample District"
ZONES = 6


def _ts(day: int, hour: int, minute: int = 0) -> int:
    d = MONDAY + dt.timedelta(days=day)
    return int(dt.datetime(d.year, d.month, d.day, hour, minute).timestamp())


# Earlier weeks, so the eight-week view has something in it.
BACKGROUND = [
    {"id": 101, "ts": _ts(-19, 10), "disease": "measles", "count": 1, "zone": "Zone B", "status": "verified", "reporter": "Volunteer, Zone B"},
    {"id": 102, "ts": _ts(-11, 14), "disease": "measles", "count": 1, "zone": "Zone C", "status": "verified", "reporter": "Nurse, Zone C"},
    {"id": 103, "ts": _ts(-6, 9), "disease": "measles", "count": 1, "zone": "Zone D", "status": "verified", "reporter": "Volunteer, Zone D"},
    {"id": 104, "ts": _ts(-4, 11), "disease": "measles", "count": 2, "zone": "Zone B", "status": "verified", "reporter": "Volunteer, Zone B"},
]

# (day, hour, minute, kind, payload). kind: note | report | verify | discard | bulletin
STEPS = [
    (0, 7, 30, "note", {"title": "Monday morning", "say": "The week starts quietly. Last week's signals have all been verified, and nothing new has come in."}),
    (0, 8, 40, "report", {"id": 1, "who": "Volunteer, Zone E", "zone": "Zone E", "title": "A volunteer's message",
                          "text": "3 people in one compound with watery diarrhoea and vomiting since yesterday. They all use the same well.",
                          "say": "A volunteer on a home visit opens the disease alert task and describes what she has seen. Akwaaba recognises signs it must report at once and drafts the alert. A signal appears on the district desk."}),
    (0, 15, 10, "verify", {"id": 1, "title": "The district verifies it",
                           "say": "The Disease Control Officer phones the compound, confirms the report and marks the signal verified. One suspected cholera case is the alert threshold, so the desk now shows it as an alert to investigate."}),
    (1, 9, 10, "report", {"id": 2, "who": "Nurse, Zone C", "zone": "Zone C", "title": "A second zone",
                          "text": "2 children with watery diarrhoea, one very weak and not drinking.",
                          "say": "A nurse in another zone reports two sick children. 'Not drinking' is a danger sign, so the emergency card goes out before anything else. Then the alert is drafted and a second signal reaches the desk."}),
    (1, 16, 30, "verify", {"id": 2, "title": "Two zones, one condition",
                           "say": "The second signal is verified. The desk now points out that the same condition has come from two zones this week, and asks whether the cases are linked."}),
    (2, 10, 20, "report", {"id": 3, "who": "Volunteer, Zone B", "zone": "Zone B", "title": "The tool gets one wrong",
                           "text": "We gave a health talk on cholera and watery diarrhoea at the durbar today",
                           "say": "A volunteer opens the alert task by mistake and types about a health talk. The rules match the word 'cholera' and log a signal that isn't one. This is a real weakness of matching on words, and the desk is built for a person to catch it."}),
    (2, 14, 0, "discard", {"id": 3, "title": "A person catches it",
                           "say": "The officer calls Zone B, learns it was a health talk and discards the signal. It stays on the line list, struck through, and stops counting."}),
    (3, 11, 5, "report", {"id": 4, "who": "Nurse, Zone A", "zone": "Zone A", "title": "Something different",
                          "text": "7 children with fever and a rash all over, red eyes and cough in the same school",
                          "say": "On Thursday a different problem arrives: seven children at one school with fever and a rash. Akwaaba drafts a measles alert."}),
    (3, 15, 40, "verify", {"id": 4, "title": "A threshold is crossed",
                           "say": "Once verified, seven cases from one facility in a week passes the guideline's line for a suspected measles outbreak, and the desk puts it at the top."}),
    (4, 16, 0, "bulletin", {"title": "Friday: the bulletin and the file",
                            "say": "At the end of the week the bulletin is already drafted from the counts, with a reminder that three zones sent nothing. The same numbers are ready as a file for DHIS2, the system Ghana's DHIMS2 runs on. A person checks both and sends them."}),
]

MAPPING = {"dataSet": "AkwIDSRwk01", "orgUnit": "SampleDist1",
           "dataElements": {"cholera": "AkwCholera1", "measles": "AkwMeasles1", "meningitis": "AkwMeningi1", "afp": "AkwAFPcase1",
                            "yellow_fever": "AkwYellowF1", "neonatal_tetanus": "AkwNNTcase1"}}


def _wa(text: str) -> str:
    """WhatsApp formatting to HTML, for showing a reply exactly as it would read."""
    t = html.escape(text)
    t = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w/])_([^_\n]+)_(?!\w)", r"<i>\1</i>", t)
    return t.replace("\n", "<br>")


def build() -> list[dict]:
    """Every step of the week, with the desk as the real code renders it at that moment."""
    signals: list[dict] = [dict(s) for s in BACKGROUND]
    out = []
    for day, hour, minute, kind, p in STEPS:
        now = _ts(day, hour, minute)
        when = dt.datetime.fromtimestamp(now)
        chat = None
        if kind == "report":
            hits = alerts.match(p["text"])
            assert hits, "the scenario depends on the real rules matching: " + p["text"]
            replies = []
            if safety.looks_like_emergency(p["text"]):
                replies.append({"kind": "emergency", "html": _wa(prompts.EMERGENCY)})
            replies.append({"kind": "alert", "html": _wa(alerts.draft(hits, p["text"], p["zone"] + " CHPS", when.date()))})
            for h in hits:
                signals.insert(0, {"id": p["id"], "ts": now, "disease": h["key"], "count": sv.head_count(p["text"]),
                                   "zone": p["zone"], "status": "new", "reporter": p["who"]})
            chat = {"who": p["who"], "text": html.escape(p["text"]), "replies": replies}
        elif kind in ("verify", "discard"):
            for s in signals:
                if s["id"] == p["id"]:
                    s["status"] = "verified" if kind == "verify" else "discarded"
        signals.sort(key=lambda s: -s["ts"])
        desk = district.parts([dict(s) for s in signals], DISTRICT, when.date(), demo=True, zones_expected=ZONES, now=now)
        step = {"when": f"{when.strftime('%A')} {when.day} {when.strftime('%B')}, {when.strftime('%H:%M')}",
                "kind": kind, "title": p["title"], "say": p["say"], "chat": chat, "desk": desk, "dhis2": None}
        if kind == "bulletin":
            payload = dhis2_export.payload([dict(s) for s in signals], MAPPING, when.date())
            step["dhis2"] = html.escape(__import__("json").dumps(payload, indent=2))
        out.append(step)
    return out


if __name__ == "__main__":
    for s in build():
        print(s["when"], "|", s["title"])
