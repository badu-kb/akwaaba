"""Long-term memory, written in the background after each reply.

Two kinds:
  1. Profile: durable facts a person shares about their own work
     ("I'm the CHO at a CHPS compound near Ho, we serve about 3,000 people").
  2. Rolling summary: when a conversation gets long, the oldest exchanges are
     folded into a short summary, so context carries forward without the
     prompt growing forever.

Uses a small, fast model, so memory costs a fraction of a reply.
Privacy rules are in the instructions AND in code. Code keeps only a few work
fields, checks each one against what that field is allowed to hold, and removes
phone, ID and card numbers and name-like phrases from the summary. The model's
instructions are the first line of defence; code is the backstop, not a guarantee.
"""

import json
import logging
import os
import re

log = logging.getLogger("akwaaba")

MEMORY_MODEL = os.getenv("MEMORY_MODEL", "claude-haiku-5-5")
RECENT_MESSAGES = int(os.getenv("RECENT_MESSAGES", "24"))   # kept word for word (12 exchanges)
FOLD_COUNT = 8                                              # oldest messages folded into the summary at a time

PROFILE_KEYS = {
    "health": ["role", "facility", "town_or_region", "focus_areas", "community_language"],
}

PROFILE_INSTRUCTIONS = """You maintain a tiny profile of a {who} in Ghana who uses a WhatsApp assistant.
Update the profile ONLY with durable facts this person states about their OWN work, for example their role,
facility type, town or region, the communities and languages they serve, focus areas, preferences.

STRICT RULES
- Never record names of patients, family members or any other person, phone numbers, or ID numbers.
- Never record health conditions, diagnoses or symptoms of anyone.
- Never record one-off details of a single request (e.g. today's talk topic).
- Keep each value under 60 characters. Allowed keys only: {keys}.
- If nothing new and durable was said, return the profile unchanged.

Current profile (JSON): {profile}

The person just wrote: \"\"\"{message}\"\"\"

Return ONLY the full updated profile as a JSON object."""

SUMMARY_INSTRUCTIONS = """Fold these older WhatsApp messages between a {who} in Ghana and their assistant
into the running summary. Keep what helps future answers: topics covered, what worked, what they asked for
again, preferences, unfinished threads. Drop greetings and detail that won't matter later.
Never include names of patients or other people, or anyone's health details.
Maximum 110 words, plain sentences.

Running summary so far: {summary}

Older messages:
{turns}

Return only the new running summary."""

_PII = re.compile(r"(\+?\d[\d\s-]{7,}\d)|(@\w)|(\b\d{6,}\b)")
# Words that mean a value is about someone's health or identity, not the person's own work.
_PERSONAL = re.compile(r"\b(hiv|aids|positive|negative|diagnos|pregnan|miscarr|sick|ill\b|symptom|disease|patient|"
                       r"named|called|husband|wife|son|daughter|mother|father|child|baby|mr|mrs|madam|auntie|maame|"
                       r"nana|agya|papa|card|reg(ister)?\s*\d|anc\s*\d|house\s*\d)", re.I)
_PLACE = re.compile(r"^[A-Za-zɛɔŋƒʋɖƐƆŊƑƲƉ' .,-]{2,40}$")
_LANGS = {"twi", "asante twi", "akuapem twi", "fante", "fanti", "ewe", "ga", "dangme", "dagbani", "hausa", "english",
          "nzema", "dagaare", "gonja", "kasem", "frafra", "guan", "sisaala", "likpakpaanl", "konkomba", "akan", "pidgin"}
_FACILITY_WORDS = re.compile(r"\b(chps|compound|health (centre|center|post)|clinic|hospital|polyclinic|district|"
                             r"sub-?district|maternity|community)\b", re.I)


def _valid(key: str, v: str) -> str | None:
    """Each field may only hold what it's for. Anything else is dropped."""
    v = re.sub(r"\s+", " ", v).strip()
    if not v or _PII.search(v) or re.search(r"\d{3,}", v):
        return None
    if key == "community_language":
        langs = [x.strip() for x in v.split(",") if x.strip()]
        ok = [x for x in langs if x.lower() in _LANGS]
        return ", ".join(ok)[:60] or None
    if _PERSONAL.search(v):
        return None
    if key == "town_or_region":
        return v[:40] if _PLACE.match(v) and not re.search(r"'s\b", v) else None
    if key == "facility":
        return v[:60] if _FACILITY_WORDS.search(v) else None
    if key == "focus_areas":
        items = [x.strip() for x in v.split(",") if x.strip()][:4]
        return ", ".join(x[:25] for x in items) or None
    return v[:40] if "," not in v else None  # role: one short job title


_NAMEISH = re.compile(r"\b(?:named|called|Mr\.?|Mrs\.?|Madam|Auntie|Maame|Nana|Agya|Papa|Sister|Brother)\s+[A-Z][\w'-]+", re.U)
_IDS = re.compile(r"\b(card|reg(?:ister)?|anc|pnc|cwc|opd|house|hse|folder)\s*(no\.?)?\s*[-#:]?\s*\d+\w*", re.I)


def clean_summary(text: str) -> str:
    """Remove numbers that identify people, and name-like phrases, from the rolling summary."""
    text = _PII.sub("[number]", text or "")
    text = _IDS.sub("[record]", text)
    text = _NAMEISH.sub("[someone]", text)
    return text.strip()


def _who(mode: str) -> str:
    return "community health worker"


def _text(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


def worth_checking(message: str) -> bool:
    """Skip the profile call for short or command-like messages."""
    m = message.strip()
    return len(m) >= 25 and not m.startswith("[")


def update_profile(claude, mode: str, profile: dict, message: str) -> dict:
    keys = PROFILE_KEYS["health"]
    resp = claude.messages.create(
        model=MEMORY_MODEL, max_tokens=300,
        messages=[{"role": "user", "content": PROFILE_INSTRUCTIONS.format(
            who=_who(mode), keys=", ".join(keys),
            profile=json.dumps(profile, ensure_ascii=False), message=message[:1500])}],
    )
    raw = _text(resp)
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return profile
    try:
        new = json.loads(match.group(0))
    except json.JSONDecodeError:
        return profile
    clean = {}
    for k in keys:  # enforce the allow-list and size limits in code, not just in the prompt
        v = new.get(k, profile.get(k))
        if v in (None, "", [], {}):
            continue
        v = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
        v = _valid(k, v)
        if v:
            clean[k] = v
    return clean


def fold_summary(claude, mode: str, summary: str, turns: list[dict]) -> str:
    lines = []
    for t in turns:
        who = "Person" if t["role"] == "user" else "Assistant"
        content = t["content"] if isinstance(t["content"], str) else "[attachment]"
        lines.append(f"{who}: {content[:600]}")
    resp = claude.messages.create(
        model=MEMORY_MODEL, max_tokens=300,
        messages=[{"role": "user", "content": SUMMARY_INSTRUCTIONS.format(
            who=_who(mode), summary=summary or "(none)", turns="\n".join(lines))}],
    )
    return clean_summary(_text(resp))[:900] or summary


def describe(profile: dict) -> str:
    """Human-readable list for the MY DATA screen."""
    return "\n".join(f"• {k.replace('_', ' ').capitalize()}: {v}" for k, v in profile.items())
