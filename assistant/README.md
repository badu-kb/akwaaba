# Akwaaba: WhatsApp assistant for Ghana's community health workers

Akwaaba is a Claude-powered WhatsApp assistant for community health workers in Ghana: community health officers at CHPS compounds, nurses, midwives and volunteers. It is health-only by design. It works in English, Asante Twi and Ewe, and accepts text, voice notes, photos and PDFs. It connects through Meta's WhatsApp Cloud API.

| Page | Address |
| --- | --- |
| Landing page with QR code | `https://<NGROK_DOMAIN>/` |
| Live impact dashboard | `https://<NGROK_DOMAIN>/dashboard` |
| Privacy policy | `https://<NGROK_DOMAIN>/privacy` |

## Run it

```
cd ~/Documents/akwaaba/assistant
bash setup.sh          # one time
open -e .env           # add your keys (see .env.example), save, quit TextEdit
bash start.sh          # starts ngrok, keeps the Mac awake, runs the assistant
```
The Terminal shows "Connected to WhatsApp number ..." and "Voice notes: ON" when everything is working. Press Ctrl+C to stop.

### Turning on voice notes (optional, recommended)
1. Get a free API key at https://translation.ghananlp.org (GhanaNLP Khaya) and add it to `.env` as `KHAYA_API_KEY`.
2. Install ffmpeg: `brew install ffmpeg`
3. Restart with `bash start.sh`.

### Turning on native forms (WhatsApp Flows, recommended)
Flows are app-style forms that slide up inside WhatsApp: a one-screen setup and a danger-sign checklist.
```
source ~/.akwaaba-venv/bin/activate
python flows_setup.py
```
This registers the two forms in `flows/` (`setup.json`, `danger.json`) with Meta and saves their IDs in `flows/registry.json`. Restart `bash start.sh` afterwards. If Meta won't publish them yet, they stay as drafts, which still work for your test numbers. Without Flows, everything falls back to buttons and menus.

**Upgrading from the teacher-and-health version:** the old setup form asked "Teacher or health worker", and a published form can't be edited. Akwaaba now ignores `FLOW_ONBOARDING_ID` and `FLOW_LESSON_ID`, so setup uses buttons until you register the new form: run `python flows_setup.py` (or create it by hand from `flows/setup.json` in WhatsApp Manager), publish it, and put its ID in `.env` as `FLOW_SETUP_ID`. Anyone who set up as a teacher is moved to health automatically the next time they write; their teaching profile and conversation are cleared.

### Turning on register photos (monthly returns)
Install Tesseract: `brew install tesseract`, then restart. It hides names on every register photo before Claude sees it. Without it, register photos stay switched off (the assistant says so), because Akwaaba never sends a photo with names on it.
The return layout in `report_formats/chps_monthly_draft.json` is a **placeholder**. Before a pilot, copy the district's real monthly return form into a new file there (sections, fields, checks) and add `redact_bands` for the register's name columns, measured from a blank register page.

### Follow-ups and the Monday list
Menu > *Follow-ups* (or type DUE). Nurses add children, pregnancies and new mothers by **card or register number** and one date; any other words, such as names, are dropped before saving. The schedules live in `schedules/ghana.json` (Ghana Health Service child immunisation schedule, WHO antenatal and postnatal timing): have the district confirm them before the pilot.

Every Monday from 07:00 (Ghana time) each nurse with people due gets the week's list. WhatsApp only allows free-form messages within 24 hours of the person's last message; outside that window it needs an approved template:
1. In WhatsApp Manager, create a **Utility** template named `akwaaba_weekly_due`, body: `Good morning! {{1}} of your follow-ups are due this week. Reply DUE to see the list.`
2. Once approved, set `WA_TEMPLATE_DUE=akwaaba_weekly_due` in `.env`. Add Twi and Ewe versions of the template and set `WA_TEMPLATE_LANGS=en,tw,ee` when they're approved.

Set `WEEKLY_LIST=off` to stop the Monday list, or `WEEKLY_LIST_HOUR` to change the hour.

### Teams, visits, referrals and disease alerts
Menus follow the person's role: **volunteers** log home visits, refer people and raise disease alerts; **nurses and midwives** get returns, follow-ups, referrals, alerts and stock; **supervisors** get a team overview, notices to the team and a supervision-visit checklist.
- *My team* (Settings, or type TEAM) shows a code like `AK-4821`. Volunteers send `JOIN AK-4821` to join their nurse; nurses join their supervisor the same way.
- Referrals reach the team lead, who taps *Arrived*; after 48 hours the referrer is asked whether the person arrived. The dashboard shows the completion rate.
- Disease alerts follow `diseases/idsr.json` (based on WHO AFRO's IDSR guidelines; the district must confirm Ghana's 2022 list). A matching message gets a draft alert for the District Disease Control Officer, and the lead is told.
- Messages to someone outside WhatsApp's 24-hour window wait in their outbox and arrive with their next message. Optionally set `WA_TEMPLATE_INBOX` to an approved template ("You have {{1}} new messages on Akwaaba. Reply HI to read them.") to nudge them.

### Stock check
Menu > *Stock check* (or type STOCK). The nurse types shelf counts (`RDT 40, AL 12, ORS 60`); Akwaaba flags stock-outs and items below minimum and drafts a requisition up to the maximum. Minimum and maximum levels in `stock_lists/chps_core.json` are **placeholders** until the district's own levels are added.

## Host it (instead of the Mac)

The Mac plus ngrok is fine for demos. For a pilot, run the container on a host with a persistent disk:

```
# Fly.io (region jnb, Johannesburg)
fly launch --no-deploy --copy-config
fly volumes create akwaaba_data --region jnb --size 1
fly secrets set ANTHROPIC_API_KEY=... META_ACCESS_TOKEN=... META_PHONE_NUMBER_ID=... \
                META_VERIFY_TOKEN=... META_APP_SECRET=... PUBLIC_BASE_URL=https://<app>.fly.dev
fly deploy
```
Then set the webhook in Meta to `https://<app>.fly.dev/whatsapp`. `render.yaml` does the same on Render. The `Dockerfile` installs ffmpeg and Tesseract, keeps the database at `/data`, and the host checks `/healthz`. Keep it to **one** machine: SQLite lives on its volume. Copy your existing data across with `fly ssh sftp shell` (put `~/.akwaaba/akwaaba.db` at `/data/akwaaba.db`) if you want to keep test users.

| Setting | Needed | What it is |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Claude API key |
| `META_ACCESS_TOKEN`, `META_PHONE_NUMBER_ID`, `META_VERIFY_TOKEN`, `META_APP_SECRET` | yes | WhatsApp Cloud API |
| `PUBLIC_BASE_URL` | yes when hosted | e.g. `https://akwaaba.fly.dev`, for images and links |
| `KHAYA_API_KEY` | for voice | GhanaNLP speech |
| `TTS_PROVIDER_EN` / `_TW` / `_EE` | optional | Who speaks each language: `abena` (Abena AI, Ghanaian voices) or `khaya`. English defaults to `abena`, Twi and Ewe to `khaya` |
| `ABENA_API_KEY` | optional | From a free Abena AI account; without it you're on the shared free tier |
| `ABENA_VOICE_EN` / `_TW` / `_EE` | optional | Abena voice, e.g. `akua_eng_high`, `kwabena_eng_high`, `abena_twi_high`, `mawuli_ewe` |
| `TTS_VOICE_EN` / `_TW` / `_EE` | optional | Khaya voice per language: `female`, `male_low` or `male_high`. Compare every voice with `python voice_samples.py` |
| `ENGLISH_VOICE` | optional | `off` keeps English text only (Twi and Ewe still get voice) |
| `DASHBOARD_KEY` | optional | protects `/dashboard` and `/stats`. If empty, a key is made for you and the dashboard link is printed at start-up |
| `LOG_SALT` | optional | secret for the one-way phone codes in the usage log; made for you if empty |
| `REGISTER_PHOTOS` | off | `on` only after district and Data Protection Commission approval. Needs a real register layout (`redact_bands`) in `report_formats/` |
| `META_APP_SECRET` | **required** | without it, every WhatsApp message is refused |
| `FLOW_SETUP_ID`, `FLOW_DANGER_ID` | optional | WhatsApp Flows |
| `WA_TEMPLATE_DUE`, `WA_TEMPLATE_LANGS` | recommended | Approved template for the Monday list outside the 24-hour window |
| `CLAUDE_MODEL`, `MEMORY_MODEL`, `RATE_LIMIT_PER_HOUR` | optional | defaults are fine |

## Safety checks

`python3 evals/run_red_flags.py` scores the emergency fast-path against `evals/red_flags.json`: 62 urgent cases (child, newborn, maternal, adult danger signs) and 27 routine questions. Today: 62/62 urgent caught, 0/27 false alarms. The tests fail if any urgent case is missed. A Ghanaian clinician should review and extend the set, including Twi and Ewe phrasings.

## The experience, end to end

1. **Welcome card.** A branded image, a short pitch and three language buttons: English, Twi, Ewe. The welcome even accepts a question first; it's answered as soon as setup ends.
2. **One-screen setup.** With Flows on, language, role (community health officer, nurse, volunteer, midwife) and town are chosen in a single native form. Without Flows, it's two taps with a "Step 2 of 2" progress hint.
3. **A task menu.** A WhatsApp list with guided tasks:
   - Community talk, Check danger signs, Nearest facility, Test yourself, Home visit guide, Read a register (photo), Answer a myth.
   - Plus Share Akwaaba, Settings and My data.
4. **Native form for danger signs.** The danger-sign checklist. Ticking any WHO general danger sign triggers the emergency card instantly, without waiting for the AI. Other tasks offer three one-tap example topics.
5. **Answers shaped by the task.** A community talk is one minute to say aloud with one action for today. A danger-sign check opens with a red, amber or green verdict. A register photo comes back as counts, anything unusual flagged, and what to tell the supervisor.
6. **One-tap follow-ups under every answer**, specific to the task: Quiz for group, Danger signs, What to watch, Note to supervisor, and so on.
7. **Voice notes.** Recognised by GhanaNLP's Khaya model (Twi, Ewe, Ghanaian English). The assistant shows "I heard: ..." first, so people can catch mistakes.
8. **Feels alive.** A "typing…" bubble while it works, a 👀 reaction on photos and a 🎧 on voice notes.
9. **Safety fast-path.** Messages describing an emergency (convulsions, heavy bleeding, snakebite and so on) get an instant "go to a facility now, call 112 / 193" card before the full answer. This check is deterministic code, not the AI.
10. **Ratings without friction.** People react 👍 or 👎 to any answer, and every sixth answer asks once. Both feed the dashboard.
11. **Modern answer style.** A bold title with an emoji, blank lines between parts, and quoted key tips.
12. **Growth loop.** "Share Akwaaba" is a single button that opens WhatsApp's forward screen with a ready-made invite.

## Memory: context that carries forward

| Layer | What it holds | How long |
| --- | --- | --- |
| Recent messages | The last 24 messages, word for word (`RECENT_MESSAGES`) | Until 7 days of silence |
| Rolling summary | Older messages folded into a short summary by a small background model | Until FORGET ME |
| Profile | Durable work facts the person shares: role, facility, town, focus areas, community language | Until FORGET ME |

- Memory updates run **after** the reply is sent, so they never slow anyone down.
- Privacy is enforced in code as well as instructions: only allow-listed profile fields are kept, values are capped in length, and anything that looks like a phone or ID number is dropped. Patients' names and health details are excluded.
- People can type **MY DATA** to see exactly what's remembered, and **FORGET ME** to erase it.

## Reliability

- Sessions and memory live in SQLite at `~/.akwaaba/akwaaba.db` (outside iCloud) and survive restarts.
- Duplicate deliveries from Meta are ignored, and each person's messages are handled one at a time.
- Meta and Claude calls are retried with backoff, with a friendly "busy" message if Claude is overloaded.
- An expired Meta token produces a clear message in the Terminal, and undelivered replies are logged.
- A per-person rate limit (`RATE_LIMIT_PER_HOUR`) protects your API bill.
- Files are checked for type and size before anything goes to Claude.
- Over-long answers are trimmed at a sentence and offer "Reply MORE".

Run the 140 automated tests (no keys needed):
```
source ~/.akwaaba-venv/bin/activate && pip install pytest && python -m pytest -q tests
```

## Code map

| File | Job |
| --- | --- |
| `app.py` | Web layer: webhook, landing page, dashboard, privacy page |
| `bot.py` | Conversation: onboarding, menus, tasks, answers, voice, feedback |
| `prompts.py` | Everything the assistant says or is told, including the guided tasks |
| `memory.py` | Profile learning and rolling summaries |
| `speech.py` | Voice notes to text (GhanaNLP Khaya + ffmpeg) |
| `safety.py`, `evals/` | Emergency fast-path and its red-flag test set |
| `redact.py`, `register.py`, `report_formats/` | Register photos to a draft monthly return, names hidden first |
| `followups.py`, `schedules/` | Immunisation, antenatal and postnatal follow-ups by register number, Monday lists |
| `stock.py`, `stock_lists/` | Shelf counts to a draft requisition |
| `alerts.py`, `diseases/` | Immediately notifiable disease alerts |
| `i18n/` | Interface text in Twi (`tw.json`) and Ewe (`ee.json`) |
| `Dockerfile`, `fly.toml`, `render.yaml` | Hosting |
| `flows/`, `flows_setup.py` | Native WhatsApp forms and the script that registers them |
| `whatsapp.py` | Meta API client: buttons, lists, reactions, retries |
| `store.py` | SQLite persistence |
| `stats.py`, `dashboard.py`, `landing.py` | Metrics and pages |

## Troubleshooting

| What you see | Fix |
| --- | --- |
| "META ACCESS TOKEN REJECTED" | Use a system user token that never expires (Business settings, then System users) |
| "Your .env is missing ..." | `open -e .env`, add the value, save, then **quit TextEdit** |
| "Voice notes: off (...)" | Follow the message: add `KHAYA_API_KEY`, or `brew install ffmpeg` |
| No welcome image | Set `NGROK_DOMAIN` in `.env` |
| "WhatsApp could not deliver a reply: code ..." | The recipient isn't on the test number's list, or 24 hours passed since their last message |

## Limits (be upfront about these)

- Meta's test number can only message up to five registered numbers. A real pilot needs a verified business and its own number.
- Menus are translated into Twi and Ewe. The Ewe was drafted by Claude and has not been reviewed by a native speaker yet; please review `i18n/ee.json` before anyone in the Volta Region uses it.
- Register photos: OCR is weaker on handwriting, so name hiding needs the district's `redact_bands`, and the return layout is a placeholder until the district's form is added.
- The emergency fast-path sends the card on English phrases only. Draft Twi and Ewe phrases in `safety_local.json` run in shadow mode (counted, never acted on) until native-speaking clinicians review them.
- Speech recognition quality in Twi and Ewe hasn't been measured yet. That belongs in the language check.
- A real deployment in Ghana would need a review under Ghana's Data Protection Act.

## Rich features

- **➕ More** under every answer: 🔊 listen as a voice note (Twi, Ewe or English, via GhanaNLP Khaya),
  📄 printable A4 PDF (quizzes get a separate answer-key page), 🖼️ image card to forward to a group,
  ⭐ save to My library, 🎯 turn the answer into a tap quiz.
- **🎯 Quiz game / Test yourself**: five tap-to-answer questions in the person's language, instant
  feedback, a score card image and a review of missed questions.
- **📚 My library**: health talks, home visit guides and register summaries are saved automatically; anything else on request.
- **📍 Nearest facility**: share a location, get the three closest mapped clinics and hospitals
  as map pins (OpenStreetMap). The location is used for that search only and never stored.
- **🔊 Voice replies**: Settings, or type VOICE ON, to get every answer as a voice note too.
- **Interface language**: menus and buttons follow the chosen language. Twi text lives in `i18n/tw.json`
  and Ewe in `i18n/ee.json`, for native-speaker review; anything missing falls back to English.
- **📊 Monthly return**: photograph each register or tally-sheet page; names are painted over first,
  Claude reads only the counts, code adds the pages up, checks them and compares with last month,
  then sends the draft as text and a printable PDF. The nurse checks and submits it herself.

## Companion app (iPhone, Android, desktop)

Open `https://YOUR-NGROK-DOMAIN/app/`. People sign in with their WhatsApp number and a 6-digit
code that Akwaaba sends on WhatsApp (or they send LOGIN to the WhatsApp number). It's the same
account: history, library, language and settings are shared with the chat.

- **Install on iPhone**: open the link in Safari, tap Share, then Add to Home Screen.
  It opens full screen with its own icon. Android/Chrome offers "Install app".
- Streaming answers, voice input, photos, a danger-sign checklist (same fields
  as the WhatsApp form), Listen / PDF / image card / Save on every answer, tap quizzes with a score
  ring, My library (readable offline), nearest facility with a map, light and dark themes, Twi interface.
- Files: `webapp.py` (API under `/api/...`), `static/app/` (the app; no build step).
- The app runs from the same server as the WhatsApp assistant, so it's available while that server is running.
