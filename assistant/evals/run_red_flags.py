"""Scores the emergency fast-path against evals/red_flags.json.

    python3 evals/run_red_flags.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import safety  # noqa: E402

data = json.loads((Path(__file__).with_name("red_flags.json")).read_text(encoding="utf-8"))
total, hit, missed = Counter(), Counter(), []
for case in data["urgent"]:
    total[case["group"]] += 1
    if safety.looks_like_emergency(case["text"]):
        hit[case["group"]] += 1
    else:
        missed.append(case["text"])
alarms = [t for t in data["routine"] if safety.looks_like_emergency(t)]
for g in total:
    print(f"{g:9} sensitivity {hit[g]}/{total[g]}")
n = sum(total.values())
print(f"overall   sensitivity {n - len(missed)}/{n}  ({(n - len(missed)) / n:.0%})")
print(f"routine   specificity {len(data['routine']) - len(alarms)}/{len(data['routine'])}")
for t in missed:
    print("  MISSED:", t)
for t in alarms:
    print("  FALSE ALARM:", t)
sys.exit(1 if missed else 0)
