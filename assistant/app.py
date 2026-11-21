"""Akwaaba: a Claude-powered WhatsApp assistant for Ghana's community health
workers, on Meta's WhatsApp Cloud API.

This file is the web layer: webhook, landing page, dashboard, privacy page.
The conversation itself lives in bot.py.

Run:  bash start.sh
"""

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import ids
import prompts
import speech
import voice
from bot import Bot
from stats import summarize
from store import Store
from whatsapp import TokenError, WhatsApp

load_dotenv()
HERE = Path(__file__).resolve().parent

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "3000"))
LOG_PATH = Path(os.getenv("USAGE_LOG", "usage_log.jsonl"))
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("META_APP_SECRET", "")
NGROK_DOMAIN = os.getenv("NGROK_DOMAIN", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"https://{NGROK_DOMAIN}" if NGROK_DOMAIN else "")

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("akwaaba")

_log_lock = threading.Lock()


def log_event(phone: str, **fields) -> None:
    """Anonymised usage log: hashed number, never message text."""
    record = {"ts": int(time.time()), "user": ids.user_code(phone), **fields}
    with _log_lock:
        new = not LOG_PATH.exists()
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if new:
            try:
                os.chmod(LOG_PATH, 0o600)
            except OSError:
                pass


claude = anthropic.Anthropic(max_retries=3, timeout=90)  # reads ANTHROPIC_API_KEY
wa = WhatsApp(os.getenv("META_ACCESS_TOKEN", ""), os.getenv("META_PHONE_NUMBER_ID", ""),
              os.getenv("META_GRAPH_VERSION", "v23.0"))
store = Store()
bot = Bot(wa, claude, store, log_event, MODEL, MAX_TOKENS,
          rate_limit_per_hour=int(os.getenv("RATE_LIMIT_PER_HOUR", "40")),
          public_base_url=PUBLIC_BASE_URL,
          display_number=os.getenv("WHATSAPP_DISPLAY_NUMBER", ""))


def startup_check() -> None:
    """Confirm settings in the background, so problems show up in the Terminal
    straight away rather than when the first person writes in."""
    def run():
        missing = [k for k in ("ANTHROPIC_API_KEY", "META_ACCESS_TOKEN", "META_PHONE_NUMBER_ID",
                               "META_VERIFY_TOKEN") if not os.getenv(k)]
        if missing:
            log.error("Missing settings in .env: %s", ", ".join(missing))
            return
        try:
            log.info("Connected to WhatsApp number %s", wa.check())
            if not bot.display_number:
                bot.display_number = wa.display_number()
        except TokenError:
            pass  # already explained loudly
        except Exception as e:
            log.error("Could not confirm the WhatsApp number (check META_PHONE_NUMBER_ID): %s", e)
        ok, why = speech.available()
        log.info("Voice notes: %s", "ON (GhanaNLP Khaya)" if ok else f"off ({why})")
        if not APP_SECRET:
            log.error("META_APP_SECRET is empty, so WhatsApp messages are being refused. Add it to .env "
                      "(Meta dashboard > App settings > Basic > App secret) and restart.")
        if PUBLIC_BASE_URL:
            log.info("Dashboard: %s/dashboard?key=%s", PUBLIC_BASE_URL, ids.dashboard_key())
        if not PUBLIC_BASE_URL:
            log.info("Tip: set NGROK_DOMAIN in .env to show the branded welcome image.")
    threading.Thread(target=run, daemon=True).start()


def weekly_lists() -> None:
    """Sends each health worker their follow-up list on Monday mornings (Ghana time = UTC).
    WEEKLY_LIST_HOUR sets the hour (default 7); WEEKLY_LIST=off turns it off."""
    if os.getenv("WEEKLY_LIST", "on").lower() == "off" or os.getenv("PYTEST_CURRENT_TEST"):
        return

    def run():
        hour = int(os.getenv("WEEKLY_LIST_HOUR", "7"))
        while True:
            now = time.gmtime()
            try:
                store.purge()
                voice.purge_cache()
            except Exception as e:
                log.warning("Clean-up of old data failed: %s", e)
            try:
                bot.check_referrals()
            except Exception as e:
                log.warning("Referral checks failed: %s", e)
            if now.tm_wday == 0 and now.tm_hour >= hour:
                try:
                    n = bot.weekly_digest()
                    if n:
                        log.info("Sent %d weekly follow-up lists", n)
                except Exception as e:
                    log.warning("Weekly lists failed: %s", e)
            time.sleep(900)
    threading.Thread(target=run, daemon=True).start()


@asynccontextmanager
async def lifespan(_app):
    startup_check()
    weekly_lists()
    yield


app = FastAPI(title="Akwaaba", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

MAX_BODY = 12 * 1024 * 1024   # the largest thing anyone may post: one photo or voice note
_CSP_APP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "media-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'")
_CSP_PAGE = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
             "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class _TooLarge(Exception):
    pass


@app.middleware("http")
async def harden(request: Request, call_next):
    """Refuses oversized uploads before they are read into memory, and adds the
    browser security headers to every response."""
    declared = request.headers.get("content-length")
    if declared and (not declared.isdigit() or int(declared) > MAX_BODY):
        return Response(status_code=413)
    if request.method in ("POST", "PUT", "PATCH") and not declared:
        seen, receive = 0, request._receive  # chunked upload with no length: count as it arrives

        async def counted():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > MAX_BODY:
                    raise _TooLarge()
            return message
        request._receive = counted
    try:
        resp = await call_next(request)
    except _TooLarge:
        return Response(status_code=413)
    path = request.url.path
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=(self), payment=(), usb=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    csp = _CSP_APP if path.startswith(("/app", "/api")) else _CSP_PAGE
    if path.startswith("/district"):
        csp = csp.replace("form-action 'none'", "form-action 'self'")
    h.setdefault("Content-Security-Policy", csp)
    if path.startswith(("/api", "/dashboard", "/stats", "/district")):
        h.setdefault("Cache-Control", "no-store")
    return resp

app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

# The companion app (installable web app) and its API.
import webapp  # noqa: E402
webapp.init(bot, store, wa, log_event)
app.include_router(webapp.router)


@app.get("/app", include_in_schema=False)
def app_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/app/")


app.mount("/app", StaticFiles(directory=HERE / "static" / "app", html=True), name="app")


def signature_ok(raw_body: bytes, header: str) -> bool:
    """Checks the request really came from Meta (X-Hub-Signature-256). Without
    META_APP_SECRET nothing is accepted: anyone could otherwise send messages that
    claim to come from someone else's number."""
    if not APP_SECRET:
        return False
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


# ================================================================ webhook

@app.get("/whatsapp")
def verify_webhook(request: Request):
    """Meta calls this once when you click 'Verify and save' in the dashboard."""
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and VERIFY_TOKEN and q.get("hub.verify_token") == VERIFY_TOKEN:
        log.info("Webhook verified by Meta")
        return PlainTextResponse(q.get("hub.challenge", ""))
    log.warning("Webhook verification failed: check META_VERIFY_TOKEN matches the dashboard")
    return Response(status_code=403)


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks) -> Response:
    raw = await request.body()
    if not signature_ok(raw, request.headers.get("x-hub-signature-256", "")):
        log.warning("Rejected request with bad Meta signature")
        return Response(status_code=403)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return Response(status_code=400)

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            for st in value.get("statuses", []) or []:
                if st.get("status") == "failed":
                    err = (st.get("errors") or [{}])[0]
                    log.error("WhatsApp could not deliver a reply: code %s, %s. %s",
                              err.get("code"), err.get("title", ""),
                              (err.get("error_data") or {}).get("details", ""))
                    log_event(st.get("recipient_id", ""), event="delivery_failed", code=err.get("code"))
            for msg in value.get("messages", []) or []:
                if isinstance(msg, dict) and store.first_time_seen(str(msg.get("id", ""))[:200]):
                    background.add_task(bot.handle, msg)
    return Response(status_code=200)


def _key_ok(key: str) -> bool:
    """Constant-time check of the dashboard key. Bytes, so an odd character can't cause an error."""
    return bool(key) and hmac.compare_digest(str(key).encode(), ids.dashboard_key().encode())


@app.get("/district", response_class=HTMLResponse)
def district_page(key: str = "", demo: int = 0) -> HTMLResponse:
    """The district surveillance desk. demo=1 shows invented signals for a fictional district."""
    import district
    import surveillance
    if not _key_ok(key):
        raise HTTPException(status_code=403, detail="This page needs its key.")
    if demo:
        return HTMLResponse(district.render(surveillance.sample(), "Sample District", demo=True, zones_expected=6))
    sigs = store.signals(since=int(time.time()) - 70 * 86400)
    names = {u: f"Reporter {ids.user_code(u)[:4]}" for u in {s["user"] for s in sigs}}
    return HTMLResponse(district.render(sigs, os.getenv("DISTRICT_NAME", ""), key=key, names=names))


@app.post("/district/signal")
async def district_signal(request: Request) -> Response:
    """An officer marks a signal verified or discarded. Needs the same key as the page."""
    form = await request.form()
    key = str(form.get("key", ""))
    if not _key_ok(key):
        raise HTTPException(status_code=403, detail="This page needs its key.")
    sid = str(form.get("id", ""))
    if not sid.isdigit() or not store.set_signal_status(int(sid), str(form.get("status", ""))):
        raise HTTPException(status_code=400, detail="Unknown signal or status.")
    log_event("district", event="signal_status", status=str(form.get("status")))
    return Response(status_code=303, headers={"Location": f"/district?key={key}"})


@app.post("/sms/inbound/{key}")
async def sms_inbound(key: str, request: Request, background: BackgroundTasks) -> Response:
    """Africa's Talking calls this for each incoming SMS. The key in the address is the
    shared secret; without the right one, or with SMS switched off, this looks like a 404."""
    import sms
    if not sms.enabled() or not sms.key_ok(key):
        return Response(status_code=404)
    form = await request.form()
    phone = sms.phone_digits(str(form.get("from", "")))
    text = str(form.get("text", ""))[:1000]
    msg_id = str(form.get("id", ""))[:120]
    if not phone or not msg_id or not store.first_time_seen("sms:" + msg_id):
        return Response(status_code=200)

    def work():
        sender = sms.Sender()
        for part in bot.sms_reply(phone, text):
            sender.send(phone, part)
    background.add_task(work)
    return Response(status_code=200)


# ================================================================ pages

def read_events() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    out = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


@app.get("/stats")
def stats(key: str = ""):
    if not _key_ok(key):
        return Response(status_code=404)
    return summarize(read_events(), profiles=store.count_profiles())


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(key: str = "") -> HTMLResponse:
    if not _key_ok(key):
        return HTMLResponse("Not found", status_code=404)
    from dashboard import render
    events = read_events()
    return HTMLResponse(render(summarize(events, profiles=store.count_profiles()), events, prompts.LANGUAGES))


@app.get("/", response_class=HTMLResponse)
def landing() -> HTMLResponse:
    from landing import render
    return HTMLResponse(render(bot.display_number or os.getenv("WHATSAPP_DISPLAY_NUMBER", "")))


@app.get("/healthz")
def healthz() -> dict:
    """Liveness for the host's health check: cheap, no secrets."""
    return {"ok": True}


@app.get("/health")
def health() -> dict:
    ok, why = speech.available()
    import redact
    return {"ok": True, "model": MODEL, "voice": why, "register_photos": redact.available()[1],
            "configured": all(os.getenv(k) for k in ("ANTHROPIC_API_KEY", "META_ACCESS_TOKEN",
                                                      "META_PHONE_NUMBER_ID", "META_VERIFY_TOKEN"))}


PRIVACY_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Akwaaba privacy policy</title>
<style>body{{font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:680px;margin:40px auto;padding:0 16px;line-height:1.55;color:#222}}h1{{font-size:1.6em}}h2{{font-size:1.15em;margin-top:1.6em}}</style></head><body>
<h1>Akwaaba privacy policy</h1>
<p>Akwaaba is a free, experimental WhatsApp assistant for community health workers in Ghana. This page explains what happens to information you send it.</p>
<h2>What we receive</h2>
<p>When you message Akwaaba on WhatsApp, we receive your WhatsApp phone number, the text, voice notes, photos or documents you send, and basic delivery information from Meta's WhatsApp Business Platform.</p>
<h2>How we use it</h2>
<p>Your message, and any photo or document you send, is passed to Anthropic's Claude API to write a reply. Voice notes are converted to text by GhanaNLP's Khaya speech service, and the text is shown back to you before you get an answer.</p>
<h2>What Akwaaba remembers</h2>
<p>To give better answers over time, Akwaaba remembers your role and language, recent messages, a short summary of older conversations, and work details you share about yourself, such as your role or your type of facility. It is instructed never to keep names of patients or other people, or anyone's health details. Send <b>MY DATA</b> to see what it remembers, and <b>FORGET ME</b> to delete all of it.</p>
<h2>Register photos</h2>
<p>If you photograph a register page to draft a monthly return, Akwaaba first paints over names and long numbers on the photo, then sends only that covered copy to Claude to read the counts. The original photo is not stored. If names can't be hidden automatically, the photo is not sent at all. Akwaaba keeps the monthly totals so it can compare with last month; it never submits a return for you.</p>
<h2>What we keep for measurement</h2>
<p>We keep a usage log to measure whether the assistant is useful: dates and times, the role, language and task chosen, whether a photo or voice note was sent, response times and ratings. Phone numbers in this log are replaced by an irreversible code. The log does not contain the text of your messages, your voice notes or your photos.</p>
<h2>What we don't do</h2>
<p>We do not sell or share your information or use it for advertising.</p>
<h2>Health information</h2>
<p>Akwaaba does not diagnose illness or prescribe medicine. In an emergency, go to the nearest health facility or call 112 or 193.</p>
<h2>Deleting your data</h2>
<p>Send FORGET ME to delete everything Akwaaba remembers about you. For anything else, contact {contact}.</p>
<p><small>Last updated {updated}.</small></p>
</body></html>"""


@app.get("/privacy", response_class=HTMLResponse)
def privacy() -> str:
    return PRIVACY_HTML.format(
        contact=os.getenv("PRIVACY_CONTACT", "the person who shared this assistant with you"),
        updated="October 2026")
