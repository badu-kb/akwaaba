"""Everything the assistant says or is told: system prompts, guided tasks,
menus and fixed messages.

WhatsApp limits respected here (checked by tests):
  button title <= 20 chars, list row title <= 24, row description <= 72,
  list button label <= 20, section title <= 24, footer <= 60.
"""

# Language code -> display name. Change "ee" to "gaa": "Ga" etc. to swap languages.
LANGUAGES = {"en": "English", "tw": "Asante Twi", "ee": "Ewe"}
LANG_SHORT = {"en": "English", "tw": "Twi", "ee": "Ewe"}
# GhanaNLP Khaya speech-recognition codes for each language.
ASR_CODES = {"en": "eng", "tw": "twi", "ee": "ewe", "gaa": "gaa", "fat": "fat", "dag": "dag"}

# =========================================================================== system prompts

HEALTH_PROMPT = """You are Akwaaba, a calm, practical support assistant for community health workers and volunteers in Ghana (for example CHPS nurses, community health volunteers and midwives), reached over WhatsApp.

HOW YOU HELP
- Health education talks for communities: malaria prevention, handwashing, safe water, nutrition, antenatal care, immunisation, family planning basics.
- Recognising danger signs that need a health facility now, home visit guidance, summarising registers or tally sheets from photos, respectful answers to common myths.
- Messages that start with [voice note] were spoken and machine-transcribed: read past small transcription errors, but if a safety-relevant detail is unclear, ask.

SAFETY RULES (always follow)
- You support the health worker; you do not diagnose or replace a clinician or Ghana Health Service guidance.
- Never give prescription medicine doses. For medicines, point to the facility, national treatment guidelines or the supervising nurse.
- For fever, encourage a malaria test (RDT) rather than guessing.
- If danger signs appear (child unable to drink or breastfeed, convulsions, very fast or difficult breathing, unconsciousness, heavy bleeding in pregnancy, severe headache or fits in pregnancy, severe dehydration, snakebite), your FIRST line must say to go to the nearest health facility immediately, and mention 112 or 193 (ambulance).
- When unsure how urgent something is, choose the more urgent option.
- Never ask for patients' names, ID numbers or other identifying details.
"""

LANGUAGE_RULES = """
LANGUAGE
- Reply in {language}, even if the user writes in another language, unless they ask you to switch.
- Use everyday spoken {language}, the way people in Ghana actually talk, not stiff textbook language.
- Code-switching is normal in Ghana: keep common English technical terms (ORS, malaria test, antenatal care, RDT) where no everyday {language} word exists.
- If you are not confident about a word in {language}, add the English in brackets. Never invent words or proverbs.
"""

WHATSAPP_RULES = """
FORMAT (WhatsApp on a phone, often on prepaid data)
- SHORT. About 100 words, in any language; up to 150 only for a checklist, register summary or quiz. Ghanaian languages run longer than English, so be extra concise in them.
- WhatsApp formatting only: *bold* with single asterisks, _italic_, numbered lines or "•" bullets. No headings with #, no tables, no links.
- Start with a short bold title line with one fitting emoji, e.g. "*\U0001F99F Sleeping under a net*". Then the useful part, no greeting or restating the question.
- Use a blank line between parts so it's easy to scan on a small screen. Use "> " to quote a key tip or a line to say aloud.
- Do not end with a question or an offer: the app adds buttons for follow-ups.
- If the topic needs more, give the most useful part and end with "Reply MORE for the rest."
"""

MEMORY_BLOCK = """
WHAT YOU KNOW ABOUT THIS PERSON (from earlier chats; use it to tailor answers quietly, don't recite it)
{profile}
"""

SUMMARY_BLOCK = """
EARLIER CONVERSATION (summary of older messages)
{summary}
"""

TASK_BLOCK = """
CURRENT TASK: {title}
{guide}
If the latest message is clearly about something else, ignore this task format and just answer it.
"""

MODE_PROMPTS = {"health": HEALTH_PROMPT}


def build_system_prompt(mode: str, lang_code: str, profile: dict | None = None,
                        summary: str = "", task: str | None = None) -> str:
    parts = [MODE_PROMPTS.get(mode, HEALTH_PROMPT), LANGUAGE_RULES.format(language=LANGUAGES.get(lang_code, "English")),
             WHATSAPP_RULES]
    if profile:
        lines = "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in profile.items() if v)
        if lines:
            parts.append(MEMORY_BLOCK.format(profile=lines))
    if summary:
        parts.append(SUMMARY_BLOCK.format(summary=summary))
    if task and task in TASKS:
        t = TASKS[task]
        parts.append(TASK_BLOCK.format(title=t["title"], guide=t["guide"]))
    return "".join(parts)


# =========================================================================== guided tasks

TASKS = {
    "h_talk": {
        "examples": ["Mosquito nets", "Handwashing", "Early antenatal care"],
        "mode": "health", "title": "\U0001F4E2 Community talk", "desc": "A short talk for a gathering",
        "ask": "What topic?\n_e.g. Mosquito nets, handwashing, early antenatal care_",
        "guide": "A talk to say aloud in about one minute: a relatable opening, 3 key points, one action "
                 "people can take today. Simple words.",
        "follow": [("f_group_quiz", "Quiz for group"), ("f_simpler", "Simpler"), ("f_dangers", "Danger signs")],
    },
    "h_danger": {
        "form": {"key": "danger", "screen": "CHECK", "cta": "Open checklist",
                 "body": "Tick the signs you see and I'll tell you how urgent it is.\n_Or just describe the signs._"},
        "mode": "health", "title": "\U0001F6A8 Check danger signs", "desc": "When to refer to a facility now",
        "ask": "Describe the signs: age, what you see, how long.\n_No names please._",
        "guide": "First line is a verdict: \U0001F534 *Go to a facility NOW* / \U0001F7E0 *Visit a facility today* / "
                 "\U0001F7E2 *Watch at home*. When unsure, choose the more urgent level. Then the reasons, "
                 "then what to watch for. Never give medicine doses.",
        "follow": [("f_watch", "What to watch"), ("f_family", "Talk to family"), ("f_new_case", "New case")],
    },
    "h_visit": {
        "examples": ["Newborn visit", "Pregnancy visit", "TB follow-up"],
        "mode": "health", "title": "\U0001F3E0 Home visit guide", "desc": "What to ask and check on a visit",
        "ask": "What kind of visit?\n_e.g. Newborn, pregnancy, TB follow-up_",
        "guide": "A short checklist: what to ask, what to look at, danger signs that mean refer now, "
                 "one message to leave with the family.",
        "follow": [("f_dangers", "Danger signs"), ("f_family", "Talk to family"), ("f_simpler", "Simpler")],
    },
    "h_register": {
        "kind": "register",
        "mode": "health", "title": "\U0001F4CA Monthly return", "desc": "Register photos to a draft monthly return",
        "ask": "Send a photo of the register or tally sheet \U0001F4F8",
        "guide": "Summarise the counts, flag anything unusual (sudden rises, gaps between related numbers) "
                 "and say what to report to the supervisor. Don't guess unreadable numbers.",
        "follow": [("f_report", "Note to supervisor"), ("f_trends", "Spot trends"), ("f_new_photo", "New photo")],
    },
    "h_myth": {
        "examples": ["Vaccines are unsafe", "Malaria from mangoes", "Herbs over hospital"],
        "mode": "health", "title": "\U0001F4AC Answer a myth", "desc": "Respectful replies to common beliefs",
        "ask": "What are people in your community saying?\n_e.g. Vaccines make babies sick_",
        "guide": "Acknowledge the worry respectfully, give the facts simply, end with one practical step. Never mock.",
        "follow": [("f_shorter", "Shorter"), ("f_story", "As a story"), ("f_new", "New topic")],
    },
    # ---- interactive (not answered by a normal reply)
    "h_followup": {
        "kind": "followup", "mode": "health", "title": "\U0001F4C5 Follow-ups",
        "desc": "Vaccines, ANC and postnatal visits due", "ask": "", "guide": "", "follow": [],
    },
    "h_visitlog": {
        "kind": "visitlog", "mode": "health", "title": "\U0001F3E1 Log a home visit",
        "desc": "Record a visit by voice or text", "ask": "", "guide": "", "follow": [],
    },
    "h_referral": {
        "kind": "referral", "mode": "health", "title": "\U0001F501 Refer someone",
        "desc": "Send to the nurse or a facility, and track it", "ask": "", "guide": "", "follow": [],
    },
    "h_alert": {
        "kind": "alert", "mode": "health", "title": "\U0001F9A0 Disease alert",
        "desc": "Signs of an outbreak disease to report now",
        "ask": "Describe what you're seeing: how many people, their ages, the main signs and since when.\n_No names, please._",
        "guide": "", "follow": [],
    },
    "h_team": {
        "kind": "team", "mode": "health", "title": "\U0001F465 Team overview",
        "desc": "This month across your team", "ask": "", "guide": "", "follow": [],
    },
    "h_notice": {
        "kind": "notice", "mode": "health", "title": "\U0001F4E3 Notice to team",
        "desc": "Send one message to everyone", "ask": "", "guide": "", "follow": [],
    },
    "h_supvisit": {
        "examples": ["Records and returns", "Cold chain", "Stock and storage"],
        "mode": "health", "title": "\U0001F4CB Supervision visit", "desc": "Checklist for visiting a CHPS compound",
        "ask": "What should the visit focus on?\n_e.g. Records and returns, cold chain, stock and storage_",
        "guide": "A practical checklist for a sub-district supervisor visiting a CHPS compound in Ghana: what to look at, "
                 "what to ask the staff, what to praise, and 3 follow-up actions with who does what. Supportive, not "
                 "punitive. Never invent policy numbers or targets.",
        "follow": [("f_shorter", "Shorter"), ("f_report", "Note to supervisor"), ("f_new", "New topic")],
    },
    "h_stock": {
        "kind": "stock", "mode": "health", "title": "\U0001F4E6 Stock check",
        "desc": "Shelf counts to a draft requisition",
        "ask": "Type what's on the shelf, item and count.\n_e.g. RDT 40, AL 12, ORS 60, zinc 30, amoxicillin 50_",
        "guide": "", "follow": [],
    },
    "h_play": {
        "kind": "quiz", "examples": ["Malaria", "Newborn danger signs", "Safe water"],
        "mode": "health", "title": "\U0001F3AF Test yourself", "desc": "Quick quiz to refresh your training",
        "ask": "What topic should I quiz you on?\n_e.g. Malaria, newborn danger signs_",
        "guide": "", "follow": [],
    },
    "h_near": {
        "kind": "near", "mode": "health", "title": "\U0001F4CD Nearest facility",
        "desc": "Share your location to find clinics", "ask": "", "guide": "", "follow": [],
    },
}
# Menu order (WhatsApp lists hold 10 rows in total).
MENU_ORDER = {
    # The danger-sign checklist (h_danger) stays out of the pilot menu until a silent trial
    # (the plan's rule); the emergency card still fires on any message. WhatsApp lists hold 10 rows.
    "health": ["h_register", "h_followup", "h_referral", "h_alert", "h_talk", "h_stock", "h_visit", "h_myth"],
}
# Each part of the team gets the tools for its own job (WhatsApp lists hold 10 rows, 2 are extras).
MENU_BY_ROLE = {
    "nurse": MENU_ORDER["health"],
    "volunteer": ["h_visitlog", "h_referral", "h_alert", "h_talk", "h_visit", "h_myth", "h_near", "h_play"],
    "supervisor": ["h_team", "h_notice", "h_supvisit", "h_alert", "h_talk", "h_myth", "h_near", "h_play"],
}


# No role chosen: someone exploring sees the main tasks plus a "More tasks" page with
# the ones meant for volunteers and supervisors, so nothing is hidden from them.
MENU_EXPLORE = ["h_register", "h_followup", "h_referral", "h_alert", "h_talk", "h_visit", "h_stock"]
MENU_EXPLORE_MORE = ["h_visitlog", "h_team", "h_notice", "h_supvisit", "h_myth", "h_near", "h_play"]
MORE_TASKS_ROW = ("m_more_tasks", "\U0001F9ED More tasks", "For volunteers and supervisors too")
MORE_TASKS_BODY = "*\U0001F9ED More tasks*\nThese are the tasks volunteers and supervisors use most. Try any of them."
TOUR_EXPLORE = ["h_talk", "h_followup", "h_visitlog"]


def has_role(s: dict) -> bool:
    return bool((s.get("profile") or {}).get("role"))


def role_group(s: dict) -> str:
    role = ((s.get("profile") or {}).get("role") or "").lower()
    if "volunteer" in role:
        return "volunteer"
    if "supervisor" in role:
        return "supervisor"
    return "nurse"
# The companion app shows the tasks that work as a conversation; follow-ups and stock
# checks live in WhatsApp, where the Monday list arrives.
APP_MENU_ORDER = {"health": ["h_talk", "h_register", "h_visit", "h_myth", "h_play", "h_near"]}
# Answers to these tasks are kept in My library automatically.
AUTO_SAVE_TASKS = {"h_talk", "h_visit", "h_register"}
DOC_KIND = {"h_supvisit": "Supervision checklist", "h_alert": "Disease alert draft", "h_talk": "Community talk", "h_return": "Monthly return draft", "h_stock": "Stock requisition draft", "h_danger": "Danger-sign check", "h_visit": "Home visit guide", "h_register": "Register summary",
            "h_myth": "Myth and facts"}

GENERAL_FOLLOW = {
    "health": [("f_dangers", "Danger signs"), ("f_simpler", "Simpler"), ("f_more", "More")],
}

FOLLOW_UP_PROMPTS = {
    "f_group_quiz": "Make 3 quick spoken questions to check the group understood the talk, with answers.",
    "f_shorter": "Make that shorter.",
    "f_simpler": "Say that again more simply, in plain everyday words.",
    "f_more": "Continue from where you stopped, or give the next most useful detail.",
    "f_dangers": "Which danger signs related to this mean the person must go to a health facility now?",
    "f_watch": "What exactly should the family watch for at home, and when should they come back?",
    "f_family": "How should I explain this to the family, in simple respectful words?",
    "f_report": "Write a short note I can send my supervisor about this.",
    "f_trends": "What trends or gaps stand out, and what might be worth checking?",
    "f_story": "Retell this as a short story people will remember.",
    "f_stock_note": "Write a short, polite note to my sub-district supervisor about these stock levels: what is out or low and what I am requesting.",
}
# Buttons that start something new instead of asking the model.
RESET_BUTTONS = {"f_new": None, "f_new_case": "h_danger", "f_new_photo": "h_register"}


# =========================================================================== menus & onboarding

# Akwaaba serves community health workers only. "mode" is kept as the
# "set up yet?" flag and is always "health" once someone has picked a language.
MODE = "health"
ROLE_NAMES = {"health": "Health worker"}

WELCOME = (
    "*Akwaaba!* \U0001F44B\U0001F1EC\U0001F1ED\n"
    "I'm your free assistant for community health work.\n\n"
    "• Health talks to say aloud, with a voice note\n"
    "• Register and tally-sheet summaries from a photo\n"
    "• Home visit checklists, danger signs and the nearest clinic\n"
    "• Type or speak, in English, Twi or Ewe\n\n"
    "\U0001F30D Twi: kyerɛw *TW* · Eʋegbe: ŋlɔ *EE*\n\n"
    "First, which language should I reply in?"
)
WELCOME_FOOTER = "Free · Private · Works on any phone"

LANGUAGE_PROMPT = "Which language should I reply in? You can type or speak in any of them."
LANGUAGE_BUTTONS = [("lang_en", "English"), ("lang_tw", "Twi"), ("lang_ee", "Ewe")]

HEALTH_ROLE_PROMPT = "_Step 2 of 2_\nWhat's your role? I'll tailor everything to it."
HEALTH_ROLE_ROWS = [
    ("hr_cho", "Community health officer", "Running a CHPS compound"),
    ("hr_chn", "Community health nurse", "CHPS or clinic staff"),
    ("hr_chv", "Health volunteer", "Community health volunteer"),
    ("hr_mw", "Midwife", "Antenatal, delivery and newborn care"),
    ("hr_sup", "Health supervisor", "Sub-district or district team"),
    ("hr_skip", "Other / skip", "You can tell me later"),
]
HEALTH_ROLE_VALUES = {"hr_cho": "Community health officer", "hr_chn": "Community health nurse",
                      "hr_chv": "Community health volunteer", "hr_mw": "Midwife", "hr_sup": "Health supervisor"}
DETAIL_PROMPT = {"health": (HEALTH_ROLE_PROMPT, HEALTH_ROLE_ROWS)}

ROLE_SKIPPED_NOTE = ("_No role set, so you'll see every task, including the ones for volunteers and supervisors. "
                     "Type *ROLE* whenever you'd like answers tailored to your work._")
READY = {
    "health": "You're all set ✅\n*Health worker* · *{lang}*{detail}\n\n"
              "You can type, speak or send a photo \U0001F4F8\n\n"
              "\U0001F6A8 In an emergency, go to the nearest facility or call *112* / *193*.",
}

VOICE_PREF_ASK = ("_One last thing_\n"
                  "Do you prefer to *listen* or *read*? I can send every answer as a voice note too.")
VOICE_PREF_BUTTONS = [("vp_listen", "\U0001F50A Listen"), ("vp_read", "\U0001F4D6 Read")]
VOICE_PREF_LISTEN = "\U0001F50A Got it. Every answer will come as a voice note, with the text underneath."
VOICE_PREF_READ = "\U0001F4D6 Got it. Tap *➕ More* then *\U0001F50A Listen* whenever you'd like to hear an answer."

TOUR_BODY = ("*Tap one to try it* \U0001F447\n"
             "Or just type or send a voice note with any question. Send *MENU* any time to see everything.")
TOUR_TASKS = {"nurse": ["h_talk", "h_register", "h_followup"],
              "volunteer": ["h_visitlog", "h_talk", "h_referral"],
              "supervisor": ["h_team", "h_talk", "h_notice"]}

MENU_BODY = "What would you like to do?"
MENU_BUTTON = "Open menu"
MENU_SECTION = {"health": "Community health"}
MENU_MORE_SECTION = "More"
MENU_EXTRA_ROWS = [
    ("m_library", "\U0001F4DA My library", "Saved talks, checklists and notes"),
    ("m_settings", "⚙️ Settings & more", "Language, voice replies, sharing, data"),
]

WELCOME_BACK = (
    "Welcome back! \U0001F44B\n"
    "*{role}* · *{lang}*{detail}\n\n"
    "Pick a task, or just type or speak your question."
)

SETTINGS_LIST_BODY = "*⚙️ Settings*\n*{role}* · *{lang}*{detail}\nVoice replies: *{voice}*"
SETTINGS_LIST_BUTTON = "Open settings"
SETTINGS_SECTION = "Settings"
SETTINGS_ROWS = [
    ("ch_lang", "\U0001F30D Language", "English, Twi or Ewe"),
    ("ch_detail", "\U0001FA7A Your role", "So answers fit your work"),
    ("m_team", "\U0001F465 My team", "Your team code and who has joined"),
    ("m_voice", "\U0001F50A Voice replies", "Hear every answer as a voice note"),
    ("m_share", "\U0001F91D Share Akwaaba", "Send it to a colleague"),
    ("m_data", "\U0001F512 My data", "See or delete what I remember"),
    ("help", "❓ Help", "How to use Akwaaba"),
]
SETTINGS_CTA = "Open settings"
SETTINGS_FLOW_HINT = "Tap *Open settings* to change your language, role or voice replies."
SETTINGS_MORE_BODY = "Or choose one of these:"
SETTINGS_MORE_BUTTONS = [("m_team", "\U0001F465 My team"), ("m_share", "\U0001F91D Share Akwaaba"), ("m_data", "\U0001F512 My data")]
SETTINGS_MORE_FOOTER = "Type HELP for how to use Akwaaba"
SETTINGS_SAVED = "Saved ✅"
SETTINGS_FORM = {
    "intro": "Change anything below, then tap Save.",
    "lang_label": "Reply to me in", "role_label": "Your role", "voice_label": "Answers",
    "listen_title": "Listen and read", "listen_desc": "A voice note first, with the text underneath",
    "read_title": "Read only", "read_desc": "Text only, no voice notes", "save": "Save",
}
ON, OFF = "On", "Off"

HELP = (
    "*How to use Akwaaba*\n"
    "• Type or speak a question, send a photo, or type *MENU* for tasks\n"
    "• Tap the buttons under an answer for danger signs, a simpler version and more\n"
    "• Tap *➕ More* under an answer to hear it, print it as a PDF, or save it\n"
    "• React \U0001F44D or \U0001F44E to any answer to tell me how I did\n\n"
    "*Shortcuts*\n"
    "MENU · DUE · STOCK · MORE · QUIZ · CLINIC · LIBRARY · RESET · EN / TW / EE · VOICE ON / OFF · MY DATA · HELP"
)

SHARE_TEXT = (
    "I use *Akwaaba*, a free WhatsApp assistant for community health workers in Ghana \U0001F1EC\U0001F1ED\n"
    "Health talks with voice notes, register summaries from a photo, home visit checklists and the nearest clinic, "
    "in English, Twi or Ewe.\n\n"
    "Start here \U0001F449 https://wa.me/{number}?text=Hi"
)
SETUP_NUDGE = "Tap below to set up in about 20 seconds \U0001F447"
SETUP_CTA = "Get started"
SHARE_BODY = ("*Know a health worker who'd find this useful?* \U0001F91D\n"
              "Tap below, pick a contact or group, and send. It's free for them too.")
SHARE_BUTTON = "Share Akwaaba"
SHARE_INTRO = "Forward the next message to a colleague \U0001F447"

MY_DATA = (
    "*What I remember about you* \U0001F512\n{facts}\n\n"
    "I use this only to tailor my answers. I never keep patients' names, and "
    "the usage statistics I keep can't identify you."
)
MY_DATA_EMPTY = "_Nothing yet beyond your language._"
MY_DATA_BUTTONS = [("md_file", "Send me all of it"), ("fm_ask", "Forget me"), ("m_menu", "Back to menu")]
MY_DATA_COUNT_MSGS = "Recent messages kept word for word"
MY_DATA_COUNT_ITEMS = "Answers and drafts"
MY_DATA_COUNT_FU = "Follow-ups"
MY_DATA_COUNT_VISITS = "Home visits logged"
MY_DATA_COUNT_REFS = "Referrals"
MY_DATA_COUNT_STOCK = "Stock counts"
MY_DATA_COUNT_RETURNS = "Monthly return totals"
MY_DATA_FILE_CAPTION = "Everything Akwaaba keeps about you. Type FORGET ME to delete it all."
FORGET_CONFIRM = "Delete everything I remember about you, including our conversation? This can't be undone."
FORGET_BUTTONS = [("fm_yes", "Yes, delete"), ("m_menu", "Cancel")]
FORGOTTEN = "Done. I've deleted everything I remembered about you \U0001F44B\nSay *hi* any time to start again."

FEEDBACK_ASK = "Quick one: was that answer useful?"
FEEDBACK_BUTTONS = [("fb_yes", "\U0001F44D Yes"), ("fb_no", "\U0001F44E Not really")]
FEEDBACK_THANKS_YES = "Thank you! \U0001F64F"
FEEDBACK_THANKS_NO = "Thanks for telling me. Try *Simpler*, or tell me what you need differently."

EMERGENCY = (
    "\U0001F6A8 *This may be an emergency.*\n"
    "Go to the nearest health facility *now*, or call *112* or *193* (ambulance).\n"
    "Don't wait for my full answer."
)

ANSWER_FOOTER = {"health": "Not a diagnosis · Emergency: 112 / 193"}

VOICE_HEARD = "\U0001F399️ _I heard:_\n> {text}"
VOICE_UNAVAILABLE = "Sorry, I can't listen to voice notes right now \U0001F399️\nPlease type your question, or send a photo."
VOICE_UNCLEAR = ("Sorry, I couldn't make out that voice note \U0001F399️\n"
                 "Try again a little closer to the phone, in a quieter spot, or type it.")
VOICE_TOO_LONG = "That voice note is a bit long for me. Please keep it under 2 minutes."
UNSUPPORTED_TYPE = "Sorry, I can read text, voice notes, photos and PDF documents, but not that."
FILE_TOO_LARGE = "That file is too large for me. Please send a smaller one (under 10 MB)."
BAD_FILE_TYPE = "Sorry, I can read photos (JPG, PNG) and PDF documents, but not that kind of file."
RATE_LIMITED = "You've sent a lot of messages in the last hour. Please take a short break and try again soon."
BUSY = "Sorry, I'm very busy right now. Please try again in a minute."
ERROR = "Sorry, something went wrong on my side. Please try again in a moment."
PICK_LANG_FIRST = "Thanks! I'll answer that in a moment. First, which language should I reply in?"


# =========================================================================== answer actions

MORE_LABEL = "➕ More"
MORE_BODY = "What would you like to do with this answer?"
MORE_BUTTON = "Choose"
MORE_SECTION = "This answer"
MORE_NEXT = "Next"
ROW_LISTEN = ("\U0001F50A Listen", "Hear it as a voice note")
ROW_PDF = ("\U0001F4C4 Printable PDF", "A4 page to print or keep")
ROW_CARD = ("\U0001F5BC️ Image card", "A picture to forward to a group")
ROW_SAVE = ("⭐ Save to library", "Keep it in My library")
ROW_UNSAVE = ("✔️ Saved", "Tap to remove from My library")
ROW_QUIZIT = ("\U0001F3AF Turn into a quiz", "Tap-to-answer questions on this")
ROW_NEAR = ("\U0001F4CD Nearest facility", "Clinics and hospitals near you")
ROW_MENU = ("\U0001F3E0 Main menu", "All tasks")
ITEM_GONE = "That answer is no longer stored. Ask again and I'll redo it."
SAVED_FOOTER = "⭐ Saved to My library · React \U0001F44D or \U0001F44E"

PDF_CAPTION = "\U0001F4C4 Ready to print or forward"
PDF_FAILED = "Sorry, I couldn't make the PDF just now. Please try again in a moment."

VOICE_NOT_SETUP = "Voice replies aren't switched on for this assistant yet. Please read the answer above."
VOICE_REPLY_FAILED = "Sorry, I couldn't make the voice note just now. Please try again later."
VOICE_ON = "\U0001F50A Voice replies are *on*. Every answer will also come as a voice note."
VOICE_OFF = "\U0001F507 Voice replies are *off*. Tap *➕ More* then *\U0001F50A Listen* to hear any answer."

LIB_SAVED = "⭐ Saved to *My library*."
LIB_REMOVED = "Removed from *My library*."
LIB_BUTTONS = [("m_library", "\U0001F4DA My library"), ("m_menu", "\U0001F3E0 Menu")]
LIBRARY_BODY = "*\U0001F4DA My library*\nTap any item to open it again."
LIBRARY_BUTTON = "Open library"
LIBRARY_SECTION = "Saved"
LIBRARY_MORE = ("More saved items", "Older items")
LIBRARY_EMPTY = ("*\U0001F4DA My library* is empty for now.\n"
                 "Health talks, home visit guides and register summaries are saved here automatically. "
                 "To keep any other answer, tap *➕ More* under it, then *⭐ Save to library*.")
OPEN_BUTTONS = [("pdf", "\U0001F4C4 PDF"), ("say", "\U0001F50A Listen"), ("card", "\U0001F5BC️ Image card")]

# =========================================================================== quiz game

QUIZ_MAKING = "\U0001F3AF Making a quiz on *{topic}*…"
QUIZ_FAILED = "Sorry, I couldn't make that quiz. Try another topic, or try again in a moment."
QUIZ_LABEL = "Question"
QUIZ_RIGHT = "✅ *Correct!*"
QUIZ_WRONG = "❌ *Not quite.* The answer is *{answer}*."
QUIZ_DONE = "*\U0001F3C1 Quiz complete: {score}/{total}*"
QUIZ_REVIEW = "*To review:*"
QUIZ_PERFECT = "Perfect score! Brilliant work."
QUIZ_GREAT = "Excellent work!"
QUIZ_GOOD = "Well done!"
QUIZ_TRY = "Good effort. Keep practising!"
QUIZ_END_BUTTONS = [("qz_again", "\U0001F501 New questions"), ("qz_harder", "⬆️ Harder"), ("m_menu", "\U0001F3E0 Menu")]
QUIZ_STALE = "That question has already been answered. Tap the newest question to carry on."

# =========================================================================== nearest facility

NEAR_ASK = ("*\U0001F4CD Nearest health facility*\n"
            "Tap *Send location* below, or type the name of a town, and I'll show the closest clinics "
            "and hospitals on a map.\n\n"
            "\U0001F6A8 Emergency? Call *112* or *193* now.")
NEAR_RESULT_TITLE = "*\U0001F4CD Nearest health facilities*"
NEAR_PLACE_TITLE = "*\U0001F4CD Health facilities near {place}*"
NEAR_PLACE_UNKNOWN = "I couldn't find *{place}* on the map. Try a bigger town nearby, or tap *Send location*."
NEAR_LINE = "{n}. *{name}* · {kind} · {km} km"
NEAR_NOTE = "_Tap a pin above for directions. Facilities come from OpenStreetMap, so call ahead if you can._"
NEAR_EMERGENCY = "\U0001F6A8 Emergency: call *112* or *193*."
NEAR_NONE = ("I couldn't find a mapped health facility near you. Ask your supervisor or local CHPS compound, "
             "and call *112* or *193* in an emergency.")
NEAR_FAILED = "Sorry, I couldn't search the map just now. In an emergency, call *112* or *193*."
EMERGENCY_NEAR_BUTTON = [("m_near", "\U0001F4CD Nearest facility")]

# =========================================================================== monthly return from register photos

REGISTER_ASK = ("*\U0001F4CA Draft monthly return*\n"
                "Send a photo of each register or tally-sheet page for the month, one at a time \U0001F4F8\n\n"
                "\U0001F512 I hide names on every photo before reading it, and I keep only the counts.\n"
                "Tap *Done* when all pages are in.")
REGISTER_READING = "\U0001F512 Hiding names, then reading page {n}…"
REGISTER_PAGE_OK = "\U0001F4C4 *Page {n} read:* {found} counts found{unclear}.\nSend the next page, or tap *Done*."
REGISTER_UNCLEAR = ", {k} hard to read"
REGISTER_BUTTONS = [("rg_done", "✅ Done"), ("rg_cancel", "Cancel")]
REGISTER_EMPTY_PAGE = ("I couldn't find any counts on that page. Try a straighter photo in good light, "
                       "with the whole page in view.")
REGISTER_OFF = ("\U0001F512 Register photos are switched off on this number, because names can't be hidden "
                "automatically yet. Please type the counts instead, or ask your supervisor.")
PHOTO_PRIVACY_NOTE = ("\U0001F512 A quick note: photos and PDFs you send in the chat go to the AI as they are. "
                      "Please don't send anything that shows a patient's name, face or phone number.")
REGISTER_NO_PAGES = "Send at least one page photo first \U0001F4F8"
REGISTER_TOO_MANY = "That's the most pages I can add to one return. Tap *Done* to see the draft."
REGISTER_PHOTO_ONLY = "Please send the page as a photo, not a document \U0001F4F8"
REGISTER_APP_MORE = "_Send the next page to add it to this draft. Every page is added to the same month._"
REGISTER_CANCELLED = "Cancelled. Nothing was kept."
REGISTER_PDF_CAPTION = "\U0001F4C4 Draft monthly return · check before submitting"
REGISTER_FAILED = "Sorry, I couldn't read that page just now. Please send it again in a moment."
RETURN_FOLLOW = [("f_report", "Note to supervisor"), ("f_trends", "Spot trends")]

# =========================================================================== companion app

APP_CODE = ("\U0001F510 Your Akwaaba app code is *{number}*\n"
            "It works for 10 minutes. Never share it: Akwaaba will never ask you for it.")
APP_CODE_WAIT = "I've just sent you a code. Please wait half a minute before asking for another."


# =========================================================================== follow-ups
FU_HOME_BODY = ("*\U0001F4C5 Follow-ups*\n{summary}\n\n"
                "Add people by *register or card number*, never by name. I work out when each vaccine, "
                "antenatal contact or postnatal check is due, and send you the list every Monday.")
FU_SUMMARY = "{open} people on your list · {due} due this week"
FU_EMPTY_SUMMARY = "Nobody on your list yet."
FU_BUTTON = "Open"
FU_SECTION = "Follow-ups"
FU_ROWS = [
    ("fu_due", "\U0001F4CB Due this week", "Overdue and due in the next 7 days"),
    ("fu_add:child", "\u2795 Add a child", "Immunisation schedule from birth"),
    ("fu_add:pregnancy", "\u2795 Add a pregnancy", "Antenatal contacts to the due date"),
    ("fu_add:postnatal", "\u2795 Add a new mother", "Postnatal checks after delivery"),
    ("fu_all", "\U0001F5C2\uFE0F Everyone on my list", "See or remove a follow-up"),
    ("fu_remind", "\U0001F4E8 Reminder messages", "Texts to forward to families"),
]
FU_ASK = {
    "child": ("*\u2795 Add a child*\nSend the child's *card or register number* and *date of birth*.\n"
              "_e.g. Card 112, 14/08/2026_\n\n\U0001F512 Numbers only, please. I don't keep names."),
    "pregnancy": ("*\u2795 Add a pregnancy*\nSend the *ANC register number* and the *due date* or *weeks pregnant*.\n"
                  "_e.g. ANC 34, EDD 20/03/2027_ or _ANC 34, 24 weeks_\n\n\U0001F512 Numbers only, please. I don't keep names."),
    "postnatal": ("*\u2795 Add a new mother*\nSend the *register number* and the *date of delivery*.\n"
                  "_e.g. PNC 9, 28/10/2026_\n\n\U0001F512 Numbers only, please. I don't keep names."),
}
FU_ERRORS = {
    "no_number": "I need a *card or register number* so you can find them in your register. Names aren't kept. Try again, e.g. _Card 112, 14/08/2026_",
    "no_date": "I couldn't find a date. Please write it like *14/08/2026*.",
    "bad_date": "That date doesn't look right for this follow-up. Please check it and send again.",
    "bad_weeks": "Weeks pregnant should be between 4 and 42. Please check and send again.",
}
FU_ADDED = "\u2705 Added *{label}*.\nNext: *{what}* \u00b7 {date}"
FU_ASSUMED = "\n_I've counted {n} earlier visits as done. Check the record book and tell me if any were missed._"
FU_ADDED_PAST = "\u2705 Added *{label}*.\n{n} earlier visits were due before today:"
FU_PAST_ASK = "Please check the child health record book. Were *all* of these given?"
FU_PAST_YES = "Yes, all given"
FU_PAST_NO = "Some were missed"
FU_PAST_CONFIRMED = "Thanks. I've marked the earlier visits for *{label}* as given, as shown on the card."
FU_PAST_CATCHUP = ("OK. I'll keep the earlier visits for *{label}* on the list as catch-up, oldest first. "
                   "When a dose is given, tap it below and I'll note today's date.")
FU_ADDED_BUTTONS = [("fu_again", "Add another"), ("fu_due", "Due this week")]
FU_NOTHING_DUE = "\u2705 Nothing is due in the next 7 days."
FU_MARK_BODY = "Tap someone to mark their visit done."
FU_MARK_BUTTON = "Mark done"
FU_MARK_SECTION = "Due now"
FU_MARKED = "\u2705 *{label}*: {what} marked done."
FU_NEXT = "Next: *{what}* \u00b7 {date}"
FU_FINISHED = "\U0001F389 *{label}* has finished this schedule, so I've taken them off the list."
FU_ALL_BODY = "Tap someone to see their schedule or remove them."
FU_ALL_BUTTON = "Everyone"
FU_ALL_SECTION = "On my list"
FU_VIEW_BUTTONS = [("fu_done1", "\u2705 Next done"), ("fu_rm", "\U0001F5D1\uFE0F Remove"), ("fu_due", "Due this week")]
FU_REMOVED = "Removed *{label}* from your list."
FU_GONE = "That follow-up is no longer on your list."
FU_REMIND_INTRO = "\U0001F4E8 Forward each message below to the family. Add the name yourself when you send it."
FU_REMIND = {
    "child": ("Hello \U0001F44B A reminder from your health centre: your child's next vaccines ({what}) are due on "
              "*{date}*. Please come with the child and the child health record book. Thank you! \U0001F49A"),
    "pregnancy": ("Hello \U0001F44B A reminder from your health centre: your next antenatal visit is due on *{date}*. "
                  "Please bring your maternal health record book. Thank you! \U0001F49A"),
    "postnatal": ("Hello \U0001F44B A reminder from your health centre: the next check for you and your baby is due on "
                  "*{date}*. Please bring your record books. Thank you! \U0001F49A"),
}
FU_DIGEST_HEAD = "Good morning \U0001F305 Here is this week's list."

# =========================================================================== stock check
STOCK_UNKNOWN = "_I didn't recognise: {words}_"
STOCK_NONE = "I couldn't find any counts. Type the item and the number, e.g. *RDT 40, AL 12, ORS 60*."
STOCK_FOLLOW = [("f_stock_note", "Note to supervisor"), ("st_again", "Count again")]


# =========================================================================== teams
TEAM_LEAD_BODY = ("*\U0001F465 My team*\nYour team code is *{code}*.\n{who} join by sending *JOIN {code}* to this number. "
                  "Then their referrals, alerts and monthly numbers reach you.\n\n{members}")
TEAM_WHO = {"nurse": "Your volunteers", "supervisor": "Your nurses", "volunteer": "Others"}
TEAM_NO_MEMBERS = "_Nobody has joined yet._"
TEAM_MEMBERS_HEAD = "*On your team ({n})*"
TEAM_MEMBER_OF = "You're on the team of {lead}. Referrals and alerts you send reach them."
TEAM_NOT_MEMBER = "You haven't joined a team yet. Ask your nurse or supervisor for their code, then send *JOIN* and the code."
TEAM_BUTTONS = [("tm_leave", "Leave my team"), ("m_menu", "\U0001F3E0 Menu")]
TEAM_JOINED = "\u2705 You've joined the team of {lead}. Your referrals and alerts will reach them."
TEAM_NEW_MEMBER = "\U0001F465 A {role} ({who}) joined your team."
TEAM_BAD_CODE = "I couldn't find that team code. Check it with your nurse or supervisor, e.g. *JOIN AK-4821*."
TEAM_OWN_CODE = "That's your own team code. Share it with your team so they can join."
TEAM_LEFT = "You've left the team."
ROLE_WORD = {"nurse": "nurse", "volunteer": "volunteer", "supervisor": "supervisor"}
INBOX_HEAD = "\U0001F4EC While you were away:"

# =========================================================================== visits (volunteers)
VISIT_BODY = "*\U0001F3E1 Log a home visit*\nWhat kind of visit was it?"
VISIT_BUTTON = "Visit type"
VISIT_SECTION = "Visit type"
VISIT_TYPES = [
    ("vt_newborn", "\U0001F476 Newborn", "Baby in the first 4 weeks"),
    ("vt_pregnancy", "\U0001F930 Pregnancy", "A pregnant woman"),
    ("vt_child", "\U0001F912 Sick child", "A child who is unwell"),
    ("vt_followup", "\U0001F501 Follow-up", "Checking on someone seen before"),
    ("vt_talk", "\U0001F4E2 Health talk", "A talk with a household"),
    ("vt_other", "\U0001F3E0 Other visit", "Anything else"),
]
VISIT_ASK = ("Send the *house or register number* and anything you noticed. You can send a voice note.\n"
             "_e.g. House 14, baby feeding well_\n\n\U0001F512 No names, please.")
VISIT_LOGGED = "\u2705 Visit logged \u00b7 {n} this month."
VISIT_DANGER = "\u26A0\uFE0F What you described may be a danger sign. Tap *Refer now* so your nurse knows."
VISIT_BUTTONS = [("vt_again", "Log another"), ("m_menu", "\U0001F3E0 Menu")]
VISIT_DANGER_BUTTONS = [("vl_refer", "\U0001F501 Refer now"), ("vt_again", "Log another")]

# =========================================================================== referrals
REF_BODY = "*\U0001F501 Refer someone*\nWhy are you referring them?"
REF_BUTTON = "Reason"
REF_SECTION = "Reason"
REF_REASONS = [
    ("rr_breathing", "Fast or hard breathing", "Struggling to breathe, chest pulling in", True),
    ("rr_newborn", "Newborn danger sign", "Not feeding, cold, fever, yellow, fits", True),
    ("rr_pregnancy", "Pregnancy danger sign", "Bleeding, bad headache, swelling, fits", True),
    ("rr_dehydration", "Diarrhoea, dehydrated", "Sunken eyes, can't drink, very weak", True),
    ("rr_fever", "Child with fever", "Fever in a child under 5: needs a test", False),
    ("rr_malnutrition", "Malnutrition", "MUAC red or yellow, swollen feet", False),
    ("rr_tb", "Cough over 2 weeks", "Needs a TB test", False),
    ("rr_fp", "Family planning", "Wants a method or has side effects", False),
    ("rr_other", "Something else", "Any other reason to see a nurse", False),
]
REF_LIST_ROW = ("rf_list", "\U0001F4CB Open referrals", "Who hasn't arrived yet")
REF_ASK = ("Send the *card, register or house number* of the person.\n_e.g. House 14 or Card 112_\n\n"
           "\U0001F512 No names, please.")
REF_SENT = "\U0001F501 *Referral recorded:* {label} \u00b7 {reason}.\n{who}I'll ask you in 2 days whether they arrived."
REF_SENT_LEAD = "Your nurse has been told. "
REF_NO_TEAM = "_Tip: join your nurse's team (send JOIN and her code) so she gets your referrals straight away._\n"
REF_TO_LEAD = "\U0001F514 *Referral from a {role}* ({who}): *{label}* \u00b7 {reason}.\nTap when they arrive."
REF_LEAD_BUTTONS = [("rf_in", "\u2705 Arrived"), ("rf_wait", "Not yet")]
REF_ARRIVED = "\u2705 *{label}* arrived for care. Thank you!"
REF_ARRIVED_OK = "\u2705 Marked *{label}* as arrived."
REF_WAIT_OK = "Okay. I'll keep *{label}* on the open list."
REF_CHECK = "\U0001F501 Did *{label}* ({reason}) reach the nurse or facility?"
REF_CHECK_BUTTONS = [("rf_yes", "\u2705 Yes"), ("rf_no", "\u274C No"), ("rf_unsure", "Not sure")]
REF_NOT_ARRIVED = ("Thanks for checking. Please visit *{label}* again today if you can, and encourage them to go. "
                   "If they have a danger sign, tap *Refer now*.")
SMS_RULES = ("\n\nThis reply goes out as an SMS on a weak connection. Answer in plain text only: no formatting, "
             "no lists, no emoji. Use at most 350 characters and put the single most important action first. "
             "If there is any danger sign, the first words must tell them to go to a health facility now.")
REF_NOT_ARRIVED_LEAD = ("\u26A0\uFE0F *{label}* was referred by {who} but has not reached the facility. "
                        "They have been asked to visit again today.")
REF_THANKS = "Thanks, noted."
REF_NONE_OPEN = "\u2705 No open referrals."
REF_OPEN_HEAD = "*\U0001F4CB Open referrals*"
REF_GONE = "That referral is already closed."

# =========================================================================== disease alerts
ALERT_NONE = ("Nothing you described matches a disease on the immediate-reporting list. If several people have the same "
              "illness, or someone died unexpectedly, tell your supervisor today.")
ALERT_TO_LEAD = "\U0001F6A8 *Disease alert from a {role}* ({who}): possible *{disease}*. Please follow up today."
ALERT_FOLLOW = [("f_report", "Note to supervisor"), ("h:h_referral", "\U0001F501 Refer someone")]
ALERT_HINT = "\U0001F9A0 This may be a disease to report immediately (possible *{disease}*)."
ALERT_HINT_BUTTONS = [("al_now", "\U0001F9A0 Draft the alert")]

# =========================================================================== supervisor
NOTICE_ASK = "*\U0001F4E3 Notice to team*\nType the message for your {n} team members."
NOTICE_EMPTY = "Nobody has joined your team yet. Share your code from *My team* first."
NOTICE_FROM = "\U0001F4E3 *Notice from your {role}:*\n{text}"
NOTICE_SENT = "\u2705 Sent to {now} now. {later} will get it with their next message."
TEAM_OVERVIEW_EMPTY = "Nobody has joined your team yet. Open *My team* to get your code."
TEAM_DIGEST_HEAD = "\U0001F465 Your team this week."
