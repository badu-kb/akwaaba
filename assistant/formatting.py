"""Convert the model's Markdown into WhatsApp's own formatting.

WhatsApp uses *bold*, _italic_, ~strike~ and plain bullet lines. Markdown
headers, **double asterisks**, tables and code fences show up as clutter.
"""

import re


def to_whatsapp(text: str) -> str:
    t = text.replace("\r\n", "\n").strip()
    t = re.sub(r"```[a-zA-Z]*\n?", "", t)                       # code fences
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", r"*\1*", t)  # headers -> bold line
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", t)                     # **bold** -> *bold*
    t = re.sub(r"__(.+?)__", r"_\1_", t)                         # __x__ -> _x_
    t = re.sub(r"\*\*+", "*", t)                                 # stray doubles
    t = re.sub(r"(?m)^(\s*)[-*]\s+", r"\1• ", t)            # bullets -> •
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", t)  # [text](url)
    t = re.sub(r"(?m)^\s*\|?\s*:?-{3,}.*$\n?", "", t)            # table separator rows
    t = re.sub(r"(?m)^\s*\|(.+)\|\s*$", lambda m: " – ".join(
        c.strip() for c in m.group(1).split("|") if c.strip()), t)  # table rows -> a – b
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def trim_to_sentence(text: str) -> str:
    """Cut an over-long reply back to its last complete paragraph or sentence."""
    cut = max(text.rfind("\n"), text.rfind(". "), text.rfind("! "), text.rfind("? "))
    return text[: cut + 1].rstrip() if cut > len(text) // 3 else text.rstrip()
