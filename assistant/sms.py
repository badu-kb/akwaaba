"""SMS fallback for when mobile data is too weak for WhatsApp.

Text only, short answers, the same emergency check first. Off unless it is
configured: SMS_PROVIDER=africastalking, AT_USERNAME, AT_API_KEY and
SMS_INBOUND_KEY (a long random string that goes in the callback address you give
the provider, because Africa's Talking does not sign its callbacks).

What changes for privacy when this is switched on: the question and the answer
travel through the mobile network and the SMS provider as ordinary text
messages, which are not end-to-end encrypted. The privacy notice must say so
before real use.
"""

import hmac
import logging
import os
import re

import httpx

log = logging.getLogger("akwaaba")

MAX_CHARS = 420          # about three text messages in English; fewer letters fit in Twi and Ewe
EMERGENCY = "EMERGENCY: go to the nearest health facility NOW, or call 112 or 193 (ambulance). Do not wait for a reply."
RATE_LIMITED = "You have sent many messages this hour. Please wait a little. In an emergency call 112 or 193."
BUSY = "Akwaaba is busy. Please try again in a minute. In an emergency call 112 or 193."
FORGOTTEN = "Done. Everything Akwaaba kept about this number has been deleted."
HELP = ("Akwaaba by SMS: text your health work question and get a short answer. "
        "Text EN, TW or EE to change language, FORGET ME to delete your data. No patient names please.")


def enabled() -> bool:
    return (os.getenv("SMS_PROVIDER", "").strip().lower() == "africastalking"
            and all(os.getenv(k) for k in ("AT_USERNAME", "AT_API_KEY", "SMS_INBOUND_KEY")))


def key_ok(key: str) -> bool:
    expected = os.getenv("SMS_INBOUND_KEY", "")
    return len(expected) >= 24 and hmac.compare_digest(key or "", expected)


def phone_digits(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    return digits if 8 <= len(digits) <= 15 else None


def shorten(text: str, limit: int = MAX_CHARS) -> str:
    """Plain text that fits a few messages, cut at the end of a sentence where possible."""
    text = re.sub(r"[*_~`#>]", "", text or "")
    text = re.sub(r"[^\w\s.,;:!?'\"()/%+\-Ɛ-ʯ]", "", text)       # drops emoji, keeps Twi and Ewe letters
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:stop + 1] if stop > limit // 2 else cut.rsplit(" ", 1)[0]).strip()


class Sender:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=20)

    def send(self, phone: str, text: str) -> bool:
        username = os.getenv("AT_USERNAME", "")
        host = "api.sandbox.africastalking.com" if username == "sandbox" else "api.africastalking.com"
        data = {"username": username, "to": "+" + phone, "message": text}
        if os.getenv("AT_SENDER_ID"):
            data["from"] = os.getenv("AT_SENDER_ID")
        try:
            r = self.http.post(f"https://{host}/version1/messaging", data=data,
                               headers={"apiKey": os.getenv("AT_API_KEY", ""), "Accept": "application/json"})
        except httpx.HTTPError as e:
            log.warning("SMS not sent (network): %s", type(e).__name__)
            return False
        if r.status_code >= 400:
            log.warning("SMS not sent: provider answered %s", r.status_code)
            return False
        return True
