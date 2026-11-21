"""Turns Akwaaba's weekly surveillance counts into a DHIS2 data value set, the
format DHIS2's Web API imports (POST /api/dataValueSets). Ghana's DHIMS2 is built
on DHIS2.

This only builds the file. It never sends anything: a person reviews it and
imports it, or an integration approved by the health service does. The ids in
the mapping file are placeholders until the district supplies its own.

    python dhis2_export.py --demo --mapping dhis2_mapping.example.json
"""

import argparse
import datetime as dt
import json
import re
import sys

import surveillance

_UID = re.compile(r"^[A-Za-z][A-Za-z0-9]{10}$")          # DHIS2 ids: a letter, then ten letters or digits


def period(day: dt.date) -> str:
    """DHIS2 weekly period for the week containing `day`, for example 2026W46."""
    year, week = surveillance.epi_week(day)
    return f"{year}W{week}"


def check_mapping(mapping: dict) -> list[str]:
    """Problems that would make DHIS2 reject the file, in plain words. Empty means fine."""
    problems = []
    for field in ("dataSet", "orgUnit"):
        if not _UID.match(str(mapping.get(field, ""))):
            problems.append(f"{field} must be an 11-character DHIS2 id")
    for disease, de in (mapping.get("dataElements") or {}).items():
        if not _UID.match(str(de)):
            problems.append(f"data element for {disease} must be an 11-character DHIS2 id")
    if not mapping.get("dataElements"):
        problems.append("no data elements are mapped")
    return problems


def payload(signals: list[dict], mapping: dict, today: dt.date | None = None, zero_report: bool = True) -> dict:
    """One week's suspected cases as a DHIS2 data value set. Discarded signals are left out.
    With zero_report, mapped conditions with no cases are sent as 0, which is how IDSR
    tells 'nothing happened' apart from 'nobody reported'."""
    today = today or dt.date.today()
    problems = check_mapping(mapping)
    if problems:
        raise ValueError("; ".join(problems))
    start = surveillance.week_start(today)
    counts: dict = {}
    for s in signals:
        day = dt.date.fromtimestamp(s["ts"])
        if s.get("status") != "discarded" and start <= day <= start + dt.timedelta(days=6):
            counts[s["disease"]] = counts.get(s["disease"], 0) + int(s["count"])
    values, unmapped = [], sorted(set(counts) - set(mapping["dataElements"]))
    for disease, de in sorted(mapping["dataElements"].items()):
        n = counts.get(disease, 0)
        if n or zero_report:
            row = {"dataElement": de, "value": str(n)}
            if mapping.get("categoryOptionCombo"):
                row["categoryOptionCombo"] = mapping["categoryOptionCombo"]
            values.append(row)
    out = {"dataSet": mapping["dataSet"], "period": period(today), "orgUnit": mapping["orgUnit"],
           "completeDate": today.isoformat(), "dataValues": values}
    if unmapped:
        out["_notMapped"] = unmapped      # reported, but the district hasn't given an id for these yet
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build a DHIS2 data value set from Akwaaba's weekly signals.")
    ap.add_argument("--mapping", required=True, help="JSON file with dataSet, orgUnit and dataElements ids")
    ap.add_argument("--demo", action="store_true", help="use the invented sample district")
    ap.add_argument("--db", help="path to the Akwaaba database (omit with --demo)")
    args = ap.parse_args(argv)
    mapping = json.load(open(args.mapping, encoding="utf-8"))
    if args.demo:
        signals = surveillance.sample()
    elif args.db:
        from store import Store
        signals = Store(args.db).signals()
    else:
        ap.error("give --demo or --db")
    try:
        json.dump(payload(signals, mapping), sys.stdout, indent=2)
    except ValueError as e:
        print(f"Mapping problem: {e}", file=sys.stderr)
        return 2
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
