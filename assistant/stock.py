"""Stock check: shelf counts to a draft requisition.

Research behind it: a 2021 to 2022 Ghana survey found inventory tools at 50 to 95%
of sites but often not updated, and only 40% of commodity managers trained in
logistics. Stock-outs came mostly from the supply point, so early, clear
requests matter.

The nurse types counts ("RDT 40, AL 12, ORS 60"). Code reads them, compares
with the district's minimum and maximum levels, works out how much to request
(up to the maximum) and drafts a requisition note. Nothing is sent anywhere.
"""

import json
import re
from pathlib import Path

LIST = json.loads((Path(__file__).resolve().parent / "stock_lists" / "chps_core.json").read_text(encoding="utf-8"))
ITEMS = {i["key"]: i for i in LIST["items"]}
_ALIASES = sorted(((a, i["key"]) for i in LIST["items"] for a in i["aliases"]), key=lambda p: -len(p[0]))


def parse_counts(text: str) -> tuple[dict, list[str]]:
    """'RDT 40, AL 12, ORS: 60' -> ({'rdt': 40, 'al': 12, 'ors': 60}, unknown words)."""
    counts, unknown = {}, []
    for part in re.split(r"[,;\n]+", text):
        part = part.strip().lower()
        if not part:
            continue
        m = re.search(r"(\d[\d ]*)", part)
        if not m:
            continue
        n = int(m.group(1).replace(" ", ""))
        name = (part[:m.start()] + " " + part[m.end():]).strip(" :=-.")
        key = next((k for a, k in _ALIASES if re.search(rf"(^|\b){re.escape(a)}(\b|$)", name)), None)
        if key and n <= 1_000_000:
            counts[key] = n
        elif name:
            unknown.append(name[:30])
    return counts, unknown


def check(counts: dict, previous: dict | None = None, monthly_rdt_use: int | None = None) -> dict:
    low, ok, gone = [], [], []
    for key, n in counts.items():
        it = ITEMS[key]
        row = {"key": key, "label": it["label"], "unit": it["unit"], "count": n, "min": it["min"], "max": it["max"],
               "order": max(0, it["max"] - n), "before": (previous or {}).get(key)}
        (gone if n == 0 else low if n < it["min"] else ok).append(row)
    months_rdt = round(counts["rdt"] / monthly_rdt_use, 1) if "rdt" in counts and monthly_rdt_use else None
    missing = [i["label"] for k, i in ITEMS.items() if k not in counts]
    return {"stocked_out": gone, "low": low, "ok": ok, "missing": missing, "months_rdt": months_rdt,
            "rdt_use": monthly_rdt_use}


def _line(r):
    before = f" (was {r['before']})" if r.get("before") is not None and r["before"] != r["count"] else ""
    return f"• {r['label']}: *{r['count']}* {r['unit']}{before}"


def as_text(res: dict) -> str:
    out = ["*\U0001F4E6 Stock check*", ""]
    if res["stocked_out"]:
        out += ["\U0001F534 *Stocked out*", *[_line(r) for r in res["stocked_out"]], ""]
    if res["low"]:
        out += ["\U0001F7E0 *Below minimum*", *[_line(r) + f" · min {r['min']}" for r in res["low"]], ""]
    if res["ok"]:
        out += ["\U0001F7E2 *OK*", *[_line(r) for r in res["ok"]], ""]
    if res["months_rdt"] is not None:
        out.append(f"_At last month's use ({res['rdt_use']} tests), RDTs last about {res['months_rdt']} months._")
    if res["missing"]:
        out.append("_Not counted: " + ", ".join(res["missing"][:6]) + ("…" if len(res["missing"]) > 6 else "") + "_")
    if LIST.get("placeholder"):
        out.append("_Minimum levels are placeholders until your district's are added._")
    return "\n".join(out).strip()


def requisition(res: dict, facility: str = "") -> str | None:
    rows = res["stocked_out"] + res["low"]
    if not rows:
        return None
    lines = ["*Draft requisition*", f"To: Sub-district / district stores{' from ' + facility if facility else ''}", "",
             "Please supply the following, to bring stock up to the maximum level:"]
    lines += [f"• {r['label']}: *{r['order']}* {r['unit']} (on hand {r['count']})" for r in rows]
    if res["stocked_out"]:
        lines += ["", "Stocked out now: " + ", ".join(r["label"] for r in res["stocked_out"]) + "."]
    lines += ["", "_Check the quantities against your stock card before sending._"]
    return "\n".join(lines)
