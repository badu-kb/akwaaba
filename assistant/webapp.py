"""The Akwaaba companion app: an installable web app (works on iPhone, Android and
desktop) that shares one account with the WhatsApp assistant.

Sign-in: people type their WhatsApp number and Akwaaba sends a 6-digit code on
WhatsApp (or they send LOGIN to the WhatsApp number to get one). After that the
app and WhatsApp share the same history, library, language and settings.

Everything lives under /api/... (JSON) and the app itself is served from /app/.
"""

import base64
import json
import logging
import queue
import re
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import i18n
import media
import places
import prompts
import quiz
import safety
import speech
import redact
import voice
from bot import IMAGE_TYPES, ClaudeBusy, _filename
from whatsapp import SendError, TokenError

log = logging.getLogger("akwaaba")
router = APIRouter()
CTX: dict = {}           # bot, store, wa, log_event (set by init)
MAX_UPLOAD = 8 * 1024 * 1024
_ip_hits: dict[str, deque] = defaultdict(deque)


def init(bot, store, wa, log_event) -> None:
    CTX.update(bot=bot, store=store, wa=wa, log_event=log_event)


# ============================================================ helpers

def normalise_phone(raw: str, default_cc: str = "233") -> str:
    """'024 123 4567' -> '233241234567'; '+1 (555) 141-8366' -> '15551418366'."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("00"):
        digits = digits[2:]
    elif not raw.startswith("+") and digits.startswith("0") and len(digits) == 10:
        digits = default_cc + digits[1:]
    elif not raw.startswith("+") and len(digits) == 9 and digits[0] in "235":
        digits = default_cc + digits                       # Ghana number typed without the 0
    elif not raw.startswith("+") and re.fullmatch(r"[2-9]\d{2}[2-9]\d{6}", digits):
        digits = "1" + digits                              # US or Canada number typed without +1
    if not 8 <= len(digits) <= 15:
        raise HTTPException(400, "Please enter your full WhatsApp number.")
    return digits


def _throttle(key: str, limit: int, window: int = 3600) -> None:
    now = time.time()
    if len(_ip_hits) > 20000:   # never let made-up numbers or addresses grow this without bound
        for k in [k for k, v in _ip_hits.items() if not v or now - v[-1] > window]:
            del _ip_hits[k]
        if len(_ip_hits) > 20000:
            raise HTTPException(429, "Too many attempts. Please wait a little and try again.")
    q = _ip_hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many attempts. Please wait a little and try again.")
    q.append(now)


def user(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    phone = CTX["bot"].store.session_user(token)
    if not phone:
        raise HTTPException(401, "Please sign in again.")
    return phone


def _lang(s) -> str:
    return s.get("lang") or "en"


def _tr(s, text) -> str:
    return i18n.tr(text, _lang(s))


def _detail(s) -> str:
    return s["profile"].get("role") or ""


def _task_view(tid, s):
    t = prompts.TASKS[tid]
    return {"id": tid, "title": _tr(s, t["title"]), "desc": _tr(s, t["desc"]), "kind": t.get("kind", "chat"),
            "ask": _tr(s, t.get("ask", "")), "examples": [_tr(s, e) for e in t.get("examples", [])],
            "examples_en": t.get("examples", []), "form": (t.get("form") or {}).get("key")}


def _follow_view(s, follow):
    return [{"id": fid, "label": _tr(s, label)} for fid, label in follow]


def _item_view(s, it, with_body=True):
    out = {"id": it["id"], "title": it.get("title") or media.title_of(it.get("body") or ""),
           "task": it.get("task"), "kind": _tr(s, prompts.DOC_KIND.get(it.get("task") or "", "Notes")),
           "saved": bool(it.get("saved", True)), "ts": it.get("ts")}
    if with_body:
        out["body"] = it.get("body")
    return out


def _form_schema(key: str, s) -> dict | None:
    """The WhatsApp Flow form, reused so the app shows the very same fields."""
    from bot import FLOWS_DIR
    try:
        flow = json.loads((FLOWS_DIR / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    screen = flow["screens"][0]
    fields = []
    for c in screen["layout"]["children"]:
        if c["type"] in ("TextHeading", "TextBody"):
            fields.append({"type": c["type"], "text": _tr(s, c["text"])})
        elif c["type"] in ("TextInput", "TextArea", "Dropdown", "RadioButtonsGroup", "CheckboxGroup"):
            fields.append({"type": c["type"], "name": c["name"], "label": _tr(s, c["label"]),
                           "required": bool(c.get("required")), "input": c.get("input-type", "text"),
                           "helper": _tr(s, c.get("helper-text", "")), "init": c.get("init-value"),
                           "options": [{"id": o["id"], "title": _tr(s, o["title"]),
                                        "description": _tr(s, o.get("description", ""))}
                                       for o in c.get("data-source", [])]})
        elif c["type"] == "Footer":
            fields.append({"type": "Submit", "label": _tr(s, c["label"])})
    return {"key": key, "title": _tr(s, screen.get("title", "")), "fields": fields}


# ============================================================ sign-in

class StartIn(BaseModel):
    phone: str = Field(max_length=40)


class VerifyIn(BaseModel):
    phone: str = Field(max_length=40)
    code: str = Field(max_length=12)


@router.post("/api/auth/start")
def auth_start(body: StartIn, request: Request):
    _throttle(f"start:{request.client.host if request.client else '?'}", 20)
    phone = normalise_phone(body.phone)
    _throttle(f"start-phone:{phone}", 6)
    store, wa, bot = CTX["bot"].store, CTX["wa"], CTX["bot"]
    code = store.new_code(phone)
    number = bot.display_number
    link = f"https://wa.me/{number}?text=LOGIN" if number else ""
    if not code:
        return {"sent": True, "phone": phone, "wa_link": link, "note": "wait"}
    s = store.get(phone)
    sent = False
    try:
        wa.send_text(phone, i18n.tr(prompts.APP_CODE, _lang(s)).replace("{number}", code))
        sent = True
    except (SendError, TokenError) as e:
        # Usually WhatsApp's 24-hour rule: we can only message people who wrote to us recently.
        log.info("App code not delivered on WhatsApp (%s); user can send LOGIN instead", e)
    CTX["log_event"](phone, event="app_code", via="app", delivered=sent)
    return {"sent": sent, "phone": phone, "wa_link": link}


@router.post("/api/auth/verify")
def auth_verify(body: VerifyIn, request: Request):
    _throttle(f"verify:{request.client.host if request.client else '?'}", 40)
    phone = normalise_phone(body.phone)
    _throttle(f"verify-phone:{phone}", 15)
    store = CTX["bot"].store
    if not re.fullmatch(r"\d{6}", body.code.strip()) or not store.check_code(phone, body.code):
        raise HTTPException(400, "That code didn't work. Check it, or ask for a new one.")
    token = store.new_session(phone)
    CTX["log_event"](phone, event="app_signin")
    return {"token": token}


@router.post("/api/logout")
def logout(authorization: str = Header(default="")):
    CTX["bot"].store.end_session(authorization.removeprefix("Bearer ").strip())
    return {"ok": True}


# ============================================================ profile & config

@router.get("/api/strings")
def strings(lang: str = "en"):
    lang = lang if lang in prompts.LANGUAGES else "en"
    return {"lang": lang, "strings": i18n._tables.get(lang, {})}


@router.get("/api/me")
def me(phone: str = Depends(user)):
    store, bot = CTX["bot"].store, CTX["bot"]
    s = store.get(phone)
    mode = s["mode"]
    return {
        "phone": phone, "mode": mode, "lang": _lang(s), "detail": _tr(s, _detail(s)),
        "role_name": _tr(s, prompts.ROLE_NAMES[mode]) if mode else "",
        "voice_pref": bool((s.get("prefs") or {}).get("voice")), "voice_available": voice.available()[0],
        "speech_available": speech.available()[0],
        "stats": {"answers": s.get("answers", 0), "saved": store.library_count(phone)},
        "profile": {k.replace("_", " ").capitalize(): v for k, v in (s.get("profile") or {}).items()},
        "summary": s.get("summary", ""), "bot_number": bot.display_number,
        "tasks": [_task_view(t, s) for t in prompts.APP_MENU_ORDER.get(mode, [])] if mode else [],
        "languages": [{"code": c, "name": n} for c, n in prompts.LANGUAGES.items()],
        "details": {
            "health": [{"id": i, "title": _tr(s, t), "desc": _tr(s, d)} for i, t, d in prompts.HEALTH_ROLE_ROWS],
        },
    }


class ProfileIn(BaseModel):
    mode: str | None = None
    lang: str | None = None
    detail: str | None = None
    voice: bool | None = None


@router.post("/api/profile")
def set_profile(body: ProfileIn, phone: str = Depends(user)):
    store = CTX["bot"].store
    with store.user_lock(phone):
        s = store.get(phone)
        if not s["mode"] and (body.mode or body.lang or body.detail):  # finishing setup in the app
            s.update(mode=prompts.MODE, history=[], task=None, step=None, welcomed=True)
            CTX["log_event"](phone, event="mode", mode=prompts.MODE, channel="app")
        if body.lang in prompts.LANGUAGES:
            s["lang"] = body.lang
            CTX["log_event"](phone, event="language", lang=body.lang, channel="app")
        if body.detail in prompts.HEALTH_ROLE_VALUES:
            s["profile"]["role"] = prompts.HEALTH_ROLE_VALUES[body.detail]
        if body.voice is not None:
            s["prefs"] = {**(s.get("prefs") or {}), "voice": bool(body.voice)}
        s["step"] = None
        store.save(phone, s)
    return me(phone)


@router.post("/api/forget")
def forget(phone: str = Depends(user)):
    store = CTX["bot"].store
    with store.user_lock(phone):
        store.forget(phone)
    CTX["log_event"](phone, event="forget", channel="app")
    return {"ok": True}


@router.get("/api/form/{key}")
def form(key: str, phone: str = Depends(user)):
    schema = _form_schema(key, CTX["bot"].store.get(phone)) if key == "danger" else None
    if not schema:
        raise HTTPException(404, "No such form")
    return schema


# ============================================================ conversation

@router.get("/api/history")
def history(phone: str = Depends(user)):
    store = CTX["bot"].store
    s = store.get(phone)
    by_body = {it["body"]: it for it in store.recent_items(phone)}
    out = []
    for m in s.get("history") or []:
        c = m["content"]
        text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        entry = {"role": m["role"], "text": text}
        it = by_body.get(text) if m["role"] == "assistant" else None
        if it:
            entry.update(item_id=it["id"], saved=bool(it["saved"]), task=it["task"])
        out.append(entry)
    return {"messages": out}


class AskIn(BaseModel):
    text: str = Field(default="", max_length=8000)
    task: str | None = Field(default=None, max_length=40)
    follow: str | None = Field(default=None, max_length=40)
    form: dict | None = None
    image: str | None = None       # base64 (data URL or plain)
    voice: bool = False


_MAGIC = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",), "image/gif": (b"GIF87a", b"GIF89a"),
          "image/webp": (b"RIFF",), "application/pdf": (b"%PDF-",)}


def _attachment(image: str) -> list:
    """Checks an uploaded photo or PDF really is what it says before it goes anywhere."""
    m = re.match(r"data:([A-Za-z0-9.+/-]{1,60});base64,", image) if image.startswith("data:") else None
    if image.startswith("data:") and not m:
        raise HTTPException(400, prompts.BAD_FILE_TYPE)
    mime = m.group(1).lower() if m else "image/jpeg"
    raw = image[m.end():] if m else image
    if mime not in IMAGE_TYPES and mime != "application/pdf":
        raise HTTPException(400, prompts.BAD_FILE_TYPE)
    if len(raw) * 3 / 4 > MAX_UPLOAD:
        raise HTTPException(400, prompts.FILE_TOO_LARGE)
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(400, prompts.BAD_FILE_TYPE)
    if not data.startswith(_MAGIC.get(mime, ())):
        raise HTTPException(400, prompts.BAD_FILE_TYPE)
    return [{"type": "document" if mime == "application/pdf" else "image",
             "source": {"type": "base64", "media_type": mime, "data": raw}}]


def _sse(event: str, data) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


@router.post("/api/ask")
def ask(body: AskIn, phone: str = Depends(user)):
    store, bot = CTX["bot"].store, CTX["bot"]
    if bot._rate_limited(phone):
        # Safety comes before the limit: an emergency still gets its card, then the limit applies.
        if safety.looks_like_emergency((body.text or "")[:4000]):
            s = store.get(phone)
            CTX["log_event"](phone, event="emergency_card", mode=s["mode"] or "health", source="app_rate_limited")
            return StreamingResponse(iter([_sse("emergency", {"text": _tr(s, prompts.EMERGENCY)}),
                                           _sse("error", {"message": _tr(s, prompts.RATE_LIMITED)})]),
                                     media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
        raise HTTPException(429, prompts.RATE_LIMITED)
    if body.form is not None and len(json.dumps(body.form)) > 20000:
        raise HTTPException(400, "That form is too long.")
    attachments = _attachment(body.image) if body.image else []
    q: queue.Queue = queue.Queue()

    def work():
        jobs = []
        with store.user_lock(phone):
            s = store.get(phone)
            i18n.use(s)
            try:
                card_sent = False
                first_text = (body.text or "").strip()[:4000]
                if first_text and safety.looks_like_emergency(first_text):  # before setup checks or anything else
                    q.put(("emergency", {"text": _tr(s, prompts.EMERGENCY)}))
                    CTX["log_event"](phone, event="emergency_card", mode=s["mode"] or "health", source="app")
                    card_sent = True
                if not s["mode"]:
                    q.put(("error", {"message": "Please finish setting up first: pick your language and role.", "code": "setup"}))
                    return
                learn, emergency_checked = True, card_sent
                text = body.text.strip()[:4000]
                task = body.task if body.task in prompts.TASKS and prompts.TASKS[body.task]["mode"] == s["mode"] \
                    else None
                s["task"] = task
                if body.follow in prompts.FOLLOW_UP_PROMPTS:
                    text, learn = prompts.FOLLOW_UP_PROMPTS[body.follow], False
                elif body.form and body.form.get("kind") == "danger":
                    text, red = bot.form_request(s, body.form["kind"], body.form.get("data") or {})
                    learn, emergency_checked = False, body.form["kind"] == "danger"
                    if red:
                        q.put(("emergency", {"text": _tr(s, prompts.EMERGENCY)}))
                        CTX["log_event"](phone, event="emergency_card", mode="health", source="app_checklist")
                if not text and not attachments:
                    q.put(("error", {"message": "Type a question first."}))
                    return
                if not emergency_checked and safety.looks_like_emergency(text, s["mode"]):
                    q.put(("emergency", {"text": _tr(s, prompts.EMERGENCY)}))
                    CTX["log_event"](phone, event="emergency_card", mode=s["mode"], source="app")
                if task == "h_register" and attachments and attachments[0]["type"] == "image":
                    q.put(("start", {"task": task}))
                    res = bot.register_app_page(phone, s, base64.b64decode(attachments[0]["source"]["data"]))
                    q.put(("delta", {"t": res["reply"]}))
                    q.put(("done", {"item_id": res["item_id"], "reply": res["reply"], "saved": bool(res["item_id"]),
                                    "task": "h_register", "truncated": False, "title": media.title_of(res["reply"]),
                                    "follow": _follow_view(s, prompts.RETURN_FOLLOW)}))
                    return
                q.put(("start", {"task": s["task"]}))
                out = bot.compose(phone, s, ("[voice note] " if body.voice else "") + text, attachments,
                                  voice=body.voice, learn=learn, channel="app",
                                  on_delta=lambda t: q.put(("delta", {"t": t})))
                jobs = out["jobs"]
                q.put(("done", {"item_id": out["item_id"], "reply": out["reply"], "saved": out["saved"],
                                "task": out["task"], "truncated": out["truncated"],
                                "title": media.title_of(out["reply"]),
                                "follow": _follow_view(s, out["follow"])}))
            except ClaudeBusy:
                q.put(("error", {"message": _tr(s, prompts.BUSY)}))
            except redact.RedactionUnavailable:
                q.put(("error", {"message": _tr(s, prompts.REGISTER_OFF)}))
            except Exception:
                log.exception("App question failed")
                q.put(("error", {"message": _tr(s, prompts.ERROR)}))
            finally:
                i18n.use(None)
                store.save(phone, s)
                q.put(("end", None))
        for job in jobs:
            try:
                job()
            except Exception as e:
                log.warning("Memory update skipped: %s", e)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            try:
                kind, data = q.get(timeout=120)
            except queue.Empty:
                yield _sse("error", {"message": "Timed out"})
                return
            if kind == "end":
                return
            yield _sse(kind, data)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), phone: str = Depends(user)):
    _throttle(f"transcribe:{phone}", 60)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, prompts.VOICE_TOO_LONG)
    s = CTX["bot"].store.get(phone)
    try:
        text = speech.transcribe(data, _lang(s), prompts.ASR_CODES)
    except speech.SpeechUnavailable:
        raise HTTPException(503, _tr(s, prompts.VOICE_UNAVAILABLE))
    except speech.SpeechTooLong:
        raise HTTPException(400, _tr(s, prompts.VOICE_TOO_LONG))
    except speech.SpeechUnclear:
        raise HTTPException(422, _tr(s, prompts.VOICE_UNCLEAR))
    CTX["log_event"](phone, event="voice_transcribed", lang=_lang(s), channel="app")
    return {"text": text}


# ============================================================ library & items

@router.get("/api/library")
def library(phone: str = Depends(user)):
    store = CTX["bot"].store
    s = store.get(phone)
    return {"items": [_item_view(s, it) for it in store.library_all(phone)]}


def _item(phone, item_id):
    it = CTX["bot"].store.item(phone, item_id)
    if not it:
        raise HTTPException(404, "Not found")
    return it


@router.get("/api/items/{item_id}")
def item(item_id: int, phone: str = Depends(user)):
    return _item_view(CTX["bot"].store.get(phone), _item(phone, item_id))


@router.post("/api/items/{item_id}/save")
def save_item(item_id: int, phone: str = Depends(user)):
    _item(phone, item_id)
    CTX["bot"].store.set_saved(phone, item_id, True)
    return {"saved": True}


@router.delete("/api/items/{item_id}/save")
def unsave_item(item_id: int, phone: str = Depends(user)):
    _item(phone, item_id)
    CTX["bot"].store.set_saved(phone, item_id, False)
    return {"saved": False}


@router.get("/api/items/{item_id}/pdf")
def item_pdf(item_id: int, phone: str = Depends(user)):
    it = _item(phone, item_id)
    s = CTX["bot"].store.get(phone)
    data = media.pdf(it["body"], kind=_tr(s, prompts.DOC_KIND.get(it["task"] or "", "Notes")))
    CTX["log_event"](phone, event="item_action", action="pdf", channel="app")
    name = _filename(it["title"] or "akwaaba") + ".pdf"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.get("/api/items/{item_id}/card")
def item_card(item_id: int, phone: str = Depends(user)):
    it = _item(phone, item_id)
    s = CTX["bot"].store.get(phone)
    CTX["log_event"](phone, event="item_action", action="card", channel="app")
    return Response(media.card(it["body"], label=_tr(s, prompts.DOC_KIND.get(it["task"] or "", ""))),
                    media_type="image/png")


@router.get("/api/items/{item_id}/voice")
def item_voice(item_id: int, phone: str = Depends(user)):
    it = _item(phone, item_id)
    s = CTX["bot"].store.get(phone)
    if not voice.available()[0]:
        raise HTTPException(503, _tr(s, prompts.VOICE_NOT_SETUP))
    try:
        data = voice.speak(it["body"], it["lang"] or _lang(s), fmt="m4a")
    except voice.VoiceFailed:
        raise HTTPException(502, _tr(s, prompts.VOICE_REPLY_FAILED))
    CTX["log_event"](phone, event="voice_reply", lang=it["lang"], channel="app")
    return Response(data, media_type="audio/mp4")


# ============================================================ quiz

class QuizIn(BaseModel):
    topic: str = ""
    difficulty: str = "mixed"
    item_id: int | None = None


@router.post("/api/quiz")
def make_quiz(body: QuizIn, phone: str = Depends(user)):
    store, bot = CTX["bot"].store, CTX["bot"]
    s = store.get(phone)
    if not s["mode"]:
        raise HTTPException(400, "Please finish setting up first.")
    source, topic = "", body.topic.strip()[:120]
    if body.item_id:
        it = _item(phone, body.item_id)
        source, topic = it["body"], topic or it["title"]
    if not topic:
        raise HTTPException(400, "Choose a topic first.")
    difficulty = body.difficulty if body.difficulty in ("mixed", "easier", "harder") else "mixed"
    try:
        qz = quiz.generate(bot.claude, bot.model, s["mode"], prompts.LANGUAGES.get(_lang(s), "English"), topic,
                           "", difficulty, source)
    except Exception as e:
        log.error("Quiz generation failed: %s", e)
        qz = None
    if not qz:
        CTX["log_event"](phone, event="quiz_failed", channel="app")
        raise HTTPException(502, _tr(s, prompts.QUIZ_FAILED))
    CTX["log_event"](phone, event="quiz_started", mode=s["mode"], lang=_lang(s), channel="app")
    return {**qz, "topic": topic, "difficulty": difficulty}


class QuizResult(BaseModel):
    score: int
    total: int


@router.post("/api/quiz/result")
def quiz_result(body: QuizResult, phone: str = Depends(user)):
    CTX["log_event"](phone, event="quiz_finished", score=max(0, body.score), total=max(0, body.total), channel="app")
    return {"ok": True}


# ============================================================ nearest facility

@router.get("/api/nearest")
def nearest(lat: float | None = None, lng: float | None = None, place: str = "", phone: str = Depends(user)):
    s = CTX["bot"].store.get(phone)
    name = ""
    try:
        if place.strip():
            hit = places.geocode(place.strip()[:60])
            if not hit:
                return JSONResponse({"place": place, "found": False, "results": []})
            lat, lng, name = hit
        if lat is None or lng is None:
            raise HTTPException(400, "Share your location or type a town.")
        found = places.nearest(lat, lng)
    except HTTPException:
        raise
    except Exception as e:
        log.warning("Nearest facility search failed: %s", e)
        raise HTTPException(502, _tr(s, prompts.NEAR_FAILED))
    CTX["log_event"](phone, event="near_results", count=len(found), channel="app")
    return {"place": name, "found": True, "center": [lat, lng],
            "results": [{**f, "kind": _tr(s, f["kind"]), "km": round(f["km"], 1)} for f in found]}
