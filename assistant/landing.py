"""Public landing page at "/": what Akwaaba is, a phone preview of a real
conversation, and a QR code that opens the chat on WhatsApp."""

import html


def _qr_svg(url: str) -> str:
    try:
        import segno
        return segno.make(url, error="m").svg_inline(scale=5, dark="#17160f", light="#ffffff", border=2)
    except Exception:
        return ""


def render(number: str) -> str:
    digits = "".join(ch for ch in number if ch.isdigit())
    link = f"https://wa.me/{digits}?text=Hi" if digits else "#"
    qr = _qr_svg(link) if digits else ""
    shown = f"+{digits[0]} {digits[1:4]} {digits[4:7]} {digits[7:]}" if len(digits) == 11 else (f"+{digits}" if digits else "")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Akwaaba · WhatsApp assistant for community health workers in Ghana</title>
<meta name="description" content="Free WhatsApp assistant for Ghana's community health workers. Health talks with voice notes, register summaries from a photo, home visit checklists and the nearest clinic, in English, Twi and Ewe.">

<style>
:root{{--bg:#f6f3ec;--ink:#17160f;--muted:#5f5b52;--card:#fff;--line:#e6e0d2;--red:#ce1126;--gold:#e9a400;--green:#006b3f;--wa:#0b8a5c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f0f0c;--ink:#f4f2ec;--muted:#aaa59a;--card:#1a1a16;--line:#2e2c26}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 Inter,-apple-system,"Segoe UI",Roboto,sans-serif}}
a{{color:inherit}}
.kente{{height:6px;background:repeating-linear-gradient(90deg,var(--red) 0 26px,var(--gold) 26px 52px,var(--green) 52px 78px,#111 78px 83px)}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 20px}}
nav{{display:flex;justify-content:space-between;align-items:center;padding:18px 0}}
.logo{{font-weight:800;font-size:20px;letter-spacing:-.02em}}.logo span{{color:var(--green)}}
nav a{{font-size:14px;color:var(--muted);text-decoration:none}}
.hero{{display:grid;grid-template-columns:1.1fr .9fr;gap:40px;align-items:center;padding:28px 0 56px}}
@media (max-width:820px){{.hero{{grid-template-columns:1fr}}}}
h1{{font-size:clamp(34px,5vw,54px);line-height:1.05;letter-spacing:-.035em;margin:0 0 16px}}
h1 em{{font-style:normal;background:linear-gradient(90deg,var(--green),#0a9a5c);-webkit-background-clip:text;background-clip:text;color:transparent}}
.lead{{font-size:19px;color:var(--muted);max-width:540px;margin:0 0 26px}}
.cta{{display:flex;gap:18px;align-items:center;flex-wrap:wrap}}
.btn{{display:inline-flex;align-items:center;gap:10px;background:var(--wa);color:#fff;text-decoration:none;font-weight:650;padding:14px 22px;border-radius:14px;box-shadow:0 8px 24px rgba(11,138,92,.28)}}
.btn:hover{{filter:brightness(1.06)}}
.btn2{{background:#fff;color:#0E5E3A;box-shadow:inset 0 0 0 2px #0E5E3A}}
.qr{{background:#fff;border-radius:14px;padding:6px;line-height:0;border:1px solid var(--line)}}.qr svg{{width:112px;height:112px}}
.small{{font-size:13px;color:var(--muted)}}
.langs{{display:flex;gap:8px;margin-top:22px;flex-wrap:wrap}}.chip{{border:1px solid var(--line);background:var(--card);border-radius:99px;padding:5px 12px;font-size:13px}}
/* phone */
.phone{{width:min(340px,100%);margin:0 auto;border-radius:38px;padding:12px;background:#111;box-shadow:0 30px 60px rgba(0,0,0,.25),0 0 0 1px #2a2a2a inset}}
.screen{{border-radius:28px;overflow:hidden;background:#efe7dc url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Cpath d='M0 20h40M20 0v40' stroke='%23e2d8c8' stroke-width='.6'/%3E%3C/svg%3E")}}
.bar{{background:#075e54;color:#fff;padding:12px 14px;display:flex;align-items:center;gap:10px;font-size:14px}}
.avatar{{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,var(--gold),var(--red));display:grid;place-items:center;font-weight:800;font-size:14px}}
.bar small{{display:block;opacity:.8;font-size:11px}}
.chat{{padding:12px 10px 16px;display:flex;flex-direction:column;gap:7px;font-size:13.5px;line-height:1.4}}
.msg{{max-width:84%;padding:7px 10px 6px;border-radius:10px;box-shadow:0 1px 1px rgba(0,0,0,.08);color:#111}}
.me{{align-self:flex-end;background:#d9fdd3;border-top-right-radius:2px}}
.bot{{align-self:flex-start;background:#fff;border-top-left-radius:2px}}
.voice{{display:flex;align-items:center;gap:8px;width:190px}}.wave{{flex:1;height:18px;background:repeating-linear-gradient(90deg,#4a8f6f 0 2px,transparent 2px 5px);mask:linear-gradient(90deg,#000 0 70%,rgba(0,0,0,.3) 70%);-webkit-mask:linear-gradient(90deg,#000 0 70%,rgba(0,0,0,.3) 70%);border-radius:3px}}
.btns{{display:flex;flex-direction:column;gap:4px;margin-top:6px}}.btns span{{display:block;text-align:center;color:#027eb5;font-weight:600;border-top:1px solid #eee;padding-top:5px;font-size:13px}}
.foot-n{{font-size:11px;color:#777;margin-top:3px}}
/* features */
section{{padding:40px 0}}
h2{{font-size:30px;letter-spacing:-.025em;margin:0 0 6px}}.sub{{color:var(--muted);margin:0 0 24px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px}}
.card .ic{{font-size:26px}}.card h3{{margin:8px 0 6px;font-size:17px}}.card p{{margin:0;color:var(--muted);font-size:15px}}
.band{{background:linear-gradient(135deg,#0d3b26,#006b3f);color:#fff;border-radius:22px;padding:30px;display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px}}
.band b{{display:block;font-size:17px;margin-bottom:4px}}.band span{{opacity:.85;font-size:14.5px}}
footer{{padding:30px 0 50px;color:var(--muted);font-size:13px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}}
</style></head><body>
<div class="kente"></div>
<div class="wrap">
<nav><div class="logo">Akwaaba<span>.</span></div><div><a href="/app/">App</a> &nbsp;&middot;&nbsp; <a href="/privacy">Privacy</a></div></nav>

<div class="hero">
<div>
<h1>A colleague for every <em>CHPS compound in Ghana</em>.</h1>
<p class="lead">A free WhatsApp assistant for community health officers, nurses, midwives and volunteers. Prepare a health talk with a voice note, summarise a register from a photo, plan a home visit or find the nearest clinic, in English, Twi or Ewe, by text or voice.</p>
<div class="cta">
<a class="btn" href="{html.escape(link)}"><svg width="20" height="20" viewBox="0 0 24 24" fill="#fff"><path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2Zm5.3 14.2c-.2.6-1.3 1.2-1.8 1.3-.5.1-1 .1-3.3-.8a11.6 11.6 0 0 1-4.3-3.8c-.3-.5-1-1.5-1-2.8s.7-2 1-2.3c.2-.3.5-.3.7-.3h.5c.2 0 .4 0 .6.5l.8 1.9c.1.2.1.4 0 .5l-.3.5-.4.4c-.1.2-.3.3-.1.6.2.3.8 1.3 1.7 2.1 1.2 1 2.1 1.3 2.4 1.5.3.1.5.1.6-.1l.9-1c.2-.3.4-.2.6-.1l1.8.9c.3.1.5.2.5.3.1.1.1.7-.1 1.2Z"/></svg>Chat on WhatsApp</a>
<a class="btn btn2" href="/app/">Open the app</a>
{f'<div class="qr" title="Scan to chat">{qr}</div>' if qr else ''}
<div class="small">{('Scan or tap to start<br>' + html.escape(shown)) if shown else 'Pilot: by invitation'}</div>
</div>
<div class="langs"><span class="chip">\U0001F1EC\U0001F1ED English</span><span class="chip">Asante Twi</span><span class="chip">Ewe</span><span class="chip">\U0001F399️ Voice notes</span><span class="chip">\U0001F4F8 Photos</span></div>
</div>

<div class="phone"><div class="screen">
<div class="bar"><div class="avatar">A</div><div>Akwaaba<small>typing…</small></div></div>
<div class="chat">
<div class="msg me"><div class="voice">▶<span class="wave"></span><span class="small" style="color:#555">0:07</span></div></div>
<div class="msg bot">\U0001F399️ <i>I heard:</i> “A one-minute talk on sleeping under mosquito nets, for the weighing day.”</div>
<div class="msg bot"><b>\U0001F99F Every night, under the net</b><br>• Mosquitoes that carry malaria bite at night…<br>• Tuck the net under the mat on all sides…<br>• <b>Today:</b> check your net for holes and mend them
<div class="foot-n">Not a diagnosis · Emergency: 112 / 193</div>
<div class="btns"><span>Quiz for group</span><span>Simpler</span><span>➕ More</span></div></div>
<div class="msg me">\U0001F44D</div>
</div></div></div>
</div>

<section><h2>Built for how community health work really happens</h2><p class="sub">Paper registers, prepaid data, long walks between homes, and a community that speaks Ewe or Twi.</p>
<div class="cards">
<div class="card"><div class="ic">\U0001F4E2</div><h3>Health talks, with voice</h3><p>A one-minute talk on nets, handwashing or antenatal care, plus a voice note to play at a durbar.</p></div>
<div class="card"><div class="ic">\U0001F4CA</div><h3>Registers from a photo</h3><p>Photograph a register or tally sheet; get the counts summarised and anything unusual flagged for your report.</p></div>
<div class="card"><div class="ic">\U0001F6A8</div><h3>Danger signs, fast</h3><p>Clear "go to a facility now" guidance, instant emergency numbers, and no medicine doses, ever.</p></div>
<div class="card"><div class="ic">\U0001F399️</div><h3>Speak, don't type</h3><p>Voice notes in Twi, Ewe or English, understood by GhanaNLP's Khaya speech recognition.</p></div>
<div class="card"><div class="ic">\U0001F4CD</div><h3>Nearest facility</h3><p>Share a location or type a town to see the closest mapped clinics and hospitals as map pins.</p></div>
<div class="card"><div class="ic">\U0001F512</div><h3>Private by design</h3><p>No patient names kept. See everything it remembers with MY DATA; erase it with FORGET ME.</p></div>
</div></section>

<section><div class="band">
<div><b>Measured, not assumed</b><span>Ratings, return visits and usage by language are tracked anonymously from day one.</span></div>
<div><b>Safety first</b><span>An emergency fast-path that doesn't depend on the AI, no medicine doses, and test before you treat.</span></div>
<div><b>Made for Ghana</b><span>Ghana Health Service context, local languages and Ghana's emergency numbers.</span></div>
</div></section>

<footer><span>Akwaaba · powered by Claude · speech by GhanaNLP Khaya · an independent pilot</span><span><a href="/privacy">Privacy</a></span></footer>
</div></body></html>"""
