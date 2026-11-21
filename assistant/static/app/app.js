/* Akwaaba companion app. Plain JavaScript, no build step.
   Shares one account with the WhatsApp assistant: same history, library and settings. */
"use strict";

// ================================================================ state & tiny helpers
const S = {
  token: null, phone: "", me: null, strings: {}, lang: "en",
  chat: { messages: null, task: null, busy: false, image: null },
  library: null, quiz: null, online: navigator.onLine,
};
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} },
};
S.token = store.get("akw_token");

const $ = (sel, root = document) => root.querySelector(sel);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function fill(el, ...kids) { el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false).map((k) => (k instanceof Node ? k : document.createTextNode(String(k))))); return el; }
const t = (en) => (S.strings && S.strings[en]) || en;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const ICONS = {
  home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/>',
  quiz: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>',
  book: '<path d="M4 4h6a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4z"/><path d="M20 4h-6a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h7z"/>',
  me: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  send: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
  play: '<path d="M8 5l11 7-11 7z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  pdf: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-6-6-9 9"/>',
  star: '<path d="M12 3l2.8 5.8 6.2.9-4.5 4.4 1.1 6.2L12 17.4l-5.6 2.9 1.1-6.2L3 9.7l6.2-.9z"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  pin: '<path d="M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/>',
  share: '<path d="M12 3v12"/><path d="M7 8l5-5 5 5"/><path d="M5 13v7h14v-7"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  wa: '<path d="M4 20l1.3-4A8 8 0 1 1 8 18.7z"/><path d="M9 9.5c.5 2 2 3.5 4.5 4.5l1.2-1.2 2 1-.5 1.7c-3.5.5-8-4-7.7-7.7L10.2 7l1 2z"/>',
  phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  out: '<path d="M10 5H5v14h5"/><path d="M14 8l4 4-4 4M18 12H9"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  speaker: '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
};
const ADINKRA = {
  adinkrahene: "<circle cx=\"24\" cy=\"24\" r=\"20\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"4\"/><circle cx=\"24\" cy=\"24\" r=\"12.5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"4\"/><circle cx=\"24\" cy=\"24\" r=\"5\" fill=\"currentColor\"/>",
  matemasie: "<g fill=\"none\" stroke=\"currentColor\" stroke-width=\"3.6\"><circle cx=\"15\" cy=\"15\" r=\"9\"/><circle cx=\"33\" cy=\"15\" r=\"9\"/><circle cx=\"15\" cy=\"33\" r=\"9\"/><circle cx=\"33\" cy=\"33\" r=\"9\"/></g><g fill=\"currentColor\"><circle cx=\"18\" cy=\"18\" r=\"3.4\"/><circle cx=\"30\" cy=\"18\" r=\"3.4\"/><circle cx=\"18\" cy=\"30\" r=\"3.4\"/><circle cx=\"30\" cy=\"30\" r=\"3.4\"/></g>",
  nkyinkyim: "<path d=\"M18 3 L30 11 L18 19 L30 27 L18 35 L30 43\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"5.5\" stroke-linejoin=\"miter\" stroke-linecap=\"square\"/><path d=\"M12 3 H36 M12 45 H36\" stroke=\"currentColor\" stroke-width=\"3.5\"/>",
  sankofa: "<path d=\"M24 42 C14 34 6 27 6 17 C6 10 11 6 16 6 C21 6 24 9 24 13 C24 16 22 18 19.5 18 C17.5 18 16 16.6 16 14.8 C16 13.4 17 12.4 18.3 12.4\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"3.8\" stroke-linecap=\"round\"/><path d=\"M24 42 C34 34 42 27 42 17 C42 10 37 6 32 6 C27 6 24 9 24 13 C24 16 26 18 28.5 18 C30.5 18 32 16.6 32 14.8 C32 13.4 31 12.4 29.7 12.4\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"3.8\" stroke-linecap=\"round\"/><path d=\"M24 42 V46 M17 44 C15 46 12 46 11 44 M31 44 C33 46 36 46 37 44\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"3.2\" stroke-linecap=\"round\"/>",
  nsoromma: "<g fill=\"currentColor\"><path transform=\"rotate(0 24 24)\" d=\"M24 2 L28 14 L20 14 Z\"/><path transform=\"rotate(45 24 24)\" d=\"M24 2 L28 14 L20 14 Z\"/><path transform=\"rotate(90 24 24)\" d=\"M24 2 L28 14 L20 14 Z\"/><path transform=\"rotate(135 24 24)\" d=\"M24 2 L28 14 L20 14 Z\"/><path transform=\"rotate(180 24 24)\" d=\"M24 2 L28 14 L20 14 Z\"/><path transform=\"rotate(225 24 24)\" d=\"M24 2 L28 14 L20 14 Z\"/><path transform=\"rotate(270 24 24)\" d=\"M24 2 L28 14 L20 14 Z\"/><path transform=\"rotate(315 24 24)\" d=\"M24 2 L28 14 L20 14 Z\"/></g><circle cx=\"24\" cy=\"24\" r=\"9\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"3.6\"/><circle cx=\"24\" cy=\"24\" r=\"3.5\" fill=\"currentColor\"/>",
  aya: "<path d=\"M24 46 V4\" stroke=\"currentColor\" stroke-width=\"3.8\" stroke-linecap=\"round\"/><path d=\"M24 14 Q14 12 10 6 M24 14 Q34 12 38 6\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"3.6\" stroke-linecap=\"round\"/><path d=\"M24 22 Q13 20 9 14 M24 22 Q35 20 39 14\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"3.6\" stroke-linecap=\"round\"/><path d=\"M24 30 Q12 28 8 22 M24 30 Q36 28 40 22\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"3.6\" stroke-linecap=\"round\"/><path d=\"M24 38 Q11 36 7 30 M24 38 Q37 36 41 30\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"3.6\" stroke-linecap=\"round\"/>",
  akoma: "<path d=\"M24 43 C11 33 4 25 4 16 C4 9 9 5 15 5 C19.5 5 22.5 8 24 11 C25.5 8 28.5 5 33 5 C39 5 44 9 44 16 C44 25 37 33 24 43 Z\" fill=\"currentColor\"/><path d=\"M24 34 C17 28 13 24 13 18\" fill=\"none\" stroke=\"var(--stamp-bg, #fff)\" stroke-width=\"3\" stroke-linecap=\"round\" opacity=\".9\"/>",
};
// Each task wears an adinkra symbol whose meaning fits it.
const TASK_SYMBOL = {
  h_talk: "matemasie", h_danger: "akoma", h_near: "adinkrahene",
  h_play: "nsoromma", h_visit: "akoma", h_register: "nkyinkyim", h_myth: "sankofa",
};
const SYMBOL_MEANING = [
  ["adinkrahene", "Adinkrahene", "Chief of the symbols: leadership"],
  ["matemasie", "Mate masie", "What I hear, I keep: wisdom"],
  ["nkyinkyim", "Nkyinkyim", "Twisting: initiative, versatility"],
  ["sankofa", "Sankofa", "Go back and fetch it: learn from the past"],
  ["nsoromma", "Nsoromma", "Child of the heavens: guardianship"],
  ["aya", "Aya", "The fern: endurance, resourcefulness"],
  ["akoma", "Akoma", "The heart: patience, care"],
];
const sym = (name) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 48 48"); s.setAttribute("class", "sym"); s.setAttribute("aria-hidden", "true"); s.innerHTML = ADINKRA[name] || ADINKRA.adinkrahene; return s; };
const taskSym = (id) => sym(TASK_SYMBOL[id] || "adinkrahene");
const isLatin = (s) => !/[^\u0000-\u00FF\u2019\u2026]/.test(stripEmoji(String(s)));
const icon = (name) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "i"); s.setAttribute("aria-hidden", "true"); s.innerHTML = ICONS[name] || ""; return s; };

function toast(msg) {
  const el = $("#toast"); el.textContent = msg; el.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove("show"), 2600);
}

// ================================================================ API
class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
async function api(path, { method = "GET", body, form, blob } = {}) {
  const headers = { "ngrok-skip-browser-warning": "1" };
  if (S.token) headers.Authorization = "Bearer " + S.token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, { method, headers, body: form || (body !== undefined ? JSON.stringify(body) : undefined) });
  } catch {
    throw new ApiError(t("You're offline. Check your connection and try again."), 0);
  }
  if (res.status === 401 && S.token) { signOut(true); throw new ApiError(t("Please sign in again."), 401); }
  if (!res.ok) {
    let msg = t("Something went wrong. Please try again.");
    try { const j = await res.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : msg; } catch {}
    throw new ApiError(msg, res.status);
  }
  if (blob) return res.blob();
  return res.json();
}

async function loadStrings(lang) {
  S.lang = lang || "en";
  document.documentElement.lang = S.lang === "tw" ? "ak" : S.lang === "ee" ? "ee" : "en";
  if (S.lang === "en") { S.strings = {}; return; }
  try { S.strings = (await api("/api/strings?lang=" + S.lang)).strings || {}; } catch { S.strings = {}; }
}

async function loadMe() {
  S.me = await api("/api/me");
  document.body.dataset.role = "health";
  if (S.me.lang !== S.lang || !Object.keys(S.strings).length) await loadStrings(S.me.lang);
  store.set("akw_me", JSON.stringify(S.me));
  return S.me;
}

function signOut(expired) {
  if (!expired && S.token) api("/api/logout", { method: "POST" }).catch(() => {});
  S.token = null; S.me = null; S.chat.messages = null; S.library = null;
  store.set("akw_token", null); store.set("akw_me", null);
  store.set("akw_lib", null);
  if (window.caches) caches.delete("akwaaba-data-v1").catch(() => {});
  location.hash = "#/signin";
}

// ================================================================ WhatsApp-style text -> HTML
function fmtInline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*(?!\s)(.+?)(?<!\s)\*(?=[\s).,:;!?]|$)/g, "$1<b>$2</b>")
    .replace(/(^|[\s(])_(?!\s)(.+?)(?<!\s)_(?=[\s).,:;!?]|$)/g, "$1<i>$2</i>")
    .replace(/(^|\s)~(?!\s)(.+?)(?<!\s)~(?=\s|$)/g, "$1<s>$2</s>");
}
function render(text) {
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  let html = "", list = null, para = [], first = true;
  const flushPara = () => { if (para.length) { html += "<p>" + para.join("<br>") + "</p>"; para = []; } };
  const flushList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (let raw of lines) {
    let line = raw.trim();
    if (!line) { flushPara(); flushList(); continue; }
    const head = line.match(/^#{1,6}\s+(.+)$/) || (first && line.match(/^\*([^*]+)\*:?$/));
    if (head) { flushPara(); flushList(); html += `<h3>${fmtInline(head[1].replace(/\*/g, ""))}</h3>`; first = false; continue; }
    first = false;
    let m;
    if ((m = line.match(/^(?:•|-|\*)\s+(.+)/))) { flushPara(); if (list !== "ul") { flushList(); html += "<ul>"; list = "ul"; } html += `<li>${fmtInline(m[1])}</li>`; continue; }
    if ((m = line.match(/^(\d+)[.)]\s+(.+)/))) { flushPara(); if (list !== "ol") { flushList(); html += `<ol start="${m[1]}">`; list = "ol"; } html += `<li>${fmtInline(m[2])}</li>`; continue; }
    if ((m = line.match(/^>\s?(.*)/))) { flushPara(); flushList(); html += `<blockquote>${fmtInline(m[1])}</blockquote>`; continue; }
    flushList(); para.push(fmtInline(line));
  }
  flushPara(); flushList();
  return html;
}

// ================================================================ greeting
function greeting() {
  const hr = new Date().getHours();
  if (hr < 12) return { tw: "Maakye", en: t("Good morning") };
  if (hr < 17) return { tw: "Maaha", en: t("Good afternoon") };
  return { tw: "Maadwo", en: t("Good evening") };
}
const kente = () => h("div", { class: "kente", "aria-hidden": "true" }, [1, 2, 3, 4, 5, 6, 7].map(() => h("i")));
const emojiOf = (title) => (String(title).match(/^\p{Extended_Pictographic}️?/u) || [""])[0];
const stripEmoji = (title) => String(title).replace(/^\p{Extended_Pictographic}️?\s*/u, "");

// ================================================================ photography (same Unsplash set as akwaabahealth.org)
const PHOTO = {
  coast: "img/hero-akwidaa.jpg", phone: "img/phone-accra.jpg", mother: "img/mother-village.jpg",
  writing: "img/kumasi-writing.jpg", river: "img/butre-river.jpg",
};
const TASK_PHOTO = {
  h_talk: ["mother", "center 28%"], h_register: ["writing", "center 38%"], h_visit: ["river", "center 60%"],
  h_myth: ["phone", "center 30%"], h_play: ["coast", "center 55%"], h_near: ["river", "20% 50%"],
  h_followup: ["mother", "center 60%"], h_referral: ["river", "70% 50%"], h_alert: ["coast", "30% 50%"],
  h_stock: ["writing", "center 70%"], h_visitlog: ["river", "center 40%"], h_team: ["phone", "center 45%"],
};
const ROTATE = ["mother", "writing", "river", "phone", "coast"];
const photo = (key, pos, cls) => h("img", { src: PHOTO[key] || PHOTO.coast, alt: "", loading: "lazy", decoding: "async", class: cls || null, style: pos ? `object-position:${pos}` : null });
const taskPhoto = (id, i = 0, cls) => { const [k, pos] = TASK_PHOTO[id] || [ROTATE[i % ROTATE.length], "center"]; return photo(k, pos, cls); };
function photoHead(key, pos, label, title, sub, { back } = {}) {
  return h("header", { class: "photo-head" }, photo(key, pos),
    back ? h("div", { class: "top" }, h("button", { class: "iconbtn", "aria-label": t("Back"), onclick: () => history.length > 1 ? history.back() : (location.hash = back) }, icon("back"))) : null,
    h("div", { class: "in" }, label ? h("span", { class: "label", html: label }) : null, h("h1", {}, title), sub ? h("p", {}, sub) : null));
}

// ================================================================ router
const ROUTES = {
  signin: viewSignIn, setup: viewSetup, home: viewHome, ask: viewAsk, quiz: viewQuiz,
  library: viewLibrary, me: viewMe, near: viewNear, item: viewItem,
};
const TABS = [["home", "adinkrahene", "Home"], ["ask", "matemasie", "Ask"], ["quiz", "nkyinkyim", "Quiz"], ["library", "sankofa", "Library"], ["me", "nsoromma", "Me"]];

async function route() {
  const [name, arg] = (location.hash.replace(/^#\/?/, "") || "home").split("/");
  closeSheet();
  if (!S.token && name !== "signin") { location.hash = "#/signin"; return; }
  if (S.token && !S.me) {
    try { await loadMe(); }
    catch (e) {
      const cached = store.get("akw_me");
      if (cached && e.status === 0) { S.me = JSON.parse(cached); document.body.dataset.role = "health"; }
      else if (e.status !== 401) { mount(errorView(e.message, route)); return; }
      else return;
    }
  }
  if (S.token && S.me && !S.me.mode && name !== "setup" && name !== "signin") { location.hash = "#/setup"; return; }
  const view = ROUTES[name] || viewHome;
  const showTabs = !["signin", "setup"].includes(name);
  const content = await view(arg);
  mount(content, showTabs ? name : null);
}
function mount(content, tab) {
  const app = $("#app");
  fill(app, content);
  if (tab) app.append(tabBar(tab));
  window.scrollTo(0, 0);
}
function tabBar(active) {
  return h("nav", { class: "tabs", "aria-label": t("Main") },
    TABS.map(([id, ic, label]) => h("a", { href: "#/" + id, "aria-current": id === active || (active === "near" && id === "home") || (active === "item" && id === "library") ? "page" : null }, sym(ic), t(label))));
}
function errorView(msg, retry) {
  return h("main", { class: "screen" }, h("div", { class: "empty" }, h("div", { class: "big" }, sym("aya")), h("b", {}, msg),
    h("button", { class: "btn", onclick: retry }, t("Try again"))));
}
function bar(title, { back, right } = {}) {
  return h("header", { class: "bar" },
    back ? h("button", { class: "iconbtn", "aria-label": t("Back"), onclick: () => history.length > 1 ? history.back() : (location.hash = back) }, icon("back")) : null,
    h("h1", {}, title), h("div", { class: "spacer" }), right || null);
}

// ================================================================ sign in
function viewSignIn() {
  if (S.token) { location.hash = "#/home"; return h("div"); }
  const g = greeting();
  const body = h("div", { class: "body" });
  const wrap = h("main", { class: "welcome" },
    h("section", { class: "hero" }, photo("coast", "center 40%"),
      h("div", { class: "brand" }, sym("sankofa"), "Akwaaba"),
      h("div", { class: "in" },
        h("span", { class: "label", html: "Ghana · <b>Free for health workers</b>" }),
        h("div", { class: "hello" }, "Akwaaba", h("em", {}, "!")),
        h("p", { class: "sub" }, g.tw + "! Your free assistant for community health work, in English, Twi and Ewe.")),
      h("span", { class: "credit" }, "Akwidaa · Ato Aikins / Unsplash")),
    h("div", { class: "kente-strip" }),
    body);
  stepPhone(body);
  return wrap;
}
function stepPhone(body, err) {
  const input = h("input", { class: "input", type: "tel", inputmode: "tel", autocomplete: "tel", placeholder: "024 123 4567", value: S.phone || "", "aria-describedby": "ph-help" });
  const btn = h("button", { class: "btn block", type: "submit" }, "Send me a code");
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault(); btn.disabled = true; btn.textContent = "Sending…";
    try {
      const r = await api("/api/auth/start", { method: "POST", body: { phone: input.value } });
      S.phone = r.phone; stepCode(body, r);
    } catch (ex) { stepPhone(body, ex.message); }
  } },
    h("label", { class: "field" }, h("span", {}, "Your WhatsApp number"), input,
      h("small", { id: "ph-help" }, "We'll send a 6-digit code to your WhatsApp. Outside Ghana? Start with + and your country code, like +1 for the US.")),
    err ? h("p", { role: "alert", style: "color:var(--red)" }, err) : null, btn,
    h("p", { class: "helper" }, "It's the same account as the Akwaaba WhatsApp chat, so your history and saved items come with you."));
  fill(body, form);
  setTimeout(() => input.focus(), 50);
}
function stepCode(body, r, err) {
  const input = h("input", { class: "input otp", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "6", pattern: "\\d{6}", placeholder: "••••••", "aria-label": "6-digit code" });
  const btn = h("button", { class: "btn block", type: "submit" }, "Sign in");
  const verify = async () => {
    btn.disabled = true; btn.textContent = "Checking…";
    try {
      const v = await api("/api/auth/verify", { method: "POST", body: { phone: S.phone, code: input.value } });
      S.token = v.token; store.set("akw_token", v.token);
      await loadMe();
      location.hash = S.me.mode ? "#/home" : "#/setup";
    } catch (ex) { stepCode(body, r, ex.message); }
  };
  input.addEventListener("input", () => { input.value = input.value.replace(/\D/g, "").slice(0, 6); if (input.value.length === 6) verify(); });
  fill(body, h("form", { onsubmit: (e) => { e.preventDefault(); verify(); } },
    h("label", { class: "field" }, h("span", {}, r.sent ? "Enter the code we sent on WhatsApp" : "Get your code on WhatsApp"), input,
      h("small", {}, "For +" + S.phone + ". ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); stepPhone(body); } }, "Change number"))),
    err ? h("p", { role: "alert", style: "color:var(--red)" }, err) : null,
    btn,
    r.wa_link ? h("a", { class: "wa", href: r.wa_link, target: "_blank", rel: "noopener" }, icon("wa"),
      h("span", {}, r.sent ? "No code? Open WhatsApp and send LOGIN to Akwaaba." : "Open WhatsApp and send LOGIN to Akwaaba. It will reply with your code.")) : null));
  setTimeout(() => input.focus(), 50);
}

// ================================================================ setup
function viewSetup() {
  const pick = { lang: S.me?.lang || "en", detail: null };
  const main = h("main", { class: "screen full" });
  const draw = () => {
    const details = S.me.details.health || [];
    fill(main, 
      bar(t("Set up Akwaaba")),
      h("p", { class: "lead" }, t("Two quick choices, so every answer fits your work.")),
      h("h2", { class: "h" }, t("Reply to me in")),
      h("div", { class: "seg", role: "group" }, S.me.languages.map((l) =>
        h("button", { "aria-pressed": String(pick.lang === l.code), onclick: async () => { pick.lang = l.code; await loadStrings(l.code); draw(); } }, l.code === "tw" ? "Twi" : l.code === "ee" ? "Ewe" : "English"))),
      h("h2", { class: "h" }, t("What's your role?")),
      h("div", { class: "list" }, details.map((d) => h("button", { class: "row", onclick: () => { pick.detail = d.id; draw(); } },
        h("span", { class: "tx" }, h("b", {}, d.title), h("small", {}, d.desc)), pick.detail === d.id ? h("span", { style: "color:var(--brand-ink)" }, icon("check")) : null))),
      h("div", { style: "height:20px" }),
      h("button", { class: "btn block", onclick: async (e) => {
        e.target.disabled = true;
        S.me = await api("/api/profile", { method: "POST", body: { mode: "health", lang: pick.lang, detail: pick.detail } });
        await loadStrings(S.me.lang); location.hash = "#/home";
      } }, t("Start")));
  };
  draw();
  return main;
}

// ================================================================ home
async function viewHome() {
  const me = S.me, g = greeting();
  const tasks = me.tasks || [];
  const [feature, ...rest] = tasks;
  let recent = [];
  try { recent = (await getLibrary()).slice(0, 8); } catch {}
  const main = h("main", { class: "screen" },
    h("section", { class: "hero" }, photo("phone", "center 30%"),
      h("div", { class: "brand" }, sym("sankofa"), "Akwaaba"),
      h("div", { class: "in" },
        h("span", { class: "label", html: esc(new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })) + " · <b>" + esc(t("Community health")) + "</b>" }),
        h("div", { class: "hello" }, g.tw, h("em", {}, "!")),
        h("p", { class: "sub" }, g.en + (/[?.!]$/.test(g.en) ? " " : ". ") + t("What do you need today?")),
        h("a", { class: "who", href: "#/me" }, sym("nsoromma"), me.role_name + (me.detail ? ", " + me.detail : "")))),
    h("div", { class: "kente-strip" }),
    h("div", { class: "sos", role: "note" }, icon("phone"), h("span", {}, h("b", {}, t("Emergency?")), " ", t("Call an ambulance")), h("a", { href: "tel:112" }, "112")),
    h("div", { class: "sect" }, h("span", { class: "label", html: "01 · <b>" + esc(t("Start here")) + "</b>" }),
      feature ? h("button", { class: "feature", onclick: () => openTask(feature) }, taskPhoto(feature.id),
        h("span", { class: "in" }, h("span", {}, h("small", {}, t("Most used")), h("b", {}, stripEmoji(feature.title)), h("span", { class: "d" }, feature.desc)),
          h("span", { class: "go" }, icon("send")))) : null),
    rest.length ? h("div", { class: "sect" }, h("span", { class: "label", html: "02 · <b>" + esc(t("Your tools")) + "</b>" }),
      h("h2", {}, t("Everything for today's work")),
      h("div", { class: "tasks" }, rest.map((tk, i) => h("button", { class: "task" + (tk.id === "h_danger" ? " danger" : ""), onclick: () => openTask(tk) },
        taskPhoto(tk.id, i), h("span", { class: "stamp" }, taskSym(tk.id)),
        h("span", { class: "in" }, h("b", {}, stripEmoji(tk.title)), h("small", {}, tk.desc)))))) : null,
    h("section", { class: "story" }, photo("mother", "center 62%"),
      h("div", { class: "in" }, h("span", { class: "label", html: "<b>" + esc(t("Why we built this")) + "</b>" }),
        h("p", {}, t("Less time on paperwork. More time with mothers and children.")))),
    recent.length ? h("div", { class: "sect" }, h("span", { class: "label", html: "03 · <b>" + esc(t("Saved for you")) + "</b>" }),
      h("h2", {}, t("Pick up where you left off")),
      h("div", { class: "recent" }, recent.map((it, i) => h("button", { onclick: () => (location.hash = "#/item/" + it.id) },
        h("div", { class: "kente-strip", style: `background-position:${-(it.id * 37) % 192}px -64px` }),
        h("div", {}, h("small", {}, it.kind), h("b", {}, stripEmoji(it.title))))))) : null,
    h("div", { class: "stats" },
      h("div", {}, h("b", {}, me.stats.answers), t("answers")),
      h("div", {}, h("b", {}, me.stats.saved), t("saved"))),
    installHint());
  return main;
}
function openTask(tk) {
  if (tk.kind === "near") { location.hash = "#/near"; return; }
  if (tk.kind === "quiz") { S.quiz = null; location.hash = "#/quiz"; return; }
  S.chat.task = tk; S.chat.taskStart = S.chat.messages ? S.chat.messages.length : 0;
  location.hash = "#/ask";
  if (tk.form) setTimeout(() => openForm(tk), 150);
}
function installHint() {
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (standalone || store.get("akw_hint_closed")) return null;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const box = h("div", { class: "taskpill", style: "margin-top:22px" },
    h("span", { class: "stamp" }, icon("share")),
    h("b", { style: "font-weight:400;flex:1" }, ios ? t("Install: tap Share, then Add to Home Screen.") : t("Install Akwaaba from your browser menu for one-tap access, even offline.")),
    h("button", { class: "iconbtn", "aria-label": t("Close"), onclick: () => { store.set("akw_hint_closed", "1"); box.remove(); } }, icon("close")));
  return box;
}

// ================================================================ ask (chat)
async function viewAsk() {
  const main = h("main", { class: "screen chat" });
  const msgs = h("div", { class: "msgs", id: "msgs" });
  main.append(h("header", { class: "chat-head" }, photo("phone", "center 30%"),
    h("div", { class: "in" }, h("span", { class: "label", html: "<b>English · Twi · Ewe</b>" }), h("h1", {}, t("Ask Akwaaba")))), msgs);
  if (S.chat.messages === null) {
    msgs.append(h("div", { class: "typing" }, h("i"), h("i"), h("i")));
    try { S.chat.messages = (await api("/api/history")).messages; } catch { S.chat.messages = []; }
  }
  drawMessages(msgs);
  if (S.chat.task && S.chat.taskStart == null) S.chat.taskStart = S.chat.messages.length;
  main.append(composer());
  setTimeout(() => window.scrollTo(0, document.body.scrollHeight), 30);
  return main;
}
function drawMessages(msgs) {
  fill(msgs);
  if (!S.chat.messages.length) {
    msgs.append(h("div", { class: "empty" }, h("div", { class: "big" }, sym("matemasie")), h("b", {}, t("Ask anything about your work")),
      h("span", {}, t("Type, speak or send a photo. Answers can be heard, printed or saved."))));
  }
  for (const m of S.chat.messages) msgs.append(m.role === "user" ? userBubble(m) : botBubble(m));
}
function userBubble(m) {
  return h("div", { class: "msg me" }, m.image ? h("img", { src: m.image, alt: t("Your photo") }) : null,
    String(m.text || "").replace(/^\[(voice note|photo|document)\]\s*/, ""));
}
function botBubble(m) {
  if (m.emergency) {
    return h("div", { class: "msg bot" }, h("div", { class: "emergency", role: "alert" }, h("div", { html: render(m.text) }),
      h("a", { class: "btn small", href: "#/near" }, icon("pin"), t("Nearest facility"))));
  }
  const wrap = h("div", { class: "msg bot" }, h("div", { class: "answer" + (m.streaming ? " streaming" : ""), html: render(m.text) }));
  if (m.item_id) wrap.append(answerActions(m));
  if (m.follow && m.follow.length) {
    wrap.append(h("div", { class: "chips follow" }, m.follow.map((f) =>
      h("button", { class: "chip", onclick: () => send({ follow: f.id, label: f.label, task: m.task }) }, f.label))));
  }
  return wrap;
}
function answerActions(m) {
  const row = h("div", { class: "actions" });
  const save = h("button", { class: "act" + (m.saved ? " on" : ""), "aria-pressed": String(!!m.saved) }, icon("star"), m.saved ? t("Saved") : t("Save"));
  save.onclick = async () => {
    const was = m.saved; m.saved = !was;
    save.classList.toggle("on", m.saved); save.lastChild.textContent = m.saved ? t("Saved") : t("Save");
    try { await api(`/api/items/${m.item_id}/save`, { method: m.saved ? "POST" : "DELETE" }); S.library = null; toast(m.saved ? t("Saved to My library") : t("Removed from My library")); }
    catch (e) { m.saved = was; toast(e.message); }
  };
  if (S.me.voice_available) row.append(listenButton(m.item_id));
  row.append(
    h("button", { class: "act", onclick: (e) => fileAction(e.currentTarget, `/api/items/${m.item_id}/pdf`, "akwaaba.pdf", "application/pdf") }, icon("pdf"), "PDF"),
    h("button", { class: "act", onclick: (e) => fileAction(e.currentTarget, `/api/items/${m.item_id}/card`, "akwaaba.png", "image/png") }, icon("image"), t("Card")),
    save,
    h("button", { class: "act", onclick: () => { S.quiz = { fromItem: m.item_id, topic: "" }; location.hash = "#/quiz"; startQuiz({ item_id: m.item_id }); } }, icon("target"), t("Quiz")));
  return row;
}
let currentAudio = null;
function listenButton(itemId) {
  const b = h("button", { class: "act" }, icon("play"), t("Listen"));
  b.onclick = async () => {
    if (currentAudio && currentAudio._btn === b && !currentAudio.paused) { currentAudio.pause(); return; }
    if (currentAudio) currentAudio.pause();
    b.setAttribute("aria-busy", "true");
    try {
      const blob = await api(`/api/items/${itemId}/voice`, { blob: true });
      const a = new Audio(URL.createObjectURL(blob)); a._btn = b; currentAudio = a;
      const setIcon = (name, label) => { fill(b, icon(name), label); };
      a.onplay = () => setIcon("pause", t("Pause")); a.onpause = a.onended = () => setIcon("play", t("Listen"));
      await a.play();
    } catch (e) { toast(e.message); }
    b.removeAttribute("aria-busy");
  };
  return b;
}
async function fileAction(btn, path, name, type) {
  btn.setAttribute("aria-busy", "true");
  try {
    const blob = await api(path, { blob: true });
    const file = new File([blob], name, { type });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file], title: "Akwaaba" }); } catch {}
    } else {
      const url = URL.createObjectURL(blob);
      const a = h("a", { href: url, download: name, target: "_blank" }); document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    }
  } catch (e) { toast(e.message); }
  btn.removeAttribute("aria-busy");
}

function composer() {
  const tk = S.chat.task;
  const fresh = tk && S.chat.messages.length <= (S.chat.taskStart ?? 0);  // nothing asked in this task yet
  const ta = h("textarea", { rows: "1", placeholder: t("Type your question…"), "aria-label": t("Your question"), enterkeyhint: "send" });
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(140, ta.scrollHeight) + "px"; sendBtn.hidden = !ta.value.trim() && !S.chat.image; micBtn.hidden = !sendBtn.hidden; });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !/iphone|android/i.test(navigator.userAgent)) { e.preventDefault(); go(); } });
  const sendBtn = h("button", { class: "round send", "aria-label": t("Send"), hidden: true, onclick: () => go() }, icon("send"));
  const micBtn = h("button", { class: "round", "aria-label": t("Speak your question"), hidden: !S.me.speech_available, onclick: () => record(micBtn, ta) }, icon("mic"));
  const fileIn = h("input", { type: "file", accept: "image/*,application/pdf", hidden: true, onchange: async () => {
    const f = fileIn.files[0]; if (!f) return;
    S.chat.image = f.type === "application/pdf" ? await readDataUrl(f) : await shrinkImage(f);
    drawAttach(); ta.dispatchEvent(new Event("input"));
  } });
  const attach = h("div", { class: "attach", hidden: true });
  const drawAttach = () => {
    attach.hidden = !S.chat.image;
    fill(attach, ...(S.chat.image ? [S.chat.image.startsWith("data:image") ? h("img", { src: S.chat.image, alt: "" }) : icon("pdf"),
      h("span", {}, t("Photo ready. Add a note or just send.")),
      h("button", { class: "iconbtn", "aria-label": t("Remove photo"), onclick: () => { S.chat.image = null; drawAttach(); ta.dispatchEvent(new Event("input")); } }, icon("close"))] : []));
  };
  const go = () => { const text = ta.value.trim(); if (!text && !S.chat.image) return; ta.value = ""; ta.dispatchEvent(new Event("input")); send({ text }); };
  const pill = tk ? h("div", { class: "taskpill" }, h("span", { class: "stamp" }, taskSym(tk.id)),
    h("span", { style: "flex:1;min-width:0" }, h("b", { style: "display:block" }, stripEmoji(tk.title)), tk.ask && fresh ? h("small", { class: "helper", style: "display:block;line-height:1.3" }, stripTip(tk.ask)) : null),
    tk.form ? h("button", { class: "act", onclick: () => openForm(tk) }, t("Open form")) : null,
    h("button", { class: "iconbtn", "aria-label": t("Leave this task"), onclick: () => { S.chat.task = null; route(); } }, icon("close"))) : null;
  const examples = tk && fresh && tk.examples.length ? h("div", { class: "chips" }, tk.examples.map((ex) =>
    h("button", { class: "chip", onclick: () => send({ text: stripEmoji(tk.title) + ": " + ex }) }, ex))) : null;
  drawAttach();
  return h("div", { class: "composer" }, h("div", { class: "inner" }, pill, examples, attach,
    h("div", { class: "row2" }, h("button", { class: "round", "aria-label": t("Add a photo"), onclick: () => fileIn.click() }, icon("camera")), fileIn, ta, sendBtn, micBtn)));
}
const stripTip = (s) => String(s).split("\n")[0];

async function send({ text = "", follow, label, form, task, voice } = {}) {
  if (S.chat.busy) return;
  if (location.hash !== "#/ask") { location.hash = "#/ask"; await new Promise((r) => setTimeout(r, 120)); }
  const msgs = $("#msgs"); if (!msgs) return;
  S.chat.busy = true;
  const image = S.chat.image; S.chat.image = null;
  const shown = label || (form ? formSummary(form) : text);
  S.chat.messages.push({ role: "user", text: shown, image: image && image.startsWith("data:image") ? image : null });
  const bot = { role: "assistant", text: "", streaming: true };
  drawMessages(msgs);
  const typing = h("div", { class: "typing", role: "status", "aria-label": t("Akwaaba is writing") }, h("i"), h("i"), h("i"));
  msgs.append(typing); window.scrollTo(0, document.body.scrollHeight);
  const body = { text, follow, form, image, voice: !!voice, task: task !== undefined ? task : (S.chat.task ? S.chat.task.id : null) };
  try {
    const res = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + S.token, "ngrok-skip-browser-warning": "1" }, body: JSON.stringify(body) });
    if (!res.ok) { let m = t("Something went wrong. Please try again."); try { m = (await res.json()).detail || m; } catch {} throw new Error(m); }
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "", bubble = null, lastPaint = 0;
    const paint = (force) => {
      const now = performance.now(); if (!force && now - lastPaint < 60) return; lastPaint = now;
      if (!bubble) { typing.remove(); bubble = botBubble(bot); msgs.append(bubble); }
      else { $(".answer", bubble).innerHTML = render(bot.text); }
      if (window.innerHeight + window.scrollY > document.body.scrollHeight - 260) window.scrollTo(0, document.body.scrollHeight);
    };
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = (block.match(/^event: (.+)$/m) || [])[1]; const dataLine = (block.match(/^data: (.*)$/m) || [])[1];
        const data = dataLine ? JSON.parse(dataLine) : {};
        if (ev === "emergency") { const em = { role: "assistant", text: data.text, emergency: true }; S.chat.messages.push(em); msgs.insertBefore(botBubble(em), typing.isConnected ? typing : null); }
        else if (ev === "delta") { bot.text += data.t; paint(); }
        else if (ev === "done") { Object.assign(bot, { text: data.reply, item_id: data.item_id, saved: data.saved, follow: data.follow, task: data.task, streaming: false }); if (data.saved) S.library = null; }
        else if (ev === "error") throw new Error(data.message);
      }
    }
    if (!bot.text) throw new Error(t("Something went wrong. Please try again."));
    S.chat.messages.push(bot); S.me.stats.answers++;
    drawMessages(msgs);
    msgs.lastElementChild?.scrollIntoView({ block: "start", behavior: "smooth" });
  } catch (e) {
    typing.remove();
    S.chat.messages.push({ role: "assistant", text: "⚠️ " + e.message });
    drawMessages(msgs);
  } finally { S.chat.busy = false; refreshComposer(); }
}
function refreshComposer() {
  const old = $(".composer");
  if (old) old.replaceWith(composer());
}

// ---- voice input
let recorder = null;
async function record(btn, ta) {
  if (recorder) { recorder.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia) { toast(t("Voice input isn't available in this browser.")); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { toast(t("Allow the microphone to speak your question.")); return; }
  const type = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((m) => window.MediaRecorder?.isTypeSupported?.(m)) || "";
  const chunks = [];
  recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const limit = setTimeout(() => recorder && recorder.stop(), 120000);
  recorder.onstop = async () => {
    clearTimeout(limit); stream.getTracks().forEach((tr) => tr.stop()); recorder = null;
    btn.classList.remove("rec"); fill(btn, icon("mic")); btn.setAttribute("aria-busy", "true");
    const fd = new FormData(); fd.append("file", new Blob(chunks, { type: type || "audio/webm" }), type.includes("mp4") ? "voice.m4a" : "voice.webm");
    try {
      const r = await api("/api/transcribe", { method: "POST", form: fd });
      send({ text: r.text, voice: true, label: "🎙️ " + r.text });
    } catch (e) { toast(e.message); }
    btn.removeAttribute("aria-busy");
  };
  recorder.start(); btn.classList.add("rec"); fill(btn, icon("stop")); toast(t("Listening… tap again to finish."));
}

// ---- photos (shrunk on the phone to save data)
function readDataUrl(file) { return new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(file); }); }
async function shrinkImage(file, max = 1600) {
  const url = await readDataUrl(file);
  const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
  const k = Math.min(1, max / Math.max(img.width, img.height));
  const c = h("canvas", { width: Math.round(img.width * k), height: Math.round(img.height * k) });
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  return c.toDataURL("image/jpeg", 0.82);
}

// ================================================================ forms (danger-sign checklist)
async function openForm(tk) {
  let schema;
  try { schema = await api("/api/form/" + tk.form); } catch (e) { toast(e.message); return; }
  const values = {};
  const fields = schema.fields.map((f) => {
    if (f.type === "TextHeading") return h("h2", {}, f.text);
    if (f.type === "TextBody") return h("p", { class: "lead" }, f.text);
    if (f.type === "Submit") return null;
    if (f.init != null) values[f.name] = f.init;
    let control;
    if (f.type === "Dropdown") {
      control = h("select", { class: "input", required: f.required, onchange: (e) => (values[f.name] = e.target.value) },
        h("option", { value: "" }, t("Choose…")), f.options.map((o) => h("option", { value: o.id }, o.title)));
    } else if (f.type === "RadioButtonsGroup" || f.type === "CheckboxGroup") {
      const multi = f.type === "CheckboxGroup";
      if (multi) values[f.name] = [];
      control = h("div", { class: multi ? "" : "chips", style: multi ? "" : "flex-wrap:wrap" }, f.options.map((o) => {
        const b = h("button", { type: "button", class: multi ? "choice" : "chip", "aria-pressed": String(values[f.name] === o.id) },
          multi ? [h("span", { class: "tx" }, h("b", {}, o.title), o.description ? h("small", {}, o.description) : null)] : o.title);
        b.onclick = () => {
          if (multi) { const arr = values[f.name]; const i = arr.indexOf(o.id); i >= 0 ? arr.splice(i, 1) : arr.push(o.id); b.setAttribute("aria-pressed", String(i < 0)); }
          else { values[f.name] = o.id; [...b.parentNode.children].forEach((c) => c.setAttribute("aria-pressed", String(c === b))); }
        };
        return b;
      }));
    } else if (f.type === "TextArea") {
      control = h("textarea", { class: "input", oninput: (e) => (values[f.name] = e.target.value) });
    } else {
      control = h("input", { class: "input", inputmode: f.input === "number" ? "numeric" : "text", required: f.required, oninput: (e) => (values[f.name] = e.target.value) });
    }
    return h("label", { class: "field" }, h("span", {}, f.label, f.required ? "" : h("small", { style: "display:inline;margin-left:6px" }, t("optional"))), control, f.helper ? h("small", {}, f.helper) : null);
  });
  const submit = schema.fields.find((f) => f.type === "Submit");
  const err = h("p", { role: "alert", style: "color:var(--red)" });
  openSheet(h("form", { onsubmit: (e) => {
    e.preventDefault();
    const missing = schema.fields.filter((f) => f.required && (!values[f.name] || (Array.isArray(values[f.name]) && !values[f.name].length)));
    if (missing.length) { err.textContent = t("Please fill in:") + " " + missing.map((f) => f.label).join(", "); return; }
    closeSheet(); send({ form: { kind: tk.form, data: values }, task: tk.id, label: null });
    S._lastForm = { schema, values };
  } }, fields, err, h("button", { class: "btn block", type: "submit" }, submit ? submit.label : t("Done"))));
}
function formSummary(form) {
  const f = S._lastForm; if (!f) return "📝";
  const parts = [];
  for (const fd of f.schema.fields) {
    const v = form.data[fd.name]; if (v == null || v === "" || (Array.isArray(v) && !v.length)) continue;
    const label = (id) => (fd.options || []).find((o) => o.id === id)?.title || id;
    parts.push(Array.isArray(v) ? v.map(label).join(", ") : label(v));
  }
  return "📝 " + parts.join(" · ");
}

// ================================================================ sheets
function openSheet(content) {
  closeSheet();
  const root = $("#sheet-root");
  const sheet = h("div", { class: "sheet", role: "dialog", "aria-modal": "true" }, h("div", { class: "kente-strip" }), content);
  root.append(h("div", { class: "scrim", onclick: closeSheet }), sheet);
  document.addEventListener("keydown", escClose);
  setTimeout(() => sheet.querySelector("input,select,button")?.focus(), 80);
}
function escClose(e) { if (e.key === "Escape") closeSheet(); }
function closeSheet() { const r = $("#sheet-root"); if (r) fill(r); document.removeEventListener("keydown", escClose); }

// ================================================================ library
async function getLibrary() {
  if (!S.library) {
    try { S.library = (await api("/api/library")).items; store.set("akw_lib", JSON.stringify(S.library)); }
    catch (e) { const c = store.get("akw_lib"); if (c) S.library = JSON.parse(c); else throw e; }
  }
  return S.library;
}
async function viewLibrary() {
  const main = h("main", { class: "screen" }, photoHead("writing", "center 35%", "<b>" + esc(t("Saved for you")) + "</b>", t("My library"), t("Talks, visit guides and register summaries, ready offline.")));
  let items;
  try { items = await getLibrary(); } catch (e) { return errorView(e.message, route); }
  const kinds = [...new Set(items.map((i) => i.kind))];
  let filter = null, q = "";
  const listBox = h("div");
  const draw = () => {
    const shown = items.filter((i) => (!filter || i.kind === filter) && (!q || (i.title + " " + i.body).toLowerCase().includes(q)));
    fill(listBox, shown.length ? h("div", { class: "list" }, shown.map((it) => h("a", { class: "row", href: "#/item/" + it.id },
      taskPhoto(it.task, it.id, "thumb"), h("span", { class: "tx" }, h("b", {}, stripEmoji(it.title)), h("small", {}, it.kind + ", " + dateOf(it.ts))))))
      : h("div", { class: "empty" }, h("div", { class: "big" }, sym("sankofa")), h("b", {}, items.length ? t("Nothing matches") : t("Your library is empty")),
        h("span", {}, items.length ? t("Try another word.") : t("Health talks, home visit guides and register summaries are saved here automatically. Tap Save under any other answer to keep it."))));
  };
  if (items.length) {
    main.append(
      h("label", { class: "field", style: "margin:8px 0 10px" }, h("span", { class: "sr" }, t("Search")),
        h("input", { class: "input", type: "search", placeholder: t("Search your library"), oninput: (e) => { q = e.target.value.toLowerCase(); draw(); } })),
      kinds.length > 1 ? h("div", { class: "chips" }, [null, ...kinds].map((k) => {
        const b = h("button", { class: "chip", "aria-pressed": String(filter === k) }, k || t("All"));
        b.onclick = () => { filter = k; [...b.parentNode.children].forEach((c) => c.setAttribute("aria-pressed", String(c === b))); draw(); };
        return b;
      })) : "");
  }
  main.append(listBox);
  draw();
  return main;
}
const dateOf = (ts) => ts ? new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";

async function viewItem(id) {
  let it;
  try { it = await api("/api/items/" + id); }
  catch (e) { it = (S.library || []).find((x) => String(x.id) === String(id)); if (!it) return errorView(e.message, route); }
  const m = { item_id: it.id, saved: it.saved, text: it.body };
  const [pk, pp] = TASK_PHOTO[it.task] || [ROTATE[it.id % ROTATE.length], "center"];
  return h("main", { class: "screen reader" }, photoHead(pk, pp, "<b>" + esc(it.kind) + "</b>", stripEmoji(it.title || it.kind), dateOf(it.ts), { back: "#/library" }),
    h("div", { class: "answer", html: render(it.body) }), answerActions(m));
}

// ================================================================ quiz
async function viewQuiz() {
  const main = h("main", { class: "screen" });
  const draw = () => {
    const qz = S.quiz;
    if (qz && qz.loading) {
      fill(main, bar(t("Quiz")), h("div", { class: "empty" }, h("div", { class: "big" }, sym("nkyinkyim")), h("b", {}, t("Making your quiz…")), h("span", {}, qz.topic || "")));
    } else if (qz && qz.questions && qz.i < qz.questions.length) drawQuestion(main, draw);
    else if (qz && qz.questions) drawResults(main, draw);
    else drawQuizStart(main);
  };
  S._quizDraw = draw;
  draw();
  return main;
}
function drawQuizStart(main) {
  const tk = (S.me.tasks || []).find((x) => x.kind === "quiz") || { examples: [], title: "🎯 Quiz", ask: "" };
  let difficulty = "mixed";
  const input = h("input", { class: "input", placeholder: t("e.g. Fractions, malaria, the water cycle"), enterkeyhint: "go" });
  const go = (topic) => topic.trim() && startQuiz({ topic: topic.trim(), difficulty });
  fill(main, photoHead("coast", "center 55%", "<b>" + esc(t("Five questions")) + "</b>", t("Quiz"), stripTip(tk.ask) || t("Five questions. Tap to answer and see how you did.")),
    h("form", { onsubmit: (e) => { e.preventDefault(); go(input.value); } },
      h("label", { class: "field" }, h("span", {}, t("Topic")), input),
      h("div", { class: "chips", style: "margin-bottom:14px;flex-wrap:wrap" }, tk.examples.map((ex) => h("button", { type: "button", class: "chip", onclick: () => go(ex) }, ex))),
      h("label", { class: "field" }, h("span", {}, t("Difficulty")),
        h("div", { class: "seg" }, [["easier", t("Easier")], ["mixed", t("Mixed")], ["harder", t("Harder")]].map(([id, label]) => {
          const b = h("button", { type: "button", "aria-pressed": String(id === difficulty) }, label);
          b.onclick = () => { difficulty = id; [...b.parentNode.children].forEach((c) => c.setAttribute("aria-pressed", String(c === b))); };
          return b;
        }))),
      h("button", { class: "btn block", type: "submit" }, t("Start quiz"))));
}
async function startQuiz(opts) {
  S.quiz = { loading: true, topic: opts.topic || "" };
  S._quizDraw && S._quizDraw();
  try {
    const qz = await api("/api/quiz", { method: "POST", body: opts });
    S.quiz = { ...qz, i: 0, score: 0, picked: null, missed: [], opts };
  } catch (e) { S.quiz = null; toast(e.message); }
  S._quizDraw && S._quizDraw();
}
function drawQuestion(main, draw) {
  const qz = S.quiz, q = qz.questions[qz.i], n = qz.questions.length;
  const answered = qz.picked != null;
  fill(main, bar(qz.title, { right: h("button", { class: "iconbtn", "aria-label": t("End quiz"), onclick: () => { S.quiz = null; draw(); } }, icon("close")) }),
    h("div", { class: "qtrack", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(n), "aria-valuenow": String(qz.i + (answered ? 1 : 0)) },
      h("i", { style: `width:${((qz.i + (answered ? 1 : 0)) / n) * 100}%` })),
    h("p", { class: "helper" }, `${t("Question")} ${qz.i + 1}/${n}`),
    h("p", { class: "question" }, q.q),
    q.options.map((o, k) => h("button", {
      class: "opt" + (answered && k === q.answer ? " right" : "") + (answered && k === qz.picked && k !== q.answer ? " wrong" : ""),
      disabled: answered, onclick: () => { qz.picked = k; if (k === q.answer) qz.score++; else qz.missed.push(qz.i); draw(); },
    }, h("span", { class: "l" }, "ABC"[k]), o)),
    answered ? [h("div", { class: "why", role: "status" }, h("b", {}, qz.picked === q.answer ? t("✅ *Correct!*").replace(/\*/g, "") : t("Not quite.")), " ", q.why || ""),
      h("button", { class: "btn block", onclick: () => { qz.i++; qz.picked = null; if (qz.i >= n) api("/api/quiz/result", { method: "POST", body: { score: qz.score, total: n } }).catch(() => {}); draw(); } },
        qz.i + 1 < n ? t("Next question") : t("See my score"))] : null);
}
function drawResults(main, draw) {
  const qz = S.quiz, n = qz.questions.length, ratio = qz.score / n, C = 2 * Math.PI * 88;
  const head = qz.score === n ? t("Perfect score! Brilliant work.") : ratio >= 0.8 ? t("Excellent work!") : ratio >= 0.6 ? t("Well done!") : t("Good effort. Keep practising!");
  const ring = h("div", { class: "result" },
    h("div", { class: "ring", style: `--p:${Math.round(ratio * 100)}` }, h("div", {}, h("div", { class: "score" }, `${qz.score}/${n}`), h("div", { class: "pct" }, Math.round(ratio * 100) + "%"))),
    h("div", { class: "stars", "aria-label": `${qz.score} / ${n}` }, qz.questions.map((_, k) => h("span", { class: k < qz.score ? "on" : "" }, sym("nsoromma")))));
  fill(main, bar(qz.title), ring, h("h2", { class: "h", style: "justify-content:center" }, head),
    qz.missed.length ? [h("h2", { class: "h" }, sym("sankofa"), t("To review")), h("div", { class: "list" }, qz.missed.map((k) => {
      const q = qz.questions[k];
      return h("div", { class: "row" }, h("span", { class: "tx" }, h("b", { style: "white-space:normal" }, q.q), h("small", {}, "→ " + q.options[q.answer] + (q.why ? ". " + q.why : ""))));
    }))] : null,
    h("div", { style: "display:grid;gap:10px;margin-top:20px" },
      h("button", { class: "btn block", onclick: () => startQuiz({ ...qz.opts, topic: qz.topic }) }, t("New questions")),
      h("button", { class: "btn block gold", onclick: () => startQuiz({ ...qz.opts, topic: qz.topic, difficulty: "harder" }) }, t("Make it harder")),
      h("button", { class: "btn block line", onclick: () => { S.quiz = null; draw(); } }, t("Choose another topic"))));
}

// ================================================================ nearby facilities
let leafletReady = null;
function loadLeaflet() {
  if (leafletReady) return leafletReady;
  leafletReady = new Promise((res, rej) => {
    document.head.append(h("link", { rel: "stylesheet", href: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" }));
    const s = h("script", { src: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js" });
    s.onload = () => res(window.L); s.onerror = rej; document.head.append(s);
  });
  return leafletReady;
}
function viewNear() {
  const results = h("div");
  const input = h("input", { class: "input", placeholder: t("Town, e.g. Abrepo or Ho"), enterkeyhint: "search" });
  const mapBox = h("div", { id: "map", hidden: true });
  const search = async (params, label) => {
    fill(results, h("div", { class: "typing" }, h("i"), h("i"), h("i")));
    try {
      const r = await api("/api/nearest?" + new URLSearchParams(params));
      if (!r.found) { fill(results, h("div", { class: "empty" }, h("b", {}, t("We couldn't find that place on the map")), t("Try a bigger town nearby, or use your location."))); return; }
      if (!r.results.length) { fill(results, h("div", { class: "empty" }, h("b", {}, t("No mapped facilities nearby")), t("Ask your supervisor or local CHPS compound. In an emergency call 112."))); return; }
      fill(results, 
        h("h2", { class: "h" }, r.place ? t("Near") + " " + r.place : t("Closest to you")),
        h("div", { class: "list" }, r.results.map((f) => h("a", { class: "row", href: `https://maps.google.com/?q=${f.lat},${f.lng}`, target: "_blank", rel: "noopener" },
          h("span", { class: "stamp" }, sym(f.kind === t("Hospital") ? "adinkrahene" : "akoma")),
          h("span", { class: "tx" }, h("b", {}, f.name), h("small", {}, `${f.kind}, ${f.km} km${f.phone ? ", " + f.phone : ""}`)),
          h("span", { class: "end" }, t("Directions"))))),
        h("p", { class: "helper" }, t("From OpenStreetMap. Call ahead if you can.")));
      try {
        const L = await loadLeaflet(); mapBox.hidden = false;
        if (mapBox._map) mapBox._map.remove();
        const map = L.map(mapBox, { zoomControl: false, attributionControl: true }); mapBox._map = map;
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);
        const pts = r.results.map((f) => [f.lat, f.lng]);
        r.results.forEach((f) => L.marker([f.lat, f.lng]).addTo(map).bindPopup(`<b>${esc(f.name)}</b><br>${esc(f.kind)}, ${f.km} km`));
        L.circleMarker(r.center, { radius: 8, color: "#fff", weight: 3, fillColor: getComputedStyle(document.body).getPropertyValue("--brand"), fillOpacity: 1 }).addTo(map);
        map.fitBounds([...pts, r.center], { padding: [30, 30] });
      } catch {}
    } catch (e) { fill(results, h("p", { role: "alert" }, e.message)); }
  };
  return h("main", { class: "screen" }, photoHead("river", "center 55%", "<b>" + esc(t("Clinics and hospitals")) + "</b>", t("Nearest facility"), null, { back: "#/home" }),
    h("div", { class: "sos" }, icon("phone"), h("span", {}, h("b", {}, t("Emergency?")), " ", t("Call an ambulance")), h("a", { href: "tel:112" }, "112")),
    h("button", { class: "btn block", onclick: () => {
      if (!navigator.geolocation) { toast(t("Location isn't available. Type a town instead.")); return; }
      navigator.geolocation.getCurrentPosition((p) => search({ lat: p.coords.latitude, lng: p.coords.longitude }),
        () => toast(t("Location is off. Type a town instead.")), { enableHighAccuracy: true, timeout: 12000 });
    } }, icon("pin"), t("Use my location")),
    h("form", { style: "display:flex;gap:8px;margin:12px 0", onsubmit: (e) => { e.preventDefault(); if (input.value.trim()) search({ place: input.value.trim() }); } },
      input, h("button", { class: "btn ghost", type: "submit", "aria-label": t("Search") }, icon("search"))),
    h("p", { class: "helper" }, t("Your location is used for this search only and is never stored.")),
    mapBox, results);
}

// ================================================================ me / settings
function viewMe() {
  const me = S.me;
  const update = async (body) => {
    try {
      S.me = await api("/api/profile", { method: "POST", body });
      document.body.dataset.role = S.me.mode; await loadStrings(S.me.lang);
      S.chat.messages = body.mode ? null : S.chat.messages; S.chat.task = null;
      toast(t("Saved")); route();
    } catch (e) { toast(e.message); }
  };
  const voiceToggle = h("button", { class: "toggle", role: "switch", "aria-checked": String(me.voice_pref), "aria-label": t("Voice replies"), disabled: !me.voice_available,
    onclick: () => update({ voice: !me.voice_pref }) });
  return h("main", { class: "screen" }, photoHead("mother", "center 30%", "<b>" + esc(me.role_name) + "</b>", t("Me"), me.detail || null),
    h("div", { class: "list" },
      h("div", { class: "row" }, h("span", { class: "stamp", style: "background:var(--brand);color:#fff" }, sym("akoma")),
        h("span", { class: "tx" }, h("b", {}, me.role_name), h("small", {}, me.detail || t("Add your role")))),
      h("div", { class: "row" }, h("span", { class: "stamp" }, sym("adinkrahene")), h("span", { class: "tx" }, h("b", {}, t("Role"))),
        h("select", { class: "input", style: "max-width:55%;min-height:40px", onchange: (e) => e.target.value && update({ detail: e.target.value }) },
          h("option", { value: "" }, me.detail || t("Choose…")), (me.details.health || []).filter((d) => !d.id.endsWith("skip")).map((d) => h("option", { value: d.id }, d.title))))),
    h("h2", { class: "h" }, t("Language")),
    h("div", { class: "seg" }, me.languages.map((l) => h("button", { "aria-pressed": String(me.lang === l.code), onclick: () => update({ lang: l.code }) },
      l.code === "tw" ? "Twi" : l.code === "ee" ? "Ewe" : "English"))),
    h("h2", { class: "h" }, t("Listening")),
    h("div", { class: "list" }, h("div", { class: "row" }, h("span", { class: "stamp" }, icon("speaker")),
      h("span", { class: "tx" }, h("b", {}, t("Voice replies")), h("small", {}, me.voice_available ? t("Play every answer aloud in WhatsApp") : t("Not switched on for this assistant yet"))), voiceToggle)),
    me.profile && Object.keys(me.profile).length ? [h("h2", { class: "h" }, t("What Akwaaba remembers")),
      h("div", { class: "list" }, Object.entries(me.profile).map(([k, v]) => h("div", { class: "row" }, h("span", { class: "tx" }, h("small", {}, t(k)), h("b", { style: "white-space:normal" }, v)))))] : null,
    h("h2", { class: "h" }, t("More")),
    h("div", { class: "list" },
      me.bot_number ? h("a", { class: "row", href: `https://wa.me/${me.bot_number}`, target: "_blank", rel: "noopener" }, h("span", { class: "stamp" }, icon("wa")), h("span", { class: "tx" }, h("b", {}, t("Open Akwaaba on WhatsApp")), h("small", {}, t("Same account, same history")))) : null,
      me.bot_number ? h("button", { class: "row", onclick: () => shareApp() }, h("span", { class: "stamp" }, icon("share")), h("span", { class: "tx" }, h("b", {}, t("Share Akwaaba")), h("small", {}, t("Send it to a colleague"))))  : null,
      h("a", { class: "row", href: "/privacy", target: "_blank" }, h("span", { class: "stamp" }, icon("lock")), h("span", { class: "tx" }, h("b", {}, t("Privacy")), h("small", {}, t("What we keep and why")))),
      h("button", { class: "row", onclick: () => signOut() }, h("span", { class: "stamp" }, icon("out")), h("span", { class: "tx" }, h("b", {}, t("Sign out")))),
      h("button", { class: "row", onclick: () => openSheet(h("div", {}, h("h2", {}, t("Delete my data?")),
        h("p", { class: "lead" }, t("This deletes your history, library and settings in the app and on WhatsApp. It can't be undone.")),
        h("button", { class: "btn block danger", onclick: async () => { await api("/api/forget", { method: "POST" }); closeSheet(); signOut(true); toast(t("Your data has been deleted")); } }, t("Delete everything")),
        h("div", { style: "height:10px" }), h("button", { class: "btn block ghost", onclick: closeSheet }, t("Cancel")))) },
        h("span", { class: "stamp", style: "color:var(--red)" }, icon("trash")), h("span", { class: "tx" }, h("b", { style: "color:var(--red)" }, t("Delete my data"))))),
    h("h2", { class: "h" }, sym("adinkrahene"), t("The symbols in this app")),
    h("p", { class: "helper", style: "margin-top:-4px" }, t("Adinkra symbols from Ghana, each with a meaning.")),
    h("div", { class: "symbols" }, SYMBOL_MEANING.map(([k, name, meaning]) => h("div", {}, h("span", { class: "stamp" }, sym(k)), h("span", {}, h("b", {}, name), h("small", {}, t(meaning)))))),
    h("p", { class: "helper", style: "text-align:center;margin-top:20px" }, t("Signed in as") + " +" + me.phone),
    h("p", { class: "credits" }, "Photos: Ato Aikins, Kojo Kwarteng, Yoel Winkler and Pius Quainoo on Unsplash. The people pictured are not Akwaaba users."));
}
async function shareApp() {
  const url = location.origin + "/app/";
  const text = t("Akwaaba is a free assistant for community health workers in Ghana. Use it on WhatsApp or as an app:");
  if (navigator.share) { try { await navigator.share({ title: "Akwaaba", text, url }); } catch {} }
  else { try { await navigator.clipboard.writeText(text + " " + url); toast(t("Link copied")); } catch { toast(url); } }
}

// ================================================================ start
window.addEventListener("hashchange", route);
window.addEventListener("online", () => { S.online = true; $(".offline")?.remove(); });
window.addEventListener("offline", () => { S.online = false; document.body.append(h("div", { class: "offline" }, t("Offline: showing saved items"))); });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
(async () => {
  if (S.token) { const c = store.get("akw_me"); if (c) { try { S.me = JSON.parse(c); document.body.dataset.role = "health"; await loadStrings(S.me.lang); S.me = null; } catch {} } }
  route();
})();
