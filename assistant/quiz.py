"""Tap-to-answer quizzes played inside WhatsApp.

Claude writes 5 multiple-choice questions as JSON in the person's language; the
app then asks them one at a time with three answer buttons, marks each tap, and
ends with a score card. Health workers use it to refresh their training, or read it
aloud to check a group understood a health talk.
"""

import json
import logging
import re

log = logging.getLogger("akwaaba")

N_QUESTIONS = 5

PROMPT = """Write a {n}-question multiple-choice quiz for a {who} in Ghana.
Topic: {topic}
{level}Difficulty: {difficulty}.
{source}
Write EVERYTHING (questions, options, explanations) in {language}{lang_note}.

Rules:
- Exactly 3 options per question, only one correct. Plausible wrong options. Vary which position is correct.
- Each option at most 20 characters. Each question at most 200 characters. Use Ghanaian everyday contexts.
- "why": one short sentence (max 140 characters) explaining the correct answer.
- {safety}

Reply with ONLY this JSON, no other text:
{{"title": "short quiz title", "questions": [{{"q": "...", "options": ["...", "...", "..."], "answer": 0, "why": "..."}}]}}"""

HEALTH_SAFETY = ("Follow Ghana Health Service and WHO guidance. Never include medicine doses. "
                 "Danger signs always mean: go to a health facility now.")


def build_prompt(mode, language, topic, level="", difficulty="mixed", source=""):
    return PROMPT.format(
        n=N_QUESTIONS, who="community health worker",
        topic=topic[:200], level=f"Class level: {level}.\n" if level else "", difficulty=difficulty,
        source=f"Base the questions on this material:\n\"\"\"\n{source[:2500]}\n\"\"\"" if source else "",
        language=language,
        lang_note=" (everyday spoken style; keep common English technical terms)" if language != "English" else "",
        safety=HEALTH_SAFETY)


def _salvage(text: str) -> dict | None:
    """Rescue the complete questions from a reply that was cut off or slightly malformed."""
    title = re.search(r'"title"\s*:\s*"([^"]*)"', text)
    items = []
    for m in re.finditer(r'\{\s*"q"\s*:.*?"why"\s*:\s*"(?:[^"\\]|\\.)*"\s*\}', text, re.S):
        try:
            items.append(json.loads(m.group(0)))
        except json.JSONDecodeError:
            continue
    return {"title": title.group(1) if title else "Quiz", "questions": items} if items else None


def parse(text: str) -> dict | None:
    """Validated quiz, or None if the model's reply can't be used."""
    text = re.sub(r"```(?:json)?", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    data = None
    if m:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        data = _salvage(text)
    if not data:
        return None
    qs = []
    for item in data.get("questions") or []:
        try:
            q = str(item["q"]).strip()
            opts = [str(o).strip() for o in item["options"]][:3]
            ans = int(item["answer"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(opts) != 3 or not q or not all(opts) or not 0 <= ans < 3 or len({o.lower() for o in opts}) < 3:
            continue
        qs.append({"q": q[:300], "options": [o[:60] for o in opts], "answer": ans,
                   "why": str(item.get("why") or "").strip()[:200]})
    if len(qs) < 3:
        return None
    return {"title": str(data.get("title") or "Quiz").strip()[:60], "questions": qs[:N_QUESTIONS]}


def generate(claude, model, mode, language, topic, level="", difficulty="mixed", source="") -> dict | None:
    for attempt in range(2):
        # Ghanaian languages use many more tokens than English, so leave plenty of room.
        resp = claude.messages.create(model=model, max_tokens=8000, messages=[
            {"role": "user", "content": build_prompt(mode, language, topic, level, difficulty, source)}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        quiz = parse(text)
        if quiz:
            return quiz
        log.warning("Quiz JSON unusable (attempt %d, stop=%s): %s", attempt + 1,
                    getattr(resp, "stop_reason", "?"), text[:300].replace("\n", " "))
    return None


LETTERS = "ABC"


def buttons_for(index: int, q: dict) -> list[tuple[str, str]]:
    """Option text on the buttons when it fits, otherwise A / B / C."""
    fits = all(len(f"{LETTERS[i]}. {o}") <= 20 for i, o in enumerate(q["options"]))
    return [(f"qa:{index}:{i}", f"{LETTERS[i]}. {o}" if fits else LETTERS[i]) for i, o in enumerate(q["options"])]


def question_text(index: int, total: int, q: dict, label: str) -> str:
    opts = "\n".join(f"*{LETTERS[i]}.* {o}" for i, o in enumerate(q["options"]))
    bar = "\U0001F7E9" * index + "⬜" * (total - index)
    return f"_{label} {index + 1}/{total}_  {bar}\n\n*{q['q']}*\n\n{opts}"
