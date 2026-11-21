"""The district surveillance desk page, served at /district. It shows the week's
signals, the thresholds that have been reached, eight weeks of counts and the
drafted bulletin. Everything on it comes from surveillance.py; nothing is
diagnosed here and nothing leaves the district without a person sending it."""

import datetime as dt
import html

import surveillance as sv

E = html.escape
LEVELS = {"action": ("Outbreak threshold", "thr"), "verify": ("Verify within 48 hours", "imm"),
          "alert": ("Alert threshold", "alr"), "watch": ("Watch", "wat")}
STATUS = {"new": "Awaiting verification", "verified": "Verified", "discarded": "Discarded"}

CSS = """
@font-face{font-family:Bricolage;src:url(/app/fonts/bricolage-600.woff2) format("woff2");font-weight:600 800;font-display:swap}
@font-face{font-family:Hanken;src:url(/app/fonts/hanken-400.woff2) format("woff2");font-weight:400;font-display:swap}
@font-face{font-family:Hanken;src:url(/app/fonts/hanken-600.woff2) format("woff2");font-weight:600 700;font-display:swap}
@font-face{font-family:Mono;src:url(/app/fonts/mono-500.woff2) format("woff2");font-weight:500;font-display:swap}
:root{--paper:#F6F3EC;--card:#fff;--ink:#17160F;--ink2:#5E5A50;--rule:#E3DED2;--green:#0A7B3E;--gold:#B87A00;--red:#B3261E;
--thr:#FBEAE8;--imm:#FFF5DA;--wat:#E8F0F6;--soft:#EEF4EF}
@media (prefers-color-scheme:dark){:root{--paper:#12120F;--card:#1B1B17;--ink:#F3F1EA;--ink2:#A8A397;--rule:#302E28;--green:#4CC38A;--gold:#E2B24A;--red:#F2857C;
--thr:#3A1F1C;--imm:#3A3018;--wat:#1B2A36;--soft:#17241C}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 Hanken,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px 56px}
header{padding:28px 0 20px;border-bottom:2px solid var(--ink);display:flex;flex-wrap:wrap;gap:8px 24px;align-items:flex-end;justify-content:space-between}
.k{font:500 11px/1 Mono,ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--ink2)}
h1{font:700 clamp(26px,4vw,38px)/1.08 Bricolage,Hanken,sans-serif;letter-spacing:-.03em;margin:8px 0 0}
h2{font:600 20px/1.2 Bricolage,Hanken,sans-serif;letter-spacing:-.02em;margin:0 0 4px}
.sub{color:var(--ink2);font-size:14px;margin:0 0 14px}
.demo{background:var(--imm);border:1px solid var(--gold);color:var(--ink);border-radius:8px;padding:10px 14px;margin:18px 0 0;font-size:14px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:0;border-bottom:1px solid var(--rule);margin-bottom:28px}
.tiles div{padding:20px 20px 20px 0;border-left:1px solid var(--rule);padding-left:20px}.tiles div:first-child{border-left:0;padding-left:0}
.tiles b{display:block;font:700 34px/1 Bricolage,Hanken,sans-serif;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.tiles span{display:block;margin-top:8px;color:var(--ink2);font-size:13.5px;line-height:1.35}
section{margin-top:34px}
.flag{display:grid;grid-template-columns:170px 1fr;gap:6px 18px;padding:14px 16px;border-radius:10px;margin-top:10px;border:1px solid var(--rule)}
.flag.thr{background:var(--thr)}.flag.imm{background:var(--imm)}.flag.wat{background:var(--wat)}.flag.alr{background:var(--soft)}.flag.late{background:var(--thr)}
.flag .lv{font:500 11px/1.3 Mono,ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase}
.flag.thr .lv,.flag.late .lv{color:var(--red)}.flag.imm .lv{color:var(--gold)}.flag.alr .lv{color:var(--green)}
.flag b{display:block}.flag p{margin:2px 0 0;color:var(--ink2);font-size:14.5px}
.none{background:var(--soft);border-radius:10px;padding:14px 16px;margin-top:10px}
.two{display:grid;grid-template-columns:1.05fr 1fr;gap:34px}
table{width:100%;border-collapse:collapse;font-size:14.5px}
th{font:500 10.5px/1.3 Mono,ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--ink2);text-align:left;padding:8px 10px 8px 0;border-bottom:1.5px solid var(--ink)}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--rule);vertical-align:middle}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.wk{display:flex;align-items:flex-end;gap:4px;height:44px}
.wk i{flex:1;background:var(--green);border-radius:2px 2px 0 0;min-height:2px;opacity:.55}.wk i.z{background:var(--rule);opacity:1}.wk i.now{opacity:1}
.st{display:inline-block;font:500 10.5px/1 Mono,ui-monospace,monospace;letter-spacing:.06em;text-transform:uppercase;padding:5px 8px;border-radius:99px;border:1px solid var(--rule);white-space:nowrap}
.st.new{background:var(--imm);border-color:transparent}.st.verified{background:var(--soft);border-color:transparent;color:var(--green)}.st.discarded{color:var(--ink2);text-decoration:line-through}
tr.discarded td{color:var(--ink2)}
form{display:inline}button{font:600 12.5px Hanken,sans-serif;border:1px solid var(--rule);background:var(--card);color:var(--ink);border-radius:99px;padding:5px 10px;cursor:pointer;margin-left:4px}
button:hover{border-color:var(--ink)}
pre{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:18px;white-space:pre-wrap;font:500 13px/1.6 Mono,ui-monospace,monospace;margin:0;overflow-wrap:anywhere}
.foot{margin-top:34px;padding-top:16px;border-top:1px solid var(--rule);color:var(--ink2);font-size:13px;line-height:1.6;max-width:86ch}
.scroll{overflow-x:auto}
@media (max-width:820px){.tiles{grid-template-columns:1fr 1fr}.tiles div:nth-child(3){border-left:0;padding-left:0}.tiles div{border-top:1px solid var(--rule)}.tiles div:nth-child(-n+2){border-top:0}
.two{grid-template-columns:1fr}.flag{grid-template-columns:1fr}}
"""


def parts(signals: list[dict], district: str = "", today: dt.date | None = None, demo: bool = False,
          key: str = "", zones_expected: int | None = None, names: dict | None = None, now: int | None = None) -> dict:
    """The desk as separate pieces of HTML, so a page can be assembled or updated piece by piece."""
    today = today or dt.date.today()
    year, week = sv.epi_week(today)
    start = sv.week_start(today)
    signals = [s for s in signals if dt.date.fromtimestamp(s["ts"]) <= today]
    live = [s for s in signals if s.get("status") != "discarded"]
    this_week = [s for s in live if start <= dt.date.fromtimestamp(s["ts"])]
    fl = sv.flags(signals, today, now)
    wk = sv.weekly(signals, today)
    zones = {s["zone"] for s in this_week}

    tiles = [(len(this_week), "signals received this week"),
             (sum(s["count"] for s in this_week), "people in those reports"),
             (sum(1 for s in this_week if s["status"] == "new"), "signals awaiting verification"),
             (f"{len(zones)}{' of ' + str(zones_expected) if zones_expected else ''}", "zones heard from this week")]
    tile_html = "".join(f"<div><b>{E(str(v))}</b><span>{E(l)}</span></div>" for v, l in tiles)

    if fl:
        flag_html = ""
        for f in fl:
            label, cls = LEVELS[f["level"]]
            if f.get("overdue"):
                label, cls = "Overdue for verification", "late"
            flag_html += (f'<div class="flag {cls}"><span class="lv">{label}</span>'
                          f'<div><b>{E(sv.NAMES.get(f["disease"], f["disease"]).capitalize())} · {E(f["zone"])} · {f["cases"]} '
                          f'{"person" if f["cases"] == 1 else "people"}</b><p>{E(f["say"])}</p></div></div>')
    else:
        flag_html = '<div class="none">Nothing needs attention right now.</div>'

    peak = max((max(v) for v in wk["cases"].values()), default=0) or 1
    rows = ""
    for d, counts in sorted(wk["cases"].items(), key=lambda kv: (-kv[1][-1], -sum(kv[1]), kv[0])):
        bars = "".join(f'<i class="{"z" if not n else ""}{" now" if i == len(counts) - 1 else ""}" '
                       f'style="height:{max(4, round(100 * n / peak)) if n else 4}%" title="Week {wk["weeks"][i][1]}: {n}"></i>'
                       for i, n in enumerate(counts))
        rows += (f'<tr><td>{E(sv.NAMES.get(d, d).capitalize())}</td><td><div class="wk" role="img" '
                 f'aria-label="Weekly counts, oldest first: {", ".join(map(str, counts))}">{bars}</div></td>'
                 f'<td class="n">{counts[-2] if len(counts) > 1 else 0}</td><td class="n"><b>{counts[-1]}</b></td></tr>')
    trend = (f'<div class="scroll"><table><tr><th>Condition</th><th>Weeks {wk["weeks"][0][1]} to {wk["weeks"][-1][1]}</th>'
             f'<th class="n">Last week</th><th class="n">This week</th></tr>{rows}</table></div>'
             if rows else '<div class="none">No signals in the last eight weeks.</div>')

    ll = ""
    for s in signals[:60]:
        day = dt.date.fromtimestamp(s["ts"])
        who = (names or {}).get(s.get("user"), s.get("reporter", "Health worker"))
        act = ""
        if not demo and key:
            act = "".join(
                f'<form method="post" action="/district/signal"><input type="hidden" name="key" value="{E(key)}">'
                f'<input type="hidden" name="id" value="{int(s["id"])}"><input type="hidden" name="status" value="{st}">'
                f'<button>{lab}</button></form>'
                for st, lab in (("verified", "Verify"), ("discarded", "Discard")) if st != s["status"])
        ll += (f'<tr class="{E(s["status"])}"><td>{day.day} {day.strftime("%b")}</td><td>{E(sv.NAMES.get(s["disease"], s["disease"]).capitalize())}</td>'
               f'<td class="n">{int(s["count"])}</td><td>{E(s["zone"])}</td><td>{E(str(who))}</td>'
               f'<td><span class="st {E(s["status"])}">{STATUS.get(s["status"], s["status"])}</span>{act}</td></tr>')
    line_list = (f'<div class="scroll"><table><tr><th>Date</th><th>Suspected</th><th class="n">People</th><th>Zone</th><th>Reported by</th><th>Status</th></tr>{ll}</table></div>'
                 if ll else '<div class="none">No signals yet.</div>')
    return {"title": f"{district or 'District'} · Week {week}, {year}",
            "dates": f"{start.day} {start.strftime('%b')} to {today.day} {today.strftime('%b %Y')}",
            "tiles": tile_html, "flags": flag_html, "trend": trend, "lines": line_list,
            "bulletin": E(sv.bulletin(signals, district, today, zones_expected))}


SOURCE_NOTE = ("These are signals from community health workers and volunteers, drafted with Akwaaba. They are suspected cases "
               "and events, to be verified by the district, and they are not confirmed diagnoses. Thresholds are taken from "
               "Ghana's Technical Guidelines for Integrated Disease Surveillance and Response (Ministry of Health, 2002), the "
               "edition we were able to read. They must be re-checked against the 2022 edition before real use.")


def render(signals: list[dict], district: str = "", today: dt.date | None = None, demo: bool = False,
           key: str = "", zones_expected: int | None = None, names: dict | None = None) -> str:
    p = parts(signals, district, today, demo, key, zones_expected, names)
    note = ('<div class="demo"><b>Demonstration.</b> Every signal on this page is invented, for a fictional district. '
            'It shows how the desk works and is not real surveillance data.</div>') if demo else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>District surveillance desk · Akwaaba</title><style>{CSS}</style></head><body><div class="wrap">
<header><div><span class="k">Akwaaba · District surveillance desk · Prototype</span>
<h1>{E(p["title"])}</h1></div>
<span class="k">{p["dates"]}</span></header>
{note}
<div class="tiles">{p["tiles"]}</div>
<section><h2>Needs attention</h2><p class="sub">Each flag names the rule behind it. The district can read and change the rules.</p>{p["flags"]}</section>
<section class="two"><div><h2>Eight weeks at a glance</h2><p class="sub">Suspected cases signalled by health workers, by week.</p>{p["trend"]}</div>
<div><h2>This week's bulletin, drafted</h2><p class="sub">Built from the counts. Check it, add the actions taken, then send it yourself.</p><pre>{p["bulletin"]}</pre></div></section>
<section><h2>Line list</h2><p class="sub">One row per signal: a suspected condition, a number of people and a zone. No names are stored.</p>{p["lines"]}</section>
<p class="foot">{SOURCE_NOTE}</p>
</div></body></html>"""
