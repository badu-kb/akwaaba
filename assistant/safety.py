"""Emergency fast-path that does not depend on the model.

If a message describes an obvious emergency, the person gets the
"go to a facility now / call 112 or 193" card instantly, before the model's
fuller answer. Deliberately simple and over-inclusive: a false alarm costs a
line of text, a miss could cost a life.

Two kinds of rule:
  * signs that are an emergency on their own (convulsions, not breathing, ...)
  * signs that are an emergency for a pregnant or newly delivered woman, or a
    newborn (bleeding, severe headache, fever in a newborn, ...)

Measured against evals/red_flags.json (WHO IMCI general danger signs, maternal
and newborn danger signs, common community emergencies): every urgent case
must trigger, and tests/test_app.py enforces that.

English phrases are trusted and tested. Twi and Ewe phrases live in
safety_local.json as DRAFTS awaiting review by native-speaking clinicians. Until
that review they only run in shadow mode: local_hit() says whether a draft phrase
matched, callers count it in the usage log, and no card is sent. Setting
SAFETY_LOCAL=on makes them send the card as well.
"""

import json
import os
import re
import unicodedata
from pathlib import Path

_ALWAYS = [
    # child general danger signs (IMCI) and other life threats
    r"\bconvuls", r"\bseizure", r"\b(had|having) a fit\b", r"\bfitting\b", r"\bunconscious", r"\bwon'?t wake\b",
    r"\bcan'?t wake\b", r"\bcannot wake\b", r"\bhard to wake\b", r"\blethargic\b", r"\bunusually sleepy\b",
    r"\bvomit(s|ing)? everything\b", r"\bvomit(s|ing)? blood\b",
    r"\bnot breathing\b", r"\bcan'?t breathe\b", r"\bcannot breathe\b", r"\bstruggling to breathe\b",
    r"\b(difficult|difficulty|trouble) breathing\b", r"\bbreathless", r"\bbreathing (very )?fast\b",
    r"\bchest (is )?(pulling|drawing) in\b", r"\bchest in-?drawing\b", r"\bgrunting\b",
    r"\b(lips|tongue) (are |is )?(blue|purple)\b", r"\bturn(ed|ing)? blue\b",
    r"\bheavy bleeding\b", r"\bbleeding heavily\b", r"\blot of blood\b", r"\bwon'?t stop bleeding\b",
    r"\bbleed(ing|s)? (a lot|so much|too much|badly|profusely|non-?stop)\b", r"\blos(ing|t) (a lot of |so much |too much )?blood\b",
    r"\bsoaked (in|with) blood\b", r"\bblood everywhere\b",
    r"\bsnake ?bite", r"\bbitten by a (snake|dog)\b", r"\bdog bite", r"\brabi",
    r"\bpoison", r"\bweedicide", r"\bherbicide", r"\bpesticide", r"\binsecticide",
    r"\b(swallowed|drank|drunk|ate) (some |the )?(bleach|kerosene|paraffin|petrol|medicine|pills|tablets|acid|weedicide)",
    r"\b(can'?t|cannot|can not|won'?t|will not|refus(es|ing) to) (drink|breastfeed|suck|feed|eat)\b",
    r"\b(unable|not able) to (drink|breastfeed|suck|feed)\b",
    r"\b(not|isn'?t) (conscious|responding|responsive|awake)\b", r"\bunresponsive\b", r"\bpassed out\b",
    r"\bfaint(ed|ing)\b", r"\bcollaps(e|ed|ing)\b", r"\bno breath", r"\bstopped breathing\b",
    r"\bnot (sucking|feeding|breastfeeding)\b",
    r"(\bnot|n'?t|\bstopped|\bno longer) drinking\b(?! (alcohol|beer|akpeteshie|pito|palm wine))",
    r"\bstiff neck\b", r"\bchest pain\b", r"\bsevere (headache|dehydration|burn|pain|abdominal|anaemia|anemia)",
    r"\bburn(ed|t) badly\b", r"\bnot moving\b", r"\bsunken eyes\b",
    r"\b(can'?t|cannot|unable to) speak\b", r"\bone side (is |of the body )?(weak|numb|paralys)", r"\bface drooping\b",
    r"\bdrown", r"\bkill (him|her|my|them)self\b", r"\bkill themselves\b", r"\bsuicid", r"\bend (his|her|my) life\b",
    r"\baccident\b", r"\bwaters? (broke|has broken|have broken)\b",
    # added after the clinician question set found them missing (November 2026)
    r"\b(having|has|had|with) fits\b", r"\b(stiff|locked) jaw\b", r"\bbody (goes|going|went|is) stiff\b",
    r"\b(can'?t|cannot|unable to) open (the |his |her |their )?mouth\b",
    r"\b(very|extremely) pale\b", r"\bbreathing hard\b", r"\bfloppy\b",
    r"\b(can'?t|cannot|unable to) move (his|her|the|their|one) (leg|arm|legs|arms)\b",
]
# danger signs in pregnancy, labour and after delivery
_MATERNAL_CONTEXT = r"\b(pregnan|labou?r|deliver|gave birth|given birth|womb|antenatal|postpartum|weeks gone)"
_MATERNAL = [
    r"\bbleed", r"\bblood\b", r"\bheadache", r"\bblurr", r"\bswollen (face|hands)\b", r"\bswelling of (the )?(face|hands)\b",
    r"\bwater(s)? (broke|has broken|broken)\b", r"\bfever\b", r"\bsmelly\b", r"\bfoul", r"\babdominal pain\b",
    r"\bplacenta (not|hasn'?t|has not)\b", r"\bstopped moving\b", r"\bnot moving\b", r"\bnot coming\b",
    r"\bmore than 12 hours\b", r"\bfit\b",
]
# danger signs in a newborn (first 28 days)
_NEWBORN_CONTEXT = (r"\b(newborn|new-born|neonate|\d+ ?days? old|day[- ]old|baby born|([1-4]|one|two|three|four) ?weeks? old"
                    r"|born (yesterday|today|last week|this week)|first (week|month) of life)\b")
_NEWBORN = [
    r"\bfever\b", r"\bcold to (the )?touch\b", r"\bhot\b", r"\byellow", r"\bjaundice", r"\bcord\b", r"\bnot feeding\b",
    r"\bfeeding (poorly|badly)\b", r"\bnot moving\b", r"\bfast breathing\b", r"\bweak\b",
]
_CORD = re.compile(r"\bredness spreading\b.*\bcord\b|\bcord\b.*\b(pus|red|smell)", re.I)
_YELLOW_FEET = re.compile(r"\byellow\b.*\b(palms|soles)\b", re.I)

_RE = re.compile("|".join(_ALWAYS), re.I)
_MAT_CTX, _MAT = re.compile(_MATERNAL_CONTEXT, re.I), re.compile("|".join(_MATERNAL), re.I)
_NB_CTX, _NB = re.compile(_NEWBORN_CONTEXT, re.I), re.compile("|".join(_NEWBORN), re.I)


# A sign is only ignored when it is denied right before it ("no chest pain", "without
# bleeding", "denies seizures"). Anything looser could hide a real emergency, so a
# word in between ("no energy and convulsing") still counts as a sign.
_DENIED = re.compile(r"(?:\b(?:no|not|without|denies|never|no sign of|no signs of|hasn'?t had|has not had))\s*$", re.I)


def _signs(pattern: re.Pattern, text: str) -> bool:
    for m in pattern.finditer(text):
        if not _DENIED.search(text[max(0, m.start() - 24):m.start()]):
            return True
    return False


_FOLD = str.maketrans({"ɔ": "o", "ɛ": "e", "ʋ": "v", "ƒ": "f", "ɖ": "d", "ŋ": "n", "ɣ": "g",
                       "Ɔ": "o", "Ɛ": "e", "Ʋ": "v", "Ƒ": "f", "Ɖ": "d", "Ŋ": "n", "Ɣ": "g"})


def fold(text: str) -> str:
    """Lower-case, drop tone marks and map Twi and Ewe letters to their plain-keyboard
    spellings, because people type 'mogya retu' and 'ɔretu mogya' alike."""
    t = unicodedata.normalize("NFKD", text or "").translate(_FOLD)
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _load_local() -> dict:
    try:
        data = json.loads((Path(__file__).resolve().parent / "safety_local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for lang in ("tw", "ee"):
        pats = [fold(row["p"]) for row in data.get(lang, []) if row.get("p")]
        if pats:
            out[lang] = re.compile(r"(?<!\w)(?:" + "|".join(pats) + r")(?!\w)")
    return out


_LOCAL = _load_local()


def local_hit(text: str) -> str | None:
    """'tw' or 'ee' if a draft Twi or Ewe danger phrase matches, else None."""
    t = fold(text)
    return next((lang for lang, rx in _LOCAL.items() if rx.search(t)), None)


def local_enabled() -> bool:
    return os.getenv("SAFETY_LOCAL", "off").strip().lower() == "on"


def looks_like_emergency(text: str, mode: str = "health") -> bool:
    """mode is accepted for older callers; every user is a health worker now."""
    if not text:
        return False
    if local_enabled() and local_hit(text):
        return True
    if _signs(_RE, text) or _CORD.search(text) or _YELLOW_FEET.search(text):
        return True
    if _MAT_CTX.search(text) and _signs(_MAT, text):
        return True
    return bool(_NB_CTX.search(text) and _signs(_NB, text))
