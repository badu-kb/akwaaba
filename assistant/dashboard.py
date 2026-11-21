"""Live impact dashboard served at /dashboard. Built only from the anonymised
usage log: no phone numbers, no message text."""

import datetime as dt
import html
from collections import Counter

import prompts

ROLE_LABELS = {"health": "Health workers", "teacher": "Early test accounts"}
TASK_LABELS = {k: v["title"].split(" ", 1)[1] for k, v in prompts.TASKS.items()}
TASK_LABELS["free"] = "Free question"


def _bars(counts: dict, labels: dict | None = None, color: str = "var(--green)") -> str:
    if not counts:
        return '<p class="muted">No data yet</p>'
    top = max(counts.values()) or 1
    rows = []
    for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        name = html.escape((labels or {}).get(key, str(key)))
        rows.append(f'<div class="row"><span class="lbl">{name}</span><span class="track">'
                    f'<span class="fill" style="width:{round(100 * n / top)}%;background:{color}"></span>'
                    f'</span><span class="num">{n}</span></div>')
    return "\n".join(rows)


def _daily(events: list[dict], days: int = 14) -> str:
    today = dt.date.today()
    per_day = Counter(dt.date.fromtimestamp(e["ts"]) for e in events if e.get("event") == "answer")
    span = [today - dt.timedelta(days=i) for i in range(days - 1, -1, -1)]
    peak = max((per_day[d] for d in span), default=0) or 1
    return "".join(
        f'<div class="col" title="{d:%a %d %b}: {per_day[d]} answers"><span class="cnum">{per_day[d] or ""}</span>'
        f'<span class="bar" style="height:{max(3, round(130 * per_day[d] / peak)) if per_day[d] else 3}px"></span>'
        f'<span class="day">{d:%d}</span></div>' for d in span)


def _ring(pct) -> str:
    if pct is None:
        return '<div class="ring empty"><span>&ndash;</span></div>'
    return (f'<div class="ring" style="--p:{pct}"><span>{pct}%</span></div>')


def render(s: dict, events: list[dict], languages: dict) -> str:
    def tile(label, value, note="", accent=""):
        v = "&ndash;" if value in (None, "") else html.escape(str(value))
        n = f'<div class="note">{note}</div>' if note else ""
        return f'<div class="tile {accent}"><div class="tl">{label}</div><div class="tv">{v}</div>{n}</div>'

    lat = f'{s["avg_latency_s"]}s' if s.get("avg_latency_s") is not None else None
    ret = f'{s["returning_pct"]}% came back' if s.get("returning_pct") is not None else "came back another day"
    updated = dt.datetime.now().strftime("%d %b %Y, %H:%M")
    sat_note = f'{s.get("positive_ratings", 0)} of {s.get("ratings", 0)} ratings positive' if s.get("ratings") else "No ratings yet"

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="60">
<title>Akwaaba impact</title>

<style>
:root{{--bg:#f5f3ee;--card:#fff;--ink:#17160f;--muted:#6d695f;--line:#e7e2d6;--red:#ce1126;--gold:#e9a400;--green:#006b3f;--soft:#eef6f1}}
@media (prefers-color-scheme:dark){{:root{{--bg:#11110e;--card:#1b1b17;--ink:#f3f1ea;--muted:#a39f93;--line:#2f2d27;--soft:#16261d}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 Inter,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1040px;margin:0 auto;padding:0 16px 48px}}
.hero{{background:linear-gradient(135deg,#0d3b26,#006b3f 55%,#0b4f30);color:#fff;border-radius:0 0 22px 22px;padding:26px 24px 28px;margin:0 -16px 18px;position:relative;overflow:hidden}}
.hero:after{{content:"";position:absolute;right:-60px;top:-60px;width:240px;height:240px;border-radius:50%;background:radial-gradient(circle,rgba(233,164,0,.35),transparent 65%)}}
.kente{{height:5px;background:repeating-linear-gradient(90deg,var(--red) 0 22px,var(--gold) 22px 44px,#2fbf71 44px 66px);border-radius:3px;margin-bottom:18px}}
.hero h1{{font-size:28px;letter-spacing:-.02em;margin:0}}.hero p{{margin:4px 0 0;opacity:.85}}
.pill{{display:inline-block;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.25);padding:3px 10px;border-radius:99px;font-size:12px;margin-top:12px}}
.dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:#5ef29b;margin-right:6px;box-shadow:0 0 0 3px rgba(94,242,155,.25)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:12px}}
.tile,.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}}
.tile.hl{{background:var(--soft);border-color:transparent}}
.tl{{color:var(--muted);font-size:13px}}.tv{{font-size:30px;font-weight:750;letter-spacing:-.02em;margin-top:2px;font-variant-numeric:tabular-nums}}.note{{color:var(--muted);font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-bottom:12px}}
h2{{font-size:14px;font-weight:650;margin:0 0 12px}}
.row{{display:grid;grid-template-columns:160px 1fr 44px;align-items:center;gap:10px;margin:9px 0}}
.lbl{{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.num{{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}
.track{{height:10px;background:var(--line);border-radius:5px;overflow:hidden}}.fill{{display:block;height:100%;border-radius:5px}}
.chart{{display:flex;align-items:flex-end;gap:6px;height:180px}}
.col{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;min-width:0}}
.bar{{width:100%;max-width:30px;background:linear-gradient(180deg,var(--gold),#d58a00);border-radius:5px 5px 2px 2px}}
.cnum{{font-size:11px;color:var(--muted);height:15px}}.day{{font-size:11px;color:var(--muted);margin-top:5px}}
.sat{{display:flex;align-items:center;gap:18px}}
.ring{{--p:0;width:108px;height:108px;border-radius:50%;flex:none;display:grid;place-items:center;background:conic-gradient(var(--green) calc(var(--p)*1%),var(--line) 0)}}
.ring span{{width:82px;height:82px;border-radius:50%;background:var(--card);display:grid;place-items:center;font-size:22px;font-weight:750}}
.ring.empty{{background:var(--line)}}
.kv{{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--line);font-size:14px}}.kv:last-child{{border:0}}.kv b{{font-variant-numeric:tabular-nums}}
.muted{{color:var(--muted)}}.foot{{color:var(--muted);font-size:12px;margin-top:18px}}
</style></head><body><div class="wrap">
<div class="hero"><div class="kente"></div><h1>Akwaaba</h1>
<p>WhatsApp assistant for community health workers in Ghana</p>
<span class="pill"><span class="dot"></span>Live impact &middot; updated {updated}</span></div>
<div class="tiles">
{tile("People reached", s.get("unique_users"), "", "hl")}
{tile("Answers given", s.get("answers"), "", "hl")}
{tile("Returning users", s.get("returning_users"), ret)}
{tile("Photos &amp; documents", (s.get("photos") or 0) + (s.get("documents") or 0), "Exercise books, registers")}
{tile("Voice questions", s.get("voice_answers"), "Twi, Ewe and English")}
{tile("Avg reply time", lat, f'p90 {s["p90_latency_s"]}s' if s.get("p90_latency_s") is not None else "")}
</div>
<div class="grid">
<div class="card"><h2>How useful people find it</h2><div class="sat">{_ring(s.get("satisfaction_pct"))}
<div><div class="kv"><span>Ratings</span><b>{s.get("ratings", 0)}</b></div>
<div class="kv"><span>Follow-up taps</span><b>{s.get("follow_up_taps", 0)}</b></div>
<div class="kv"><span>Shared with colleagues</span><b>{s.get("shares", 0)}</b></div>
<p class="note" style="margin:6px 0 0">{sat_note}. People rate by reacting \U0001F44D or \U0001F44E.</p></div></div></div>
<div class="card"><h2>Answers per day &middot; last 14 days</h2><div class="chart">{_daily(events)}</div></div>
</div>
<div class="grid">
<div class="card"><h2>What people ask for</h2>{_bars(s.get("by_task", {}), TASK_LABELS, "var(--gold)")}</div>
<div class="card"><h2>Who and which language</h2>{_bars(s.get("by_mode", {}), ROLE_LABELS)}
<div style="height:8px"></div>{_bars(s.get("by_language", {}), languages)}</div>
</div>
<div class="grid">
<div class="card"><h2>Paperwork and follow-ups</h2>
<div class="kv"><span>\U0001F4CA Monthly returns drafted</span><b>{s.get("returns_drafted", 0)}</b></div>
<div class="kv"><span>Register pages read (names hidden first)</span><b>{s.get("register_pages", 0)}</b></div>
<div class="kv"><span>\U0001F4C5 People added to follow-up lists</span><b>{s.get("followups_added", 0)}</b></div>
<div class="kv"><span>Follow-up visits marked done</span><b>{s.get("followup_visits_done", 0)}</b></div>
<div class="kv"><span>Reminders forwarded to families</span><b>{s.get("family_reminders", 0)}</b></div>
<div class="kv"><span>Monday lists sent</span><b>{s.get("weekly_lists_sent", 0)}</b></div></div>
<div class="card"><h2>The team</h2>
<div class="kv"><span>\U0001F3E1 Home visits logged by volunteers</span><b>{s.get("visits_logged", 0)}</b></div>
<div class="kv"><span>\U0001F501 Referrals made</span><b>{s.get("referrals_made", 0)}</b></div>
<div class="kv"><span>Referrals that arrived</span><b>{s.get("referrals_arrived", 0)}{(" (" + str(s["referral_completion_pct"]) + "%)") if s.get("referral_completion_pct") is not None else ""}</b></div>
<div class="kv"><span>\U0001F9A0 Disease alerts drafted</span><b>{s.get("alerts_raised", 0)}</b></div>
<div class="kv"><span>People who joined a team</span><b>{s.get("team_joins", 0)}</b></div></div>
<div class="card"><h2>Stock</h2>
<div class="kv"><span>\U0001F4E6 Stock checks</span><b>{s.get("stock_checks", 0)}</b></div>
<div class="kv"><span>Stock-outs flagged</span><b>{s.get("stockouts_flagged", 0)}</b></div>
<div class="kv"><span>Register photos refused (names couldn't be hidden)</span><b>{s.get("register_refused", 0)}</b></div></div>
</div>
<div class="grid">
<div class="card"><h2>Reply length by language (tokens)</h2>{_bars(s.get("avg_output_tokens_by_language", {}), languages, "var(--red)")}
<p class="note" style="margin:8px 0 0">Longer for the same task means higher cost and slower replies in that language: a real equity gap.</p></div>
<div class="card"><h2>Safety, memory and reliability</h2>
<div class="kv"><span>\U0001F6A8 Emergency cards shown</span><b>{s.get("emergency_cards", 0)}</b></div>
<div class="kv"><span>\U0001F9E0 People remembered</span><b>{s.get("people_remembered", 0)}</b></div>
<div class="kv"><span>Voice notes not understood</span><b>{s.get("voice_unclear", 0)}</b></div>
<div class="kv"><span>Errors</span><b>{s.get("errors", 0)}</b></div>
<div class="kv"><span>Undelivered replies</span><b>{s.get("delivery_failures", 0)}</b></div></div>
</div>
<p class="foot">Built from an anonymised log: no phone numbers, message text, voice notes or photos. Refreshes every minute.</p>
</div></body></html>"""
