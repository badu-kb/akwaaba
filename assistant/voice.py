"""Answers read aloud, in Twi, Ewe or English.

English uses Abena AI's Ghanaian-accented voices by default; Twi and Ewe use
GhanaNLP's Khaya. Either language can be switched with TTS_PROVIDER_EN / _TW / _EE
("abena" or "khaya"), and the voice with ABENA_VOICE_EN / _TW / _EE.

For people who'd rather listen than read (or share a phone with someone who
can't read English), any answer can come back as a WhatsApp voice note.

Khaya needs KHAYA_API_KEY and the khaya package. Abena works without a key on its
free tier; set ABENA_API_KEY from a free account for higher limits. Both need ffmpeg.
Audio is cached on disk, so replaying an answer doesn't use up the API quota.
"""

import base64
import hashlib
import json
import logging
import shutil
import time
import urllib.error
import urllib.request
import os
import re
import subprocess
import tempfile
from pathlib import Path

import media
import speech

log = logging.getLogger("akwaaba")

TTS_CODES = {"en": "eng", "tw": "twi", "ee": "ewe", "gaa": "gaa", "fat": "fat", "dag": "dag"}
MAX_CHARS = 700           # about 45 seconds: long notes lose listeners, and the full text is right underneath
CHUNK = 400               # Khaya handles short passages best
CACHE = Path(os.getenv("AKWAABA_DB", str(Path.home() / ".akwaaba" / "akwaaba.db"))).parent / "tts"


class VoiceFailed(RuntimeError):
    pass


ABENA_URL = "https://abena.mobobi.com/playground/api/v1/tts/synthesize/"
ABENA_DEFAULTS = {"en": "akua_eng_high", "tw": "abena_twi_high", "ee": "mawuli_ewe"}
ABENA_SAMPLES = {"en": ("akua_eng_high", "kwabena_eng_high", "akua_eng_medium", "kwabena_eng_medium"),
                 "tw": ("abena_twi_high", "kofi_twi_high"), "ee": ("mawuli_ewe",)}


def provider_for(lang: str) -> str:
    default = "abena" if lang == "en" else "khaya"
    p = (os.getenv(f"TTS_PROVIDER_{lang.upper()}") or default).strip().lower()
    return p if p in ("abena", "khaya") else default


def available(lang: str | None = None) -> tuple[bool, str]:
    """Whether voice notes can be made: for one language, or for any of English, Twi and Ewe."""
    if lang is None:
        results = [available(l) for l in ("en", "tw", "ee")]
        return next((r for r in results if r[0]), results[0])
    if provider_for(lang) == "abena":
        if not shutil.which("ffmpeg"):
            return False, "ffmpeg is not installed (brew install ffmpeg)"
        return True, "ready"
    return speech.available()


SPEAKERS = ("female", "male_low", "male_high")


def speaker_for(lang: str) -> str:
    """Voice for this language. Abena: ABENA_VOICE_EN / _TW / _EE. Khaya: TTS_VOICE_EN / _TW / _EE, else TTS_VOICE, else female."""
    if provider_for(lang) == "abena":
        return os.getenv(f"ABENA_VOICE_{lang.upper()}") or ABENA_DEFAULTS.get(lang, "akua_eng_high")
    return os.getenv(f"TTS_VOICE_{lang.upper()}") or os.getenv("TTS_VOICE", "female")


def lang_enabled(lang: str) -> bool:
    """English voice can be switched off (ENGLISH_VOICE=off) until a voice sounds Ghanaian enough."""
    if lang == "en" and os.getenv("ENGLISH_VOICE", "on").strip().lower() in ("off", "0", "no", "false"):
        return False
    return True


ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
SAY_AS = {"CHPS": "chips", "ANC": "antenatal care", "PNC": "postnatal care", "CWC": "child welfare clinic",
          "ORS": "O R S", "RDT": "R D T", "GHS": "Ghana Health Service", "CHO": "C H O", "CHN": "C H N",
          "IPTp": "I P T P", "SP": "S P", "MR": "measles rubella", "OPV": "polio", "e.g.": "for example"}


def _number(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return ONES[n // 100] + " hundred" + ("" if rest == 0 else " and " + _number(rest))
    return str(n)


def english(text: str) -> str:
    """Spell out what voices often get wrong in English: small numbers and health abbreviations."""
    for short, said in SAY_AS.items():
        text = re.sub(rf"(?<![\w-]){re.escape(short)}(?![\w-])", said, text)
    # Card and register numbers are read digit by digit; other small numbers as words. Times and dates stay as they are.
    text = re.sub(r"(?i)\b(card|reg|register|no\.?|number|#)\s*(\d+)\b",
                  lambda m: m.group(1) + " " + " ".join(ONES[int(d)] for d in m.group(2)), text)
    text = re.sub(r"(?<![\d:/.,])(\d{1,3})(?!\d)(?![:/.,]\d)", lambda m: _number(int(m.group(1))), text)
    return text


def script(text: str) -> str:
    """What to say: formatting, emoji, quote marks and the 'Reply MORE' hint removed."""
    t = re.sub(r"\n?\(?Reply MORE.*$", "", text, flags=re.I | re.S)
    t = re.sub(r"\n?\(Kyerɛw MORE.*$", "", t, flags=re.S)
    lines = [media.plain(l) for l in t.split("\n")]
    lines = [l if re.search(r"[.!?:;]$", l) else l + "." for l in lines if l]
    out = " ".join(lines)
    if len(out) > MAX_CHARS:
        cut = out.rfind(". ", 0, MAX_CHARS)
        out = out[: cut + 1 if cut > 200 else MAX_CHARS]
    return out


def _chunks(text: str) -> list[str]:
    parts, cur = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(cur) + len(sentence) + 1 > CHUNK and cur:
            parts.append(cur)
            cur = ""
        cur = f"{cur} {sentence}".strip()
        while len(cur) > CHUNK:  # a very long sentence
            cut = cur.rfind(" ", 0, CHUNK)
            cut = cut if cut > 50 else CHUNK
            parts.append(cur[:cut])
            cur = cur[cut:].strip()
    if cur:
        parts.append(cur)
    return parts


def _tier_fallbacks(voice_id: str) -> list[str]:
    """akua_eng_high, then akua_eng_medium, then akua_eng (lite), so a busy tier doesn't mean silence."""
    base = re.sub(r"_(high|medium|lite)$", "", voice_id)
    order = [voice_id] + [base + t for t in ("_high", "_medium", "", "_lite")]
    return [v for i, v in enumerate(order) if v not in order[:i]]


def _abena(part: str, voice_id: str) -> bytes:
    headers = {"Content-Type": "application/json", "User-Agent": "akwaaba-assistant"}
    if os.getenv("ABENA_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['ABENA_API_KEY']}"
    speed = float(os.getenv("ABENA_SPEED", "1.0"))
    last = "no voice"
    for vid in _tier_fallbacks(voice_id):
        for attempt in range(2):
            body = json.dumps({"text": part, "voice": vid, "speed": speed}).encode()
            req = urllib.request.Request(ABENA_URL, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                return base64.b64decode(data["audio_base64"])
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code == 401:
                    log.error("Abena rejected the API key. Check ABENA_API_KEY in .env.")
                    raise VoiceFailed("Abena authentication")
                if e.code == 402:
                    log.error("Abena credits are used up. Create a free account and set ABENA_API_KEY in .env.")
                    raise VoiceFailed("Abena credits used")
                if e.code in (429, 503) and attempt == 0:
                    try:
                        wait = float(e.headers.get("Retry-After") or 3)
                    except ValueError:
                        wait = 3.0
                    if wait > 30:  # honour the service's wait; if it's long, give up on voice for this answer
                        raise VoiceFailed(f"Abena busy, retry after {wait:.0f}s")
                    time.sleep(wait)
                    continue
                break  # 400 (unknown voice), 503 again or 500: try the next tier
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
                last = type(e).__name__
                break
    log.warning("Abena text-to-speech failed (%s)", last)
    raise VoiceFailed(f"Abena {last}")


def speak(text: str, lang: str, fmt: str = "ogg", speaker: str | None = None) -> bytes:
    """Voice-note bytes for this text: "ogg" (Opus, for WhatsApp) or "m4a" (AAC, for iPhone browsers).

    `speaker` overrides the voice: a Khaya speaker (female, male_low, male_high) or an Abena voice id.
    """
    provider = provider_for(lang)
    if speaker:
        provider = "khaya" if speaker in SPEAKERS else "abena"
    if provider == "abena":
        ok, why = (True, "ready") if shutil.which("ffmpeg") else (False, "ffmpeg is not installed (brew install ffmpeg)")
    else:
        ok, why = speech.available()
    if not ok:
        raise VoiceFailed(why)
    words = script(text)
    if lang == "en":
        words = english(words)
    if len(words) < 2:
        raise VoiceFailed("nothing to say")
    code = TTS_CODES.get(lang, "eng")
    voice_name = speaker or speaker_for(lang)
    key = hashlib.sha256(f"{provider}|{code}|{voice_name}|{words}".encode()).hexdigest()[:24]
    ext = "m4a" if fmt == "m4a" else "ogg"
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{key}.{ext}"
    if cached.exists():
        return cached.read_bytes()

    tmp = Path(tempfile.mkdtemp(prefix="akwaaba-tts-"))
    try:
        files = []
        if provider == "abena":
            for i, part in enumerate(_chunks(words)):
                f = tmp / f"{i:02d}.audio"
                f.write_bytes(_abena(part, voice_name))
                files.append(f)
        else:
            from khaya import KhayaClient
            with KhayaClient(os.environ["KHAYA_API_KEY"]) as client:
                for i, part in enumerate(_chunks(words)):
                    result = client.synthesize(part, code, voice_name)
                    f = tmp / f"{i:02d}.audio"
                    f.write_bytes(result.audio)
                    files.append(f)
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{f}'\n" for f in files))
        out = tmp / f"out.{ext}"
        # Re-encode every piece to the same format, then join them as one voice note.
        normalised = []
        for f in files:
            n = f.with_suffix(".wav")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(f), "-ar", "48000", "-ac", "1", str(n)],
                           check=True, capture_output=True, timeout=60)
            normalised.append(n)
        listing.write_text("".join(f"file '{n}'\n" for n in normalised))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                        *(["-c:a", "aac", "-b:a", "48k", "-movflags", "+faststart"] if ext == "m4a" else
                          ["-c:a", "libopus", "-b:a", "32k", "-application", "voip"]), "-ac", "1", str(out)],
                       check=True, capture_output=True, timeout=120)
        data = out.read_bytes()
        cached.write_bytes(data)
        return data
    except subprocess.CalledProcessError as e:
        log.warning("Couldn't convert speech audio: %s", (e.stderr or b"")[:200])
        raise VoiceFailed("conversion failed")
    except VoiceFailed:
        raise
    except Exception as e:
        name = type(e).__name__
        if "Authentication" in name:
            log.error("Khaya rejected the API key. Check KHAYA_API_KEY in .env.")
        else:
            log.warning("Text-to-speech failed (%s): %s", name, e)
        raise VoiceFailed(name)
    finally:
        for f in tmp.glob("*"):
            f.unlink(missing_ok=True)
        tmp.rmdir()


def purge_cache(max_age_s: float = 24 * 3600) -> int:
    """Spoken audio is kept for a day so replays are free, then deleted."""
    n, now = 0, __import__("time").time()
    if CACHE.exists():
        for f in CACHE.glob("*"):
            if f.is_file() and now - f.stat().st_mtime > max_age_s:
                f.unlink(missing_ok=True)
                n += 1
    return n
