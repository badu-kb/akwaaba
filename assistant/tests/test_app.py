"""End-to-end tests with fake Meta, fake Claude and fake speech.  Run:  python -m pytest -q"""

import hashlib
import hmac
import json
import base64
import io
import shutil
import subprocess
import json as json_mod
import os
import re
import time
import sys
import types
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
PHONE = "233200000000"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k, v in {"ANTHROPIC_API_KEY": "x", "META_ACCESS_TOKEN": "tok", "META_PHONE_NUMBER_ID": "123",
                 "META_VERIFY_TOKEN": "vt", "META_APP_SECRET": "sec", "NGROK_DOMAIN": "demo.ngrok-free.dev",
                 "WHATSAPP_DISPLAY_NUMBER": "15551418366",
                 "USAGE_LOG": str(tmp_path / "log.jsonl"), "AKWAABA_DB": str(tmp_path / "db.sqlite")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("KHAYA_API_KEY", raising=False)
    monkeypatch.setenv("TTS_PROVIDER_EN", "khaya")  # no network voices in tests unless a test turns them on
    for m in ("app", "bot", "store", "whatsapp", "prompts", "formatting", "dashboard", "memory",
              "speech", "safety", "stats", "landing"):
        sys.modules.pop(m, None)
    import app as app_module
    from store import Store
    import webapp as _webapp
    _webapp._ip_hits.clear()          # sign-in throttles are per process; start each test fresh
    app_module.store = Store(tmp_path / "db.sqlite")
    app_module.bot.store = app_module.store

    sent, counter, uploads = [], [0], []

    class FakeMeta:
        token_ok = True

        def request(self, method, url, headers=None, json=None, params=None, follow_redirects=False,
                    data=None, files=None, **kw):
            req = httpx.Request(method, url)
            if method == "POST" and url.endswith("/media"):
                uploads.append({"type": (data or {}).get("type"), "name": files["file"][0], "bytes": files["file"][1]})
                return httpx.Response(200, json={"id": f"UP{len(uploads)}"}, request=req)
            if not self.token_ok:
                return httpx.Response(401, json={"error": {"message": "Authentication Error", "code": 190}}, request=req)
            if method == "GET" and url.endswith(("/MEDIA1", "/AUDIO1")):
                return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/x", "file_size": 100,
                                                 "mime_type": "audio/ogg" if "AUDIO" in url else "image/jpeg"}, request=req)
            if method == "GET" and url.endswith("/BIG"):
                return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/y", "file_size": 50_000_000}, request=req)
            if method == "GET":
                return httpx.Response(200, content=b"\xff\xd8fake", json=None, request=req) if False else \
                    httpx.Response(200, content=b"\xff\xd8fake", request=req)
            if json and json.get("type"):
                counter[0] += 1
                sent.append(json)
                return httpx.Response(200, json={"messages": [{"id": f"wamid.out{counter[0]}"}]}, request=req)
            return httpx.Response(200, json={"success": True}, request=req)

    fake = FakeMeta()
    app_module.wa.http = fake
    calls, memory_calls = [], []

    def fake_create(**kw):
        if kw["model"] == os.getenv("MEMORY_MODEL", "claude-haiku-5-5"):
            memory_calls.append(kw)
            prompt = kw["messages"][0]["content"]
            body = ('{"role": "Community health officer", "facility": "CHPS compound", "town_or_region": "Ho", '
                    '"patient_name": "Kofi"}') if "maintain a tiny profile" in prompt else "They prepared malaria talks."
        elif kw.get("tools"):  # register page: a forced record_counts tool call
            calls.append(kw)
            counts = {"opd_total": 40, "opd_under5": 15, "malaria_suspected": 12, "malaria_tested": 10,
                      "malaria_positive": 11, "home_visits": 30}
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="tool_use", name="record_counts",
                                               input={"counts": counts, "unclear": ["referrals", "made_up_key"],
                                                      "page_kind": "OPD register"})],
                usage=types.SimpleNamespace(input_tokens=10, output_tokens=20), stop_reason="tool_use")
        else:
            calls.append(kw)
            last = kw["messages"][-1]["content"]
            text = last[-1]["text"] if isinstance(last, list) else last
            if "multiple-choice quiz" in text:
                qs = [{"q": f"Question {i}?", "options": ["Right", "Wrong", "Other"], "answer": 0,
                       "why": "Because."} for i in range(5)]
                body = json_mod.dumps({"title": "Malaria quiz", "questions": qs})
                return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=body)],
                                             usage=types.SimpleNamespace(input_tokens=10, output_tokens=20),
                                             stop_reason="end_turn")
            body = ("## Talk\n**Point 1:** Sleep under a net.\n- Every night" if "net" in text.lower()
                    else f"Answer to: {text}")
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=body)],
                                     usage=types.SimpleNamespace(input_tokens=10, output_tokens=20),
                                     stop_reason="end_turn")

    app_module.claude.messages.create = fake_create

    class FakeStream:
        def __init__(self, kw):
            self.final = fake_create(**kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def text_stream(self):
            text = self.final.content[0].text
            return [text[i:i + 7] for i in range(0, len(text), 7)]

        def get_final_message(self):
            return self.final

    app_module.claude.messages.stream = lambda **kw: FakeStream(kw)
    app_module.bot.claude = app_module.claude
    from fastapi.testclient import TestClient
    return types.SimpleNamespace(app=app_module, client=TestClient(app_module.app), sent=sent, calls=calls,
                                 memory_calls=memory_calls, meta=fake, tmp=tmp_path, uploads=uploads)


_n = [0]


def post(env, msg, secret=b"sec", phone=PHONE):
    _n[0] += 1
    msg = {"from": phone, "id": f"wamid.in{_n[0]}", **msg}
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}).encode()
    sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return env.client.post("/whatsapp", content=body, headers={"x-hub-signature-256": sig})


def text(t):
    return {"type": "text", "text": {"body": t}}


def tap(bid):
    return {"type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": bid, "title": bid}}}


def pick(rid):
    return {"type": "interactive", "interactive": {"type": "list_reply", "list_reply": {"id": rid, "title": rid}}}


def body_of(m):
    if m["type"] == "text":
        return m["text"]["body"]
    if m["type"] == "interactive":
        return m["interactive"]["body"]["text"]
    return ""


def bodies(env):
    return [body_of(m) for m in env.sent if m["type"] != "reaction"]


def last_interactive(env):
    return next(m for m in reversed(env.sent) if m["type"] == "interactive")["interactive"]


def button_ids(env):
    i = last_interactive(env)
    return [b["reply"]["id"] for b in i["action"].get("buttons", [])]


def list_ids(env):
    i = last_interactive(env)
    return [r["id"] for sec in i["action"].get("sections", []) for r in sec["rows"]]


def events(env):
    return [json.loads(l) for l in Path(os.environ["USAGE_LOG"]).read_text().splitlines()]


def onboard(env, lang="lang_en", detail="hr_cho"):
    post(env, text("hi")); post(env, tap(lang)); post(env, pick(detail))


# --------------------------------------------------------------------------- WhatsApp limits

def test_ui_text_respects_whatsapp_limits():
    import prompts
    for group in (prompts.LANGUAGE_BUTTONS, prompts.FEEDBACK_BUTTONS,
                  prompts.MY_DATA_BUTTONS, prompts.FORGET_BUTTONS, *prompts.GENERAL_FOLLOW.values(),
                  *(t["follow"] for t in prompts.TASKS.values())):
        for _, title in group:
            assert len(title) <= 20, title
    for group in (prompts.LIB_BUTTONS, prompts.QUIZ_END_BUTTONS, prompts.EMERGENCY_NEAR_BUTTON, prompts.OPEN_BUTTONS):
        for _, title in group:
            assert len(title) <= 20, title
    for rows in (prompts.HEALTH_ROLE_ROWS, prompts.MENU_EXTRA_ROWS, prompts.SETTINGS_ROWS,
                 [(0, *r) for r in (prompts.ROW_LISTEN, prompts.ROW_PDF, prompts.ROW_CARD, prompts.ROW_SAVE,
                                    prompts.ROW_UNSAVE, prompts.ROW_QUIZIT, prompts.ROW_NEAR, prompts.ROW_MENU,
                                    prompts.LIBRARY_MORE)],
                 [(k, t["title"], t["desc"]) for k, t in prompts.TASKS.items()]):
        for _, title, desc in rows:
            assert len(title) <= 24 and len(desc) <= 72, title
    for f in [*prompts.ANSWER_FOOTER.values(), prompts.WELCOME_FOOTER]:
        assert len(f) <= 60
    for mode in ("health",):
        assert all(prompts.TASKS[t]["mode"] == mode for t in prompts.MENU_ORDER[mode])
        n = len(prompts.MENU_ORDER[mode]) + len(prompts.MENU_EXTRA_ROWS)
        assert n <= 10  # WhatsApp list maximum
    for t in prompts.TASKS.values():
        for bid, _ in t["follow"]:
            assert bid in prompts.FOLLOW_UP_PROMPTS or bid in prompts.RESET_BUTTONS


def test_no_teacher_left_in_the_assistant():
    import prompts
    assert set(prompts.MODE_PROMPTS) == {"health"} and all(t["mode"] == "health" for t in prompts.TASKS.values())
    words = " ".join(str(v) for k, v in vars(prompts).items() if k.isupper()).lower()
    assert "teacher" not in words and "pupil" not in words and "lesson" not in words


# --------------------------------------------------------------------------- security

def test_webhook_verification_and_forged_requests(env):
    ok = env.client.get("/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"})
    assert ok.text == "42"
    assert post(env, text("hi"), secret=b"wrong").status_code == 403 and env.sent == []


# --------------------------------------------------------------------------- onboarding

def test_onboarding_flow(env):
    post(env, text("hi"))
    welcome = last_interactive(env)
    assert welcome["header"]["image"]["link"] == "https://demo.ngrok-free.dev/static/welcome.png"
    assert "community health work" in welcome["body"]["text"] and "teach" not in welcome["body"]["text"].lower()
    assert button_ids(env) == ["lang_en", "lang_tw", "lang_ee"]
    post(env, tap("lang_tw"))
    assert list_ids(env)[0] == "hr_cho" and "Anammɔn 2 wɔ 2 mu" in last_interactive(env)["body"]["text"]
    post(env, pick("hr_cho"))
    assert "Mpɔtam apɔmuden ɔfesa" in bodies(env)[-2] and "Twi" in bodies(env)[-2]
    assert button_ids(env) == ["h_talk", "h_register", "h_followup"]  # first-use tour
    post(env, text("menu"))
    assert "h_talk" in list_ids(env) and "m_library" in list_ids(env) and "m_settings" in list_ids(env)
    assert "ch_role" not in list_ids(env)
    assert env.app.store.get(PHONE)["mode"] == "health"


def test_question_before_setup_is_answered_after(env):
    post(env, text("Give me a talk on mosquito nets"))
    assert "which language" in bodies(env)[-1].lower()
    post(env, tap("lang_en")); post(env, pick("hr_skip"))
    assert any("Sleep under a net" in b for b in bodies(env))


def test_welcome_back_shows_menu(env, tmp_path):
    onboard(env)
    from store import Store
    env.app.bot.store = Store(tmp_path / "db.sqlite")  # simulate a restart
    post(env, text("Hello"))
    assert "Welcome back" in bodies(env)[-1] and "Community health officer" in bodies(env)[-1]
    assert "h_talk" in list_ids(env)


def test_old_teacher_accounts_move_to_health(env):
    onboard(env)
    s = env.app.store.get(PHONE)
    s.update(mode="teacher", task="t_lesson", history=[{"role": "user", "content": "fractions"}])
    s["profile"] = {"class_level": "Basic 4", "town_or_region": "Ho"}
    env.app.store.save(PHONE, s)
    s = env.app.store.get(PHONE)
    assert s["mode"] == "health" and s["task"] is None and s["history"] == []
    assert s["profile"] == {"town_or_region": "Ho"}
    post(env, tap("role_teacher"))  # an old button still sitting in their chat
    assert "h_talk" in list_ids(env)


# --------------------------------------------------------------------------- tasks & answers

def test_guided_task_shapes_prompt_and_buttons(env):
    onboard(env, lang="lang_tw")
    post(env, pick("h_talk"))
    assert "Asɛmti bɛn" in bodies(env)[-1]
    post(env, text("Mosquito nets"))
    system = env.calls[-1]["system"]
    assert "CURRENT TASK" in system and "Community talk" in system and "Asante Twi" in system
    assert "role: Community health officer" in system and "community health workers" in system
    ans = bodies(env)[-1]
    assert "*Talk*" in ans and "**" not in ans and "• Every night" in ans
    assert button_ids(env)[:2] == ["f_group_quiz", "f_simpler"] and button_ids(env)[2].startswith("more:")
    post(env, tap("f_group_quiz"))
    assert "questions" in env.calls[-1]["messages"][-1]["content"][-1]["text"]


def test_new_topic_returns_to_menu(env):
    onboard(env)
    post(env, pick("h_myth")); post(env, text("Vaccines make babies sick"))
    post(env, tap("f_new"))
    assert "h_myth" in list_ids(env)


def test_health_emergency_fast_path(env):
    onboard(env, lang="lang_ee", detail="hr_chv")
    post(env, text("A 2 year old had a fit and has a fever"))
    texts = bodies(env)
    assert "may be an emergency" in texts[-2] and "112" in texts[-2]
    assert "112" in last_interactive(env)["footer"]["text"]


def test_photo_gets_reaction_and_goes_to_claude(env):
    onboard(env)
    post(env, {"type": "image", "image": {"id": "MEDIA1", "mime_type": "image/jpeg", "caption": "Summarise this register"}})
    assert any(m["type"] == "reaction" and m["reaction"]["emoji"] == "\U0001F440" for m in env.sent)
    content = env.calls[-1]["messages"][-1]["content"]
    assert content[0]["type"] == "image" and content[-1]["text"] == "Summarise this register"


def test_bad_files(env):
    onboard(env)
    post(env, {"type": "image", "image": {"id": "BIG", "mime_type": "image/jpeg"}})
    assert "too large" in bodies(env)[-1]
    post(env, {"type": "document", "document": {"id": "MEDIA1", "mime_type": "application/zip"}})
    assert "not that kind of file" in bodies(env)[-1]


# --------------------------------------------------------------------------- voice

def test_voice_note_without_speech_setup(env):
    onboard(env)
    post(env, {"type": "audio", "audio": {"id": "AUDIO1", "mime_type": "audio/ogg; codecs=opus"}})
    assert "can't listen to voice notes right now" in bodies(env)[-1]


def test_voice_note_transcribed_and_answered(env, monkeypatch):
    onboard(env, lang="lang_tw")
    import speech
    monkeypatch.setattr(speech, "available", lambda: (True, "ready"))
    seen = {}

    def fake_transcribe(audio, lang, codes):
        seen["lang"] = codes[lang]
        return "A talk on handwashing for mothers"
    monkeypatch.setattr(speech, "transcribe", fake_transcribe)
    post(env, {"type": "audio", "audio": {"id": "AUDIO1", "mime_type": "audio/ogg"}})
    assert seen["lang"] == "twi"
    assert any("Metee sɛ" in b and "handwashing" in b for b in bodies(env))  # transcript itself never translated
    assert env.calls[-1]["messages"][-1]["content"][-1]["text"].startswith("[voice note]")
    assert events(env)[-2]["event"] in ("answer", "voice_transcribed") or any(
        e["event"] == "answer" and e.get("voice") for e in events(env))


def test_unclear_voice_note(env, monkeypatch):
    onboard(env)
    import speech
    monkeypatch.setattr(speech, "available", lambda: (True, "ready"))

    def bad(*a):
        raise speech.SpeechUnclear("noise")
    monkeypatch.setattr(speech, "transcribe", bad)
    post(env, {"type": "audio", "audio": {"id": "AUDIO1", "mime_type": "audio/ogg"}})
    assert "couldn't make out" in bodies(env)[-1]


# --------------------------------------------------------------------------- memory

def test_profile_is_learned_filtered_and_used(env):
    onboard(env)
    post(env, text("I run the CHPS compound near Ho, and a patient called Kofi missed his follow-up"))
    profile = env.app.store.get(PHONE)["profile"]
    assert profile["facility"] == "CHPS compound" and profile["town_or_region"] == "Ho"
    assert "patient_name" not in profile  # not an allowed key: dropped in code
    post(env, text("Ideas for a durbar talk?"))
    assert "facility: CHPS compound" in env.calls[-1]["system"]


def test_long_conversation_is_summarised(env, monkeypatch):
    onboard(env)
    import memory
    monkeypatch.setattr(memory, "RECENT_MESSAGES", 6)
    monkeypatch.setattr(memory, "FOLD_COUNT", 4)
    for i in range(5):
        post(env, text(f"q{i}"))
    s = env.app.store.get(PHONE)
    assert s["summary"] == "They prepared malaria talks." and len(s["history"]) <= 8
    post(env, text("next"))
    assert "EARLIER CONVERSATION" in env.calls[-1]["system"]


def test_my_data_and_forget_me(env):
    onboard(env)
    post(env, text("MY DATA"))
    assert "What I remember" in bodies(env)[-1] and "Community health officer" in bodies(env)[-1]
    post(env, tap("fm_ask")); post(env, tap("fm_yes"))
    assert "deleted everything" in bodies(env)[-1]
    s = env.app.store.get(PHONE)
    assert s["new"] and s["mode"] is None and s["profile"] == {}


# --------------------------------------------------------------------------- feedback, growth, reliability

def test_reaction_to_answer_counts_as_rating(env):
    onboard(env)
    post(env, text("Explain oral rehydration"))
    answer_id = f"wamid.out{len(env.sent)}"
    post(env, {"type": "reaction", "reaction": {"message_id": answer_id, "emoji": "\U0001F44D"}})
    st = env.client.get("/stats", params={"key": __import__("ids").dashboard_key()}).json()
    assert st["ratings"] == 1 and st["satisfaction_pct"] == 100


def test_feedback_prompt_every_few_answers(env):
    onboard(env)
    for i in range(6):
        post(env, text(f"question {i}"))
    assert "was that answer useful" in bodies(env)[-1]


def test_share_is_one_tap_forward(env):
    onboard(env)
    post(env, pick("m_share"))
    i = last_interactive(env)
    assert i["type"] == "cta_url" and i["action"]["parameters"]["display_text"] == "Share Akwaaba"
    from urllib.parse import unquote
    url = unquote(i["action"]["parameters"]["url"])
    assert url.startswith("https://wa.me/?text=") and "https://wa.me/15551418366?text=Hi" in url


def test_duplicate_delivery_ignored(env):
    onboard(env)
    msg = {"from": PHONE, "id": "wamid.dup", **text("Same question")}
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"sec", body, hashlib.sha256).hexdigest()
    before = len(env.calls)
    for _ in range(3):
        env.client.post("/whatsapp", content=body, headers={"x-hub-signature-256": sig})
    assert len(env.calls) == before + 1


def test_rate_limit(env):
    env.app.bot.rate_limit_per_hour = 3
    for i in range(4):
        post(env, text(f"msg {i}"))
    assert "lot of messages" in bodies(env)[-1]


def test_truncated_reply_trimmed(env):
    onboard(env)
    env.app.bot.claude = types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text="First complete sentence. Second one is cut mid-wo")],
        usage=types.SimpleNamespace(input_tokens=1, output_tokens=3000), stop_reason="max_tokens")))
    post(env, text("Long"))
    ans = bodies(env)[-1]
    assert ans.startswith("First complete sentence.") and "mid-wo" not in ans and "Reply MORE" in ans


def test_expired_token_does_not_crash(env):
    onboard(env)
    env.meta.token_ok = False
    assert post(env, text("anything")).status_code == 200
    assert events(env)[-1] == {**events(env)[-1], "event": "error", "kind": "token"}


def test_pages(env):
    onboard(env)
    post(env, text("A talk on mosquito nets"))
    assert "People reached" in env.client.get("/dashboard", params={"key": __import__("ids").dashboard_key()}).text
    land = env.client.get("/").text
    assert "wa.me/15551418366" in land and "<svg" in land and "teacher" not in land.lower()
    assert "FORGET ME" in env.client.get("/privacy").text
    assert env.client.get("/static/welcome.png").status_code == 200


def test_long_text_split_cleanly():
    from whatsapp import split_text
    t = ("Sentence number one is here. " * 200).strip()
    parts = split_text(t, 3500)
    assert all(len(p) <= 3500 for p in parts) and "".join(parts).replace(" ", "") == t.replace(" ", "")


def test_role_can_be_changed_from_anywhere(env):
    onboard(env)
    post(env, pick("h_visit"))               # inside a task
    post(env, text("role"))
    assert list_ids(env)[0] == "hr_cho"
    post(env, pick("hr_mw"))
    s = env.app.store.get(PHONE)
    assert s["mode"] == "health" and s["profile"].get("role") == "Midwife"
    post(env, pick("m_settings"))
    post(env, pick("ch_detail"))
    assert list_ids(env)[0] == "hr_cho"


def test_main_menu_words(env):
    onboard(env)
    for word in ("start over", "Main menu", "home", "back", "switch"):
        post(env, text(word))
        if word != "switch":
            assert "h_talk" in list_ids(env), word


# --------------------------------------------------------------------------- native forms (WhatsApp Flows)

def use_flows(env):
    env.app.bot.flows = {"setup": {"id": "F1", "status": "DRAFT"}, "danger": {"id": "F3", "status": "PUBLISHED"}}


def submit(env, token, **fields):
    data = json.dumps({"flow_token": f"{token}:abcd", **fields})
    return post(env, {"type": "interactive", "interactive": {"type": "nfm_reply", "nfm_reply": {
        "name": "flow", "body": "Sent", "response_json": data}}})


def flow_messages(env):
    return [m["interactive"] for m in env.sent if m["type"] == "interactive" and m["interactive"]["type"] == "flow"]


def test_welcome_opens_setup_form(env):
    use_flows(env)
    post(env, text("hi"))
    assert env.sent[0]["type"] == "image"  # branded card first
    f = flow_messages(env)[-1]["action"]["parameters"]
    assert f["flow_id"] == "F1" and f["mode"] == "draft" and f["flow_token"].startswith("setup:")
    submit(env, "setup", lang="ee", detail="hr_mw", town="Ho")
    s = env.app.store.get(PHONE)
    assert s["mode"] == "health" and s["lang"] == "ee" and s["profile"] == {"role": "Midwife", "town_or_region": "Ho"}
    assert "Health worker" in bodies(env)[-2] and "h_talk" in button_ids(env)


def test_setup_form_answers_pending_question(env):
    use_flows(env)
    post(env, text("Give me a talk on mosquito nets"))
    submit(env, "setup", lang="en", detail="hr_chv", town="")
    assert any("Sleep under a net" in b for b in bodies(env))


def test_older_setup_form_still_works_and_ignores_role(env):
    use_flows(env)
    post(env, text("hi"))
    submit(env, "onboarding", role="teacher", lang="tw", detail="lvl_lower", town="Ho")
    s = env.app.store.get(PHONE)
    assert s["mode"] == "health" and s["lang"] == "tw" and "class_level" not in s["profile"]


def test_danger_checklist_red_flag_is_instant(env):
    use_flows(env)
    onboard(env, lang="lang_en", detail="hr_chv")
    post(env, pick("h_danger"))
    assert flow_messages(env)[-1]["action"]["parameters"]["flow_cta"] == "Open checklist"
    submit(env, "danger", who="child", signs=["convulsions", "high_fever"], days="2", notes="")
    texts = bodies(env)
    assert "may be an emergency" in texts[-2]
    assert sum("may be an emergency" in t for t in texts) == 1  # not doubled by the text check
    assert "Convulsions or fits" in env.calls[-1]["messages"][-1]["content"][-1]["text"]


def test_danger_checklist_no_red_flag(env):
    use_flows(env)
    onboard(env, lang="lang_en", detail="hr_chv")
    submit(env, "danger", who="older", signs=["cough_2w"], days="20", notes="")
    assert not any("may be an emergency" in b for b in bodies(env))


def test_flow_failure_falls_back_to_buttons(env):
    use_flows(env)
    env.app.bot.flows["setup"]["id"] = "BROKEN"
    orig = env.meta.request

    def failing(method, url, headers=None, json=None, **kw):
        if json and json.get("type") == "interactive" and json["interactive"]["type"] == "flow":
            return httpx.Response(400, json={"error": {"message": "Flow not found", "code": 100}},
                                  request=httpx.Request(method, url))
        return orig(method, url, headers=headers, json=json, **kw)
    env.meta.request = failing
    post(env, text("hi"))
    assert button_ids(env) == ["lang_en", "lang_tw", "lang_ee"]


def test_example_topics_one_tap(env):
    onboard(env)
    post(env, pick("h_talk"))
    assert button_ids(env) == ["ex:h_talk:0", "ex:h_talk:1", "ex:h_talk:2"]
    post(env, tap("ex:h_talk:1"))
    assert env.calls[-1]["messages"][-1]["content"][-1]["text"] == "Community talk: Handwashing"


def test_onboarding_shows_progress_without_forms(env):
    post(env, text("hi")); post(env, tap("lang_en"))
    assert "Step 2 of 2" in last_interactive(env)["body"]["text"]



def test_flow_ids_from_env(monkeypatch):
    monkeypatch.setenv("FLOW_SETUP_ID", "777")
    monkeypatch.setenv("FLOW_ONBOARDING_ID", "OLD")
    monkeypatch.setenv("FLOWS_STATUS", "draft")
    sys.modules.pop("bot", None)
    import bot
    reg, labels = bot.load_flows()
    assert reg["setup"] == {"id": "777", "status": "DRAFT"} and labels["convulsions"] == "Convulsions or fits"
    assert "onboarding" not in reg and "lesson" not in reg and labels["hr_cho"] == "Community health officer"


# ------------------------------------------------------------------ interface language

def _ui_strings():
    import prompts as P
    out = [P.MENU_BODY, P.MENU_BUTTON, P.MENU_MORE_SECTION, P.HELP, P.SHARE_BODY, P.WELCOME, P.PICK_LANG_FIRST, P.MY_DATA,
           P.SHARE_BUTTON, P.SHARE_INTRO, P.MY_DATA_EMPTY, P.FORGET_CONFIRM, P.FORGOTTEN, P.FEEDBACK_ASK,
           P.FEEDBACK_THANKS_YES, P.FEEDBACK_THANKS_NO, P.EMERGENCY, P.VOICE_UNAVAILABLE, P.VOICE_UNCLEAR,
           P.VOICE_TOO_LONG, P.UNSUPPORTED_TYPE, P.FILE_TOO_LARGE, P.BAD_FILE_TYPE, P.RATE_LIMITED, P.BUSY,
           P.ERROR, P.HEALTH_ROLE_PROMPT, P.LANGUAGE_PROMPT, *P.ANSWER_FOOTER.values(),
           *P.MENU_SECTION.values(), P.VOICE_PREF_ASK, P.VOICE_PREF_LISTEN, P.VOICE_PREF_READ, P.TOUR_BODY,
           *[l for _, l in P.VOICE_PREF_BUTTONS]]
    out += [t for _, t in P.MY_DATA_BUTTONS + P.FORGET_BUTTONS + P.FEEDBACK_BUTTONS]
    out += [x for _, t, d in P.MENU_EXTRA_ROWS + P.HEALTH_ROLE_ROWS for x in (t, d)]
    for t in P.TASKS.values():
        out += [t["title"], t["desc"], t["ask"], *t.get("examples", []), *[l for _, l in t["follow"]]]
        if t.get("form"):
            out += [t["form"]["body"], t["form"]["cta"]]
    out += [l for v in P.GENERAL_FOLLOW.values() for _, l in v]
    out += [x for _, t, d in P.SETTINGS_ROWS for x in (t, d)]
    out += [x for r in (P.ROW_LISTEN, P.ROW_PDF, P.ROW_CARD, P.ROW_SAVE, P.ROW_UNSAVE, P.ROW_QUIZIT, P.ROW_NEAR,
                        P.ROW_MENU, P.LIBRARY_MORE) for x in r]
    out += [t for _, t in P.LIB_BUTTONS + P.QUIZ_END_BUTTONS + P.EMERGENCY_NEAR_BUTTON + P.OPEN_BUTTONS]
    out += [P.SETTINGS_LIST_BODY, P.SETTINGS_LIST_BUTTON, P.SETTINGS_SECTION, P.ON, P.OFF, P.MORE_LABEL,
            P.MORE_BODY, P.MORE_SECTION, P.MORE_NEXT, P.ITEM_GONE, P.SAVED_FOOTER, P.PDF_CAPTION, P.PDF_FAILED,
            P.VOICE_NOT_SETUP, P.VOICE_REPLY_FAILED, P.VOICE_ON, P.VOICE_OFF, P.LIB_SAVED, P.LIB_REMOVED,
            P.LIBRARY_BODY, P.LIBRARY_BUTTON, P.LIBRARY_SECTION, P.LIBRARY_EMPTY, P.QUIZ_MAKING, P.QUIZ_FAILED,
            P.QUIZ_LABEL, P.QUIZ_RIGHT, P.QUIZ_WRONG, P.QUIZ_DONE, P.QUIZ_REVIEW, P.QUIZ_PERFECT, P.QUIZ_GREAT,
            P.QUIZ_GOOD, P.QUIZ_TRY, P.QUIZ_STALE, P.NEAR_ASK, P.NEAR_RESULT_TITLE, P.NEAR_LINE, P.NEAR_NOTE,
            P.NEAR_EMERGENCY, P.NEAR_NONE, P.NEAR_FAILED, P.NEAR_PLACE_TITLE, P.NEAR_PLACE_UNKNOWN, *P.DOC_KIND.values()]
    out += [P.REGISTER_APP_MORE, P.REGISTER_ASK, P.REGISTER_READING, P.REGISTER_PAGE_OK, P.REGISTER_UNCLEAR,
            P.REGISTER_EMPTY_PAGE, P.REGISTER_OFF, P.REGISTER_NO_PAGES, P.REGISTER_TOO_MANY, P.REGISTER_PHOTO_ONLY,
            P.REGISTER_CANCELLED, P.REGISTER_PDF_CAPTION, P.REGISTER_FAILED, *[l for _, l in P.REGISTER_BUTTONS + P.RETURN_FOLLOW]]
    out += [P.FU_HOME_BODY, P.FU_SUMMARY, P.FU_EMPTY_SUMMARY, P.FU_BUTTON, P.FU_SECTION, *P.FU_ASK.values(),
            *P.FU_ERRORS.values(), P.FU_ADDED, P.FU_ASSUMED.strip("\n"), P.FU_NOTHING_DUE, P.FU_MARK_BODY, P.FU_MARK_BUTTON,
            P.FU_MARK_SECTION, P.FU_MARKED, P.FU_NEXT, P.FU_FINISHED, P.FU_ALL_BODY, P.FU_ALL_BUTTON, P.FU_ALL_SECTION,
            P.FU_REMOVED, P.FU_GONE, P.FU_REMIND_INTRO, *P.FU_REMIND.values(), P.FU_DIGEST_HEAD, P.STOCK_UNKNOWN,
            P.STOCK_NONE, *[x for _, t, d in P.FU_ROWS for x in (t, d)],
            *[l for _, l in P.FU_ADDED_BUTTONS + P.FU_VIEW_BUTTONS + P.STOCK_FOLLOW]]
    out += [P.TEAM_LEAD_BODY, *P.TEAM_WHO.values(), P.TEAM_NO_MEMBERS, P.TEAM_MEMBERS_HEAD, P.TEAM_MEMBER_OF,
            P.TEAM_NOT_MEMBER, P.TEAM_JOINED, P.TEAM_NEW_MEMBER, P.TEAM_BAD_CODE, P.TEAM_OWN_CODE, P.TEAM_LEFT, P.INBOX_HEAD,
            P.VISIT_BODY, P.VISIT_BUTTON, P.VISIT_SECTION, P.VISIT_ASK, P.VISIT_LOGGED, P.VISIT_DANGER,
            *[x for _, t, d in P.VISIT_TYPES for x in (t, d)], P.REF_BODY, P.REF_BUTTON, P.REF_SECTION,
            *[x for _, t, d, _u in P.REF_REASONS for x in (t, d)], P.REF_LIST_ROW[1], P.REF_LIST_ROW[2], P.REF_ASK,
            P.REF_SENT, P.REF_SENT_LEAD, P.REF_NO_TEAM, P.REF_TO_LEAD, P.REF_ARRIVED, P.REF_ARRIVED_OK, P.REF_WAIT_OK,
            P.REF_CHECK, P.REF_NOT_ARRIVED, P.REF_THANKS, P.REF_NONE_OPEN, P.REF_OPEN_HEAD, P.REF_GONE, P.ALERT_NONE,
            P.ALERT_TO_LEAD, P.ALERT_HINT, P.NOTICE_ASK, P.NOTICE_EMPTY, P.NOTICE_FROM, P.NOTICE_SENT,
            P.TEAM_OVERVIEW_EMPTY, P.TEAM_DIGEST_HEAD, "Team overview",
            *[l for _, l in P.TEAM_BUTTONS + P.VISIT_BUTTONS + P.VISIT_DANGER_BUTTONS + P.REF_LEAD_BUTTONS
              + P.REF_CHECK_BUTTONS + P.ALERT_HINT_BUTTONS + P.ALERT_FOLLOW]]
    return out


def test_twi_covers_every_interface_string_within_whatsapp_limits(env):
    import i18n
    import prompts as P
    assert i18n.untranslated(_ui_strings(), "tw") == []
    for _, t in P.MY_DATA_BUTTONS + P.FORGET_BUTTONS + P.FEEDBACK_BUTTONS:
        assert len(i18n.tr(t, "tw")) <= 20, t
    for t in P.TASKS.values():
        for label in [*t.get("examples", []), *[l for _, l in t["follow"]]]:
            assert len(i18n.tr(label, "tw")) <= 20, label
        assert len(i18n.tr(t["title"], "tw")) <= 24 and len(i18n.tr(t["desc"], "tw")) <= 72, t["title"]
    for _, t, d in P.MENU_EXTRA_ROWS + P.HEALTH_ROLE_ROWS:
        assert len(i18n.tr(t, "tw")) <= 24 and len(i18n.tr(d, "tw")) <= 72, t
    # Safety: the emergency card always keeps the English too.
    assert P.EMERGENCY in i18n.tr(P.EMERGENCY, "tw")


def test_menus_follow_the_chosen_language(env):
    import prompts as P
    onboard(env, lang="lang_tw")
    joined = "\n".join(bodies(env))
    assert "Biribiara asiesie" in joined and "You're all set" not in joined
    assert "Mia baako na sɔ hwɛ" in last_interactive(env)["body"]["text"]
    post(env, text("menu"))
    menu = last_interactive(env)
    assert menu["action"]["button"] == "Bue menu"
    titles = [r["title"] for sec in menu["action"]["sections"] for r in sec["rows"]]
    assert "\U0001F4E2 Mpɔtam nkɔmmɔ" in titles
    post(env, text("Handwashing for mothers"))
    i = last_interactive(env)
    # Claude's answer is untouched, the buttons and footer are in Twi
    assert [b["reply"]["title"] for b in i["action"]["buttons"]][0] == "Asiane nsɛnkyerɛnne"
    post(env, text("EN"))
    assert bodies(env)[-1] == "Done ✅ I'll reply in *English*."
    post(env, text("menu"))
    assert last_interactive(env)["action"]["button"] == P.MENU_BUTTON


def test_claude_answers_are_never_rewritten(env):
    import i18n
    answer = "*\U0001F99F Nets*\nSleep under a net.\n\nMore"
    assert i18n.tr(answer, "tw") == answer



# ------------------------------------------------------------------ rich features

def more_id(env):
    return next(b for b in button_ids(env) if b.startswith("more:"))


def test_more_menu_pdf_card_and_library(env):
    onboard(env)
    post(env, pick("h_talk"))
    post(env, text("Mosquito nets"))
    mid = more_id(env)
    post(env, tap(mid))
    ids = list_ids(env)
    item = mid.split(":")[1]
    assert f"pdf:{item}" in ids and f"card:{item}" in ids and f"unsave:{item}" in ids  # health talks auto-saved
    assert f"say:{item}" not in ids  # voice not set up in tests
    post(env, pick(f"pdf:{item}"))
    doc = env.sent[-1]
    assert doc["type"] == "document" and doc["document"]["filename"].endswith(".pdf")
    assert env.uploads[-1]["bytes"][:4] == b"%PDF"
    post(env, pick(f"card:{item}"))
    assert env.sent[-1]["type"] == "image" and env.uploads[-1]["bytes"][:4] == b"\x89PNG"
    post(env, text("menu"))
    post(env, pick("m_library"))
    assert f"open:{item}" in list_ids(env)
    post(env, pick(f"open:{item}"))
    assert f"pdf:{item}" in button_ids(env)
    post(env, tap(f"unsave:{item}"))
    post(env, pick("m_library"))
    assert "empty" in bodies(env)[-1]


def test_plain_answers_can_be_saved(env):
    onboard(env)
    post(env, text("How do I explain exclusive breastfeeding?"))
    item = more_id(env).split(":")[1]
    post(env, pick(f"save:{item}"))
    assert "Saved" in bodies(env)[-1]
    assert env.app.store.library_count(PHONE) == 1


def test_quiz_game_plays_through(env):
    onboard(env)
    post(env, pick("h_play"))
    post(env, text("Malaria"))
    assert "Making a quiz" in bodies(env)[-2]
    i = last_interactive(env)
    assert i["header"]["text"].endswith("Malaria quiz") and "1/5" in i["body"]["text"]
    assert button_ids(env) == ["qa:0:0", "qa:0:1", "qa:0:2"]
    post(env, tap("qa:0:0"))
    assert "Correct" in bodies(env)[-1] and "2/5" in bodies(env)[-1]
    post(env, tap("qa:0:1"))  # stale tap
    assert "already been answered" in bodies(env)[-1]
    post(env, tap("qa:1:1"))
    assert "Not quite" in bodies(env)[-1] and "A. Right" in bodies(env)[-1]
    for n in (2, 3, 4):
        post(env, tap(f"qa:{n}:0"))
    assert any(m["type"] == "image" for m in env.sent[-3:])  # results card
    assert "4/5" in bodies(env)[-1] and "To review" in bodies(env)[-1]
    assert button_ids(env) == ["qz_again", "qz_harder", "m_menu"]
    finished = [e for e in events(env) if e["event"] == "quiz_finished"][-1]
    assert finished["score"] == 4 and finished["total"] == 5
    post(env, tap("qz_harder"))
    assert "harder" in env.calls[-1]["messages"][0]["content"].lower()


def test_quiz_in_twi_uses_twi_labels(env):
    onboard(env, lang="lang_tw")
    post(env, text("quiz"))
    post(env, tap("ex:h_play:0"))
    assert "Twi" in env.calls[-1]["messages"][0]["content"]
    post(env, tap("qa:0:0"))
    assert "Ɛyɛ nokorɛ" in bodies(env)[-1] and "Asɛmmisa 2/5" in bodies(env)[-1]


def test_nearest_facility_from_shared_location(env, monkeypatch):
    onboard(env, detail="hr_chv")
    import places
    seen = {}

    def fake_nearest(lat, lng, n=3, http=None):
        seen["at"] = (lat, lng)
        return [{"name": "Ho Teaching Hospital", "kind": "Hospital", "lat": 6.6, "lng": 0.47, "address": "",
                 "phone": "", "km": 1.234},
                {"name": "Kpodzi CHPS", "kind": "Clinic", "lat": 6.61, "lng": 0.48, "address": "Kpodzi",
                 "phone": "0302000000", "km": 2.5}]
    monkeypatch.setattr(places, "nearest", fake_nearest)
    post(env, pick("h_near"))
    assert last_interactive(env)["type"] == "location_request_message"
    post(env, {"type": "location", "location": {"latitude": 6.6, "longitude": 0.47}})
    assert seen["at"] == (6.6, 0.47)
    pins = [m for m in env.sent if m["type"] == "location"]
    assert [p["location"]["name"] for p in pins] == ["Ho Teaching Hospital", "Kpodzi CHPS"]
    assert "1.2 km" in bodies(env)[-1] and "112" in bodies(env)[-1]
    # location is never stored
    assert "6.6" not in json_mod.dumps(env.app.store.get(PHONE), default=str)


def test_emergency_card_offers_nearest_facility(env):
    onboard(env, detail="hr_chv")
    post(env, text("the baby is having convulsions"))
    em = next(m for m in env.sent if m["type"] == "interactive" and "emergency" in body_of(m).lower())
    assert em["interactive"]["action"]["buttons"][0]["reply"]["id"] == "m_near"


def test_voice_replies_listen_and_always_on(env, monkeypatch):
    onboard(env, lang="lang_tw")
    import voice
    spoken = []
    monkeypatch.setattr(voice, "available", lambda *a: (True, "ready"))
    monkeypatch.setattr(voice, "speak", lambda text, lang: spoken.append((text, lang)) or b"OggS-fake")
    post(env, text("Explain oral rehydration"))
    item = more_id(env).split(":")[1]
    post(env, tap(f"more:{item}"))
    assert f"say:{item}" in list_ids(env)
    post(env, pick(f"say:{item}"))
    assert env.sent[-1]["type"] == "audio" and env.sent[-1]["audio"]["voice"] is True
    assert spoken[-1][1] == "tw" and env.uploads[-1]["type"] == "audio/ogg"
    post(env, text("voice on"))
    assert "wɔ so" in bodies(env)[-1]
    n = len(spoken)
    post(env, text("Explain zinc for diarrhoea"))
    assert len(spoken) == n + 1 and env.sent[-1]["type"] == "audio"


def test_settings_list(env):
    onboard(env)
    post(env, pick("m_settings"))
    i = last_interactive(env)
    assert i["type"] == "list" and "Voice replies: *Off*" in i["body"]["text"]
    assert {"ch_lang", "ch_detail", "m_voice", "m_share", "m_data", "help"} <= set(list_ids(env))
    assert "ch_role" not in list_ids(env)


def test_media_renders_twi_text():
    import media
    body = "*\U0001F4DD Ntontom ntama ho nkɔmmɔ*\n• Ɛyɛ papa sɛ yɛda ntama no ase\n> Kae: ɔdɔ ne ahoɔden\n1. Ŋkɔ ƒe nya"
    assert media.pdf(body, kind="Community talk", worksheet=True)[:4] == b"%PDF"
    assert media.card(body, label="Quiz")[:4] == b"\x89PNG"
    assert media.results_card(4, 5, "Malaria", "Well done!")[:4] == b"\x89PNG"
    assert media.plain("*Bold* _it_ \U0001F4DD") == "Bold it"


def test_quiz_parser_rejects_bad_json():
    import quiz
    assert quiz.parse("no json") is None
    good = {"title": "T", "questions": [{"q": "Q?", "options": ["a", "b", "c"], "answer": 1, "why": "w"}] * 4}
    assert len(quiz.parse(json_mod.dumps(good))["questions"]) == 4
    bad = {"questions": [{"q": "Q?", "options": ["a", "b"], "answer": 5}] * 5}
    assert quiz.parse(json_mod.dumps(bad)) is None


def test_voice_script_strips_formatting():
    import voice
    out = voice.script("*\U0001F4DD Title*\n• Point one\n> Say this\n\n(Reply MORE for the rest.)")
    assert out == "Title. Point one. Say this."


def test_quiz_parser_salvages_cut_off_reply():
    import quiz
    q = '{"q": "Ɛdeɛn na ɛde atiridii ba?", "options": ["Ntontom", "Mango", "Awia"], "answer": 0, "why": "Ntontom na ɛde ba."}'
    cut = '```json\n{"title": "Atiridii", "questions": [' + ", ".join([q] * 4) + ', {"q": "Half'
    out = quiz.parse(cut)
    assert out and len(out["questions"]) == 4 and out["title"] == "Atiridii"


def test_forget_says_goodbye_in_their_language_and_tw_at_welcome(env):
    onboard(env, lang="lang_tw")
    post(env, text("forget me"))
    post(env, tap("fm_yes"))
    assert "Mapepa" in bodies(env)[-1]
    post(env, text("hi"))
    assert "TW" in bodies(env)[-1]
    post(env, text("TW"))
    assert "Anammɔn 2 wɔ 2 mu" in bodies(env)[-1]


def _fake_places(monkeypatch, known=("Abrepo",)):
    import places
    looked = []

    def geocode(place, http=None):
        looked.append(place)
        return (6.70, -1.65, f"{place}, Kumasi") if place in known else None
    monkeypatch.setattr(places, "geocode", geocode)
    monkeypatch.setattr(places, "nearest", lambda lat, lng, n=3, http=None: [
        {"name": "Suntreso Government Hospital", "kind": "Hospital", "lat": 6.69, "lng": -1.64, "address": "",
         "phone": "", "km": 1.9}])
    return looked


def test_typed_town_after_location_request(env, monkeypatch):
    looked = _fake_places(monkeypatch)
    onboard(env, detail="hr_chv")
    post(env, pick("h_near"))
    n_calls = len(env.calls)
    post(env, text("Abrepo"))
    assert looked == ["Abrepo"] and len(env.calls) == n_calls  # Claude never asked
    assert any(m["type"] == "location" for m in env.sent[-3:])
    assert "near Abrepo, Kumasi" in bodies(env)[-1]


def test_hospitals_near_a_town_in_free_text(env, monkeypatch):
    looked = _fake_places(monkeypatch)
    onboard(env, detail="hr_chv")
    post(env, text("hospitals close to Abrepo"))
    assert looked == ["Abrepo"] and "Suntreso" in bodies(env)[-1]
    post(env, text("clinic near Nowhereville please"))
    assert looked[-1] == "Nowhereville" and "couldn't find" in body_of(env.sent[-1])
    # A longer real question still goes to Claude
    n = len(env.calls)
    post(env, text("My patient has a fever, should I take her to the hospital in Ho today or wait until tomorrow morning?"))
    assert len(env.calls) == n + 1


def test_facility_place_parser():
    from bot import _facility_place as f
    assert f("Hospitals close to Abrepo") == "Abrepo"
    assert f("nearest clinic to Kpodzi?") == "Kpodzi"
    assert f("health centre in Ho") == "Ho"
    assert f("CHPS around Aflao please") == "Aflao"
    assert f("talk on fractions of a dose") is None
    assert f("I run the CHPS compound in Ho") is None


# ------------------------------------------------------------------ companion app API

def app_signin(env, phone=PHONE):
    r = env.client.post("/api/auth/start", json={"phone": "+" + phone})
    assert r.status_code == 200 and r.json()["phone"] == phone
    code = re.search(r"\*(\d{6})\*", bodies(env)[-1]).group(1)
    r = env.client.post("/api/auth/verify", json={"phone": phone, "code": code})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def sse_events(text):
    out = []
    for block in text.strip().split("\n\n"):
        ev = re.search(r"^event: (.+)$", block, re.M).group(1)
        out.append((ev, json_mod.loads(re.search(r"^data: (.*)$", block, re.M).group(1))))
    return out


def test_app_signin_and_shared_account(env):
    onboard(env)  # already uses WhatsApp
    auth = app_signin(env)
    me = env.client.get("/api/me", headers=auth).json()
    assert me["mode"] == "health" and me["tasks"][0]["id"] == "h_talk" and set(me["details"]) == {"health"}
    assert env.client.get("/api/me").status_code == 401
    assert env.client.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_app_code_rules(env):
    import webapp
    assert webapp.normalise_phone("024 123 4567") == "233241234567"
    assert webapp.normalise_phone("+1 (555) 141-8366") == "15551418366"
    assert webapp.normalise_phone("206 555 0143") == "12065550143"
    assert webapp.normalise_phone("241234567") == "233241234567"
    assert webapp.normalise_phone("+233 24 123 4567") == "233241234567"
    env.client.post("/api/auth/start", json={"phone": PHONE})
    bad = env.client.post("/api/auth/verify", json={"phone": PHONE, "code": "000000"})
    assert bad.status_code == 400
    # LOGIN on WhatsApp also gives a code (when the app's message can't be delivered)
    env.app.store._db.execute("UPDATE web_codes SET sent=0")
    post(env, text("login"))
    code = re.search(r"\*(\d{6})\*", bodies(env)[-1]).group(1)
    assert env.client.post("/api/auth/verify", json={"phone": PHONE, "code": code}).status_code == 200
    # codes are single use
    assert env.client.post("/api/auth/verify", json={"phone": PHONE, "code": code}).status_code == 400


def test_app_setup_ask_stream_and_library(env, monkeypatch):
    auth = app_signin(env, phone="233200000001")
    r = env.client.post("/api/profile", headers=auth, json={"mode": "health", "lang": "tw", "detail": "hr_cho"})
    assert r.json()["mode"] == "health" and r.json()["lang"] == "tw" and r.json()["detail"] == "Mpɔtam apɔmuden ɔfesa"
    r = env.client.post("/api/ask", headers=auth, json={"text": "Mosquito nets", "task": "h_talk"})
    evs = sse_events(r.text)
    kinds = [e for e, _ in evs]
    assert kinds[0] == "start" and "done" in kinds
    done = dict(evs)["done"]
    assert done["saved"] is True and done["follow"][0]["label"] == "Quiz ma kuo no"
    assert "Asante Twi" in env.calls[-1]["system"]
    lib = env.client.get("/api/library", headers=auth).json()["items"]
    assert lib[0]["id"] == done["item_id"] and lib[0]["kind"] == "Mpɔtam nkɔmmɔ"
    hist = env.client.get("/api/history", headers=auth).json()["messages"]
    assert hist[-1]["item_id"] == done["item_id"]
    pdf = env.client.get(f"/api/items/{done['item_id']}/pdf", headers=auth)
    assert pdf.content[:4] == b"%PDF"
    card = env.client.get(f"/api/items/{done['item_id']}/card", headers=auth)
    assert card.content[:4] == b"\x89PNG"
    assert env.client.delete(f"/api/items/{done['item_id']}/save", headers=auth).json() == {"saved": False}
    # someone else's item is not visible
    other = app_signin(env, phone="233200000002")
    assert env.client.get(f"/api/items/{done['item_id']}", headers=other).status_code == 404


def test_app_forms_follow_ups_and_emergency(env):
    auth = app_signin(env, phone="233200000003")
    env.client.post("/api/profile", headers=auth, json={"mode": "health"})
    schema = env.client.get("/api/form/danger", headers=auth).json()
    assert any(f.get("name") == "signs" for f in schema["fields"])
    r = env.client.post("/api/ask", headers=auth, json={"form": {"kind": "danger", "data": {
        "who": "child", "signs": ["convulsions"]}}})
    evs = dict(sse_events(r.text))
    assert "emergency" in evs and "Danger-sign check" in env.calls[-1]["messages"][-1]["content"][-1]["text"]
    r = env.client.post("/api/ask", headers=auth, json={"follow": "f_simpler", "task": "h_danger"})
    assert "done" in dict(sse_events(r.text))
    assert env.calls[-1]["messages"][-1]["content"][-1]["text"].startswith("Say that again more simply")


def test_app_quiz_and_nearest(env, monkeypatch):
    auth = app_signin(env, phone="233200000004")
    env.client.post("/api/profile", headers=auth, json={"mode": "health"})
    qz = env.client.post("/api/quiz", headers=auth, json={"topic": "Malaria"}).json()
    assert len(qz["questions"]) == 5 and qz["topic"] == "Malaria"
    _fake_places(monkeypatch)
    r = env.client.get("/api/nearest", headers=auth, params={"place": "Abrepo"}).json()
    assert r["found"] and r["results"][0]["name"].startswith("Suntreso") and r["place"].startswith("Abrepo")
    assert env.client.get("/api/nearest", headers=auth, params={"place": "Atlantis"}).json()["found"] is False


def test_app_forget_ends_sessions(env):
    auth = app_signin(env, phone="233200000005")
    env.client.post("/api/profile", headers=auth, json={"lang": "en"})
    assert env.client.post("/api/forget", headers=auth).json() == {"ok": True}
    assert env.client.get("/api/me", headers=auth).status_code == 401


def test_app_shell_is_served(env):
    r = env.client.get("/app/")
    assert r.status_code == 200 and "manifest.json" in r.text and "teacher" not in r.text.lower()
    assert "teacher" not in env.client.get("/app/app.js").text.lower()
    assert env.client.get("/app/manifest.json").json()["display"] == "standalone"
    assert env.client.get("/app/sw.js").status_code == 200


# --------------------------------------------------------------------------- register photos -> monthly return

def _page_image(lines):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 54)
    im = Image.new("RGB", (1400, 140 + 110 * len(lines)), "white")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((60, 60 + 110 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def _ocr(jpeg, tmp_path):
    path = tmp_path / "ocr.jpg"
    path.write_bytes(jpeg)
    return subprocess.run(["tesseract", str(path), "stdout"], capture_output=True, text=True).stdout


@pytest.mark.skipif(not shutil.which("tesseract"), reason="needs Tesseract")
def test_redaction_hides_names_and_keeps_counts(tmp_path):
    import redact
    page = _page_image(["Malaria tested 12", "Kwame Mensah 0244123456", "Abena Owusu 7"])
    assert "Kwame" in _ocr(page, tmp_path)  # the test page really is readable
    clean, hidden = redact.redact(page, {"redact_bands": [[0.0, 0.0]]})
    out = _ocr(clean, tmp_path)
    assert hidden >= 4
    for secret in ("Kwame", "Mensah", "Abena", "Owusu", "0244123456"):
        assert secret not in out
    assert "Malaria" in out and "12" in out


def test_redaction_fails_closed(monkeypatch):
    import redact
    monkeypatch.setattr(redact.shutil, "which", lambda name: None)
    with pytest.raises(redact.RedactionUnavailable):
        redact.redact(b"anything")
    if shutil.which("tesseract"):
        monkeypatch.undo()
        with pytest.raises(redact.RedactionUnavailable):
            redact.redact(b"not a photo")


def test_register_bands_are_painted(monkeypatch):
    import redact
    from PIL import Image
    monkeypatch.setattr(redact, "available", lambda: (True, "ready"))
    monkeypatch.setattr(redact, "_words", lambda path: [{"text": "Malaria", "box": (700, 10, 760, 30)}])
    buf = io.BytesIO()
    Image.new("RGB", (1000, 400), "white").save(buf, "JPEG")
    clean, hidden = redact.redact(buf.getvalue(), {"redact_bands": [[0.1, 0.4]]})
    im = Image.open(io.BytesIO(clean))
    assert hidden == 1 and im.getpixel((250, 200))[0] < 80 and im.getpixel((700, 200))[0] > 200


def test_compile_return_adds_pages_checks_and_compares():
    import register
    fmt = register.load_format()
    pages = [{"counts": {"malaria_tested": 10, "malaria_positive": 6, "opd_total": 50}, "unclear": []},
             {"counts": {"malaria_positive": 6, "opd_total": 30}, "unclear": ["penta1"]}]
    ret = register.compile_return(fmt, pages, previous={"opd_total": 20, "malaria_tested": 9})
    assert ret["totals"] == {"malaria_tested": 10, "malaria_positive": 12, "opd_total": 80}
    assert any("More positive malaria results than tests" in f for f in ret["flags"])
    assert any("changed a lot" in f and "20 to 80" in f for f in ret["flags"])
    assert not any("Malaria: tested" in f and "changed" in f for f in ret["flags"])  # 9 -> 10 is normal
    assert "penta1" in ret["unclear"] and "deliveries" in ret["missing"] and ret["pages"] == 2
    text_ = register.as_text(fmt, ret, "2026-09", hidden=7)
    assert "September 2026" in text_ and "*80*" in text_ and "Please check" in text_ and "7 areas" in text_
    assert register.as_pdf(fmt, ret, "2026-09")[:4] == b"%PDF"


def test_report_month():
    import datetime as dt
    import register
    assert register.report_month(dt.date(2026, 10, 5)) == "2026-09"
    assert register.report_month(dt.date(2026, 10, 20)) == "2026-10"
    assert register.report_month(dt.date(2026, 1, 3)) == "2025-12"


def _photo(media="MEDIA1"):
    return {"type": "image", "image": {"id": media, "mime_type": "image/jpeg"}}


def test_register_photos_become_a_draft_return(env, monkeypatch):
    monkeypatch.setenv("REGISTER_PHOTOS", "on")
    import redact
    seen = []
    monkeypatch.setattr(redact, "redact", lambda data, fmt=None: (seen.append(data) or (b"\xff\xd8clean", 3)))
    onboard(env)
    post(env, pick("h_register"))
    assert "monthly return" in bodies(env)[-1]
    post(env, _photo())
    assert any(m["type"] == "reaction" and m["reaction"]["emoji"] == "\U0001F512" for m in env.sent)
    assert "Page 1 read" in bodies(env)[-1] and button_ids(env) == ["rg_done", "rg_cancel"]
    call = env.calls[-1]
    assert call["tool_choice"]["name"] == "record_counts"
    img = call["messages"][0]["content"][0]["source"]["data"]
    assert base64.b64decode(img) == b"\xff\xd8clean"  # only the redacted photo reaches Claude
    post(env, _photo())
    post(env, text("done"))
    body = last_interactive(env)["body"]["text"]
    assert "Draft monthly return" in body and "*80*" in body and "*24*" in body  # two pages added up
    assert "More positive malaria results than tests" in body and "6 areas" in body
    assert "made_up_key" not in body
    assert button_ids(env)[:2] == ["f_report", "f_trends"] and button_ids(env)[2].startswith("more:")
    doc = [m for m in env.sent if m["type"] == "document"][-1]
    assert doc["document"]["filename"].startswith("monthly-return-draft-")
    assert env.uploads[-1]["bytes"][:4] == b"%PDF"
    row = env.app.store._db.execute("select totals from returns where user=?", (PHONE,)).fetchone()
    assert json.loads(row[0])["opd_total"] == 80
    assert any(e.get("event") == "return_drafted" for e in events(env))
    assert env.app.store.get(PHONE).get("step") is None


def test_register_refused_when_names_cant_be_hidden(env, monkeypatch):
    monkeypatch.setenv("REGISTER_PHOTOS", "on")
    import redact
    def boom(data, fmt=None):
        raise redact.RedactionUnavailable("Tesseract OCR is not installed")
    monkeypatch.setattr(redact, "redact", boom)
    onboard(env)
    post(env, pick("h_register"))
    n = len(env.calls)
    post(env, _photo())
    assert "names can't be hidden" in bodies(env)[-1].lower() or "turned off" in bodies(env)[-1].lower()
    assert len(env.calls) == n  # nothing went to Claude


def test_register_needs_photos_and_can_be_cancelled(env, monkeypatch):
    monkeypatch.setenv("REGISTER_PHOTOS", "on")
    onboard(env)
    post(env, pick("h_register"))
    post(env, text("done"))
    assert "photo" in bodies(env)[-1].lower()
    post(env, tap("rg_cancel"))
    assert "Cancelled" in bodies(env)[-1] or "cancel" in bodies(env)[-1].lower()
    assert env.app.store.get(PHONE).get("step") is None


@pytest.mark.parametrize("lang", ["tw", "ee"])
def test_every_interface_string_is_translated_within_limits(env, lang):
    import i18n
    import prompts as P
    assert i18n.untranslated(_ui_strings(), lang) == []
    for _, t in P.MY_DATA_BUTTONS + P.FORGET_BUTTONS + P.FEEDBACK_BUTTONS + P.REGISTER_BUTTONS + P.RETURN_FOLLOW:
        assert len(i18n.tr(t, lang)) <= 20, t
    for t in P.TASKS.values():
        for label in [*t.get("examples", []), *[l for _, l in t["follow"]]]:
            assert len(i18n.tr(label, lang)) <= 20, label
        assert len(i18n.tr(t["title"], lang)) <= 24 and len(i18n.tr(t["desc"], lang)) <= 72, t["title"]
    for _, t, d in P.MENU_EXTRA_ROWS + P.HEALTH_ROLE_ROWS + P.SETTINGS_ROWS + P.FU_ROWS + P.VISIT_TYPES + [r[:3] for r in P.REF_REASONS] + [P.REF_LIST_ROW]:
        assert len(i18n.tr(t, lang)) <= 24 and len(i18n.tr(d, lang)) <= 72, t
    for _, t in (P.FU_ADDED_BUTTONS + P.FU_VIEW_BUTTONS + P.STOCK_FOLLOW + P.TEAM_BUTTONS + P.VISIT_BUTTONS
                 + P.VISIT_DANGER_BUTTONS + P.REF_LEAD_BUTTONS + P.REF_CHECK_BUTTONS + P.ALERT_HINT_BUTTONS + P.ALERT_FOLLOW):
        assert len(i18n.tr(t, lang)) <= 20, t
    for t in (P.VISIT_BUTTON, P.REF_BUTTON):
        assert len(i18n.tr(t, lang)) <= 20, t
    for t in (P.FU_BUTTON, P.FU_MARK_BUTTON, P.FU_ALL_BUTTON):
        assert len(i18n.tr(t, lang)) <= 20, t
    assert P.EMERGENCY in i18n.tr(P.EMERGENCY, lang)  # the emergency card keeps the English too
    for k in ("{n}", "{found}", "{unclear}"):
        assert k in i18n._tables[lang][P.REGISTER_PAGE_OK]


def test_ewe_menus_and_done_word(env, monkeypatch):
    monkeypatch.setenv("REGISTER_PHOTOS", "on")
    import redact
    monkeypatch.setattr(redact, "redact", lambda data, fmt=None: (b"\xff\xd8clean", 1))
    onboard(env, lang="lang_ee")
    post(env, text("menu"))
    menu = last_interactive(env)
    assert menu["action"]["button"] == "Ʋu menu"
    post(env, pick("h_register"))
    assert "Ɣletiɣletii" in bodies(env)[-1]
    post(env, _photo())
    assert "Mexlẽ axa 1" in bodies(env)[-1]
    assert [b["reply"]["title"] for b in last_interactive(env)["action"]["buttons"]][0] == "✅ Ewu enu"
    post(env, text("ewu enu"))
    assert any(m["type"] == "document" for m in env.sent)


def test_red_flag_set_sensitivity_and_specificity():
    import safety
    data = json.loads((Path(__file__).resolve().parent.parent / "evals" / "red_flags.json").read_text())
    missed = [c["text"] for c in data["urgent"] if not safety.looks_like_emergency(c["text"])]
    assert missed == []  # every urgent case must show the emergency card
    alarms = [t for t in data["routine"] if safety.looks_like_emergency(t)]
    assert len(alarms) <= len(data["routine"]) // 10


def test_health_endpoints_and_privacy_mention_register(env):
    assert env.client.get("/healthz").json() == {"ok": True}
    assert "register_photos" in env.client.get("/health").json()
    assert "Register photos" in env.client.get("/privacy").text


def test_redaction_keeps_headings_complaints_months_and_visit_numbers():
    import redact
    for w in ("Complaint", "Headache", "October", "1st", "3rd", "(wks)", "Rash"):
        assert not redact._hide(w, redact.KEEP_WORDS), w
    for w in ("Kofi", "Agbeko", "Serwaa", "0240000000", "Dzifa"):
        assert redact._hide(w, redact.KEEP_WORDS), w


# --------------------------------------------------------------------------- follow-ups and stock

def test_followup_parsing_keeps_numbers_not_names():
    import datetime as dt
    import followups as F
    b = dt.date(2026, 11, 2)
    assert F.parse_entry("child", "Card 112, Abla Mensah, 14/08/2026", b) == ("Card 112", dt.date(2026, 8, 14))
    assert F.parse_entry("pregnancy", "ANC 34 EDD 20/03/2027", b)[1] == dt.date(2026, 6, 13)
    assert F.parse_entry("pregnancy", "anc 35, 24 weeks", b) == ("ANC 35", b - dt.timedelta(weeks=24))
    assert F.parse_entry("postnatal", "PNC 9 delivered 28 Oct 2026", b) == ("PNC 9", dt.date(2026, 10, 28))
    for kind, text, err in [("child", "Abla Mensah 14/08/2026", "no_number"), ("child", "Card 5", "no_date"),
                            ("child", "Card 5, 14/08/2030", "bad_date"), ("pregnancy", "ANC 3, 60 weeks", "bad_weeks")]:
        with pytest.raises(F.FollowupError, match=err):
            F.parse_entry(kind, text, b)


def test_followup_schedule_due_and_catch_up():
    import datetime as dt
    import followups as F
    b = dt.date(2026, 11, 2)
    born = dt.date(2025, 5, 3)   # MR2 / MenA at 18 months = 2 Nov 2026
    past = F.past_visits("child", born, b)
    assert "m18" not in past and "m9" in past
    fu = {"kind": "child", "label": "Card 9", "anchor": born.isoformat(), "done": past}  # all confirmed from the card
    nv = F.next_visit(fu, b)
    assert nv["key"] == "m18" and "MR 2" in nv["what"]
    due = F.due_list([fu], b)
    assert due["soon"] and not due["overdue"]
    fu["done"].append("m18")
    assert F.next_visit(fu, b)["key"] == "m24"
    # Missed vaccines stay (catch-up); a missed ANC contact more than 2 weeks ago is skipped
    anc = {"kind": "pregnancy", "label": "ANC 1", "anchor": (b - dt.timedelta(weeks=24)).isoformat(), "done": []}
    assert F.next_visit(anc, b)["key"] == "anc26"


def test_followups_on_whatsapp(env):
    import datetime as dt
    onboard(env)
    post(env, pick("h_followup"))
    assert "Follow-ups" in body_of(env.sent[-1]) and "fu_add:child" in list_ids(env)
    post(env, pick("fu_add:child"))
    assert "card or register number" in bodies(env)[-1]
    born = (dt.date.today() - dt.timedelta(days=270)).strftime("%d/%m/%Y")  # MR1 due in ~4 days
    post(env, text(f"Card 112, Abla Mensah, {born}"))
    added = bodies(env)[-1]
    assert "Added *Card 112*" in added and "earlier visits were due" in added and "Were *all* of these given?" in added
    fus = env.app.store.followups(PHONE)
    assert fus[0]["done"] == []  # nothing assumed
    post(env, tap(f"fu_pastok:{fus[0]['id']}"))
    assert "as given" in bodies(env)[-1] and "MR 1" in bodies(env)[-1]
    fus = env.app.store.followups(PHONE)
    assert [f["label"] for f in fus] == ["Card 112"]  # the name was never stored
    row = env.app.store._db.execute("select * from followups").fetchone()
    assert "Abla" not in json.dumps(row)
    post(env, text("DUE"))
    assert "Due in the next 7 days" in "\n".join(bodies(env)) and f"fu_done:{fus[0]['id']}" in list_ids(env)
    post(env, pick("fu_remind"))
    assert any("next vaccines (MR 1" in b for b in bodies(env))
    post(env, pick(f"fu_done:{fus[0]['id']}"))
    assert "marked done" in bodies(env)[-1] and "Next:" in bodies(env)[-1]
    post(env, pick(f"fu_view:{fus[0]['id']}"))
    assert "Child immunisation" in bodies(env)[-1] and "✅" in bodies(env)[-1]
    post(env, tap(f"fu_rm:{fus[0]['id']}"))
    assert "Removed" in bodies(env)[-1] and env.app.store.followups(PHONE) == []
    assert any(e.get("event") == "followup_added" for e in events(env))


def test_followup_reminder_in_ewe(env):
    import datetime as dt
    onboard(env, lang="lang_ee")
    post(env, pick("fu_add:pregnancy"))
    edd = (dt.date.today() + dt.timedelta(days=280 - 26 * 7 - 3)).strftime("%d/%m/%Y")
    post(env, text(f"ANC 7, EDD {edd}"))
    post(env, pick("fu_remind"))
    assert any("ANC kpɔkpɔ" in b for b in bodies(env))


def test_weekly_list_respects_whatsapp_window(env, monkeypatch):
    import datetime as dt
    onboard(env)
    born = (dt.date.today() - dt.timedelta(days=272)).isoformat()
    env.app.store.add_followup(PHONE, "child", "Card 3", born, [])
    n_before = len(env.sent)
    assert env.app.bot.weekly_digest() == 1  # just wrote in: inside the 24-hour window
    assert "this week's list" in "\n".join(bodies(env)[-3:])
    assert env.app.bot.weekly_digest() == 0  # once a week only
    s = env.app.store.get(PHONE)
    s["last_in"], s["prefs"]["digest_week"] = 0, None
    env.app.store.save(PHONE, s)
    sent = len(env.sent)
    assert env.app.bot.weekly_digest() == 0 and len(env.sent) == sent  # outside the window, no template: silent
    s = env.app.store.get(PHONE); s["prefs"]["digest_week"] = None; env.app.store.save(PHONE, s)
    monkeypatch.setenv("WA_TEMPLATE_DUE", "akwaaba_weekly_due")
    assert env.app.bot.weekly_digest() == 1
    tpl = env.sent[-1]
    assert tpl["type"] == "template" and tpl["template"]["name"] == "akwaaba_weekly_due"


def test_stock_check_drafts_requisition(env):
    onboard(env)
    post(env, pick("h_stock"))
    assert "on the shelf" in bodies(env)[-1]
    post(env, text("RDT 40, AL 12, ORS 60, zinc 0, mystery 3"))
    joined = "\n".join(bodies(env)[-3:])
    assert "Stocked out" in joined and "Zinc tablets" in joined and "Below minimum" in joined
    assert "Draft requisition" in joined and "*110* tests" in joined and "mystery" in joined
    assert env.app.store.last_stock(PHONE) == {"rdt": 40, "al": 12, "ors": 60, "zinc": 0}
    assert button_ids(env)[:2] == ["f_stock_note", "st_again"]
    post(env, text("STOCK"))
    post(env, text("RDT 150"))
    assert "(was 40)" in "\n".join(bodies(env)[-2:])


def test_forget_me_clears_followups_and_stock(env):
    onboard(env)
    env.app.store.add_followup(PHONE, "child", "Card 1", "2026-09-01", [])
    env.app.store.save_stock(PHONE, {"rdt": 3})
    env.app.store.forget(PHONE)
    assert env.app.store.followups(PHONE) == [] and env.app.store.last_stock(PHONE) is None


def test_app_register_pages_build_one_monthly_return(env, monkeypatch):
    monkeypatch.setenv("REGISTER_PHOTOS", "on")
    import redact
    monkeypatch.setattr(redact, "redact", lambda data, fmt=None: (b"\xff\xd8clean", 2))
    onboard(env)
    auth = app_signin(env)
    img = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0photo").decode()
    first = dict(sse_events(env.client.post("/api/ask", headers=auth, json={"task": "h_register", "image": img, "text": ""}).text))
    assert "Draft monthly return" in first["done"]["reply"] and "*40*" in first["done"]["reply"]
    second = dict(sse_events(env.client.post("/api/ask", headers=auth, json={"task": "h_register", "image": img, "text": ""}).text))
    assert "*80*" in second["done"]["reply"] and second["done"]["item_id"] == first["done"]["item_id"]
    assert [f["id"] for f in second["done"]["follow"]] == ["f_report", "f_trends"]
    assert env.calls[-1]["tool_choice"]["name"] == "record_counts"
    lib = env.client.get("/api/library", headers=auth).json()["items"]
    assert sum(1 for i in lib if i["id"] == first["done"]["item_id"]) == 1


def test_app_register_refused_without_redaction(env, monkeypatch):
    monkeypatch.setenv("REGISTER_PHOTOS", "on")
    import redact
    def boom(data, fmt=None):
        raise redact.RedactionUnavailable("no OCR")
    monkeypatch.setattr(redact, "redact", boom)
    onboard(env)
    auth = app_signin(env)
    n = len(env.calls)
    img = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0photo").decode()
    evs = dict(sse_events(env.client.post("/api/ask", headers=auth, json={"task": "h_register", "image": img, "text": ""}).text))
    assert "error" in evs and len(env.calls) == n


# --------------------------------------------------------------------------- teams: volunteers, nurses, supervisors

VOL, SUP = "233200000777", "233200000888"


def onboard_as(env, phone, detail):
    post(env, text("hi"), phone=phone); post(env, tap("lang_en"), phone=phone); post(env, pick(detail), phone=phone)


def sent_to(env, phone):
    return [body_of(m) for m in env.sent if m.get("to") == phone and m["type"] != "reaction"]


def test_menus_follow_the_role(env):
    onboard_as(env, VOL, "hr_chv")
    assert button_ids(env) == ["h_visitlog", "h_talk", "h_referral"]  # first-use tour
    post(env, text("menu"), phone=VOL)
    assert list_ids(env)[:3] == ["h_visitlog", "h_referral", "h_alert"]
    onboard_as(env, SUP, "hr_sup")
    assert button_ids(env) == ["h_team", "h_talk", "h_notice"]
    post(env, text("menu"), phone=SUP)
    assert list_ids(env)[:3] == ["h_team", "h_notice", "h_supvisit"]
    onboard(env)
    post(env, text("menu"))
    assert list_ids(env)[:4] == ["h_register", "h_followup", "h_referral", "h_alert"]


def test_listen_or_read_and_voice_first(env, monkeypatch):
    import voice
    spoken = []
    monkeypatch.setattr(voice, "available", lambda *a: (True, "ready"))
    monkeypatch.setattr(voice, "speak", lambda text, lang, **k: spoken.append((text, lang)) or b"OggS-fake")
    post(env, text("hi")); post(env, tap("lang_ee")); post(env, pick("hr_chn"))
    assert button_ids(env) == ["vp_listen", "vp_read"]        # asked once, before the tour
    post(env, tap("vp_listen"))
    assert env.app.store.get(PHONE)["prefs"]["voice_first"] is True
    assert button_ids(env) == ["h_talk", "h_register", "h_followup"]
    before = len(env.sent)
    post(env, text("Explain oral rehydration"))
    kinds = [m["type"] for m in env.sent[before:] if m["type"] in ("audio", "interactive")]
    assert spoken and "audio" in kinds and kinds.index("audio") < kinds.index("interactive")  # voice first, text under


def test_english_voice_can_be_switched_off(env, monkeypatch):
    import voice
    monkeypatch.setattr(voice, "available", lambda *a: (True, "ready"))
    monkeypatch.setenv("ENGLISH_VOICE", "off")
    onboard(env)                                              # English: no listen-or-read question
    assert button_ids(env) == ["h_talk", "h_register", "h_followup"]
    monkeypatch.setenv("TTS_VOICE_TW", "male_low")
    assert voice.speaker_for("tw") == "male_low" and voice.speaker_for("ee") == "female"


def test_team_join_visit_referral_and_arrival(env):
    onboard(env)                       # the nurse
    post(env, tap("m_team"))
    code = re.search(r"AK-\d{4}", bodies(env)[-1]).group(0)
    onboard_as(env, VOL, "hr_chv")      # her volunteer joins
    post(env, text(f"join {code}"), phone=VOL)
    assert "joined the team" in sent_to(env, VOL)[-1]
    assert any("joined your team" in b for b in sent_to(env, PHONE))
    # a visit with a danger sign: emergency card, then refer in one tap
    post(env, pick("h_visitlog"), phone=VOL)
    post(env, pick("vt_newborn"), phone=VOL)
    post(env, text("House 14, Abla's baby 5 days old not sucking since morning"), phone=VOL)
    vol = sent_to(env, VOL)
    assert any("may be an emergency" in b for b in vol) and "Visit logged" in vol[-1]
    row = env.app.store._db.execute("select label, flagged from visits").fetchone()
    assert row == ("House 14", 1)      # no name stored
    post(env, tap("vl_refer"), phone=VOL)
    post(env, pick("rr_newborn"), phone=VOL)
    assert "Referral recorded" in sent_to(env, VOL)[-1] and "nurse has been told" in sent_to(env, VOL)[-1]
    nurse_msg = [m for m in env.sent if m.get("to") == PHONE and m["type"] == "interactive"][-1]
    assert "Referral from a volunteer" in body_of(nurse_msg) and "House 14" in body_of(nurse_msg)
    rid = env.app.store.referrals_for(PHONE)[0]["id"]
    post(env, tap(f"rf_in:{rid}"))
    assert "arrived" in bodies(env)[-1]
    assert any("arrived for care" in b for b in sent_to(env, VOL))
    assert env.app.store.referral(rid)["status"] == "arrived"
    assert any(e.get("event") == "referral_arrived" for e in events(env))


def test_referral_check_after_two_days(env):
    onboard_as(env, VOL, "hr_chv")
    post(env, pick("h_referral"), phone=VOL)
    post(env, pick("rr_fever"), phone=VOL)
    post(env, text("Card 9"), phone=VOL)
    assert "join your nurse's team" in sent_to(env, VOL)[-1]
    assert env.app.bot.check_referrals(now=time.time() + 3 * 86400) == 1
    assert "reach the nurse or facility" in sent_to(env, VOL)[-1]
    rid = env.app.store.referrals_for(VOL)[0]["id"]
    post(env, tap(f"rf_no:{rid}"), phone=VOL)
    assert "visit *Card 9* again" in sent_to(env, VOL)[-1]
    assert env.app.bot.check_referrals(now=time.time() + 6 * 86400) == 0


def test_messages_wait_in_the_outbox_outside_the_window(env):
    onboard(env)
    post(env, tap("m_team"))
    code = re.search(r"AK-\d{4}", bodies(env)[-1]).group(0)
    onboard_as(env, VOL, "hr_chv")
    post(env, text(f"JOIN {code}"), phone=VOL)
    s = env.app.store.get(PHONE); s["last_in"] = 0; env.app.store.save(PHONE, s)
    post(env, pick("h_referral"), phone=VOL); post(env, pick("rr_tb"), phone=VOL); post(env, text("House 3"), phone=VOL)
    assert env.app.store.outbox_count(PHONE) == 1
    post(env, text("hello there"))
    assert any("While you were away" in b for b in bodies(env)) and env.app.store.outbox_count(PHONE) == 0


def test_disease_alert_rules_and_flow(env):
    import alerts
    assert [d["key"] for d in alerts.match("3 year old with fever and a rash")] == ["measles"]
    assert [d["key"] for d in alerts.match("child suddenly can't walk, legs floppy")] == ["afp"]
    assert [d["key"] for d in alerts.match("4 children sick with vomiting")] == ["cluster"]
    assert alerts.match("child with a cough") == []
    onboard(env)
    post(env, pick("h_alert"))
    post(env, text("Two children in Avenui with fever and a rash all over the body"))
    joined = "\n".join(bodies(env)[-3:])
    assert "Possible measles" in joined and "District Disease Control Officer" in joined and "two children" in joined.lower()
    assert env.app.store.count_since("alerts", PHONE, 0) == 1
    # a free question that sounds notifiable gets a nudge
    post(env, text("A woman died after delivery in the village last night, what do I do?"))
    assert any("report immediately" in b for b in bodies(env))
    post(env, tap("al_now"))
    assert any("Possible maternal death" in b for b in bodies(env))


def test_supervisor_overview_and_notice(env):
    onboard_as(env, SUP, "hr_sup")
    post(env, pick("h_team"), phone=SUP)
    assert "Nobody has joined" in sent_to(env, SUP)[-1]
    post(env, tap("m_team"), phone=SUP)
    code = re.search(r"AK-\d{4}", sent_to(env, SUP)[-1]).group(0)
    onboard(env)
    post(env, text(f"join {code}"))
    env.app.store.add_followup(PHONE, "child", "Card 2", "2025-01-01", [])
    post(env, pick("h_team"), phone=SUP)
    ov = sent_to(env, SUP)[-1]
    assert "Team overview" in ov and "nurse" in ov and "overdue follow-ups" in ov
    post(env, pick("h_notice"), phone=SUP)
    post(env, text("Measles campaign starts Monday. Bring your registers."), phone=SUP)
    assert "Sent to 1 now" in sent_to(env, SUP)[-1]
    assert any("Notice from your supervisor" in b for b in bodies(env))
    post(env, pick("h_supvisit"), phone=SUP)
    assert "focus on" in sent_to(env, SUP)[-1]


def test_english_pronunciation_fixes():
    import voice
    said = voice.english("Aim for at least 8 ANC visits at the CHPS. Card 112 is due 12/11 at 07:00. Weight 3.5 kg.")
    assert "eight antenatal care visits at the chips" in said
    assert "Card one one two" in said and "12/11" in said and "07:00" in said and "3.5" in said


def test_abena_voice_for_english(monkeypatch, tmp_path):
    import base64, io, json, shutil, subprocess, voice
    monkeypatch.delenv("TTS_PROVIDER_EN", raising=False)
    monkeypatch.setattr(voice, "CACHE", tmp_path)
    assert voice.provider_for("en") == "abena" and voice.provider_for("tw") == "khaya"
    assert voice.speaker_for("en") == "akua_eng_high"
    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=0.3", str(wav)], check=True)
    sent = []

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake(req, timeout=0):
        sent.append(json.loads(req.data))
        if sent[-1]["voice"] == "akua_eng_high":
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, 400, "unknown voice", {}, None)
        return R(json.dumps({"audio_base64": base64.b64encode(wav.read_bytes()).decode()}).encode())
    monkeypatch.setattr(voice.urllib.request, "urlopen", fake)
    if not shutil.which("ffmpeg"):
        return
    data = voice.speak("Take folic acid every day. Go to the CHPS compound.", "en")
    assert data[:4] == b"OggS"
    assert [s["voice"] for s in sent] == ["akua_eng_high", "akua_eng_medium"]
    assert "chips compound" in sent[-1]["text"]


def test_emergency_card_comes_before_setup_and_tasks(env):
    post(env, text("The baby is not breathing"))  # a brand-new user, not set up yet
    assert bodies(env)[0] == __import__("prompts").EMERGENCY
    env.sent.clear()
    onboard(env)
    post(env, pick("h_followup")); post(env, pick("fu_add:child"))
    env.sent.clear()
    post(env, text("Card 5 she is convulsing now"))  # in the middle of adding a follow-up
    assert bodies(env)[0] == __import__("prompts").EMERGENCY
    assert sum(b == __import__("prompts").EMERGENCY for b in bodies(env)) == 1


def test_emergency_card_is_not_rate_limited(env, monkeypatch):
    onboard(env)
    monkeypatch.setattr(env.app.bot, "_rate_limited", lambda phone: True)
    env.sent.clear()
    post(env, text("She is bleeding a lot after delivery"))
    assert bodies(env)[0] == __import__("prompts").EMERGENCY


def test_safety_paraphrases_and_denied_signs():
    import safety
    for t in ["Child cannot drink", "Child is not conscious", "baby is 2 weeks old and has fever", "She is bleeding a lot",
              "She has no energy and is convulsing", "The child is not breathing"]:
        assert safety.looks_like_emergency(t), t
    for t in ["No chest pain, no seizures", "Pregnant, no bleeding, asking about iron", "She denies headache"]:
        assert not safety.looks_like_emergency(t), t


def test_followups_missed_doses_stay_and_real_dates_set_the_gap(env):
    import datetime as dt
    import followups as F
    onboard(env)
    post(env, pick("h_followup")); post(env, pick("fu_add:child"))
    born = (dt.date.today() - dt.timedelta(days=180)).strftime("%d/%m/%Y")
    post(env, text(f"Card 7, {born}"))
    fid = env.app.store.followups(PHONE)[0]["id"]
    post(env, tap(f"fu_pastno:{fid}"))
    assert "catch-up" in "\n".join(bodies(env)) and "Overdue" in "\n".join(bodies(env))
    fu = env.app.store.followup(PHONE, fid)
    assert F.next_visit(fu)["key"] == "birth"       # the oldest missed visit comes first
    for _ in range(2):                              # birth dose, then Penta 1, given today
        post(env, pick(f"fu_done:{fid}"))
    fu = env.app.store.followup(PHONE, fid)
    assert fu["given"]["w6"] == dt.date.today().isoformat()
    nxt = F.next_visit(fu)
    assert nxt["key"] == "w10" and nxt["date"] == dt.date.today() + dt.timedelta(days=28)


def test_followup_labels_drop_names_with_digits():
    import followups as F
    assert F.clean_label("Ama123 card 42") == "Card 42"
    assert F.clean_label("ANC34 Abla") == "ANC34"


def test_register_photos_off_by_default(env, monkeypatch):
    import redact
    called = []
    monkeypatch.setattr(redact, "redact", lambda data, fmt=None: (called.append(1) or (b"x", 1)))
    onboard(env)
    post(env, pick("h_register"))
    assert "switched off" in bodies(env)[-1]
    n = len(env.calls)
    post(env, _photo())          # a photo sent anyway goes nowhere near the register flow
    assert not called and env.app.store.get(PHONE).get("step") != "register"
    assert "don't send anything that shows a patient's name" in "\n".join(bodies(env))


def test_redaction_refuses_without_a_layout_or_when_nothing_is_read(monkeypatch):
    import redact
    from PIL import Image
    monkeypatch.setattr(redact, "available", lambda: (True, "ready"))
    buf = io.BytesIO()
    Image.new("RGB", (600, 300), "white").save(buf, "JPEG")
    with pytest.raises(redact.RedactionUnavailable, match="layout"):
        redact.redact(buf.getvalue(), {"redact_bands": []})
    monkeypatch.setattr(redact, "_words", lambda path: [])
    with pytest.raises(redact.RedactionUnavailable, match="read"):
        redact.redact(buf.getvalue(), {"redact_bands": [[0.1, 0.3]]})


def test_webhook_refuses_everything_without_an_app_secret(env, monkeypatch):
    monkeypatch.setattr(env.app, "APP_SECRET", "")
    r = post(env, text("hi"))
    assert r.status_code == 403 and env.sent == []


def test_stats_and_dashboard_need_the_key(env):
    import ids
    assert env.client.get("/stats").status_code == 404
    assert env.client.get("/dashboard").status_code == 404
    assert env.client.get("/stats", params={"key": "wrong"}).status_code == 404
    assert env.client.get("/stats", params={"key": ids.dashboard_key()}).status_code == 200


def test_usage_log_uses_a_keyed_code_not_a_plain_hash(env):
    import hashlib
    onboard(env)
    users = {e["user"] for e in events(env)}
    assert hashlib.sha256(PHONE.encode()).hexdigest()[:12] not in users


def test_memory_fields_hold_only_work_facts():
    import memory as M
    assert M._valid("town_or_region", "Ho") == "Ho"
    assert M._valid("town_or_region", "Ama's village") is None
    assert M._valid("facility", "My patient Abla is HIV positive") is None
    assert M._valid("community_language", "Ewe, Twi, Klingon") == "Ewe, Twi"
    assert "notes" not in M.PROFILE_KEYS["health"]
    s = M.clean_summary("They asked about Card 112 and Mrs Mensah; call 0244123456.")
    assert "112" not in s and "Mensah" not in s and "0244123456" not in s


def test_history_is_capped_and_expired_history_is_deleted(env):
    import time as _t
    import store as S
    st = env.app.store
    s = st.get(PHONE)
    s.update(mode="health", history=[{"role": "user", "content": f"m{i}"} for i in range(40)])
    st.save(PHONE, s)
    assert len(st.get(PHONE)["history"]) == S.HISTORY_CAP
    st._db.execute("UPDATE sessions SET updated_at=? WHERE user=?", (int(_t.time()) - S.RECENT_TTL_S - 10, PHONE))
    st.purge()
    raw = st._db.execute("SELECT history FROM sessions WHERE user=?", (PHONE,)).fetchone()[0]
    assert raw == "[]"  # gone from the database, not just hidden


def test_unsaved_answers_are_deleted_after_30_days(env):
    import time as _t
    import store as S
    st = env.app.store
    keep = st.add_item(PHONE, "Saved", "body", "h_talk", "health", "en", saved=True)
    old = st.add_item(PHONE, "Old", "body", "h_talk", "health", "en", saved=False)
    st._db.execute("UPDATE items SET ts=? WHERE id IN (?, ?)", (int(_t.time()) - S.UNSAVED_TTL_S - 10, keep, old))
    st.purge()
    ids_left = {r[0] for r in st._db.execute("SELECT id FROM items")}
    assert keep in ids_left and old not in ids_left


def test_background_memory_cannot_bring_data_back_after_forget_me(env):
    import time as _t
    st = env.app.store
    onboard(env)
    started = _t.time() - 1
    post(env, text("forget me")); post(env, tap("fm_yes"))
    st.update_memory(PHONE, profile={"role": "CHO"}, since=started)
    assert st._db.execute("SELECT 1 FROM sessions WHERE user=?", (PHONE,)).fetchone() is None


def test_my_data_counts_everything_and_sends_a_full_file(env):
    onboard(env)
    post(env, text("my data"))
    assert button_ids(env)[0] == "md_file"
    post(env, tap("md_file"))
    doc = [m for m in env.sent if m["type"] == "document"][-1]
    assert doc["document"]["filename"] == "akwaaba-my-data.txt"
    text_file = env.uploads[-1]["bytes"].decode()
    assert "== session ==" in text_file and "== followups ==" in text_file


def test_skipping_the_role_is_saved_and_said(env):
    post(env, text("hi")); post(env, tap("lang_en")); post(env, pick("hr_skip"))
    s = env.app.store.get(PHONE)
    assert "role" not in s["profile"] and s["prefs"].get("role_skipped")
    assert any("No role set" in b for b in bodies(env))


def test_voice_question_before_setup_is_answered_after_setup(env, monkeypatch):
    import speech, voice
    monkeypatch.setattr(speech, "available", lambda: (True, "ready"))
    monkeypatch.setattr(speech, "transcribe", lambda audio, lang, codes: "How do I prevent malaria in children")
    monkeypatch.setattr(voice, "available", lambda *a: (True, "ready"))
    spoken = []
    monkeypatch.setattr(voice, "speak", lambda text, lang, **k: spoken.append(text) or b"OggS")
    post(env, {"type": "audio", "audio": {"id": "AUDIO1", "mime_type": "audio/ogg"}})
    post(env, tap("lang_en")); post(env, pick("hr_skip")); post(env, tap("vp_listen"))
    assert any(b.startswith("Answer to: [voice note] How do I prevent malaria") for b in bodies(env))
    assert spoken  # and, since they chose Listen, it came as a voice note too


def test_settings_open_as_a_form_with_current_values_and_save(env, monkeypatch):
    import json as _j
    import voice
    monkeypatch.setattr(voice, "available", lambda *a: (True, "ready"))
    onboard(env, detail="hr_mw")
    env.app.bot.flows = {"settings": {"id": "F9", "status": "DRAFT"}}
    env.sent.clear()
    post(env, pick("m_settings"))
    form = flow_messages(env)[-1]
    params = form["action"]["parameters"]
    assert params["flow_cta"] == "Open settings" and params["flow_id"] == "F9"
    start = params["flow_action_payload"]["data"]
    assert start["lang"] == "en" and start["role"] == "hr_mw" and start["voice"] == "read" and start["save"] == "Save"
    assert [b["reply"]["id"] for b in last_interactive(env)["action"]["buttons"]] == ["m_team", "m_share", "m_data"]
    assert not any(m["type"] == "interactive" and m["interactive"]["type"] == "list" for m in env.sent)  # no Send button
    submit(env, "settings", lang="tw", role="hr_cho", voice="listen")
    s = env.app.store.get(PHONE)
    assert s["lang"] == "tw" and s["profile"]["role"] == "Community health officer" and s["prefs"]["voice"]
    assert "Community health officer" in bodies(env)[-1]
    assert any(e.get("event") == "settings_saved" and "lang" in e["changed"] for e in events(env))


def test_settings_fall_back_to_the_list_without_the_form(env):
    onboard(env)
    env.app.bot.flows = {}
    post(env, pick("m_settings"))
    assert last_interactive(env)["type"] == "list"


def test_settings_form_can_clear_the_role(env):
    onboard(env, detail="hr_chn")
    env.app.bot.flows = {"settings": {"id": "F9", "status": "DRAFT"}}
    submit(env, "settings", lang="en", role="none", voice="read")
    assert "role" not in env.app.store.get(PHONE)["profile"]


def test_settings_form_speaks_twi(env):
    onboard(env, lang="lang_tw")
    env.app.bot.flows = {"settings": {"id": "F9", "status": "DRAFT"}}
    post(env, pick("m_settings"))
    data = flow_messages(env)[-1]["action"]["parameters"]["flow_action_payload"]["data"]
    assert data["save"] == "Kora" and data["role_label"] == "Wo dwuma"
    assert {"id": "hr_cho", "title": "Mpɔtam apɔmuden ɔfesa"} in data["roles"]
    assert flow_messages(env)[-1]["action"]["parameters"]["flow_cta"] == "Bue nhyehyɛeɛ"


def test_alert_draft_lists_signs_count_and_ages_separately():
    import alerts
    t = "Severe Cough\nWeakness\nChest pains\nAge range: 24 to 50\n10 people"
    d = alerts.draft([{"name": "unusual cluster of illness or deaths", "action": "Report.", "key": "cluster"}], t)
    assert "Number affected: 10 people" in d and "Ages: 24 to 50" in d
    assert "Signs reported: severe cough, weakness, chest pains\n" in d
    assert alerts.signs_list("3 children with fever and rash, aged 2-5 years, suspected TB") == "fever, rash, suspected TB"


def test_no_role_shows_every_task(env):
    post(env, text("hi")); post(env, tap("lang_en")); post(env, pick("hr_skip"))
    assert button_ids(env) == ["h_talk", "h_followup", "h_visitlog"]   # the tour mixes roles
    post(env, text("menu"))
    assert "m_more_tasks" in list_ids(env) and len(list_ids(env)) <= 10
    post(env, pick("m_more_tasks"))
    assert {"h_team", "h_visitlog", "h_supvisit"} <= set(list_ids(env))
    assert any("you'll see every task" in b for b in bodies(env))


def test_voice_notes_stay_short():
    import voice
    long = "This is a sentence about malaria prevention. " * 60
    assert len(voice.script(long)) <= voice.MAX_CHARS


# --------------------------------------------------------------------------- hardening, November 2026

def test_voice_note_emergency_gets_the_card_even_when_rate_limited(env, monkeypatch):
    onboard(env)
    import speech
    monkeypatch.setattr(speech, "available", lambda: (True, "ready"))
    monkeypatch.setattr(speech, "transcribe", lambda audio, lang, codes: "The baby is not breathing")
    monkeypatch.setattr(env.app.bot, "_rate_limited", lambda phone: True)
    env.sent.clear(); n = len(env.calls)
    post(env, {"type": "audio", "audio": {"id": "AUDIO1", "mime_type": "audio/ogg"}})
    P = __import__("prompts")
    assert P.EMERGENCY in bodies(env) and bodies(env)[-1] == P.RATE_LIMITED
    assert bodies(env).index(P.EMERGENCY) < bodies(env).index(P.RATE_LIMITED)
    assert len(env.calls) == n            # the limit still stops the normal answer


def test_runaway_voice_notes_hit_a_hard_stop(env, monkeypatch):
    onboard(env)
    import speech
    heard = []
    monkeypatch.setattr(speech, "available", lambda: (True, "ready"))
    monkeypatch.setattr(speech, "transcribe", lambda audio, lang, codes: heard.append(1) or "hello")
    bot = env.app.bot
    bot._recent[PHONE].extend([time.time()] * (3 * bot.rate_limit_per_hour + 1))
    post(env, {"type": "audio", "audio": {"id": "AUDIO1", "mime_type": "audio/ogg"}})
    assert not heard and bodies(env)[-1] == __import__("prompts").RATE_LIMITED


def test_app_emergency_gets_the_card_even_when_rate_limited(env, monkeypatch):
    onboard(env)
    auth = app_signin(env)
    monkeypatch.setattr(env.app.bot, "_rate_limited", lambda phone: True)
    r = env.client.post("/api/ask", headers=auth, json={"text": "She is bleeding heavily after delivery"})
    kinds = [k for k, _ in sse_events(r.text)]
    assert kinds == ["emergency", "error"]
    assert env.client.post("/api/ask", headers=auth, json={"text": "A talk on nets"}).status_code == 429


def test_forget_me_signs_the_app_out_too(env):
    onboard(env)
    auth = app_signin(env)
    assert env.client.get("/api/me", headers=auth).status_code == 200
    post(env, text("FORGET ME")); post(env, tap("forget_yes"))
    if env.client.get("/api/me", headers=auth).status_code == 200:   # confirm button id differs: forget directly
        env.app.store.forget(PHONE)
    assert env.client.get("/api/me", headers=auth).status_code == 401


def test_security_headers_and_no_public_api_docs(env):
    r = env.client.get("/app/")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in env.client.get("/").headers["content-security-policy"]
    assert env.client.get("/healthz").headers["referrer-policy"] == "no-referrer"
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert env.client.get(path).status_code == 404


def test_oversized_uploads_are_refused_before_they_are_read(env):
    r = env.client.post("/whatsapp", content=b"x", headers={"content-length": str(50 * 1024 * 1024)})
    assert r.status_code == 413
    onboard(env)
    auth = app_signin(env)
    big = env.client.post("/api/transcribe", headers=auth, files={"file": ("a.ogg", b"0" * (13 * 1024 * 1024))})
    assert big.status_code == 413


def test_uploads_must_be_what_they_claim(env):
    onboard(env)
    auth = app_signin(env)
    fake = "data:image/jpeg;base64," + base64.b64encode(b"<html>not a photo</html>").decode()
    assert env.client.post("/api/ask", headers=auth, json={"text": "look", "image": fake}).status_code == 400
    assert env.client.post("/api/ask", headers=auth, json={"text": "look", "image": "data:text/html,<b>x"}).status_code == 400
    assert env.client.post("/api/ask", headers=auth, json={"text": "look", "image": "data:image/png;base64,@@@"}).status_code == 400
    assert env.client.post("/api/ask", headers=auth, json={"text": "x" * 9000}).status_code == 422


def test_media_is_only_fetched_from_meta_hosts(env, monkeypatch):
    wa = env.app.wa
    real = env.meta.request

    def evil(method, url, **kw):
        if method == "GET" and url.endswith("/MEDIA1"):
            return httpx.Response(200, json={"url": "https://evil.example/x", "file_size": 10}, request=httpx.Request(method, url))
        return real(method, url, **kw)
    monkeypatch.setattr(env.meta, "request", evil)
    with pytest.raises(ValueError):
        wa.download_media("MEDIA1")
    with pytest.raises(ValueError):
        wa.download_media("../../me/accounts")


def test_twi_and_ewe_danger_phrases_run_in_shadow_until_reviewed(env, monkeypatch):
    import safety
    assert safety.fold("Ɔretu mogya") == "oretu mogya"
    for t, lang in [("Ɔbaa no retu mogya pii", "tw"), ("abofra no ontumi nhome", "tw"), ("owo aka no", "tw"),
                    ("Ʋu le dodom", "ee"), ("da ɖu ɖevia", "ee"), ("vidzia mele gbɔgbɔm o", "ee")]:
        assert safety.local_hit(t) == lang, t
        assert not safety.looks_like_emergency(t), t          # drafts never send a card by default
    for t in ["Me pɛ sɛ meka mosquito nets ho asɛm", "A talk on handwashing", "Meɖe kuku, nufiafia tso asrã ŋu"]:
        assert safety.local_hit(t) is None, t
    onboard(env)
    env.sent.clear()
    post(env, text("Ɔbaa no retu mogya pii"))
    assert __import__("prompts").EMERGENCY not in bodies(env)
    assert any(e["event"] == "emergency_shadow" and e["lang"] == "tw" for e in events(env))
    assert "mogya" not in Path(os.environ["USAGE_LOG"]).read_text()     # the log counts it, never quotes it
    monkeypatch.setenv("SAFETY_LOCAL", "on")
    assert safety.looks_like_emergency("Ɔbaa no retu mogya pii")
    env.sent.clear()
    post(env, text("Ʋu le dodom"))
    assert bodies(env)[0] == __import__("prompts").EMERGENCY


def test_sms_is_off_unless_configured_and_needs_the_key(env, monkeypatch):
    assert env.client.post("/sms/inbound/anything", data={"from": "+233200000000", "text": "hi", "id": "1"}).status_code == 404
    key = "k" * 32
    for k, v in {"SMS_PROVIDER": "africastalking", "AT_USERNAME": "sandbox", "AT_API_KEY": "x", "SMS_INBOUND_KEY": key}.items():
        monkeypatch.setenv(k, v)
    assert env.client.post("/sms/inbound/wrong", data={"from": "+233200000000", "text": "hi", "id": "1"}).status_code == 404
    import sms
    out = []
    monkeypatch.setattr(sms.Sender, "send", lambda self, phone, text: out.append((phone, text)) or True)
    form = {"from": "+233200000000", "text": "Pregnant woman bleeding heavily, what do I do?", "id": "AT1"}
    assert env.client.post(f"/sms/inbound/{key}", data=form).status_code == 200
    assert out[0] == (PHONE, sms.EMERGENCY) and len(out) == 2
    assert len(out[1][1]) <= sms.MAX_CHARS and "*" not in out[1][1]
    assert "at most 350 characters" in env.calls[-1]["system"] and env.calls[-1]["max_tokens"] <= 350
    env.client.post(f"/sms/inbound/{key}", data=form)                     # the provider retries: no second answer
    assert len(out) == 2
    env.client.post(f"/sms/inbound/{key}", data={"from": "+233200000000", "text": "FORGET ME", "id": "AT2"})
    assert out[-1][1] == sms.FORGOTTEN and not env.app.store.get(PHONE)["mode"]


def test_sms_text_is_plain_and_short():
    import sms
    long = "*Go now.* " + "Keep the child warm and give fluids. " * 30
    s = sms.shorten(long)
    assert len(s) <= sms.MAX_CHARS and s.endswith(".") and "*" not in s
    assert sms.shorten("Ma ɔyarefoɔ no nsuo 🙂") == "Ma ɔyarefoɔ no nsuo"
    assert sms.phone_digits("+233 20 000 0000") == "233200000000" and sms.phone_digits("abc") is None


def test_supervisor_hears_when_a_referred_patient_did_not_arrive(env):
    NURSE, VOL2 = PHONE, "233200000077"
    onboard(env)
    code = env.app.store.team_code(NURSE)
    onboard_as(env, VOL2, "hr_chv")
    post(env, text(f"JOIN {code}"), phone=VOL2)
    post(env, pick("h_referral"), phone=VOL2); post(env, pick("rr_fever"), phone=VOL2); post(env, text("Card 12"), phone=VOL2)
    rid = env.app.store.referrals_for(VOL2)[0]["id"]
    post(env, tap(f"rf_no:{rid}"), phone=VOL2)
    assert any("has not reached the facility" in b and "Card 12" in b for b in sent_to(env, NURSE))


# --------------------------------------------------------------------------- district surveillance desk

def _sig(days_ago, disease, count, zone, status="new", today=None):
    import datetime as dt
    day = (today or dt.date(2026, 11, 12)) - dt.timedelta(days=days_ago)
    return {"id": 1, "ts": int(dt.datetime(day.year, day.month, day.day, 10).timestamp()), "disease": disease,
            "count": count, "zone": zone, "status": status}


def test_surveillance_thresholds_are_applied_in_code():
    import datetime as dt
    import surveillance as sv
    today = dt.date(2026, 11, 12)                                  # a Thursday
    assert sv.epi_week(today) == (2026, 46) and sv.week_start(today) == dt.date(2026, 11, 9)
    assert sv.head_count("7 children with rash and fever") == 7 and sv.head_count("several people vomiting") == 3
    assert sv.head_count("a child with a rash") == 1
    assert sv.zone_of({"facility": "Adaklu CHPS"}) == "Adaklu CHPS" and sv.zone_of({}) == "Zone not set"
    levels = lambda sigs: [(f["level"], f["disease"], f["zone"]) for f in sv.flags(sigs, today)]
    # a new signal must be verified; once verified, one suspected case is the alert threshold (2002 Table 6)
    assert levels([_sig(1, "cholera", 1, "A")]) == [("verify", "cholera", "A")]
    assert levels([_sig(1, "cholera", 1, "A", "verified")]) == [("alert", "cholera", "A")]
    assert "1 suspected case" in sv.flags([_sig(1, "cholera", 1, "A", "verified")], today)[0]["say"]
    # an unverified signal does not vanish when the week turns over, and goes overdue after 48 hours
    old = sv.flags([_sig(5, "measles", 1, "A")], today)
    assert old[0]["level"] == "verify" and old[0]["overdue"] and "Overdue" in old[0]["say"]
    assert not sv.flags([_sig(1, "measles", 1, "A")], today)[0]["overdue"]
    assert not sv.flags([_sig(20, "measles", 1, "A")], today)       # past two weeks it leaves the list
    # measles: more than 5 from one facility in a week is a suspected outbreak; exactly 5 is not
    assert ("action", "measles", "A") in levels([_sig(1, "measles", 6, "A", "verified")])
    assert ("action", "measles", "A") not in levels([_sig(1, "measles", 5, "A", "verified")])
    assert ("action", "measles", "A") in levels([_sig(2, "measles", 3, "A", "verified"), _sig(1, "measles", 3, "A", "verified")])
    # meningitis in a small population: 2 in a week is the alert, 5 is the epidemic threshold (only the higher one shows)
    assert ("alert", "meningitis", "A") in levels([_sig(1, "meningitis", 2, "A", "verified")])
    five = levels([_sig(1, "meningitis", 5, "A", "verified")])
    assert ("action", "meningitis", "A") in five and ("alert", "meningitis", "A") not in five
    # discarded signals never count, an unexplained cluster calls for investigation, and spread is our own prompt
    assert not sv.flags([_sig(1, "measles", 9, "A", "discarded")], today)
    assert levels([_sig(1, "cluster", 4, "A", "verified")]) == [("alert", "cluster", "A")]
    spread = sv.flags([_sig(1, "measles", 1, "A", "verified"), _sig(0, "measles", 1, "B", "verified")], today)
    assert spread[-1]["level"] == "watch" and "not a guideline threshold" in spread[-1]["say"]
    # every rule that fires can be traced to the thresholds file
    T = sv.THRESHOLDS
    assert "2002" in T["source"] and T["verify_hours"] == 48 and T["source_url"].startswith("https://")


def test_weekly_bulletin_is_built_from_counts_and_says_it_is_a_draft():
    import datetime as dt
    import surveillance as sv
    today = dt.date(2026, 11, 12)
    sigs = [_sig(1, "cholera", 3, "Zone E"), _sig(2, "measles", 2, "Zone B", "verified"), _sig(9, "measles", 1, "Zone B", "verified"),
            _sig(1, "measles", 9, "Zone F", "discarded")]
    b = sv.bulletin(sigs, "Sample District", today, zones_expected=6)
    assert "Week 46, 2026 (week to date)" in b and "Sample District" in b and "DRAFT" in b
    assert "Due at the region by Thursday 19 November." in b
    assert "Signals received: 2 from 2 of 6 reporting zones." in b and "4 zones sent nothing" in b
    assert "- Cholera: 3 (0)" in b and "- Measles: 2 (1)" in b and "9" not in b.split("THRESHOLDS")[0].split("SUSPECTED")[1]
    assert "ALERT THRESHOLD: measles, Zone B (2)" in b and "not confirmed diagnoses" in b
    assert "Verify it" not in b                                   # the to-do list for the officer is not part of the report
    assert "true zero report" in sv.bulletin([], "Sample District", today)
    # early in the week, the report that is due is last week's, complete
    last = sv.bulletin(sigs, "Sample District", dt.date(2026, 11, 17), zones_expected=6, last_complete=True)
    assert "Week 46, 2026\n" in last and "9 November to 15 November 2026" in last and "- Cholera: 3 (0)" in last
    wk = sv.weekly(sigs, today)
    assert wk["cases"]["measles"][-2:] == [1, 2] and len(wk["weeks"]) == 8
    assert all(s["zone"].endswith("(sample)") for s in sv.sample(today)) and sv.flags(sv.sample(today), today)


def test_an_alert_in_whatsapp_becomes_a_signal_on_the_district_desk(env):
    onboard(env)
    post(env, pick("h_alert"))
    post(env, text("7 children with fever and a rash all over, red eyes and cough in the same school"))
    sigs = env.app.store.signals()
    assert len(sigs) == 1 and sigs[0]["disease"] == "measles" and sigs[0]["count"] == 7 and sigs[0]["status"] == "new"
    assert "school" not in json.dumps(sigs)                        # the report text is never stored in the signal
    key = env.app.ids.dashboard_key()
    assert env.client.get("/district").status_code == 403 and env.client.get("/district?key=nope").status_code == 403
    assert env.client.get("/district?key=cl\u00e9").status_code == 403           # an odd character is refused, not an error
    page = env.client.get(f"/district?key={key}")
    assert page.status_code == 200 and "Measles" in page.text and PHONE not in page.text and "Verify within 48 hours" in page.text
    assert "form-action 'self'" in page.headers["content-security-policy"] and page.headers["cache-control"] == "no-store"
    demo = env.client.get(f"/district?key={key}&demo=1")
    assert "Every signal on this page is invented" in demo.text and "/district/signal" not in demo.text
    # the officer verifies it: no longer "report immediately", still counted
    bad = env.client.post("/district/signal", data={"key": "nope", "id": sigs[0]["id"], "status": "verified"})
    assert bad.status_code == 403
    assert env.client.post("/district/signal", data={"key": key, "id": sigs[0]["id"], "status": "hacked"}).status_code == 400
    ok = env.client.post("/district/signal", data={"key": key, "id": sigs[0]["id"], "status": "verified"}, follow_redirects=False)
    assert ok.status_code == 303 and env.app.store.signals()[0]["status"] == "verified"
    # the supervisor can ask for the bulletin on WhatsApp, and FORGET ME removes the signal
    env.sent.clear()
    post(env, text("bulletin"))
    assert any("WEEKLY SURVEILLANCE BULLETIN (DRAFT)" in b for b in bodies(env)), bodies(env)
    env.app.store.forget(PHONE)
    assert env.app.store.signals() == []


def test_dhis2_export_builds_a_valid_weekly_data_value_set():
    import datetime as dt
    import dhis2_export as dx
    mapping = json.loads((Path(__file__).resolve().parent.parent / "dhis2_mapping.example.json").read_text())
    today = dt.date(2026, 11, 12)
    sigs = [_sig(1, "cholera", 3, "Zone E"), _sig(2, "measles", 2, "Zone B", "verified"), _sig(9, "measles", 4, "Zone B", "verified"),
            _sig(1, "measles", 9, "Zone F", "discarded"), _sig(0, "cluster", 4, "Zone E")]
    p = dx.payload(sigs, mapping, today)
    assert p["period"] == "2026W46" and p["orgUnit"] == "SampleDist1" and p["completeDate"] == "2026-11-12"
    vals = {v["dataElement"]: v["value"] for v in p["dataValues"]}
    assert vals["AkwCholera1"] == "3" and vals["AkwMeasles1"] == "2"        # last week's and discarded cases are left out
    assert vals["AkwRabies01"] == "0" and len(vals) == 8                     # zero reporting for everything mapped
    assert p["_notMapped"] == ["cluster"] and all("categoryOptionCombo" not in v for v in p["dataValues"])
    assert [v["value"] for v in dx.payload(sigs, mapping, today, zero_report=False)["dataValues"]] == ["3", "2"]
    assert dx.period(dt.date(2027, 1, 3)) == "2026W53"                       # the week belongs to the year it started in
    with pytest.raises(ValueError):
        dx.payload(sigs, {**mapping, "orgUnit": "not-an-id"}, today)


def test_the_demonstration_week_runs_on_the_real_code():
    import html as h
    import scenario
    steps = scenario.build()
    assert len(steps) == 10 and steps[0]["chat"] is None and "Nothing needs attention" in steps[0]["desk"]["flags"]
    assert [r["kind"] for r in steps[1]["chat"]["replies"]] == ["alert"] and "Verify within 48 hours" in steps[1]["desk"]["flags"]
    assert "Alert threshold" in steps[2]["desk"]["flags"] and "Verify within 48 hours" not in steps[2]["desk"]["flags"]
    assert [r["kind"] for r in steps[3]["chat"]["replies"]] == ["emergency", "alert"]      # the card goes first
    assert "not a guideline threshold" in steps[4]["desk"]["flags"]
    assert "Zone B" in steps[5]["desk"]["flags"] and "Zone B" not in steps[6]["desk"]["flags"]   # the false signal is discarded
    assert "Outbreak threshold" not in steps[7]["desk"]["flags"] and "Outbreak threshold" in steps[8]["desk"]["flags"]
    last = steps[-1]
    payload = json.loads(h.unescape(last["dhis2"]))
    vals = {v["dataElement"]: v["value"] for v in payload["dataValues"]}
    assert payload["period"] == "2026W46" and vals["AkwCholera1"] == "5" and vals["AkwMeasles1"] == "7"
    assert "3 zones sent nothing" in h.unescape(last["desk"]["bulletin"])
    assert all(s["dhis2"] is None for s in steps[:-1])
