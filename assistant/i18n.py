"""Shows the app's own menus, buttons and fixed messages in the person's language.

Translations live in i18n/<code>.json as {"English text": "translation"}, one line
of English per entry, so a native speaker can review or fix them without touching code.
Anything without a translation is shown in English, so a gap never breaks the chat.

How it works: Bot.handle() calls use(state) for each incoming message, then every
outgoing string passes through tr() in whatsapp.py. tr() tries, in order:
  1. an exact entry
  2. a template entry with {placeholders}, e.g. "Done ✅ I'll reply in *{lang}*."
  3. paragraph by paragraph, then line by line, but only if EVERY piece is known,
     so Claude's own answers (already in the right language) are left untouched.
"""

import json
import logging
import re
import threading
from pathlib import Path

log = logging.getLogger("akwaaba")
DIR = Path(__file__).resolve().parent / "i18n"
RAW_FIELDS = {"text", "number"}  # placeholders holding the person's own words or data: never translated

_local = threading.local()
_tables: dict[str, dict[str, str]] = {}
_templates: dict[str, list[tuple[re.Pattern, list[str], str]]] = {}


def load() -> None:
    _tables.clear()
    _templates.clear()
    for path in sorted(DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Couldn't read translations %s: %s", path.name, e)
            continue
        table, templates = {}, []
        for en, tx in raw.items():
            if en.startswith("__") or not isinstance(tx, str) or not tx.strip():
                continue
            table[en] = tx
            # Multi-line entries also teach their single lines, when the line counts match.
            en_lines, tx_lines = en.split("\n"), tx.split("\n")
            if len(en_lines) > 1 and len(en_lines) == len(tx_lines):
                for a, b in zip(en_lines, tx_lines):
                    table.setdefault(a, b)
            if "{" in en:
                names = re.findall(r"\{(\w+)\}", en)
                seen_names, parts = set(), []
                for p in re.split(r"(\{\w+\})", en):
                    if not p:
                        continue
                    if re.fullmatch(r"\{\w+\}", p):
                        name = p[1:-1]
                        parts.append(f"(?P={name})" if name in seen_names else f"(?P<{name}>.*?)")
                        seen_names.add(name)
                    else:
                        parts.append(re.escape(p))
                pattern = "".join(parts)
                templates.append((re.compile(pattern, re.S), names, tx))
        templates.sort(key=lambda t: -len(t[0].pattern))  # most specific first
        _tables[path.stem], _templates[path.stem] = table, templates


def use(state: dict | None) -> None:
    """Remember whose message we're handling, so replies follow their language,
    even if they change it halfway through this message."""
    _local.state = state


def current_lang() -> str:
    s = getattr(_local, "state", None)
    return (s or {}).get("lang") or "en"


def tr(text: str, lang: str | None = None) -> str:
    lang = lang or current_lang()
    if not text or lang not in _tables:
        return text
    out = _strict(text, lang)
    return text if out is None else out


def fit(text: str, limit: int, lang: str | None = None) -> str:
    """Translate a button or menu label, keeping English if the translation is too long."""
    out = tr(text, lang)
    return out if len(out) <= limit else text


def _strict(text: str, lang: str):
    """The translation, or None if any part of the text is unknown."""
    table = _tables[lang]
    if text in table:
        return table[text]
    if not text.strip():
        return text
    for regex, names, tx in _templates[lang]:
        m = regex.fullmatch(text)
        if m:
            values = {n: (m.group(n) if n in RAW_FIELDS else _lenient(m.group(n), lang)) for n in names}
            try:
                return tx.format(**values)
            except (KeyError, IndexError, ValueError):
                continue
    for sep in ("\n\n", "\n"):
        if sep in text:
            parts = [_strict(p, lang) for p in text.split(sep)]
            return None if any(p is None for p in parts) else sep.join(parts)
    m = re.fullmatch(r"([*_])(.+)\1", text, re.S)  # *bold* or _italic_ around a known phrase
    if m:
        inner = _strict(m.group(2), lang)
        return None if inner is None else f"{m.group(1)}{inner}{m.group(1)}"
    return None


def _lenient(text: str, lang: str) -> str:
    """For values inside a template: translate what we can, keep the rest."""
    out = _strict(text, lang)
    if out is not None:
        return out
    if "\n" in text:
        return "\n".join(_lenient(line, lang) for line in text.split("\n"))
    return text


def untranslated(strings, lang: str) -> list[str]:
    """Strings that would still show in English (used by the tests and reviewers)."""
    return [s for s in strings if s and _strict(s, lang) is None]


load()
