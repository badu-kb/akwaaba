"""The clinician evaluation pipeline: run (dry), workbook, score."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

import clinician_eval as ce  # noqa: E402


def test_set_is_well_formed():
    cases = ce.load_cases()
    assert len(cases) >= 90
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["reply"] in ce.LANG_NAME and c["urgency"] in ("urgent", "soon", "routine")
        assert c["text"].strip() and c["should"]


def test_pipeline_end_to_end(tmp_path):
    run_path = tmp_path / "run.jsonl"
    ce.main(["run", "--dry-run", "--limit", "6", "--out", str(run_path)])
    rows = ce.read_run(run_path)
    assert len(rows) == 6 and all(r["answer"].startswith("(dry run)") for r in rows)
    assert rows[0]["emergency_card"] is True  # Q001 is a child having a fit

    paths = ce.main(["workbook", str(run_path), "--raters", "Dr A,Dr B", "--out-dir", str(tmp_path / "wb")])
    from openpyxl import load_workbook
    for n, p in enumerate(paths):
        wb = load_workbook(p)
        ws = wb["Score"]
        for r in range(2, ws.max_row + 1):
            vals = ["Yes", "", "Yes", "Right" if n == 0 or r != 3 else "Too low", 4, 3, "N/A", "Yes",
                    "Yes" if r != 3 else "No", "note"]
            for j, v in enumerate(vals, start=8):
                ws.cell(row=r, column=j, value=v)
        wb.save(p)
    out = ce.main(["score", str(run_path), *map(str, paths), "--out", str(tmp_path / "res.md")])
    text = Path(out).read_text()
    assert "| Dr A | 6 | 5 (83%)" in text
    assert "Urgency: agreed on 83% of 6 rows" in text
    assert "**Q002**" in text  # the row Dr B flagged
