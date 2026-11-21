"""Voice notes -> text, using GhanaNLP's Khaya speech recognition.

Khaya is built in Ghana and recognises Twi, Ewe, Ga, Fante, Dagbani and
African-accented English, which general-purpose speech models handle poorly.

Needs:
  - KHAYA_API_KEY in .env (sign up at https://translation.ghananlp.org)
  - ffmpeg on the computer (brew install ffmpeg), to turn WhatsApp's OGG/Opus
    voice notes into the 16 kHz mono WAV that Khaya expects
  - the khaya package (pip install khaya)
If any of these is missing, voice notes get a polite "please type" reply.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("akwaaba")

MAX_SECONDS = 120


class SpeechUnavailable(RuntimeError):
    pass


class SpeechUnclear(RuntimeError):
    pass


class SpeechTooLong(RuntimeError):
    pass


def available() -> tuple[bool, str]:
    if not os.getenv("KHAYA_API_KEY"):
        return False, "KHAYA_API_KEY is not set"
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg is not installed (brew install ffmpeg)"
    try:
        import khaya  # noqa: F401
    except ImportError:
        return False, "the khaya package is not installed (pip install khaya)"
    return True, "ready"


def to_wav(audio: bytes) -> Path:
    """Convert any audio to 16 kHz mono PCM WAV; returns the temp file path."""
    src = tempfile.NamedTemporaryFile(suffix=".audio", delete=False)
    src.write(audio)
    src.close()
    dst = Path(src.name).with_suffix(".wav")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src.name, "-t", str(MAX_SECONDS + 1),
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
            capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise SpeechUnclear(proc.stderr.decode(errors="ignore")[:200])
        return dst
    finally:
        os.unlink(src.name)


def duration_s(wav: Path) -> float:
    return max(0.0, (wav.stat().st_size - 44) / (16000 * 2))


def transcribe(audio: bytes, lang_code: str, asr_codes: dict) -> str:
    ok, why = available()
    if not ok:
        raise SpeechUnavailable(why)
    wav = to_wav(audio)
    try:
        if duration_s(wav) > MAX_SECONDS:
            raise SpeechTooLong()
        from khaya import KhayaClient
        with KhayaClient(os.environ["KHAYA_API_KEY"]) as client:
            result = client.transcribe(str(wav), asr_codes.get(lang_code, "eng"))
        text = (getattr(result, "text", None) or str(result or "")).strip()
        if len(text) < 2:
            raise SpeechUnclear("empty transcript")
        return text
    except (SpeechTooLong, SpeechUnclear):
        raise
    except Exception as e:
        name = type(e).__name__
        if "Authentication" in name:
            log.error("Khaya rejected the API key. Check KHAYA_API_KEY in .env.")
            raise SpeechUnavailable("bad key")
        log.warning("Speech recognition failed (%s): %s", name, e)
        raise SpeechUnclear(str(e))
    finally:
        try:
            wav.unlink()
        except OSError:
            pass
