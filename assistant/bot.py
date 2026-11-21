"""Akwaaba's conversation logic: onboarding, menus, guided tasks, answers,
voice notes, safety fast-path, feedback and memory.

The HTTP layer (app.py) hands every incoming WhatsApp message to Bot.handle().
"""

import base64
import json
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote

import anthropic

import i18n
import media
import memory
import places
import prompts
import quiz
import safety
import redact
import register
import alerts
import surveillance
import datetime as dt
import followups
import stock
import speech
import voice
voice_mod = voice  # _answer has a parameter called voice
from formatting import to_whatsapp, trim_to_sentence
from whatsapp import SendError, TokenError

log = logging.getLogger("akwaaba")


DONE_WORDS = ("done", "finished", "finish", "awie", "ewu enu", "ewuenu", "✅ done", "✅ awie", "✅ ewu enu")


class ClaudeBusy(RuntimeError):
    pass

IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
POSITIVE = set("\U0001F44D❤\U0001F64F\U0001F60D\U0001F525\U0001F44F\U0001F4AF✅\U0001F60A\U0001F970\U0001F929\U0001F600\U0001F603")
NEGATIVE = set("\U0001F44E\U0001F621\U0001F61E\U0001F622❌\U0001F620\U0001F615")
FEEDBACK_EVERY = 6
COMMAND_WORDS = {"bulletin", "weekly bulletin", "menu", "start", "settings", "hi", "hello", "hey", "akwaaba", "help", "reset", "more",
                 "en", "tw", "ee", "twi", "ewe", "english",
                 "my data", "mydata", "privacy", "forget me", "library", "my library", "quiz",
                 "clinic", "hospital", "nearest", "voice on", "voice off", "login", "log in", "app code",
                 "start over", "restart", "main menu", "home", "back", "role", "my role",
                 "due", "follow ups", "followups", "follow-ups", "stock", "stock check", "team", "my team",
                 "referrals", "refer", "alert", "visit", "log visit"}
FLOWS_DIR = Path(__file__).resolve().parent / "flows"
FLOW_KEYS = ("setup", "danger", "settings")
# Danger-sign checklist items that mean "go to a facility now" (WHO/IMCI general danger signs).
RED_FLAGS = {"cant_drink", "vomits_all", "convulsions", "unconscious", "breathing", "chest_in", "stiff_neck",
             "dehydrated", "bleeding", "headache_vision", "no_movement", "newborn_temp"}


def load_flows() -> tuple[dict, dict]:
    """Returns (registry {key: {id, status}}, labels {option_id: title}) from flows/."""
    registry, labels = {}, {}
    try:
        registry = json.loads((FLOWS_DIR / "registry.json").read_text())
    except (OSError, json.JSONDecodeError):
        pass
    # Forms created by hand in WhatsApp Manager: FLOW_SETUP_ID=..., FLOW_DANGER_ID=...
    # (FLOW_ONBOARDING_ID is ignored on purpose: that older form still asked "teacher or health worker".)
    status = os.getenv("FLOWS_STATUS", "PUBLISHED").upper()
    registry = {k: v for k, v in registry.items() if k in FLOW_KEYS}
    for key in FLOW_KEYS:
        fid = os.getenv(f"FLOW_{key.upper()}_ID", "").strip()
        if fid:
            registry[key] = {"id": fid, "status": status}
    for f in FLOWS_DIR.glob("*.json"):
        if f.stem not in FLOW_KEYS:
            continue
        try:
            for screen in json.loads(f.read_text()).get("screens", []):
                for c in screen["layout"]["children"]:
                    source = c.get("data-source", [])
                    for item in source if isinstance(source, list) else []:  # "${data...}" lists are filled in when sent
                        labels[item["id"]] = item["title"]
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return registry, labels


MENU_WORDS = {"start over", "restart", "main menu", "home", "back"}


def _health_only(s: dict) -> None:
    """Drops a guided task that no longer exists (teacher tasks were removed).
    Teacher accounts themselves are moved over in Store.get."""
    if s.get("task") and s["task"] not in prompts.TASKS:
        s["task"] = None


class Bot:
    def __init__(self, wa, claude, store, log_event, model: str, max_tokens: int,
                 rate_limit_per_hour: int = 40, public_base_url: str = "", display_number: str = ""):
        self.wa, self.claude, self.store, self.log_event = wa, claude, store, log_event
        self.model, self.max_tokens = model, max_tokens
        self.rate_limit_per_hour = rate_limit_per_hour
        self.public_base_url = public_base_url.rstrip("/")
        self.display_number = display_number
        self._recent: dict[str, deque] = defaultdict(deque)
        self.flows, self.flow_labels = load_flows()

    # ================================================================ entry point

    def handle(self, msg: dict) -> None:
        phone, message_id = msg.get("from", ""), msg.get("id", "")
        jobs = []
        with self.store.user_lock(phone):
            s = self.store.get(phone)
            _health_only(s)
            i18n.use(s)  # menus and fixed messages follow this person's language
            try:
                jobs = self._route(phone, s, msg, message_id) or []
            except TokenError:
                self.log_event(phone, event="error", kind="token")
            except Exception:
                log.exception("Failed to handle message")
                self.log_event(phone, event="error", kind="unexpected")
                try:
                    self.wa.send_text(phone, prompts.ERROR)
                except Exception:
                    pass
            finally:
                i18n.use(None)
                s.pop("_card_sent", None)
                s.pop("_shadow_logged", None)
                if not s.get("_forgotten"):
                    self.store.save(phone, s)
        for job in jobs if isinstance(jobs, list) else []:  # memory updates run after the reply is sent
            if not callable(job):
                continue
            try:
                job()
            except Exception as e:
                log.warning("Memory update skipped: %s", e)

    # ================================================================ routing

    def _route(self, phone: str, s: dict, msg: dict, message_id: str):
        p = parse(msg)
        kind = p["kind"]
        s["last_in"] = int(time.time())  # opens WhatsApp's 24-hour reply window
        if kind not in ("system", "unsupported", "ephemeral", None):
            self._flush_outbox(phone, s)
        if kind == "reaction":
            self._reaction_feedback(phone, msg)
            return
        if kind in ("system", "unsupported", "ephemeral", None):
            return
        # Safety first, before rate limits, onboarding or any guided task: typed text is
        # checked here, voice notes as soon as they're transcribed (below).
        if kind == "text":
            self._card_first(phone, s, p["text"])
        limited = self._rate_limited(phone)
        if limited and kind == "audio" and len(self._recent[phone]) <= 3 * self.rate_limit_per_hour:
            # Safety before the limit for voice notes too: listen, send the emergency card
            # if it's needed, and only then apply the limit. (A hard stop at three times
            # the limit keeps a runaway sender from using up the speech service.)
            heard = self._voice(phone, s, p, message_id)
            if heard:
                self._card_first(phone, s, heard)
        if limited:
            self.log_event(phone, event="rate_limited")
            self.wa.send_text(phone, prompts.RATE_LIMITED)
            return
        if kind == "location":
            s["step"] = None if s.get("step") == "near_place" else s.get("step")
            return self._nearest(phone, s, p["location"], message_id)
        if kind not in ("text", "interactive", "button", "image", "document", "audio"):
            self.wa.send_text(phone, prompts.UNSUPPORTED_TYPE)
            return

        if p.get("flow") is not None:
            return self._flow_reply(phone, s, p["flow"], message_id)
        if p["button"]:
            return self._button(phone, s, p["button"], message_id)

        if s.get("step") == "register" and kind in ("image", "document"):
            return self._register_page(phone, s, kind, p, message_id)
        if s.get("step") == "register" and kind == "text" and p["text"].strip().lower().strip(".!") in DONE_WORDS:
            return self._register_done(phone, s, message_id)

        text, attachments, voice = p["text"], [], False
        if kind == "audio":
            text = self._voice(phone, s, p, message_id)
            if text is None:
                return
            voice = True
            self._card_first(phone, s, text)
        elif p["media_id"]:
            attachments = self._attachment(phone, kind, p, s)
            if attachments is None:
                return

        if not attachments and text and re.match(r"^\s*join\b", text, re.I) and s["mode"]:
            return self._join(phone, s, text)
        if not attachments and self._command(phone, s, text, message_id):
            return
        step = str(s.get("step") or "")
        if text and not attachments and step.startswith(("visit:", "ref:")) or step in ("alert", "notice") and text and not attachments:
            if step.startswith("visit:"):
                return self._visit_log(phone, s, step.split(":", 1)[1], text)
            if step.startswith("ref:"):
                return self._refer(phone, s, step.split(":", 1)[1], text)
            if step == "alert":
                return self._alert(phone, s, text)
            return self._notice(phone, s, text)
        if str(s.get("step") or "").startswith("fu_add:") and text and not attachments:
            return self._fu_add(phone, s, s["step"].split(":", 1)[1], text)
        if s.get("step") == "stock" and text and not attachments:
            return self._stock(phone, s, text, message_id)

        if s["mode"] is None:  # not set up yet: keep the question, answer after setup
            if text and not attachments:
                s["pending"] = (("[voice note] " if voice else "") + text)[:1000]
            self._welcome(phone, s, again=s["welcomed"])
            return
        if s.get("step") == "near_place" and text and not attachments:
            s["step"] = None
            if len(text) <= 50 and len(text.split()) <= 5:  # a place name, not a new question
                return self._nearest_place(phone, s, text, message_id)
        place = _facility_place(text) if text and not attachments else None
        if place:
            return self._nearest_place(phone, s, place, message_id)
        if s.get("step") == "quiz_topic" and text and not attachments:
            return self._quiz_start(phone, s, text, message_id)
        if s.get("step") in ("need_lang", "need_detail", "need_voice") and not attachments:
            s["pending"] = text[:1000]
            return self._resume_onboarding(phone, s)
        if not text and not attachments:
            return
        return self._answer(phone, s, ("[voice note] " if voice else "") + text, attachments,
                            message_id, voice=voice)

    # ================================================================ onboarding & menus

    def _card_first(self, phone, s, text) -> bool:
        """Send the emergency card at most once per message, before anything else happens."""
        if not text or s.get("_card_sent"):
            return False
        if not safety.looks_like_emergency(text):
            hit = safety.local_hit(text)
            if hit and not s.get("_shadow_logged"):   # draft Twi/Ewe phrases: counted, never shown
                self.log_event(phone, event="emergency_shadow", lang=hit)
                s["_shadow_logged"] = True
            return False
        self.wa.send_buttons(phone, prompts.EMERGENCY, prompts.EMERGENCY_NEAR_BUTTON)
        self.log_event(phone, event="emergency_card", mode=s.get("mode") or "health", source="fast_path")
        s["_card_sent"] = True
        return True

    def _send_flow(self, phone, key: str, body: str, cta: str, screen: str, header: str | None = None,
                   data: dict | None = None) -> bool:
        """Open one of our native forms. Returns False (so callers fall back) if unavailable."""
        f = self.flows.get(key)
        if not f or not f.get("id"):
            return False
        try:
            self.wa.send_flow(phone, body, cta, f["id"], f"{key}:{secrets.token_hex(4)}", screen,
                              mode="published" if f.get("status") == "PUBLISHED" else "draft", header=header,
                              data=data)
            return True
        except SendError as e:
            log.warning("Flow %s couldn't be sent, using buttons instead: %s", key, e)
            return False

    def _welcome(self, phone, s, again=False):
        body = prompts.PICK_LANG_FIRST if again and s.get("pending") else prompts.WELCOME
        image = f"{self.public_base_url}/static/welcome.png" if self.public_base_url and not again else None
        if self.flows.get("setup"):
            if image:
                self.wa.send_image(phone, image)
            nudge = body.replace("First, which language should I reply in?", prompts.SETUP_NUDGE)
            if self._send_flow(phone, "setup", nudge,
                               prompts.SETUP_CTA, "SETUP"):
                s["welcomed"] = True
                self.log_event(phone, event="welcome", via="flow")
                return
            image = None  # already shown
        self.wa.send_buttons(phone, body, prompts.LANGUAGE_BUTTONS, footer=prompts.WELCOME_FOOTER, image_url=image)
        s["welcomed"] = True
        s["step"] = "need_lang"
        self.log_event(phone, event="welcome")

    def _language_menu(self, phone):
        self.wa.send_buttons(phone, prompts.LANGUAGE_PROMPT, prompts.LANGUAGE_BUTTONS)

    def _detail_menu(self, phone, s):
        body, rows = prompts.DETAIL_PROMPT[prompts.MODE]
        self.wa.send_list(phone, body, "Choose", [("Options", rows)])

    def _resume_onboarding(self, phone, s):
        if s["step"] == "need_lang":
            self._language_menu(phone)
        elif s["step"] == "need_voice":
            self.wa.send_buttons(phone, prompts.VOICE_PREF_ASK, prompts.VOICE_PREF_BUTTONS)
        else:
            self._detail_menu(phone, s)

    def _detail_line(self, s) -> str:
        d = s["profile"].get("role")
        return f" · {d}" if d else ""

    def _menu(self, phone, s, body: str | None = None):
        if prompts.has_role(s):
            rows = [(tid, prompts.TASKS[tid]["title"], prompts.TASKS[tid]["desc"])
                    for tid in prompts.MENU_BY_ROLE[prompts.role_group(s)]]
        else:  # exploring without a role: main tasks, then everything else one tap away
            rows = [(tid, prompts.TASKS[tid]["title"], prompts.TASKS[tid]["desc"]) for tid in prompts.MENU_EXPLORE]
            rows.append(prompts.MORE_TASKS_ROW)
        self.wa.send_list(phone, body or prompts.MENU_BODY, prompts.MENU_BUTTON, [
            (prompts.MENU_SECTION[s["mode"]], rows),
            (prompts.MENU_MORE_SECTION, prompts.MENU_EXTRA_ROWS),
        ])

    def _finish_onboarding(self, phone, s, message_id):
        prefs = s.setdefault("prefs", {})
        if ("voice" not in prefs and not prefs.get("voice_asked") and voice.available(s["lang"])[0]
                and voice.lang_enabled(s["lang"])):
            # One last question: people who speak Twi or Ewe often read it less easily, so listening comes first.
            prefs["voice_asked"] = True
            s["step"] = "need_voice"
            return self.wa.send_buttons(phone, prompts.VOICE_PREF_ASK, prompts.VOICE_PREF_BUTTONS)
        s["step"] = None
        lang = prompts.LANG_SHORT.get(s["lang"], "English")
        ready = prompts.READY[s["mode"]].format(lang=lang, detail=self._detail_line(s))
        if not s["profile"].get("role"):  # they skipped the role: say so, so the choice is visibly saved
            ready += "\n\n" + i18n.tr(prompts.ROLE_SKIPPED_NOTE)
        self.wa.send_text(phone, ready)
        self.log_event(phone, event="onboarded", mode=s["mode"], lang=s["lang"])
        if s.get("pending"):
            q, s["pending"] = s["pending"], None
            return self._answer(phone, s, q, [], message_id)
        self._tour(phone, s)

    def _tour(self, phone, s):
        """First use: three big buttons for the most common jobs, so nobody needs a guide or a command."""
        buttons = []
        tour = prompts.TOUR_TASKS.get(prompts.role_group(s), prompts.TOUR_TASKS["nurse"]) if prompts.has_role(s) \
            else prompts.TOUR_EXPLORE
        for tid in tour:
            title = i18n.tr(prompts.TASKS[tid]["title"])
            if len(title) > 20:  # WhatsApp's button limit: drop the emoji first
                title = re.sub(r"^\W+\s*", "", title)[:20]
            buttons.append((tid, title))
        self.wa.send_buttons(phone, prompts.TOUR_BODY, buttons)
        self.log_event(phone, event="tour")

    # ================================================================ buttons & list choices

    def _button(self, phone, s, bid: str, message_id: str):
        if bid.startswith("role_"):  # buttons from before Akwaaba became health-only
            return self._menu(phone, s) if s["mode"] else self._welcome(phone, s)

        if bid.startswith("lang_"):
            s["lang"] = bid.split("_", 1)[1]
            self.log_event(phone, event="language", lang=s["lang"])
            if s["mode"] is None:  # first time: language chosen, now the role
                s.update(mode=prompts.MODE, history=[], task=None)
                self.log_event(phone, event="mode", mode=prompts.MODE)
                s["step"] = "need_detail"
                return self._detail_menu(phone, s)
            if s.get("step") == "need_lang":
                s["step"] = "need_detail"
                return self._detail_menu(phone, s)
            return self.wa.send_text(phone, f"Done ✅ I'll reply in *{prompts.LANGUAGES[s['lang']]}*.")

        if bid.startswith(("lvl_", "hr_")):
            if bid in prompts.HEALTH_ROLE_VALUES:
                s["profile"]["role"] = prompts.HEALTH_ROLE_VALUES[bid]
            elif bid == "hr_skip":
                s["profile"].pop("role", None)
                s.setdefault("prefs", {})["role_skipped"] = True
            self.log_event(phone, event="detail", detail=bid)
            if s.get("step") == "need_detail" and not s["history"] and s["answers"] == 0:
                return self._finish_onboarding(phone, s, message_id)
            s["step"] = None
            self.wa.send_text(phone, "Got it ✅")
            return self._menu(phone, s)

        if bid in ("vp_listen", "vp_read"):
            listen = bid == "vp_listen"
            prefs = s.setdefault("prefs", {})
            prefs.update(voice=listen, voice_first=listen, voice_asked=True)
            self.log_event(phone, event="voice_pref", listen=listen, lang=s["lang"])
            self.wa.send_text(phone, prompts.VOICE_PREF_LISTEN if listen else prompts.VOICE_PREF_READ)
            if s.get("step") == "need_voice":
                return self._finish_onboarding(phone, s, message_id)
            return None

        if bid in prompts.TASKS:
            return self._start_task(phone, s, bid)

        if bid.startswith("ex:"):
            _, task, i = bid.split(":")
            t = prompts.TASKS.get(task)
            if not t or s["mode"] is None:
                return self._menu(phone, s) if s["mode"] else self._welcome(phone, s)
            s["task"] = task
            example = t["examples"][int(i)]
            self.log_event(phone, event="example", task=task)
            if t.get("kind") == "quiz":
                return self._quiz_start(phone, s, example, message_id)
            return self._answer(phone, s, f"{t['title'].split(' ', 1)[1]}: {example}", [], message_id, learn=False)

        if bid.startswith(("fu_", "st_")):
            return self._fu_button(phone, s, bid)
        if bid.startswith(("vt_", "vl_", "rr_", "rf_", "tm_", "al_", "h:")) or bid == "m_team":
            return self._team_button(phone, s, bid)

        if bid in prompts.RESET_BUTTONS:
            nxt = prompts.RESET_BUTTONS[bid]
            if nxt:
                return self._start_task(phone, s, nxt)
            s["task"] = None
            return self._menu(phone, s)

        if bid in prompts.FOLLOW_UP_PROMPTS:
            self.log_event(phone, event="button", button=bid)
            if s["mode"] is None:
                return self._welcome(phone, s)
            return self._answer(phone, s, prompts.FOLLOW_UP_PROMPTS[bid], [], message_id, learn=False)

        if bid in ("fb_yes", "fb_no"):
            self.log_event(phone, event="feedback", positive=bid == "fb_yes", source="button")
            return self.wa.send_text(phone, prompts.FEEDBACK_THANKS_YES if bid == "fb_yes"
                                     else prompts.FEEDBACK_THANKS_NO)

        if bid == "rg_done":
            return self._register_done(phone, s, message_id)
        if bid == "rg_cancel":
            s.update(step=None, task=None, register={})
            return self.wa.send_text(phone, prompts.REGISTER_CANCELLED)
        if bid == "m_menu":
            return self._menu(phone, s) if s["mode"] else self._welcome(phone, s)
        if bid == "m_share":
            return self._share(phone)
        if bid == "m_settings":
            return self._settings(phone, s)
        if bid == "m_voice":
            return self._toggle_voice(phone, s)
        if bid == "m_more_tasks":
            rows = [(tid, prompts.TASKS[tid]["title"], prompts.TASKS[tid]["desc"]) for tid in prompts.MENU_EXPLORE_MORE]
            return self.wa.send_list(phone, prompts.MORE_TASKS_BODY, prompts.MENU_BUTTON,
                                     [(prompts.MENU_SECTION[s["mode"]], rows)])
        if bid == "m_library":
            return self._library(phone, s)
        if bid.startswith("lib:"):
            return self._library(phone, s, int(bid.split(":")[1] or 0))
        if bid == "m_near":
            return self._near_request(phone, s)
        if bid.startswith("qa:"):
            _, i, c = bid.split(":")
            return self._quiz_answer(phone, s, int(i), int(c), message_id)
        if bid in ("qz_again", "qz_harder"):
            qz = s.get("quiz") or {}
            if not qz.get("topic") or not s["mode"]:
                return self._menu(phone, s) if s["mode"] else self._welcome(phone, s)
            return self._quiz_start(phone, s, qz["topic"], message_id,
                                    difficulty="harder" if bid == "qz_harder" else qz.get("difficulty", "mixed"))
        if ":" in bid and bid.split(":")[0] in ("more", "say", "pdf", "card", "save", "unsave", "quizit", "open"):
            action, item_id = bid.split(":", 1)
            return self._item_action(phone, s, action, item_id, message_id)
        if bid == "m_data":
            return self._my_data(phone, s)
        if bid == "md_file":
            return self._my_data_file(phone)
        if bid == "fm_ask":
            return self.wa.send_buttons(phone, prompts.FORGET_CONFIRM, prompts.FORGET_BUTTONS)
        if bid == "fm_yes":
            return self._forget(phone, s)
        if bid == "ch_role":
            return self._detail_menu(phone, s) if s["mode"] else self._welcome(phone, s)
        if bid == "ch_lang":
            return self._language_menu(phone)
        if bid == "ch_detail":
            return self._detail_menu(phone, s) if s["mode"] else self._welcome(phone, s)
        if bid == "help":
            return self.wa.send_text(phone, prompts.HELP)

    def _start_task(self, phone, s, task: str):
        t = prompts.TASKS[task]
        s["task"], s["step"] = task, None
        self.log_event(phone, event="task", task=task)
        if t.get("kind") == "near":
            s["task"] = None
            return self._near_request(phone, s)
        if t.get("kind") == "quiz":
            s["step"] = "quiz_topic"
        if t.get("kind") in ("visitlog", "referral", "alert", "team", "notice"):
            s["task"] = None
            return self._team_task(phone, s, t["kind"], task)
        if t.get("kind") == "followup":
            s["task"] = None
            return self._fu_home(phone, s)
        if t.get("kind") == "stock":
            s["step"] = "stock"
            return self.wa.send_text(phone, f"*{t['title']}*\n{t['ask']}")
        if t.get("kind") == "register":
            if not register.photos_enabled():
                s["task"] = None
                self.log_event(phone, event="register_blocked")
                return self.wa.send_text(phone, prompts.REGISTER_OFF)
            s["step"], s["register"] = "register", {"pages": [], "hidden": 0, "month": register.report_month()}
            return self.wa.send_text(phone, prompts.REGISTER_ASK)
        form = t.get("form")
        if form and self._send_flow(phone, form["key"], form["body"], form["cta"], form["screen"],
                                    header=t["title"]):
            return
        ask = f"*{t['title']}*\n{t['ask']}"
        if t.get("examples"):
            buttons = [(f"ex:{task}:{i}", ex) for i, ex in enumerate(t["examples"][:3])]
            return self.wa.send_buttons(phone, ask + "\n\n_Or tap an example:_", buttons)
        return self.wa.send_text(phone, ask)

    # ================================================================ native forms (WhatsApp Flows)

    def _label(self, value) -> str:
        if isinstance(value, list):
            return ", ".join(self._label(v) for v in value)
        return self.flow_labels.get(str(value), str(value)) if value not in (None, "") else ""

    def _flow_reply(self, phone, s, data: dict, message_id):
        kind = str(data.get("flow_token", "")).split(":")[0]
        self.log_event(phone, event="form_submitted", form=kind)

        if kind in ("setup", "onboarding"):  # "onboarding" = the older form, still in some chats
            s.update(mode=prompts.MODE, lang=data.get("lang") if data.get("lang") in prompts.LANGUAGES else s["lang"],
                     step=None, task=None, welcomed=True)
            detail = data.get("detail")
            if detail in prompts.HEALTH_ROLE_VALUES:
                s["profile"]["role"] = prompts.HEALTH_ROLE_VALUES[detail]
            town = str(data.get("town") or "").strip()
            if 1 < len(town) <= 40:
                s["profile"]["town_or_region"] = town
            self.log_event(phone, event="mode", mode=prompts.MODE)
            return self._finish_onboarding(phone, s, message_id)

        if s["mode"] is None:
            return self._welcome(phone, s)

        if kind == "settings":
            return self._settings_saved(phone, s, data)

        if kind == "danger":
            request, red = self.form_request(s, kind, data)
            if red:  # deterministic: never wait for the model when a red flag is ticked
                self.wa.send_buttons(phone, prompts.EMERGENCY, prompts.EMERGENCY_NEAR_BUTTON)
                self.log_event(phone, event="emergency_card", mode="health", source="checklist")
            return self._answer(phone, s, request, [], message_id, emergency_checked=kind == "danger", learn=False)

    def form_request(self, s, kind: str, data: dict) -> tuple[str, bool]:
        """Turns a submitted form (WhatsApp Flow or the app) into a request for Claude.
        Returns (request, red_flag). Sets the guided task on the session."""
        s["task"] = "h_danger"
        signs = data.get("signs") or []
        signs = signs if isinstance(signs, list) else [signs]
        red = [x for x in signs if x in RED_FLAGS]
        notes = str(data.get("notes") or "").strip()[:500]
        days = str(data.get("days") or "").strip()
        return (f"Danger-sign check. Person: {self._label(data.get('who'))}. "
                f"Signs ticked: {self._label(signs) or 'none'}."
                f"{f' For {days} days.' if days else ''}{f' Notes: {notes}' if notes else ''}"
                f"{' One or more of these is a general danger sign.' if red else ''}"), bool(red)

    # ================================================================ typed shortcuts

    def _command(self, phone, s, text: str, message_id: str) -> bool:
        word = text.strip().lower().strip(".!?")
        if word not in COMMAND_WORDS:
            return False
        if word in ("role", "my role"):
            self._detail_menu(phone, s) if s["mode"] else self._welcome(phone, s)
        elif word in MENU_WORDS or word in ("menu", "start", "settings"):
            self._menu(phone, s) if s["mode"] else self._welcome(phone, s)
        elif word in ("hi", "hello", "hey", "akwaaba"):
            if s["mode"] and not s.get("step"):
                lang = prompts.LANG_SHORT.get(s["lang"], "English")
                self._menu(phone, s, prompts.WELCOME_BACK.format(
                    role=prompts.ROLE_NAMES[s["mode"]], lang=lang, detail=self._detail_line(s)))
                self.log_event(phone, event="welcome_back")
            elif s.get("step"):
                self._resume_onboarding(phone, s)
            else:
                self._welcome(phone, s)
        elif word == "help":
            self.wa.send_text(phone, prompts.HELP)
        elif word == "reset":
            s.update(history=[], task=None, summary="")
            self.wa.send_text(phone, "Fresh start ✅ What would you like help with?")
        elif word in ("en", "tw", "ee", "twi", "ewe", "english"):
            code = {"twi": "tw", "ewe": "ee", "english": "en"}.get(word, word)
            self._button(phone, s, f"lang_{code}", message_id)
        elif word == "more":
            if not s["mode"]:
                return False
            self._answer(phone, s, prompts.FOLLOW_UP_PROMPTS["f_more"], [], message_id, learn=False)
        elif word in ("my data", "mydata", "privacy"):
            self._my_data(phone, s)
        elif word == "forget me":
            self.wa.send_buttons(phone, prompts.FORGET_CONFIRM, prompts.FORGET_BUTTONS)
        elif word in ("library", "my library"):
            self._library(phone, s)
        elif word in ("due", "follow ups", "followups", "follow-ups"):
            if not s["mode"]:
                return False
            self._fu_due(phone, s)
        elif word in ("bulletin", "weekly bulletin"):
            if not s["mode"]:
                return False
            self._bulletin(phone, s)
        elif word in ("team", "my team"):
            if not s["mode"]:
                return False
            self._team_task(phone, s, "myteam", None)
        elif word in ("referrals", "refer", "alert", "visit", "log visit"):
            if not s["mode"]:
                return False
            self._start_task(phone, s, {"referrals": "h_referral", "refer": "h_referral", "alert": "h_alert"}.get(word, "h_visitlog"))
        elif word in ("stock", "stock check"):
            if not s["mode"]:
                return False
            self._start_task(phone, s, "h_stock")
        elif word in ("clinic", "hospital", "nearest"):
            self._near_request(phone, s)
        elif word == "quiz":
            if not s["mode"]:
                return False
            self._start_task(phone, s, "h_play")
        elif word in ("login", "log in", "app code"):
            code = self.store.new_code(phone)
            if code:
                self.wa.send_text(phone, i18n.tr(prompts.APP_CODE, s["lang"]).replace("{number}", code))
                self.log_event(phone, event="app_code", via="whatsapp")
            else:
                self.wa.send_text(phone, prompts.APP_CODE_WAIT)
        elif word in ("voice on", "voice off"):
            self._toggle_voice(phone, s, on=word == "voice on")
        return True

    # ================================================================ inputs

    def _voice(self, phone, s, p, message_id):
        ok, why = speech.available()
        if not ok:
            log.info("Voice note received but speech is off: %s", why)
            self.log_event(phone, event="voice_unavailable")
            self.wa.send_text(phone, prompts.VOICE_UNAVAILABLE)
            return None
        self.wa.react(phone, message_id, "\U0001F3A7")
        self.wa.typing(message_id)
        try:
            audio, _ = self.wa.download_media(p["media_id"])
            started = time.time()
            text = speech.transcribe(audio, s["lang"], prompts.ASR_CODES)
        except ValueError:
            self.wa.send_text(phone, prompts.FILE_TOO_LARGE)
            return None
        except speech.SpeechTooLong:
            self.wa.send_text(phone, prompts.VOICE_TOO_LONG)
            return None
        except speech.SpeechUnavailable:
            self.log_event(phone, event="voice_unavailable")
            self.wa.send_text(phone, prompts.VOICE_UNAVAILABLE)
            return None
        except speech.SpeechUnclear:
            self.log_event(phone, event="voice_unclear", lang=s["lang"])
            self.wa.send_text(phone, prompts.VOICE_UNCLEAR)
            return None
        self.log_event(phone, event="voice_transcribed", lang=s["lang"],
                       latency_s=round(time.time() - started, 2))
        shown = text if len(text) <= 300 else text[:297] + "..."
        self.wa.send_text(phone, prompts.VOICE_HEARD.format(text=shown))
        return text

    def _attachment(self, phone, kind, p, s=None):
        if kind == "image" and p["mime"] not in IMAGE_TYPES:
            self.wa.send_text(phone, prompts.BAD_FILE_TYPE)
            return None
        if kind == "document" and p["mime"] != "application/pdf":
            self.wa.send_text(phone, prompts.BAD_FILE_TYPE)
            return None
        try:
            data, mime = self.wa.download_media(p["media_id"])
        except ValueError:
            self.wa.send_text(phone, prompts.FILE_TOO_LARGE)
            return None
        prefs = s.setdefault("prefs", {}) if s is not None else None
        if prefs is not None and not prefs.get("photo_note"):
            prefs["photo_note"] = True
            self.wa.send_text(phone, prompts.PHOTO_PRIVACY_NOTE)
        return [{"type": "image" if kind == "image" else "document", "source": {
            "type": "base64", "media_type": mime or p["mime"], "data": base64.b64encode(data).decode()}}]

    # ================================================================ monthly return from register photos

    def _register_page(self, phone, s, kind, p, message_id):
        if not register.photos_enabled():
            s["step"], s["task"] = None, None
            return self.wa.send_text(phone, prompts.REGISTER_OFF)
        rg = s.get("register") or {}
        if kind != "image" or p["mime"] not in IMAGE_TYPES:
            return self.wa.send_text(phone, prompts.REGISTER_PHOTO_ONLY)
        pages = rg.setdefault("pages", [])
        if len(pages) >= register.MAX_PAGES:
            return self.wa.send_buttons(phone, prompts.REGISTER_TOO_MANY, prompts.REGISTER_BUTTONS)
        try:
            data, _ = self.wa.download_media(p["media_id"])
        except ValueError:
            return self.wa.send_text(phone, prompts.FILE_TOO_LARGE)
        self.wa.react(phone, message_id, "\U0001F512")
        self.wa.typing(message_id)
        fmt = register.load_format()
        try:
            clean, hidden = redact.redact(data, fmt)
        except redact.RedactionUnavailable as e:
            log.warning("Register photo refused, names can't be hidden: %s", e)
            self.log_event(phone, event="register_refused", reason="redaction")
            s.update(step=None, register={})
            return self.wa.send_text(phone, prompts.REGISTER_OFF)
        del data  # the original photo with names is never kept or sent anywhere
        try:
            page = register.read_page(self.claude, self.model, fmt, clean)
        except Exception as e:
            log.warning("Couldn't read register page: %s", e)
            return self.wa.send_text(phone, prompts.REGISTER_FAILED)
        if not page["counts"]:
            return self.wa.send_buttons(phone, prompts.REGISTER_EMPTY_PAGE, prompts.REGISTER_BUTTONS)
        pages.append({"counts": page["counts"], "unclear": page["unclear"]})
        rg["hidden"] = rg.get("hidden", 0) + hidden
        s["register"] = rg
        self.log_event(phone, event="register_page", fields=len(page["counts"]), hidden=hidden)
        unclear = prompts.REGISTER_UNCLEAR.format(k=len(page["unclear"])) if page["unclear"] else ""
        return self.wa.send_buttons(phone, prompts.REGISTER_PAGE_OK.format(
            n=len(pages), found=len(page["counts"]), unclear=unclear), prompts.REGISTER_BUTTONS)

    def register_app_page(self, phone, s, jpeg_or_png: bytes) -> dict:
        """The companion app sends register pages one at a time. Each page is added to
        this month's draft and the whole draft comes back, updated in place.
        Raises redact.RedactionUnavailable when names can't be hidden (nothing is sent)."""
        if not register.photos_enabled():
            raise redact.RedactionUnavailable("register photos are switched off")
        fmt = register.load_format()
        month = register.report_month()
        rg = s.get("register") or {}
        if rg.get("channel") != "app" or rg.get("month") != month or time.time() - rg.get("ts", 0) > 12 * 3600:
            rg = {"channel": "app", "pages": [], "hidden": 0, "month": month}
        clean, hidden = redact.redact(jpeg_or_png, fmt)
        page = register.read_page(self.claude, self.model, fmt, clean)
        if not page["counts"]:
            return {"reply": i18n.tr(prompts.REGISTER_EMPTY_PAGE), "item_id": None}
        if len(rg["pages"]) >= register.MAX_PAGES:
            return {"reply": i18n.tr(prompts.REGISTER_TOO_MANY), "item_id": rg.get("item_id")}
        rg["pages"].append({"counts": page["counts"], "unclear": page["unclear"]})
        rg["hidden"] = rg.get("hidden", 0) + hidden
        rg["ts"] = time.time()
        ret = register.compile_return(fmt, rg["pages"], self.store.previous_return(phone, month))
        body = register.as_text(fmt, ret, month, rg["hidden"]) + "\n\n" + i18n.tr(prompts.REGISTER_APP_MORE)
        self.store.save_return(phone, month, ret["totals"])
        title = f"Monthly return draft \u00b7 {register.month_name(month)}"
        if rg.get("item_id") and self.store.item(phone, rg["item_id"]):
            self.store.update_item_body(phone, rg["item_id"], title, body)
        else:
            rg["item_id"] = self.store.add_item(phone, title, body, "h_return", s["mode"], s["lang"], saved=True)
        s["register"] = rg
        self.log_event(phone, event="register_page", fields=len(page["counts"]), hidden=hidden, channel="app")
        return {"reply": body, "item_id": rg["item_id"]}

    def _register_done(self, phone, s, message_id):
        rg = s.get("register") or {}
        if not rg.get("pages"):
            return self.wa.send_text(phone, prompts.REGISTER_NO_PAGES)
        fmt = register.load_format()
        month = rg.get("month") or register.report_month()
        ret = register.compile_return(fmt, rg["pages"], self.store.previous_return(phone, month))
        body = register.as_text(fmt, ret, month, rg.get("hidden", 0))
        self.store.save_return(phone, month, ret["totals"])
        item_id = self.store.add_item(phone, f"Monthly return draft · {register.month_name(month)}", body,
                                      "h_return", s["mode"], s["lang"], saved=True)
        s["history"] = (s["history"] + [{"role": "user", "content": "[register photos for the monthly return]"},
                                        {"role": "assistant", "content": body}])[-24:]
        s.update(step=None, task="h_register", register={})
        self.log_event(phone, event="return_drafted", pages=ret["pages"], flags=len(ret["flags"]),
                       missing=len(ret["missing"]))
        buttons = prompts.RETURN_FOLLOW[:2] + [(f"more:{item_id}", prompts.MORE_LABEL)]
        self.wa.send_buttons(phone, body, buttons, footer=prompts.ANSWER_FOOTER[prompts.MODE])
        try:
            self.wa.send_document(phone, register.as_pdf(fmt, ret, month),
                                  f"monthly-return-draft-{month}.pdf", caption=prompts.REGISTER_PDF_CAPTION)
        except Exception as e:
            log.warning("Return PDF failed: %s", e)

    # ================================================================ the team: volunteers, nurses, supervisors

    @staticmethod
    def _who(phone: str) -> str:
        return "\u2022\u2022" + phone[-3:]

    def notify(self, user: str, body: str, buttons: list | None = None) -> bool:
        """Message someone else on the team. Inside WhatsApp's 24-hour window it goes now;
        otherwise it waits in their outbox and arrives with their next message.
        Returns True if sent now."""
        target = self.store.get(user)
        if time.time() - (target.get("last_in") or 0) < 23.5 * 3600:
            prev = getattr(i18n._local, "state", None)
            try:
                i18n.use(target)
                if buttons:
                    self.wa.send_buttons(user, i18n.tr(body), buttons)
                else:
                    self.wa.send_text(user, i18n.tr(body))
                return True
            except Exception as e:
                log.warning("Team message failed, queued instead: %s", e)
            finally:
                i18n.use(prev)
        self.store.queue(user, body, buttons)
        template = os.getenv("WA_TEMPLATE_INBOX", "").strip()
        if template and self.store.outbox_count(user) == 1:
            try:
                self.wa.send_template(user, template, target.get("lang") or "en", ["1"])
            except Exception as e:
                log.warning("Inbox template not sent: %s", e)
        return False

    def _flush_outbox(self, phone, s):
        items = self.store.take_outbox(phone)
        if not items:
            return
        self.wa.send_text(phone, prompts.INBOX_HEAD)
        for it in items:
            if it["buttons"]:
                self.wa.send_buttons(phone, i18n.tr(it["body"]), it["buttons"])
            else:
                self.wa.send_text(phone, i18n.tr(it["body"]))

    def _join(self, phone, s, text):
        m = re.search(r"(?:ak[-\s]?)?(\d{4})", text, re.I)
        code = f"AK-{m.group(1)}" if m else ""
        owner = self.store.team_owner(code) if code else None
        if not owner:
            return self.wa.send_text(phone, prompts.TEAM_BAD_CODE)
        if owner == phone:
            return self.wa.send_text(phone, prompts.TEAM_OWN_CODE)
        self.store.join_team(phone, code)
        self.log_event(phone, event="team_joined", role=prompts.role_group(s))
        lead = self.store.get(owner)
        self.notify(owner, prompts.TEAM_NEW_MEMBER.format(role=prompts.ROLE_WORD[prompts.role_group(s)], who=self._who(phone)))
        return self.wa.send_text(phone, i18n.tr(prompts.TEAM_JOINED.format(
            lead=f"{prompts.ROLE_WORD[prompts.role_group(lead)]} {self._who(owner)}")))

    def _team_task(self, phone, s, kind, task):
        if kind == "visitlog":
            return self.wa.send_list(phone, prompts.VISIT_BODY, prompts.VISIT_BUTTON,
                                     [(prompts.VISIT_SECTION, prompts.VISIT_TYPES)])
        if kind == "referral":
            rows = [(rid, t, d) for rid, t, d, _ in prompts.REF_REASONS] + [prompts.REF_LIST_ROW]
            return self.wa.send_list(phone, prompts.REF_BODY, prompts.REF_BUTTON, [(prompts.REF_SECTION, rows[:10])])
        if kind == "alert":
            s.update(step="alert", task="h_alert")
            t = prompts.TASKS["h_alert"]
            return self.wa.send_text(phone, f"*{t['title']}*\n{t['ask']}")
        if kind == "notice":
            members = self.store.team_members(phone)
            if not members:
                return self.wa.send_text(phone, prompts.NOTICE_EMPTY)
            s["step"] = "notice"
            return self.wa.send_text(phone, i18n.tr(prompts.NOTICE_ASK.format(n=len(members))))
        if kind == "team":
            body = self.team_overview(phone)
            return self.wa.send_buttons(phone, body or prompts.TEAM_OVERVIEW_EMPTY,
                                        [("m_team", prompts.SETTINGS_ROWS[2][1]), ("m_menu", "\U0001F3E0 Menu")])
        # my team
        group = prompts.role_group(s)
        lead = self.store.lead_of(phone)
        parts = []
        if group != "volunteer" or self.store.team_code(phone, create=False):
            code = self.store.team_code(phone)
            members = self.store.team_members(phone)
            listing = (i18n.tr(prompts.TEAM_MEMBERS_HEAD.format(n=len(members))) + "\n" + "\n".join(
                f"\u2022 {prompts.ROLE_WORD[prompts.role_group(self.store.get(m))]} {self._who(m)}" for m in members)) \
                if members else i18n.tr(prompts.TEAM_NO_MEMBERS)
            parts.append(i18n.tr(prompts.TEAM_LEAD_BODY.format(code=code, who=i18n.tr(prompts.TEAM_WHO[group]), members="{members}"))
                         .replace("{members}", listing))
        if lead:
            parts.append(i18n.tr(prompts.TEAM_MEMBER_OF.format(lead=f"{prompts.ROLE_WORD[prompts.role_group(self.store.get(lead))]} {self._who(lead)}")))
        elif group != "supervisor":
            parts.append(i18n.tr(prompts.TEAM_NOT_MEMBER))
        buttons = prompts.TEAM_BUTTONS if lead else prompts.TEAM_BUTTONS[1:]
        return self.wa.send_buttons(phone, "\n\n".join(parts), buttons)

    def _team_button(self, phone, s, bid):
        kind, _, rest = bid.partition(":")
        if bid == "m_team":
            return self._team_task(phone, s, "myteam", None)
        if bid.startswith("h:") and rest in prompts.TASKS:
            return self._start_task(phone, s, rest)
        if bid == "tm_leave":
            self.store.leave_team(phone)
            return self.wa.send_text(phone, prompts.TEAM_LEFT)
        if bid == "vt_again":
            return self._team_task(phone, s, "visitlog", None)
        if bid.startswith("vt_") and any(bid == v[0] for v in prompts.VISIT_TYPES):
            s["step"] = f"visit:{bid[3:]}"
            return self.wa.send_text(phone, prompts.VISIT_ASK)
        if bid == "vl_refer":
            s.setdefault("prefs", {})["ref_label"] = s.get("prefs", {}).get("last_visit_label") or ""
            return self._team_task(phone, s, "referral", None)
        if bid.startswith("rr_") and any(bid == r[0] for r in prompts.REF_REASONS):
            label = (s.get("prefs") or {}).pop("ref_label", "")
            if label:
                return self._refer(phone, s, bid[3:], label)
            s["step"] = f"ref:{bid[3:]}"
            return self.wa.send_text(phone, prompts.REF_ASK)
        if bid == "rf_list":
            return self._open_referrals(phone)
        if kind in ("rf_in", "rf_wait", "rf_yes", "rf_no", "rf_unsure") and rest.isdigit():
            return self._referral_answer(phone, kind, int(rest))
        if bid == "al_now":
            text = (s.get("prefs") or {}).pop("alert_text", "")
            return self._alert(phone, s, text) if text else self._team_task(phone, s, "alert", None)
        return self._menu(phone, s)

    def _visit_log(self, phone, s, kind, text):
        try:
            label = followups.first_label(text)
        except followups.FollowupError:
            label = ""
        danger = safety.looks_like_emergency(text)
        self.store.add_visit(phone, kind, label, danger)
        s["step"] = None
        s.setdefault("prefs", {})["last_visit_label"] = label
        n = self.store.count_since("visits", phone, _month_start())
        self.log_event(phone, event="visit_logged", kind=kind, flagged=danger)
        if danger:
            if not s.get("_card_sent"):
                self.wa.send_buttons(phone, prompts.EMERGENCY, prompts.EMERGENCY_NEAR_BUTTON)
                self.log_event(phone, event="emergency_card", mode="health", source="visit")
            return self.wa.send_buttons(phone, i18n.tr(prompts.VISIT_LOGGED.format(n=n)) + "\n" + i18n.tr(prompts.VISIT_DANGER),
                                        prompts.VISIT_DANGER_BUTTONS)
        return self.wa.send_buttons(phone, i18n.tr(prompts.VISIT_LOGGED.format(n=n)), prompts.VISIT_BUTTONS)

    def _refer(self, phone, s, reason_key, text):
        reason = next((r for r in prompts.REF_REASONS if r[0] == f"rr_{reason_key}"), prompts.REF_REASONS[-1])
        try:
            label = followups.first_label(text)
        except followups.FollowupError:
            return self.wa.send_text(phone, prompts.REF_ASK)
        lead = self.store.lead_of(phone)
        rid = self.store.add_referral(phone, lead, label, reason[0][3:], reason[3])
        s["step"] = None
        self.log_event(phone, event="referral_made", reason=reason[0][3:], urgent=reason[3], to_lead=bool(lead))
        if reason[3]:
            self.wa.send_buttons(phone, prompts.EMERGENCY, prompts.EMERGENCY_NEAR_BUTTON)
        if lead:
            self.notify(lead, prompts.REF_TO_LEAD.format(role=prompts.ROLE_WORD[prompts.role_group(s)], who=self._who(phone),
                                                         label=label, reason=i18n.tr(reason[1], self.store.get(lead).get("lang"))),
                        [(f"{b}:{rid}", t) for b, t in prompts.REF_LEAD_BUTTONS])
        who = i18n.tr(prompts.REF_SENT_LEAD) if lead else i18n.tr(prompts.REF_NO_TEAM)
        body = i18n.tr(prompts.REF_SENT.format(label=label, reason=i18n.tr(reason[1]), who="{who}")).replace("{who}", who)
        return self.wa.send_buttons(phone, body, [("rf_list", prompts.REF_LIST_ROW[1]), ("m_menu", "\U0001F3E0 Menu")])

    def _open_referrals(self, phone):
        refs = self.store.referrals_for(phone)
        if not refs:
            return self.wa.send_text(phone, prompts.REF_NONE_OPEN)
        names = {r[0][3:]: r[1] for r in prompts.REF_REASONS}
        lines = [i18n.tr(prompts.REF_OPEN_HEAD)] + [
            f"\u2022 {r['label']} \u00b7 {i18n.tr(names.get(r['reason'], r['reason']))} \u00b7 "
            f"{time.strftime('%d %b', time.gmtime(r['created']))}" for r in refs[:15]]
        self.wa.send_text(phone, "\n".join(lines))
        mine = [r for r in refs if r["lead"] == phone][:3]
        for r in mine:
            self.wa.send_buttons(phone, f"*{r['label']}*", [(f"{b}:{r['id']}", t) for b, t in prompts.REF_LEAD_BUTTONS])

    def _referral_answer(self, phone, kind, rid):
        r = self.store.referral(rid)
        if not r or phone not in (r["user"], r["lead"]):
            return self.wa.send_text(phone, prompts.REF_GONE)
        if r["status"] != "open" and kind in ("rf_in", "rf_yes", "rf_no"):
            return self.wa.send_text(phone, prompts.REF_GONE)
        if kind in ("rf_in", "rf_yes"):
            self.store.update_referral(rid, status="arrived")
            self.log_event(phone, event="referral_arrived", reason=r["reason"])
            if kind == "rf_in" and r["user"] != phone:
                self.notify(r["user"], prompts.REF_ARRIVED.format(label=r["label"]))
            return self.wa.send_text(phone, i18n.tr(prompts.REF_ARRIVED_OK.format(label=r["label"])))
        if kind == "rf_wait":
            return self.wa.send_text(phone, i18n.tr(prompts.REF_WAIT_OK.format(label=r["label"])))
        if kind == "rf_no":
            self.store.update_referral(rid, status="not_arrived")
            self.log_event(phone, event="referral_not_arrived", reason=r["reason"])
            if r["lead"] and r["lead"] != phone:
                self.notify(r["lead"], prompts.REF_NOT_ARRIVED_LEAD.format(label=r["label"], who=self._who(phone)))
            return self.wa.send_buttons(phone, i18n.tr(prompts.REF_NOT_ARRIVED.format(label=r["label"])),
                                        [("h:h_referral", prompts.TASKS["h_referral"]["title"])])
        return self.wa.send_text(phone, prompts.REF_THANKS)

    def check_referrals(self, now: float | None = None) -> int:
        """Two days after a referral, ask the person who made it whether they arrived."""
        now = now or time.time()
        names = {r[0][3:]: r[1] for r in prompts.REF_REASONS}
        n = 0
        for r in self.store.referrals_to_check(int(now - 48 * 3600)):
            self.store.update_referral(r["id"], asked=True)
            self.notify(r["user"], prompts.REF_CHECK.format(label=r["label"], reason=names.get(r["reason"], r["reason"])),
                        [(f"{b}:{r['id']}", t) for b, t in prompts.REF_CHECK_BUTTONS])
            n += 1
        return n

    def _alert(self, phone, s, text):
        hits = alerts.match(text)
        s.update(step=None, task="h_alert")
        s["history"] = (s["history"] + [{"role": "user", "content": "[disease alert] " + text[:600]}])[-24:]
        if not hits:
            s["history"].append({"role": "assistant", "content": prompts.ALERT_NONE})
            self.log_event(phone, event="alert_checked", matched=False)
            return self.wa.send_buttons(phone, prompts.ALERT_NONE, ALERT_FOLLOW_NONE)
        body = alerts.draft(hits, text, s["profile"].get("facility", ""))
        s["history"].append({"role": "assistant", "content": body})
        for h in hits:
            self.store.add_alert(phone, h["key"])
            self.store.add_signal(phone, self.store.lead_of(phone), h["key"], surveillance.head_count(text),
                                  surveillance.zone_of(s["profile"]))
        item_id = self.store.add_item(phone, "Disease alert draft", body, "h_alert", s["mode"], s["lang"], saved=True)
        self.log_event(phone, event="alert_raised", disease=hits[0]["key"])
        lead = self.store.lead_of(phone)
        if lead:
            self.notify(lead, prompts.ALERT_TO_LEAD.format(role=prompts.ROLE_WORD[prompts.role_group(s)], who=self._who(phone),
                                                           disease=hits[0]["name"]))
        self.wa.send_text(phone, body)
        return self.wa.send_buttons(phone, "\U0001F9A0", prompts.ALERT_FOLLOW[:2] + [(f"more:{item_id}", prompts.MORE_LABEL)])

    def _notice(self, phone, s, text):
        s["step"] = None
        members = self.store.team_members(phone)
        now = sum(self.notify(m, prompts.NOTICE_FROM.format(role=prompts.ROLE_WORD[prompts.role_group(s)], text=text[:900]))
                  for m in members)
        self.log_event(phone, event="notice_sent", members=len(members), now=now)
        return self.wa.send_text(phone, i18n.tr(prompts.NOTICE_SENT.format(now=now, later=len(members) - now)))

    def _bulletin(self, phone, s):
        """The week's surveillance bulletin, drafted from the team's signals, for a supervisor to check."""
        since = int(time.time()) - 70 * 86400
        sigs = self.store.signals(lead=phone, since=since)
        zones = {surveillance.zone_of(self.store.get(m)["profile"]) for m in self.store.team_members(phone)}
        early = dt.date.today().weekday() <= 2          # Monday to Wednesday: last week's report is the one that's due
        body = surveillance.bulletin(sigs, s["profile"].get("district", "") or s["profile"].get("town", ""),
                                     zones_expected=len(zones) or None, last_complete=early)
        self.store.add_item(phone, "Weekly surveillance bulletin (draft)", body, "h_alert", s["mode"], s["lang"], saved=True)
        self.log_event(phone, event="bulletin_drafted", signals=len(sigs))
        self.wa.send_text(phone, body)

    def team_overview(self, owner: str) -> str | None:
        members = self.store.team_members(owner)
        if not members:
            return None
        since = _month_start()
        lines = [f"*\U0001F465 {i18n.tr('Team overview')}* \u00b7 {time.strftime('%B %Y')}", ""]
        tot_ref = tot_arr = 0
        for m in members:
            ms = self.store.get(m)
            role = prompts.role_group(ms)
            days = int((time.time() - (ms.get("last_in") or 0)) // 86400) if ms.get("last_in") else None
            seen = "today" if days == 0 else (f"{days}d ago" if days is not None else "not yet active")
            bits = []
            v = self.store.count_since("visits", m, since)
            if v or role == "volunteer":
                bits.append(f"{v} visits")
            refs = self.store.count_since("referrals", m, since)
            arrived = self.store.count_since("referrals", m, since, "AND status='arrived'")
            tot_ref, tot_arr = tot_ref + refs, tot_arr + arrived
            if refs:
                bits.append(f"{refs} referrals ({arrived} arrived)")
            if role != "volunteer":
                due = followups.due_list(self.store.followups(m))
                if due["overdue"]:
                    bits.append(f"{len(due['overdue'])} overdue follow-ups")
                ret = self.store.previous_return(m, "9999-99")
                if ret is not None:
                    bits.append("return drafted")
                last = self.store.last_stock(m) or {}
                outs = [stock.ITEMS[k]["label"] for k, n in last.items() if n == 0 and k in stock.ITEMS]
                if outs:
                    bits.append("out of " + ", ".join(outs[:2]))
            al = self.store.count_since("alerts", m, since)
            if al:
                bits.append(f"\U0001F6A8 {al} alerts")
            lines.append(f"\u2022 *{prompts.ROLE_WORD[role]} {self._who(m)}* \u00b7 {seen}" + (f"\n   {', '.join(bits)}" if bits else ""))
        if tot_ref:
            lines += ["", f"Referrals this month: {tot_ref}, {tot_arr} arrived ({round(100 * tot_arr / tot_ref)}%)."]
        return "\n".join(lines)

    # ================================================================ follow-ups and stock

    def _fu_home(self, phone, s):
        fus = self.store.followups(phone)
        due = followups.due_list(fus)
        n_due = len(due["overdue"]) + len(due["soon"])
        summary = prompts.FU_SUMMARY.format(open=len(fus), due=n_due) if fus else prompts.FU_EMPTY_SUMMARY
        self.log_event(phone, event="followups_open", open=len(fus), due=n_due)
        return self.wa.send_list(phone, prompts.FU_HOME_BODY.format(summary=summary), prompts.FU_BUTTON,
                                 [(prompts.FU_SECTION, prompts.FU_ROWS)])

    def _fu_add(self, phone, s, kind, text):
        try:
            label, anchor = followups.parse_entry(kind, text)
        except followups.FollowupError as e:
            return self.wa.send_text(phone, prompts.FU_ERRORS.get(str(e), prompts.FU_ERRORS["no_date"]))
        # Nothing is marked done on its own. For a child added part-way through, the nurse
        # confirms the earlier doses from the card; anything not confirmed stays as catch-up.
        past = followups.past_visits(kind, anchor) if kind == "child" else []
        fid = self.store.add_followup(phone, kind, label, anchor.isoformat(), [])
        s["step"] = None
        s.setdefault("prefs", {})["fu_last_kind"] = kind
        self.log_event(phone, event="followup_added", kind=kind, past=len(past))
        if past:
            whats = [v["what"] for v in followups.SCHEDULES[kind]["visits"] if v["key"] in past]
            listed = "\n".join(f"\u2022 {w}" for w in whats[:8])
            return self.wa.send_buttons(phone, i18n.tr(prompts.FU_ADDED_PAST.format(label=label, n=len(past))) + "\n" + listed
                                        + "\n\n" + i18n.tr(prompts.FU_PAST_ASK),
                                        [(f"fu_pastok:{fid}", prompts.FU_PAST_YES), (f"fu_pastno:{fid}", prompts.FU_PAST_NO)])
        fu = self.store.followup(phone, fid)
        nv = followups.next_visit(fu)
        body = i18n.tr(prompts.FU_ADDED.format(label=label, what=nv["what"] if nv else "-",
                                               date=followups.day(nv["date"]) if nv else "-"))
        return self.wa.send_buttons(phone, body, prompts.FU_ADDED_BUTTONS)

    def _fu_past(self, phone, s, fid: int, all_given: bool):
        fu = self.store.followup(phone, fid)
        if not fu or fu["closed"]:
            return self.wa.send_text(phone, prompts.FU_GONE)
        if all_given:
            past = followups.past_visits(fu["kind"], dt.date.fromisoformat(fu["anchor"]))
            fu["done"] = sorted(set(fu["done"]) | set(past))
            fu["given"] = {**{k: "card" for k in past}, **fu["given"]}  # confirmed from the card; exact date unknown
            self.store.update_followup(phone, fid, done=fu["done"], given=fu["given"])
            self.log_event(phone, event="followup_past_confirmed", n=len(past))
            nv = followups.next_visit(fu)
            body = i18n.tr(prompts.FU_PAST_CONFIRMED.format(label=fu["label"]))
            if nv:
                body += "\n" + i18n.tr(prompts.FU_NEXT.format(what=nv["what"], date=followups.day(nv["date"])))
            return self.wa.send_buttons(phone, body, prompts.FU_ADDED_BUTTONS)
        self.log_event(phone, event="followup_past_missed")
        return self._fu_due(phone, s, head=prompts.FU_PAST_CATCHUP.format(label=fu["label"]))

    def _fu_due(self, phone, s, head: str | None = None):
        fus = self.store.followups(phone)
        for fu in fus:  # tidy: schedules that are complete come off the list
            if followups.is_finished(fu):
                self.store.update_followup(phone, fu["id"], closed=True)
        due = followups.due_list([f for f in fus if not followups.is_finished(f)])
        pairs = due["overdue"] + due["soon"]
        self.log_event(phone, event="followups_due", overdue=len(due["overdue"]), soon=len(due["soon"]))
        if not pairs:
            return self.wa.send_buttons(phone, ((i18n.tr(head) + "\n\n") if head else "") + i18n.tr(prompts.FU_NOTHING_DUE),
                                        [("fu_add:child", "\u2795 Add a child"), ("fu_all", "Everyone")])
        self.wa.send_text(phone, ((i18n.tr(head) + "\n\n") if head else "") + followups.due_text(due))
        rows = [(f"fu_done:{fu['id']}", fu["label"], f"{v['what']} \u00b7 {followups.day(v['date'])}")
                for fu, v in pairs[:9]]
        rows.append(("fu_remind", "\U0001F4E8 Reminder messages", "Texts to forward to families"))
        return self.wa.send_list(phone, prompts.FU_MARK_BODY, prompts.FU_MARK_BUTTON, [(prompts.FU_MARK_SECTION, rows)])

    def _fu_mark(self, phone, fid: int):
        fu = self.store.followup(phone, fid)
        if not fu or fu["closed"]:
            return self.wa.send_text(phone, prompts.FU_GONE)
        nv = followups.next_visit(fu)
        if not nv:
            self.store.update_followup(phone, fid, closed=True)
            return self.wa.send_text(phone, prompts.FU_FINISHED.format(label=fu["label"]))
        fu["done"] = sorted(set(fu["done"]) | {nv["key"]})
        fu["given"] = {**fu["given"], nv["key"]: followups.today().isoformat()}  # the real date, so later doses keep their gap
        self.store.update_followup(phone, fid, done=fu["done"], given=fu["given"])
        self.log_event(phone, event="followup_done", kind=fu["kind"])
        nxt = followups.next_visit(fu)
        if not nxt or followups.is_finished(fu):
            self.store.update_followup(phone, fid, closed=True)
            return self.wa.send_text(phone, i18n.tr(prompts.FU_MARKED.format(label=fu["label"], what=nv["what"])) + "\n"
                                     + i18n.tr(prompts.FU_FINISHED.format(label=fu["label"])))
        return self.wa.send_buttons(phone, i18n.tr(prompts.FU_MARKED.format(label=fu["label"], what=nv["what"])) + "\n"
                                    + i18n.tr(prompts.FU_NEXT.format(what=nxt["what"], date=followups.day(nxt["date"]))),
                                    [("fu_due", "Due this week"), ("fu_all", "Everyone")])

    def _fu_button(self, phone, s, bid: str):
        if bid == "fu_due":
            return self._fu_due(phone, s)
        if bid == "fu_again":
            bid = "fu_add:" + s.get("prefs", {}).get("fu_last_kind", "child")
        if bid.startswith("fu_add:") and bid.split(":", 1)[1] in followups.KINDS:
            kind = bid.split(":", 1)[1]
            s["step"], s["task"] = f"fu_add:{kind}", None
            return self.wa.send_text(phone, prompts.FU_ASK[kind])
        if bid == "fu_all":
            fus = self.store.followups(phone)
            if not fus:
                return self._fu_home(phone, s)
            rows = []
            for fu in fus[:10]:
                nv = followups.next_visit(fu)
                rows.append((f"fu_view:{fu['id']}", fu["label"],
                             f"{followups.SCHEDULES[fu['kind']]['title']}" + (f" \u00b7 next {followups.day(nv['date'])}" if nv else "")))
            return self.wa.send_list(phone, prompts.FU_ALL_BODY, prompts.FU_ALL_BUTTON, [(prompts.FU_ALL_SECTION, rows)])
        if bid == "fu_remind":
            due = followups.due_list(self.store.followups(phone))
            pairs = (due["overdue"] + due["soon"])[:5]
            if not pairs:
                return self.wa.send_text(phone, prompts.FU_NOTHING_DUE)
            self.wa.send_text(phone, prompts.FU_REMIND_INTRO)
            for fu, v in pairs:
                when = v["date"] if v["date"] >= followups.today() else followups.today()
                self.wa.send_text(phone, prompts.FU_REMIND[fu["kind"]].format(what=v["what"], date=followups.day(when)))
            self.log_event(phone, event="followup_reminders", n=len(pairs))
            return
        kind, _, rest = bid.partition(":")
        if kind in ("fu_pastok", "fu_pastno") and rest.isdigit():
            return self._fu_past(phone, s, int(rest), kind == "fu_pastok")
        if kind in ("fu_view", "fu_done", "fu_rm") and rest.isdigit():
            fid = int(rest)
            if kind == "fu_done":
                return self._fu_mark(phone, fid)
            fu = self.store.followup(phone, fid)
            if not fu or fu["closed"]:
                return self.wa.send_text(phone, prompts.FU_GONE)
            if kind == "fu_rm":
                self.store.update_followup(phone, fid, closed=True)
                self.log_event(phone, event="followup_removed", kind=fu["kind"])
                return self.wa.send_text(phone, prompts.FU_REMOVED.format(label=fu["label"]))
            return self.wa.send_buttons(phone, followups.schedule_text(fu),
                                        [(f"fu_done:{fid}", prompts.FU_VIEW_BUTTONS[0][1]),
                                         (f"fu_rm:{fid}", prompts.FU_VIEW_BUTTONS[1][1]), ("fu_due", "Due this week")])
        if bid == "st_again":
            return self._start_task(phone, s, "h_stock")
        return self._menu(phone, s)

    def _stock(self, phone, s, text, message_id):
        counts, unknown = stock.parse_counts(text)
        if not counts:
            return self.wa.send_text(phone, prompts.STOCK_NONE)
        prev = self.store.last_stock(phone)
        ret = self.store.previous_return(phone, "9999-99")
        res = stock.check(counts, prev, (ret or {}).get("malaria_tested"))
        self.store.save_stock(phone, counts)
        body = stock.as_text(res)
        if unknown:
            body += "\n" + i18n.tr(prompts.STOCK_UNKNOWN.format(words=", ".join(unknown[:5])))
        req = stock.requisition(res, s["profile"].get("facility", ""))
        full = body + ("\n\n" + req if req else "")
        item_id = self.store.add_item(phone, "Stock check", full, "h_stock", s["mode"], s["lang"], saved=bool(req))
        s["history"] = (s["history"] + [{"role": "user", "content": "[stock count] " + text[:300]},
                                        {"role": "assistant", "content": full}])[-24:]
        s.update(step=None, task="h_stock")
        self.log_event(phone, event="stock_check", items=len(counts), low=len(res["low"]),
                       stocked_out=len(res["stocked_out"]))
        self.wa.send_text(phone, body)
        if req:
            return self.wa.send_buttons(phone, req, prompts.STOCK_FOLLOW[:2] + [(f"more:{item_id}", prompts.MORE_LABEL)])
        return self.wa.send_buttons(phone, "\u2705", [("st_again", "Count again"), (f"more:{item_id}", prompts.MORE_LABEL)])

    def weekly_digest(self, now: float | None = None) -> int:
        """Monday follow-up lists. Inside WhatsApp's 24-hour window the list is sent
        directly; outside it, only an approved template may open the chat
        (WA_TEMPLATE_DUE). Returns how many people were sent something."""
        import datetime as dt
        now = now or time.time()
        week = dt.date.fromtimestamp(now).isocalendar()[:2]
        week_key = f"{week[0]}-W{week[1]:02d}"
        template = os.getenv("WA_TEMPLATE_DUE", "").strip()
        sent = 0
        people = set(self.store.users_with_followups())
        with self.store._lock:
            people |= {r[0] for r in self.store._db.execute("SELECT owner FROM teams")}
        for phone in sorted(people):
            with self.store.user_lock(phone):
                s = self.store.get(phone)
                prefs = s.setdefault("prefs", {})
                if prefs.get("digest_week") == week_key or not s["mode"]:
                    continue
                due = followups.due_list(self.store.followups(phone))
                n = len(due["overdue"]) + len(due["soon"])
                prefs["digest_week"] = week_key
                try:
                    i18n.use(s)
                    team = self.team_overview(phone)
                    inside = now - (s.get("last_in") or 0) < 23.5 * 3600
                    if team and inside:
                        self.wa.send_text(phone, i18n.tr(prompts.TEAM_DIGEST_HEAD) + "\n\n" + team)
                    if n and inside:
                        self._fu_due(phone, s, head=prompts.FU_DIGEST_HEAD)
                    if (n or team) and inside:
                        sent += 1
                    elif (n or team) and template:
                        self.wa.send_template(phone, template, s["lang"], [str(n)])
                        sent += 1
                    self.log_event(phone, event="digest", due=n, sent=bool(n), template=bool(template))
                except Exception as e:
                    log.warning("Weekly list not sent: %s", e)
                finally:
                    i18n.use(None)
                    self.store.save(phone, s)
        return sent

    # ================================================================ answering

    def _answer(self, phone, s, text, attachments, message_id, voice=False, emergency_checked=False,
                learn=True):
        """learn=False for text we generated (button taps, forms), so memory only
        ever learns from what the person actually typed or said."""
        mode = s["mode"]
        if not emergency_checked and not s.get("_card_sent") and safety.looks_like_emergency(text, mode):
            self.wa.send_buttons(phone, prompts.EMERGENCY, prompts.EMERGENCY_NEAR_BUTTON)
            self.log_event(phone, event="emergency_card", mode=mode)
        hits = alerts.match(text) if not attachments and not emergency_checked else []
        if hits:
            s.setdefault("prefs", {})["alert_text"] = text[:600]
            self.wa.send_buttons(phone, i18n.tr(prompts.ALERT_HINT.format(disease=hits[0]["name"])), prompts.ALERT_HINT_BUTTONS)
        if attachments:
            self.wa.react(phone, message_id, "\U0001F440")
        self.wa.typing(message_id)
        try:
            out = self.compose(phone, s, text, attachments, voice=voice, learn=learn, channel="whatsapp")
        except ClaudeBusy:
            self.wa.send_text(phone, prompts.BUSY)
            return
        except redact.RedactionUnavailable as e:
            log.warning("Register photo refused, names can't be hidden: %s", e)
            self.wa.send_text(phone, prompts.REGISTER_OFF)
            return
        mode, task, reply, item_id = s["mode"], out["task"], out["reply"], out["item_id"]
        prefs, lang = s.get("prefs") or {}, s["lang"]
        wants_voice = prefs.get("voice") and voice_mod.lang_enabled(lang)
        if wants_voice and prefs.get("voice_first"):
            # Listeners get the voice note first, with the text underneath to read along or refer back to.
            self._send_voice(phone, reply, lang, quiet=True)
        buttons = out["follow"][:2] + [(f"more:{item_id}", prompts.MORE_LABEL)]
        footer = prompts.ANSWER_FOOTER[prompts.MODE]
        out_id = self.wa.send_buttons(phone, reply, buttons, footer=footer)
        self.store.remember_outbound(out_id, phone, mode, s["lang"], task)

        if s["answers"] % FEEDBACK_EVERY == 0:
            self.wa.send_buttons(phone, prompts.FEEDBACK_ASK, prompts.FEEDBACK_BUTTONS)
        jobs = []
        if wants_voice and not prefs.get("voice_first"):
            jobs.append(lambda: self._send_voice(phone, reply, lang, quiet=True))
        return jobs + out["jobs"]

    def _redacted(self, attachments):
        """Register photos on any channel: hide names first, or refuse (fail closed)."""
        if not register.photos_enabled():
            raise redact.RedactionUnavailable("register photos are switched off")
        fmt, out = register.load_format(), []
        for a in attachments:
            if a.get("type") != "image":
                out.append(a)
                continue
            clean, _ = redact.redact(base64.b64decode(a["source"]["data"]), fmt)  # raises RedactionUnavailable
            out.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(clean).decode()}})
        return out

    def sms_reply(self, phone: str, text: str) -> list[str]:
        """One SMS in, one or two short SMS out. Same order as WhatsApp: the emergency
        check first, then the hourly limit, then the answer."""
        import sms
        text = (text or "").strip()[:1000]
        out: list[str] = []
        with self.store.user_lock(phone):
            s = self.store.get(phone)
            try:
                i18n.use(s)
                if safety.looks_like_emergency(text):
                    out.append(sms.EMERGENCY)
                    self.log_event(phone, event="emergency_card", mode="health", source="sms")
                elif safety.local_hit(text):
                    self.log_event(phone, event="emergency_shadow", lang=safety.local_hit(text), source="sms")
                if self._rate_limited(phone):
                    self.log_event(phone, event="rate_limited", channel="sms")
                    return out or [sms.RATE_LIMITED]
                word = text.lower().strip(" .!")
                if word in ("forget me", "stop"):
                    self.store.forget(phone)
                    self.log_event(phone, event="forget", channel="sms")
                    s = None
                    return out + [sms.FORGOTTEN]
                if word in ("help", "menu", "hi", "hello", ""):
                    return out + [sms.HELP]
                if word in prompts.LANGUAGES:
                    s["lang"] = word
                    return out + [f"Language: {prompts.LANGUAGES[word]}. Text your question."]
                if not s["mode"]:
                    s.update(mode=prompts.MODE, history=[], task=None, step=None, welcomed=True)
                    self.log_event(phone, event="mode", mode=prompts.MODE, channel="sms")
                s["task"] = None
                try:
                    res = self.compose(phone, s, text, [], learn=False, channel="sms")
                except ClaudeBusy:
                    return out + [sms.BUSY]
                return out + [sms.shorten(res["reply"])]
            finally:
                i18n.use(None)
                if s is not None:
                    self.store.save(phone, s)

    def compose(self, phone, s, text, attachments, voice=False, learn=True, channel="whatsapp", on_delta=None):
        """Asks Claude and records the answer (history, library item, usage log).
        Shared by WhatsApp and the app. on_delta(text) receives the reply as it streams.
        Raises ClaudeBusy if Claude can't be reached."""
        mode = s["mode"]
        if s.get("task") == "h_register" and any(a.get("type") == "image" for a in attachments):
            attachments = self._redacted(attachments)
        content = attachments + [{"type": "text", "text": text or "Please look at this."}]
        messages = s["history"] + [{"role": "user", "content": content}]
        system = prompts.build_system_prompt(mode, s["lang"], s["profile"], s["summary"], s["task"])
        max_tokens = self.max_tokens
        if channel == "sms":
            system += prompts.SMS_RULES
            max_tokens = min(max_tokens, 350)
        started = time.time()
        try:
            if on_delta:
                with self.claude.messages.stream(model=self.model, max_tokens=max_tokens, system=system,
                                                 messages=messages) as stream:
                    for chunk in stream.text_stream:
                        on_delta(chunk)
                    resp = stream.get_final_message()
            else:
                resp = self.claude.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                                   messages=messages)
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APITimeoutError,
                anthropic.APIConnectionError) as e:
            log.error("Claude unavailable after retries: %s", e)
            self.log_event(phone, event="error", kind="claude_busy")
            raise ClaudeBusy() from e

        reply = "".join(b.text for b in resp.content if b.type == "text").strip()
        truncated = getattr(resp, "stop_reason", None) == "max_tokens"
        if truncated:
            reply = trim_to_sentence(reply) + "\n\n" + i18n.tr("(Reply MORE for the rest.)", s["lang"])
        reply = to_whatsapp(reply) or "Sorry, I couldn't put together an answer. Could you rephrase?"

        task = s.get("task")
        follow = (prompts.TASKS[task]["follow"] if task in prompts.TASKS else []) or prompts.GENERAL_FOLLOW[mode]
        auto_save = task in prompts.AUTO_SAVE_TASKS
        item_id = self.store.add_item(phone, media.title_of(reply), reply, task, mode, s["lang"], saved=auto_save)

        label = text
        if attachments:
            label = f"[{'photo' if attachments[0]['type'] == 'image' else 'document'}] {text}".strip()
        s["history"] = s["history"] + [{"role": "user", "content": label or "[attachment]"},
                                       {"role": "assistant", "content": reply}]
        s["answers"] = s.get("answers", 0) + 1
        s["step"] = None
        self.log_event(phone, event="answer", mode=mode, lang=s["lang"], task=s.get("task"),
                       has_image=any(a["type"] == "image" for a in attachments),
                       has_document=any(a["type"] == "document" for a in attachments),
                       voice=voice, in_tokens=resp.usage.input_tokens, out_tokens=resp.usage.output_tokens,
                       truncated=truncated, latency_s=round(time.time() - started, 2),
                       remembered=bool(s["profile"] or s["summary"]), channel=channel)
        return {"reply": reply, "item_id": item_id, "task": task, "follow": follow, "saved": auto_save,
                "truncated": truncated, "jobs": self._memory_jobs(phone, s, text if learn else "") or []}

    # ================================================================ settings & voice

    def _settings(self, phone, s):
        if not s["mode"]:
            return self._welcome(phone, s)
        tr = lambda x: i18n.tr(x, s["lang"])
        body = prompts.SETTINGS_LIST_BODY.format(
            role=tr(prompts.ROLE_NAMES[s["mode"]]), lang=prompts.LANG_SHORT.get(s["lang"], "English"),
            detail=self._detail_line(s), voice=tr(prompts.ON if (s.get("prefs") or {}).get("voice") else prompts.OFF))
        # Preferred: one in-chat form with its own "Open settings" and "Save" buttons, so nobody has to pick
        # an option and then press WhatsApp's own "Send" button. The plain list is the fallback.
        if self._send_flow(phone, "settings", body + "\n\n" + tr(prompts.SETTINGS_FLOW_HINT), prompts.SETTINGS_CTA,
                           "SETTINGS", data=self._settings_form_data(s)):
            self.log_event(phone, event="settings_open", via="flow")
            return self.wa.send_buttons(phone, prompts.SETTINGS_MORE_BODY, prompts.SETTINGS_MORE_BUTTONS,
                                        footer=prompts.SETTINGS_MORE_FOOTER)
        self.wa.send_list(phone, body, prompts.SETTINGS_LIST_BUTTON,
                          [(prompts.SETTINGS_SECTION, prompts.SETTINGS_ROWS)])

    def _settings_form_data(self, s) -> dict:
        """Starting values and words for the Settings form, in the person's language."""
        tr = lambda x: i18n.tr(x, s["lang"])
        prefs = s.get("prefs") or {}
        role_id = next((k for k, v in prompts.HEALTH_ROLE_VALUES.items() if v == s["profile"].get("role")), "none")
        f = prompts.SETTINGS_FORM
        return {"intro": tr(f["intro"]), "lang_label": tr(f["lang_label"]), "role_label": tr(f["role_label"]),
                "voice_label": tr(f["voice_label"]), "save": tr(f["save"])[:30],
                "lang": s["lang"] if s["lang"] in prompts.LANGUAGES else "en", "role": role_id,
                "voice": "listen" if prefs.get("voice") else "read",
                "roles": [{"id": rid, "title": tr(title)[:30]} for rid, title, _ in prompts.HEALTH_ROLE_ROWS if rid != "hr_skip"]
                + [{"id": "none", "title": tr("Not set")}],
                "voice_options": [{"id": "listen", "title": tr(f["listen_title"]), "description": tr(f["listen_desc"])},
                                  {"id": "read", "title": tr(f["read_title"]), "description": tr(f["read_desc"])}]}

    def _settings_saved(self, phone, s, data: dict):
        """The Settings form came back: apply each choice, then say what's now set."""
        changed = []
        lang = data.get("lang")
        if lang in prompts.LANGUAGES and lang != s["lang"]:
            s["lang"] = lang
            i18n.use(s)
            changed.append("lang")
        role = data.get("role")
        if role in prompts.HEALTH_ROLE_VALUES and s["profile"].get("role") != prompts.HEALTH_ROLE_VALUES[role]:
            s["profile"]["role"] = prompts.HEALTH_ROLE_VALUES[role]
            s.setdefault("prefs", {}).pop("role_skipped", None)
            changed.append("role")
        elif role == "none" and s["profile"].get("role"):
            s["profile"].pop("role", None)
            changed.append("role")
        prefs = s.setdefault("prefs", {})
        if data.get("voice") in ("listen", "read"):
            listen = data["voice"] == "listen"
            if listen and not voice.available(s["lang"])[0]:
                self.wa.send_text(phone, prompts.VOICE_NOT_SETUP)
                listen = False
            if bool(prefs.get("voice")) != listen:
                changed.append("voice")
            prefs.update(voice=listen, voice_first=listen, voice_asked=True)
        self.log_event(phone, event="settings_saved", changed=",".join(changed) or "none")
        tr = lambda x: i18n.tr(x, s["lang"])
        body = tr(prompts.SETTINGS_SAVED) + "\n" + prompts.SETTINGS_LIST_BODY.split("\n", 1)[1].format(
            role=tr(prompts.ROLE_NAMES[s["mode"]]), lang=prompts.LANG_SHORT.get(s["lang"], "English"),
            detail=self._detail_line(s), voice=tr(prompts.ON if prefs.get("voice") else prompts.OFF))
        return self.wa.send_buttons(phone, body, [("m_menu", "\U0001F3E0 Menu")])

    def _toggle_voice(self, phone, s, on: bool | None = None):
        prefs = dict(s.get("prefs") or {})
        on = (not prefs.get("voice")) if on is None else on
        if on and not voice.available()[0]:
            return self.wa.send_text(phone, prompts.VOICE_NOT_SETUP)
        prefs["voice"] = on
        s["prefs"] = prefs
        self.log_event(phone, event="voice_replies", on=on)
        self.wa.send_buttons(phone, prompts.VOICE_ON if on else prompts.VOICE_OFF,
                             [("m_menu", prompts.ROW_MENU[0])])

    def _send_voice(self, phone, text, lang, quiet=False) -> bool:
        try:
            ogg = voice.speak(text, lang)
            self.wa.send_voice(phone, ogg)
            self.log_event(phone, event="voice_reply", lang=lang)
            return True
        except (voice.VoiceFailed, SendError) as e:
            log.warning("Voice reply not sent: %s", e)
            if not quiet:
                i18n.use({"lang": lang})
                try:
                    self.wa.send_text(phone, prompts.VOICE_REPLY_FAILED)
                finally:
                    i18n.use(None)
            return False

    # ================================================================ answer actions

    def _item_action(self, phone, s, action, item_id, message_id):
        item = self.store.item(phone, item_id)
        if not item:
            return self.wa.send_text(phone, prompts.ITEM_GONE)
        self.log_event(phone, event="item_action", action=action, task=item["task"])
        if action == "more":
            return self._more(phone, s, item)
        if action == "open":
            return self._open_item(phone, s, item)
        if action == "say":
            if not voice.available()[0]:
                return self.wa.send_text(phone, prompts.VOICE_NOT_SETUP)
            self.wa.typing(message_id)
            self._send_voice(phone, item["body"], item["lang"] or s["lang"])
            return
        if action == "pdf":
            self.wa.typing(message_id)
            kind = i18n.tr(prompts.DOC_KIND.get(item["task"], "Notes"))
            try:
                data = media.pdf(item["body"], kind=kind)
                self.wa.send_document(phone, data, _filename(item["title"]) + ".pdf", caption=prompts.PDF_CAPTION)
            except Exception as e:
                log.warning("PDF failed: %s", e)
                self.wa.send_text(phone, prompts.PDF_FAILED)
            return
        if action == "card":
            self.wa.typing(message_id)
            kind = i18n.tr(prompts.DOC_KIND.get(item["task"], ""))
            try:
                self.wa.send_image_bytes(phone, media.card(item["body"], label=kind))
            except Exception as e:
                log.warning("Card failed: %s", e)
                self.wa.send_text(phone, prompts.PDF_FAILED)
            return
        if action in ("save", "unsave"):
            self.store.set_saved(phone, item["id"], action == "save")
            return self.wa.send_buttons(phone, prompts.LIB_SAVED if action == "save" else prompts.LIB_REMOVED,
                                        prompts.LIB_BUTTONS)
        if action == "quizit":
            if not s["mode"]:
                return self._welcome(phone, s)
            return self._quiz_start(phone, s, item["title"], message_id, source=item["body"])

    def _more(self, phone, s, item):
        i = item["id"]
        rows = []
        if voice.available()[0]:
            rows.append((f"say:{i}", *prompts.ROW_LISTEN))
        rows += [(f"pdf:{i}", *prompts.ROW_PDF), (f"card:{i}", *prompts.ROW_CARD),
                 (f"unsave:{i}", *prompts.ROW_UNSAVE) if item["saved"] else (f"save:{i}", *prompts.ROW_SAVE)]
        nxt = []
        if item["task"] not in ("h_register", "h_return", "h_danger"):
            nxt.append((f"quizit:{i}", *prompts.ROW_QUIZIT))
        nxt.append(("m_near", *prompts.ROW_NEAR))
        task = item["task"]
        follow = (prompts.TASKS[task]["follow"] if task in prompts.TASKS else []) or \
            prompts.GENERAL_FOLLOW[prompts.MODE]
        for fid, label in follow[2:]:
            nxt.append((fid, label, ""))
        nxt.append(("m_menu", *prompts.ROW_MENU))
        self.wa.send_list(phone, prompts.MORE_BODY, prompts.MORE_BUTTON,
                          [(prompts.MORE_SECTION, rows), (prompts.MORE_NEXT, nxt[:10 - len(rows)])])

    def _open_item(self, phone, s, item):
        i = item["id"]
        wanted = ["pdf", "say" if voice.available()[0] else "card"]
        labels = dict(prompts.OPEN_BUTTONS)
        buttons = [(f"{a}:{i}", labels[a]) for a in wanted] + [(f"more:{i}", prompts.MORE_LABEL)]
        self.wa.send_buttons(phone, item["body"], buttons)

    def _library(self, phone, s, offset: int = 0):
        items = self.store.library(phone, 8, offset)
        if not items:
            return self.wa.send_buttons(phone, prompts.LIBRARY_EMPTY, [("m_menu", prompts.ROW_MENU[0])])
        rows = []
        for it in items:
            t = prompts.TASKS.get(it["task"] or "", {})
            kind = i18n.tr(prompts.DOC_KIND.get(it["task"] or "", ""))
            when = time.strftime("%d %b", time.localtime(it["ts"] or 0))
            title = it["title"] or kind or "Saved"
            rows.append((f"open:{it['id']}", title if len(title) <= 24 else title[:23] + "…",
                         f"{t.get('title', '').split(' ', 1)[0]} {kind} · {when}".strip(" ·")))
        if self.store.library_count(phone) > offset + 8:
            rows.append((f"lib:{offset + 8}", *prompts.LIBRARY_MORE))
        self.wa.send_list(phone, prompts.LIBRARY_BODY, prompts.LIBRARY_BUTTON, [(prompts.LIBRARY_SECTION, rows)])
        self.log_event(phone, event="library", items=len(items))

    # ================================================================ quiz game

    def _quiz_start(self, phone, s, topic, message_id, difficulty="mixed", source=""):
        s["step"], s["task"] = None, None
        topic = topic.strip()[:120]
        self.wa.send_text(phone, prompts.QUIZ_MAKING.format(topic=topic))
        self.wa.typing(message_id)
        try:
            qz = quiz.generate(self.claude, self.model, s["mode"], prompts.LANGUAGES.get(s["lang"], "English"),
                               topic, s["profile"].get("class_level", ""), difficulty, source)
        except anthropic.APIError as e:
            log.error("Quiz generation failed: %s", e)
            qz = None
        if not qz:
            self.log_event(phone, event="quiz_failed")
            return self.wa.send_text(phone, prompts.QUIZ_FAILED)
        s["quiz"] = {**qz, "topic": topic, "i": 0, "score": 0, "missed": [], "difficulty": difficulty}
        self.log_event(phone, event="quiz_started", mode=s["mode"], lang=s["lang"], difficulty=difficulty)
        self._quiz_ask(phone, s)

    def _quiz_ask(self, phone, s, prefix: str = ""):
        qz = s["quiz"]
        i, qs = qz["i"], qz["questions"]
        body = quiz.question_text(i, len(qs), qs[i], i18n.tr(prompts.QUIZ_LABEL, s["lang"]))
        self.wa.send_buttons(phone, f"{prefix}\n\n{body}" if prefix else body, quiz.buttons_for(i, qs[i]),
                             header=f"\U0001F3AF {qz['title']}")

    def _quiz_answer(self, phone, s, i, choice, message_id):
        qz = s.get("quiz") or {}
        if not qz.get("questions") or qz.get("i") != i or qz.get("done"):
            return self.wa.send_text(phone, prompts.QUIZ_STALE)
        q = qz["questions"][i]
        tr = lambda x: i18n.tr(x, s["lang"])
        if choice == q["answer"]:
            qz["score"] += 1
            prefix = tr(prompts.QUIZ_RIGHT)
        else:
            qz["missed"].append(i)
            letter = quiz.LETTERS[q["answer"]]
            prefix = tr(prompts.QUIZ_WRONG).replace("{answer}", f"{letter}. {q['options'][q['answer']]}")
        if q.get("why"):
            prefix += f"\n_{q['why']}_"
        qz["i"] = i + 1
        s["quiz"] = qz
        self.log_event(phone, event="quiz_answer", correct=choice == q["answer"])
        if qz["i"] < len(qz["questions"]):
            return self._quiz_ask(phone, s, prefix)

        qz["done"] = True
        total, score = len(qz["questions"]), qz["score"]
        ratio = score / total
        headline = tr(prompts.QUIZ_PERFECT if score == total else prompts.QUIZ_GREAT if ratio >= 0.8
                      else prompts.QUIZ_GOOD if ratio >= 0.6 else prompts.QUIZ_TRY)
        self.wa.send_text(phone, prefix)
        try:
            self.wa.send_image_bytes(phone, media.results_card(score, total, qz["title"], headline,
                                                               label=tr("Quiz")))
        except Exception as e:
            log.warning("Results card failed: %s", e)
        summary = tr(prompts.QUIZ_DONE).replace("{score}", str(score)).replace("{total}", str(total))
        summary += f"\n{headline}"
        if qz["missed"]:
            lines = [f"• {qz['questions'][m]['q'][:90]}\n   → *{qz['questions'][m]['options'][qz['questions'][m]['answer']]}*"
                     for m in qz["missed"]]
            summary += "\n\n" + tr(prompts.QUIZ_REVIEW) + "\n" + "\n".join(lines)
        self.wa.send_buttons(phone, summary, prompts.QUIZ_END_BUTTONS)
        self.log_event(phone, event="quiz_finished", score=score, total=total, lang=s["lang"])

    # ================================================================ nearest facility

    def _near_request(self, phone, s):
        self.log_event(phone, event="near_request")
        s["task"], s["step"] = None, "near_place"  # a typed town name comes next, or a shared location
        self.wa.send_location_request(phone, prompts.NEAR_ASK)

    def _nearest_place(self, phone, s, place, message_id):
        place = place.strip(" ?.!,")
        self.wa.typing(message_id)
        try:
            hit = places.geocode(place)
        except Exception:
            self.log_event(phone, event="near_failed")
            return self.wa.send_text(phone, prompts.NEAR_FAILED)
        if not hit:
            self.log_event(phone, event="near_place_unknown")
            s["step"] = "near_place"
            return self.wa.send_location_request(phone, i18n.tr(prompts.NEAR_PLACE_UNKNOWN, s["lang"]).replace(
                "{place}", place))
        lat, lng, name = hit
        return self._nearest(phone, s, (lat, lng), message_id, place=name)

    def _nearest(self, phone, s, location, message_id, place: str = ""):
        """The location is used for this one search and never stored."""
        lat, lng = location
        self.wa.typing(message_id)
        try:
            found = places.nearest(lat, lng)
        except Exception as e:
            log.warning("Nearest facility search failed: %s", e)
            self.log_event(phone, event="near_failed")
            return self.wa.send_text(phone, prompts.NEAR_FAILED)
        self.log_event(phone, event="near_results", count=len(found))
        if not found:
            return self.wa.send_text(phone, prompts.NEAR_NONE)
        tr = lambda x: i18n.tr(x, s["lang"])
        for f in found:
            self.wa.send_location(phone, f["lat"], f["lng"], f["name"],
                                  f["address"] or f"{tr(f['kind'])} · {f['km']:.1f} km")
        lines = [tr(prompts.NEAR_LINE).format(n=n, name=f["name"], kind=tr(f["kind"]), km=f"{f['km']:.1f}")
                 + (f" · {f['phone']}" if f["phone"] else "") for n, f in enumerate(found, 1)]
        title = tr(prompts.NEAR_PLACE_TITLE).replace("{place}", place) if place else tr(prompts.NEAR_RESULT_TITLE)
        body = "\n".join([title, *lines, "", tr(prompts.NEAR_NOTE), tr(prompts.NEAR_EMERGENCY)])
        self.wa.send_buttons(phone, body, [("m_menu", prompts.ROW_MENU[0])])

    # ================================================================ memory

    def _memory_jobs(self, phone, s, user_text):
        jobs, mode, started = [], s["mode"], time.time()
        if user_text and memory.worth_checking(user_text.replace("[voice note] ", "")):
            profile = dict(s["profile"])

            def profile_job():
                new = memory.update_profile(self.claude, mode, profile, user_text)
                if new != profile:
                    self.store.update_memory(phone, profile=new, since=started)
                    self.log_event(phone, event="profile_updated", fields=len(new))
            jobs.append(profile_job)

        if len(s["history"]) > memory.RECENT_MESSAGES - memory.FOLD_COUNT:  # fold before the hard cap is reached
            head = s["history"][:memory.FOLD_COUNT]
            summary = s["summary"]

            def fold_job():
                new_summary = memory.fold_summary(self.claude, mode, summary, head)
                with self.store.user_lock(phone):
                    if self.store.forgotten_since(phone, started):
                        return
                    cur = self.store.get(phone)
                    if cur["history"][:memory.FOLD_COUNT] == head:  # nothing reset meanwhile
                        cur["history"] = cur["history"][memory.FOLD_COUNT:]
                        cur["summary"] = new_summary
                        self.store.save(phone, cur)
                        self.log_event(phone, event="summary_folded")
            jobs.append(fold_job)
        return jobs

    def _my_data(self, phone, s):
        facts = []
        if s["mode"]:
            facts.append(f"• Language: {prompts.LANGUAGES.get(s['lang'], 'English')}")
        if s["profile"]:
            facts.append(memory.describe(s["profile"]))
        if s["summary"]:
            facts.append(f"• Our earlier chats: _{s['summary']}_")
        data = self.store.export(phone)
        counts = [(len(s.get("history") or []), prompts.MY_DATA_COUNT_MSGS), (len(data["items"]), prompts.MY_DATA_COUNT_ITEMS),
                  (len(data["followups"]), prompts.MY_DATA_COUNT_FU), (len(data["visits"]), prompts.MY_DATA_COUNT_VISITS),
                  (len(data["referrals"]), prompts.MY_DATA_COUNT_REFS), (len(data["stock_checks"]), prompts.MY_DATA_COUNT_STOCK),
                  (len(data["returns"]), prompts.MY_DATA_COUNT_RETURNS)]
        facts += [f"• {i18n.tr(label)}: {n}" for n, label in counts if n]
        body = prompts.MY_DATA.format(facts="\n".join(facts) if facts else prompts.MY_DATA_EMPTY)
        self.wa.send_buttons(phone, body, prompts.MY_DATA_BUTTONS)

    def _my_data_file(self, phone):
        """Everything we keep about this person, as one text file they can read or keep."""
        data = self.store.export(phone)
        lines = ["Akwaaba: everything stored about you", time.strftime("%d %b %Y %H:%M UTC", time.gmtime()), ""]
        for section, rows in data.items():
            lines.append(f"== {section.replace('_', ' ')} ==")
            lines.append(json.dumps(rows, ensure_ascii=False, indent=1, default=str) if rows else "(nothing)")
            lines.append("")
        self.log_event(phone, event="my_data_file")
        self.wa.send_document(phone, "\n".join(lines).encode("utf-8"), "akwaaba-my-data.txt",
                              caption=i18n.tr(prompts.MY_DATA_FILE_CAPTION), mime="text/plain")

    def _forget(self, phone, s):
        lang = s.get("lang") or "en"
        self.store.forget(phone)
        s.clear()
        s["_forgotten"] = True
        self.log_event(phone, event="forget")
        i18n.use({"lang": lang})  # say goodbye in their language, even though the data is gone
        self.wa.send_text(phone, prompts.FORGOTTEN)

    # ================================================================ growth & feedback

    def _share(self, phone):
        number = self.display_number
        if not number:
            try:
                number = self.display_number = self.wa.display_number()
            except Exception:
                number = ""
        share = i18n.tr(prompts.SHARE_TEXT.format(number=number or "<number>"))
        try:
            self.wa.send_link_button(phone, prompts.SHARE_BODY, prompts.SHARE_BUTTON,
                                     "https://wa.me/?text=" + quote(share.replace("*", "")))
        except SendError:
            self.wa.send_text(phone, prompts.SHARE_INTRO)
            self.wa.send_text(phone, share, preview_url=True)
        self.log_event(phone, event="share")

    def _reaction_feedback(self, phone, msg):
        r = msg.get("reaction") or {}
        emoji, target = r.get("emoji", ""), r.get("message_id", "")
        if not emoji or not target:
            return  # a removed reaction
        answer = self.store.outbound(target)
        if not answer:
            return
        positive = True if any(ch in POSITIVE for ch in emoji) else False if any(ch in NEGATIVE for ch in emoji) else None
        self.log_event(phone, event="feedback", positive=positive, source="reaction",
                       mode=answer["mode"], lang=answer["lang"], task=answer["task"])

    # ================================================================ helpers

    def _rate_limited(self, phone: str) -> bool:
        now, q = time.time(), self._recent[phone]
        while q and now - q[0] > 3600:
            q.popleft()
        q.append(now)
        return len(q) > self.rate_limit_per_hour

    @staticmethod
    def _uid(phone: str) -> str:
        import ids
        return ids.user_code(phone)


_FACILITY = re.compile(
    r"\b(hospitals?|clinics?|health ?(?:centres?|centers?|facilit(?:y|ies)|post)|chps(?: compound)?|"
    r"maternity|asopiti|ayaresabea|kɔdzi|dɔkɔta ƒe)\b[^\n]{0,30}?\b(?:near|close to|around|closest to|nearest to|"
    r"next to|to|in|at|wɔ|ɛbɛn|ɖe)\s+(?P<place>[^\n,?.!]{2,40})", re.I)


ALERT_FOLLOW_NONE = [("f_report", "Note to supervisor"), ("m_menu", "\U0001F3E0 Menu")]


def _month_start() -> int:
    t = time.gmtime()
    return int(time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1)))


def _facility_place(text: str) -> str | None:
    """'Hospitals close to Abrepo' -> 'Abrepo'. Only short requests, so real questions
    that mention a hospital ('should I take her to the hospital in Ho today?') go to Claude."""
    if len(text) > 80:
        return None
    # Health workers describe their own work ("I run the CHPS compound in Ho"): that's not a search.
    if re.match(r"\s*(i|i'm|im|we|we're|my|our|me)\b", text, re.I):
        return None
    m = _FACILITY.search(text)
    if not m:
        return None
    place = re.sub(r"\b(please|pls|today|now|area|town|district)\b", "", m.group("place"), flags=re.I).strip()
    return place if 2 <= len(place) <= 40 and len(place.split()) <= 4 else None


def _filename(title: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", media.plain(title)).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-")[:50]
    return t or "akwaaba"


def parse(msg: dict) -> dict:
    """Normalise a WhatsApp message into {kind, text, button, media_id, mime}."""
    mtype = msg.get("type")
    out = {"kind": mtype, "text": "", "button": None, "media_id": None, "mime": ""}
    if mtype == "text":
        out["text"] = (msg.get("text") or {}).get("body", "").strip()
    elif mtype == "interactive":
        inter = msg.get("interactive") or {}
        if inter.get("type") == "nfm_reply":  # a submitted WhatsApp Flow form
            try:
                out["flow"] = json.loads((inter.get("nfm_reply") or {}).get("response_json") or "{}")
            except json.JSONDecodeError:
                out["flow"] = {}
            return out
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        out["button"] = reply.get("id")
        out["text"] = reply.get("title", "")
    elif mtype == "button":
        out["text"] = (msg.get("button") or {}).get("text", "")
    elif mtype == "location":
        loc = msg.get("location") or {}
        try:
            out["location"] = (float(loc["latitude"]), float(loc["longitude"]))
        except (KeyError, TypeError, ValueError):
            out["kind"] = "unsupported"
    elif mtype in ("image", "document", "audio", "sticker", "video"):
        media = msg.get(mtype) or {}
        out["media_id"] = media.get("id")
        out["mime"] = (media.get("mime_type") or "").split(";")[0]
        out["text"] = (media.get("caption") or "").strip()
    return out
