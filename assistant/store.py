"""SQLite store: conversations and memory survive restarts.

Kept outside the Documents folder by default (iCloud can offload files there,
which makes reads hang).

Per person (keyed by WhatsApp number, never shown in the usage log):
  - settings: role, language, onboarding step, current guided task
  - recent:   the last few exchanges, word for word
  - summary:  a short rolling summary of older conversation
  - profile:  durable facts they've shared (class level, subjects, town...)
Plus: message IDs already handled, and IDs of answers we sent (so a
reaction like a thumbs-up can be linked back to the answer it rates).
"""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_DB = Path(os.getenv("AKWAABA_DB", str(Path.home() / ".akwaaba" / "akwaaba.db")))
RECENT_TTL_S = 7 * 24 * 3600   # word-for-word history kept for a week of silence
HISTORY_CAP = 24               # never more than this many recent messages, word for word
UNSAVED_TTL_S = 30 * 24 * 3600 # answers not saved to the library are deleted after 30 days

FIELDS = {  # column -> default
    "mode": None, "lang": "en", "history": "[]", "welcomed": 0, "pending": None,
    "task": None, "step": None, "profile": "{}", "summary": "", "answers": 0,
    "quiz": "{}", "prefs": "{}", "register": "{}", "last_in": 0,
}
JSON_FIELDS = {"history": list, "profile": dict, "quiz": dict, "prefs": dict, "register": dict}
ITEMS_KEPT = 60        # recent answers kept per person (for PDF / voice / save actions)
LIBRARY_MAX = 50       # saved items per person


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        if str(path) != ":memory:":
            try:
                os.chmod(path, 0o600)     # the database is readable by this account only
            except OSError:
                pass
        self._lock = threading.Lock()
        self._user_locks: dict[str, threading.Lock] = {}
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (user TEXT PRIMARY KEY, updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS seen (msg_id TEXT PRIMARY KEY, ts INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS returns (
                    user TEXT NOT NULL, month TEXT NOT NULL, totals TEXT NOT NULL, ts INTEGER NOT NULL,
                    PRIMARY KEY (user, month));
                CREATE TABLE IF NOT EXISTS followups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, kind TEXT NOT NULL,
                    label TEXT NOT NULL, anchor TEXT NOT NULL, done TEXT DEFAULT '[]', created INTEGER,
                    closed INTEGER DEFAULT 0);
                CREATE INDEX IF NOT EXISTS followups_user ON followups(user, closed);
                CREATE TABLE IF NOT EXISTS teams (code TEXT PRIMARY KEY, owner TEXT NOT NULL UNIQUE, created INTEGER);
                CREATE TABLE IF NOT EXISTS members (user TEXT PRIMARY KEY, code TEXT NOT NULL, joined INTEGER);
                CREATE TABLE IF NOT EXISTS visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, kind TEXT, label TEXT,
                    flagged INTEGER DEFAULT 0, ts INTEGER);
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, lead TEXT, label TEXT, reason TEXT,
                    urgent INTEGER DEFAULT 0, status TEXT DEFAULT 'open', asked INTEGER DEFAULT 0,
                    created INTEGER, closed INTEGER);
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, disease TEXT, ts INTEGER);
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, lead TEXT, disease TEXT NOT NULL,
                    count INTEGER DEFAULT 1, zone TEXT, status TEXT DEFAULT 'new', ts INTEGER);
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, body TEXT, buttons TEXT, ts INTEGER);
                CREATE TABLE IF NOT EXISTS stock_checks (
                    user TEXT NOT NULL, ts INTEGER NOT NULL, counts TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbound (
                    msg_id TEXT PRIMARY KEY, user TEXT, mode TEXT, lang TEXT, task TEXT, ts INTEGER);
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, title TEXT, body TEXT,
                    task TEXT, mode TEXT, lang TEXT, saved INTEGER DEFAULT 0, ts INTEGER);
                CREATE INDEX IF NOT EXISTS items_user ON items(user, saved, id);
                CREATE TABLE IF NOT EXISTS web_codes (
                    user TEXT PRIMARY KEY, code_hash TEXT, expires INTEGER, tries INTEGER DEFAULT 0, sent INTEGER);
                CREATE TABLE IF NOT EXISTS forgets (user TEXT PRIMARY KEY, ts REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS web_sessions (
                    token_hash TEXT PRIMARY KEY, user TEXT NOT NULL, created INTEGER, last_seen INTEGER);
                """
            )
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(sessions)")}
            for col, default in FIELDS.items():  # add any missing columns (safe upgrades)
                if col not in cols:
                    d = "NULL" if default is None else (str(default) if isinstance(default, int) else f"'{default}'")
                    self._db.execute(f"ALTER TABLE sessions ADD COLUMN {col} DEFAULT {d}")
            if "given" not in {r[1] for r in self._db.execute("PRAGMA table_info(followups)")}:
                self._db.execute("ALTER TABLE followups ADD COLUMN given TEXT DEFAULT '{}'")

    def user_lock(self, user: str) -> threading.Lock:
        """One message at a time per person, so quick double-sends can't scramble history."""
        with self._lock:
            return self._user_locks.setdefault(user, threading.Lock())

    # ---------------------------------------------------------------- sessions
    def get(self, user: str) -> dict:
        names = list(FIELDS)
        with self._lock:
            row = self._db.execute(
                f"SELECT {', '.join(names)}, updated_at FROM sessions WHERE user=?", (user,)).fetchone()
        if not row:
            s = {k: v for k, v in FIELDS.items()}
            s.update({k: t() for k, t in JSON_FIELDS.items()}, welcomed=False, new=True)
            return s
        s = dict(zip(names, row[:-1]))
        for k, t in JSON_FIELDS.items():
            try:
                s[k] = json.loads(s[k]) if s[k] else t()
            except (TypeError, json.JSONDecodeError):
                s[k] = t()
        s["welcomed"] = bool(s["welcomed"])
        s["summary"] = s["summary"] or ""
        s["answers"] = int(s["answers"] or 0)
        s["new"] = False
        if time.time() - row[-1] > RECENT_TTL_S and s["history"]:
            s["history"] = []  # long silence: drop word-for-word turns, keep summary + profile
            with self._lock:   # and delete them from the database too, not just from view
                self._db.execute("UPDATE sessions SET history='[]' WHERE user=?", (user,))
        if s["mode"] and s["mode"] != "health":
            # Akwaaba is health-only now. Earlier teacher accounts move over and lose
            # their teaching profile, conversation and in-progress task or quiz.
            s["profile"] = {k: v for k, v in s["profile"].items()
                            if k not in ("class_level", "class_size", "subjects", "school_type")}
            s.update(mode="health", task=None, history=[], summary="", step=None, quiz={})
        return s

    def save(self, user: str, s: dict) -> None:
        if isinstance(s.get("history"), list) and len(s["history"]) > HISTORY_CAP:
            s["history"] = s["history"][-HISTORY_CAP:]  # a hard limit, whether or not the summary step succeeded
        vals = {k: s.get(k, d) for k, d in FIELDS.items()}
        for k, t in JSON_FIELDS.items():
            vals[k] = json.dumps(s.get(k) or t(), ensure_ascii=False)
        vals["welcomed"] = int(bool(s.get("welcomed")))
        cols = ["user", *vals, "updated_at"]
        updates = ", ".join(f"{c}=excluded.{c}" for c in [*vals, "updated_at"])
        with self._lock:
            self._db.execute(
                f"INSERT INTO sessions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                f"ON CONFLICT(user) DO UPDATE SET {updates}",
                (user, *vals.values(), int(time.time())))

    def forgotten_since(self, user: str, since: float) -> bool:
        """True if FORGET ME happened after `since`, so background work started earlier must not save."""
        with self._lock:
            row = self._db.execute("SELECT ts FROM forgets WHERE user=?", (user,)).fetchone()
        return bool(row and row[0] >= since)

    def update_memory(self, user: str, profile: dict | None = None, summary: str | None = None,
                      drop_oldest: int = 0, since: float | None = None) -> None:
        """Background memory updates, applied without overwriting newer turns, and never
        after the person has asked to be forgotten (or for someone with no session)."""
        with self.user_lock(user):
            if since is not None and self.forgotten_since(user, since):
                return
            with self._lock:
                exists = self._db.execute("SELECT 1 FROM sessions WHERE user=?", (user,)).fetchone()
            if not exists:
                return
            s = self.get(user)
            if profile is not None:
                s["profile"] = profile
            if summary is not None:
                s["summary"] = summary
            if drop_oldest:
                s["history"] = s["history"][drop_oldest:]
            self.save(user, s)

    def forget(self, user: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO forgets VALUES (?, ?)", (user, time.time()))
            self._db.execute("DELETE FROM sessions WHERE user=?", (user,))
            self._db.execute("DELETE FROM outbound WHERE user=?", (user,))
            self._db.execute("DELETE FROM web_sessions WHERE user=?", (user,))
            self._db.execute("DELETE FROM web_codes WHERE user=?", (user,))
            self._db.execute("DELETE FROM items WHERE user=?", (user,))
            self._db.execute("DELETE FROM returns WHERE user=?", (user,))
            self._db.execute("DELETE FROM followups WHERE user=?", (user,))
            self._db.execute("DELETE FROM stock_checks WHERE user=?", (user,))
            for t in ("members", "visits", "alerts", "outbox", "signals"):
                self._db.execute(f"DELETE FROM {t} WHERE user=?", (user,))
            self._db.execute("DELETE FROM referrals WHERE user=?", (user,))
            self._db.execute("UPDATE referrals SET lead=NULL WHERE lead=?", (user,))
            self._db.execute("UPDATE signals SET lead=NULL WHERE lead=?", (user,))
            code = self._db.execute("SELECT code FROM teams WHERE owner=?", (user,)).fetchone()
            if code:
                self._db.execute("DELETE FROM members WHERE code=?", (code[0],))
                self._db.execute("DELETE FROM teams WHERE owner=?", (user,))
            self._db.execute("DELETE FROM web_codes WHERE user=?", (user,))
            self._db.execute("DELETE FROM web_sessions WHERE user=?", (user,))

    # ---------------------------------------------------------------- app sign-in
    @staticmethod
    def _h(value: str) -> str:
        import hashlib
        return hashlib.sha256(value.encode()).hexdigest()

    def new_code(self, user: str, ttl: int = 600) -> str | None:
        """A fresh 6-digit sign-in code, or None if one was made in the last 30 seconds."""
        import secrets
        now = int(time.time())
        with self._lock:
            row = self._db.execute("SELECT sent FROM web_codes WHERE user=?", (user,)).fetchone()
            if row and row[0] and now - row[0] < 30:
                return None
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._db.execute("INSERT OR REPLACE INTO web_codes VALUES (?, ?, ?, 0, ?)",
                             (user, self._h(f"{user}:{code}"), now + ttl, now))
        return code

    def check_code(self, user: str, code: str) -> bool:
        now = int(time.time())
        with self._lock:
            row = self._db.execute("SELECT code_hash, expires, tries FROM web_codes WHERE user=?", (user,)).fetchone()
            if not row or row[1] < now or row[2] >= 5:
                return False
            import hmac
            if not hmac.compare_digest(self._h(f"{user}:{code.strip()}"), row[0] or ""):
                self._db.execute("UPDATE web_codes SET tries=tries+1 WHERE user=?", (user,))
                return False
            self._db.execute("DELETE FROM web_codes WHERE user=?", (user,))
            return True

    def new_session(self, user: str) -> str:
        import secrets
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._lock:
            self._db.execute("INSERT INTO web_sessions VALUES (?, ?, ?, ?)", (self._h(token), user, now, now))
        return token

    def session_user(self, token: str, max_age: int = 120 * 86400) -> str | None:
        if not token:
            return None
        now = int(time.time())
        with self._lock:
            row = self._db.execute("SELECT user, last_seen FROM web_sessions WHERE token_hash=?",
                                   (self._h(token),)).fetchone()
            if not row or now - row[1] > max_age:
                return None
            if now - row[1] > 3600:
                self._db.execute("UPDATE web_sessions SET last_seen=? WHERE token_hash=?", (now, self._h(token)))
        return row[0]

    def end_session(self, token: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM web_sessions WHERE token_hash=?", (self._h(token),))

    def recent_items(self, user: str, limit: int = 60) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, body, task, saved FROM items WHERE user=? ORDER BY id DESC LIMIT ?",
                                    (user, limit)).fetchall()
        return [dict(zip(("id", "body", "task", "saved"), r)) for r in rows]

    def library_all(self, user: str, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, title, body, task, mode, lang, ts FROM items WHERE user=? AND saved=1 "
                                    "ORDER BY id DESC LIMIT ?", (user, limit)).fetchall()
        return [dict(zip(("id", "title", "body", "task", "mode", "lang", "ts"), r)) for r in rows]

    # ---------------------------------------------------------------- answers & library
    def save_return(self, user: str, month: str, totals: dict) -> None:
        """Last compiled totals per month (counts only), so next month can be compared."""
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO returns (user, month, totals, ts) VALUES (?,?,?,?)",
                             (user, month, json.dumps(totals), int(time.time())))

    def previous_return(self, user: str, month: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT totals FROM returns WHERE user=? AND month<? ORDER BY month DESC LIMIT 1",
                                   (user, month)).fetchone()
        return json.loads(row[0]) if row else None

    # ---------------------------------------------------------------- follow-ups (register numbers, never names)
    _FU = ("id", "kind", "label", "anchor", "done", "created", "closed", "given")

    def _fu(self, row):
        fu = dict(zip(self._FU, row))
        fu["done"] = json.loads(fu["done"] or "[]")
        fu["given"] = json.loads(fu["given"] or "{}")  # visit key -> date given, or "card" if confirmed from the card
        return fu

    def add_followup(self, user: str, kind: str, label: str, anchor: str, done: list[str]) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO followups (user, kind, label, anchor, done, created) VALUES (?,?,?,?,?,?)",
                (user, kind, label, anchor, json.dumps(done), int(time.time())))
            return cur.lastrowid

    def followups(self, user: str, open_only: bool = True) -> list[dict]:
        q = f"SELECT {', '.join(self._FU)} FROM followups WHERE user=?" + (" AND closed=0" if open_only else "")
        with self._lock:
            rows = self._db.execute(q + " ORDER BY id", (user,)).fetchall()
        return [self._fu(r) for r in rows]

    def followup(self, user: str, fid: int) -> dict | None:
        with self._lock:
            row = self._db.execute(f"SELECT {', '.join(self._FU)} FROM followups WHERE user=? AND id=?",
                                   (user, fid)).fetchone()
        return self._fu(row) if row else None

    def update_followup(self, user: str, fid: int, done: list[str] | None = None, closed: bool | None = None,
                        given: dict | None = None) -> None:
        with self._lock:
            if done is not None:
                self._db.execute("UPDATE followups SET done=? WHERE user=? AND id=?", (json.dumps(done), user, fid))
            if given is not None:
                self._db.execute("UPDATE followups SET given=? WHERE user=? AND id=?", (json.dumps(given), user, fid))
            if closed is not None:
                self._db.execute("UPDATE followups SET closed=? WHERE user=? AND id=?", (int(closed), user, fid))

    def users_with_followups(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT DISTINCT user FROM followups WHERE closed=0")]

    # ---------------------------------------------------------------- retention and export
    def purge(self, now: float | None = None) -> dict:
        """Deletes what we said we wouldn't keep: word-for-word history after a week of silence,
        unsaved answers after 30 days, and old message bookkeeping. Runs every 15 minutes."""
        now = now or time.time()
        with self._lock:
            n = {"history": self._db.execute("UPDATE sessions SET history='[]' WHERE updated_at < ? AND history != '[]'",
                                             (int(now - RECENT_TTL_S),)).rowcount,
                 "items": self._db.execute("DELETE FROM items WHERE saved=0 AND ts < ?",
                                           (int(now - UNSAVED_TTL_S),)).rowcount,
                 "outbound": self._db.execute("DELETE FROM outbound WHERE ts < ?", (int(now - UNSAVED_TTL_S),)).rowcount,
                 "seen": self._db.execute("DELETE FROM seen WHERE ts < ?", (int(now - 7 * 24 * 3600),)).rowcount}
        return n

    _EXPORT = ("items", "followups", "visits", "referrals", "alerts", "signals", "stock_checks", "returns", "outbox", "members")

    def export(self, user: str) -> dict:
        """Everything stored about this person, for MY DATA."""
        out = {}
        with self._lock:
            row = self._db.execute(f"SELECT {', '.join(FIELDS)} FROM sessions WHERE user=?", (user,)).fetchone()
            out["session"] = dict(zip(FIELDS, row)) if row else {}
            for t in self._EXPORT:
                cur = self._db.execute(f"SELECT * FROM {t} WHERE user=?", (user,))
                cols = [c[0] for c in cur.description]
                out[t] = [dict(zip(cols, r)) for r in cur.fetchall()]
            team = self._db.execute("SELECT code, created FROM teams WHERE owner=?", (user,)).fetchone()
            out["team_you_lead"] = {"code": team[0], "created": team[1]} if team else None
        for k in JSON_FIELDS:
            v = out["session"].get(k)
            if isinstance(v, str):
                try:
                    out["session"][k] = json.loads(v)
                except json.JSONDecodeError:
                    pass
        return out

    # ---------------------------------------------------------------- teams (a lead and the people who joined with her code)
    def team_code(self, owner: str, create: bool = True) -> str | None:
        import secrets as _s
        with self._lock:
            row = self._db.execute("SELECT code FROM teams WHERE owner=?", (owner,)).fetchone()
            if row or not create:
                return row[0] if row else None
            while True:
                code = f"AK-{_s.randbelow(9000) + 1000}"
                if not self._db.execute("SELECT 1 FROM teams WHERE code=?", (code,)).fetchone():
                    break
            self._db.execute("INSERT INTO teams (code, owner, created) VALUES (?,?,?)", (code, owner, int(time.time())))
            return code

    def team_owner(self, code: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT owner FROM teams WHERE code=?", (code,)).fetchone()
        return row[0] if row else None

    def join_team(self, user: str, code: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO members (user, code, joined) VALUES (?,?,?)",
                             (user, code, int(time.time())))

    def leave_team(self, user: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM members WHERE user=?", (user,))

    def lead_of(self, user: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT t.owner FROM members m JOIN teams t ON t.code=m.code WHERE m.user=?",
                                   (user,)).fetchone()
        return row[0] if row else None

    def team_members(self, owner: str) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute(
                "SELECT m.user FROM members m JOIN teams t ON t.code=m.code WHERE t.owner=? ORDER BY m.joined", (owner,))]

    # ---------------------------------------------------------------- visits, referrals, alerts (numbers, never names)
    def add_visit(self, user: str, kind: str, label: str, flagged: bool) -> None:
        with self._lock:
            self._db.execute("INSERT INTO visits (user, kind, label, flagged, ts) VALUES (?,?,?,?,?)",
                             (user, kind, label, int(flagged), int(time.time())))

    def count_since(self, table: str, user: str, since: int, extra: str = "") -> int:
        assert table in ("visits", "alerts", "referrals")
        col = "created" if table == "referrals" else "ts"
        with self._lock:
            return self._db.execute(f"SELECT COUNT(*) FROM {table} WHERE user=? AND {col}>=? {extra}",
                                    (user, since)).fetchone()[0]

    _RF = ("id", "user", "lead", "label", "reason", "urgent", "status", "asked", "created", "closed")

    def add_referral(self, user: str, lead: str | None, label: str, reason: str, urgent: bool) -> int:
        with self._lock:
            return self._db.execute(
                "INSERT INTO referrals (user, lead, label, reason, urgent, created) VALUES (?,?,?,?,?,?)",
                (user, lead, label, reason, int(urgent), int(time.time()))).lastrowid

    def referral(self, rid: int) -> dict | None:
        with self._lock:
            row = self._db.execute(f"SELECT {', '.join(self._RF)} FROM referrals WHERE id=?", (rid,)).fetchone()
        return dict(zip(self._RF, row)) if row else None

    def referrals_for(self, user: str, open_only: bool = True) -> list[dict]:
        q = f"SELECT {', '.join(self._RF)} FROM referrals WHERE (user=? OR lead=?)" + (" AND status='open'" if open_only else "")
        with self._lock:
            return [dict(zip(self._RF, r)) for r in self._db.execute(q + " ORDER BY id DESC LIMIT 30", (user, user))]

    def referrals_to_check(self, older_than: int) -> list[dict]:
        with self._lock:
            return [dict(zip(self._RF, r)) for r in self._db.execute(
                f"SELECT {', '.join(self._RF)} FROM referrals WHERE status='open' AND asked=0 AND created<?", (older_than,))]

    def update_referral(self, rid: int, status: str | None = None, asked: bool | None = None) -> None:
        with self._lock:
            if status:
                self._db.execute("UPDATE referrals SET status=?, closed=? WHERE id=?", (status, int(time.time()), rid))
            if asked is not None:
                self._db.execute("UPDATE referrals SET asked=? WHERE id=?", (int(asked), rid))

    def add_alert(self, user: str, disease: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO alerts (user, disease, ts) VALUES (?,?,?)", (user, disease, int(time.time())))

    # ---------------------------------------------------------------- surveillance signals (a condition, a count, a zone: never a name)
    def add_signal(self, user: str, lead: str | None, disease: str, count: int, zone: str) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO signals (user, lead, disease, count, zone, status, ts) VALUES (?,?,?,?,?,?,?)",
                                   (user, lead, disease, max(1, min(999, int(count))), zone[:60], "new", int(time.time())))
            return cur.lastrowid

    def signals(self, lead: str | None = None, since: int = 0) -> list[dict]:
        """Signals for one supervisor's team (their own included), or every signal when lead is None."""
        q = "SELECT id, user, disease, count, zone, status, ts FROM signals WHERE ts>=?"
        args: tuple = (since,)
        if lead is not None:
            q += " AND (lead=? OR user=?)"
            args += (lead, lead)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY ts DESC", args).fetchall()
        return [dict(zip(("id", "user", "disease", "count", "zone", "status", "ts"), r)) for r in rows]

    def set_signal_status(self, signal_id: int, status: str, lead: str | None = None) -> bool:
        if status not in ("new", "verified", "discarded"):
            return False
        q, args = "UPDATE signals SET status=? WHERE id=?", (status, signal_id)
        if lead is not None:
            q += " AND (lead=? OR user=?)"
            args += (lead, lead)
        with self._lock:
            return self._db.execute(q, args).rowcount > 0

    # ---------------------------------------------------------------- outbox: messages waiting for someone's next message
    def queue(self, user: str, body: str, buttons: list | None = None) -> None:
        with self._lock:
            self._db.execute("INSERT INTO outbox (user, body, buttons, ts) VALUES (?,?,?,?)",
                             (user, body, json.dumps(buttons or []), int(time.time())))

    def take_outbox(self, user: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, body, buttons FROM outbox WHERE user=? ORDER BY id", (user,)).fetchall()
            self._db.execute("DELETE FROM outbox WHERE user=?", (user,))
        return [{"body": b, "buttons": [tuple(x) for x in json.loads(bt or "[]")]} for _, b, bt in rows]

    def outbox_count(self, user: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM outbox WHERE user=?", (user,)).fetchone()[0]

    # ---------------------------------------------------------------- stock checks (counts only)
    def save_stock(self, user: str, counts: dict) -> None:
        with self._lock:
            self._db.execute("INSERT INTO stock_checks (user, ts, counts) VALUES (?,?,?)",
                             (user, int(time.time()), json.dumps(counts)))

    def last_stock(self, user: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT counts FROM stock_checks WHERE user=? ORDER BY ts DESC LIMIT 1",
                                   (user,)).fetchone()
        return json.loads(row[0]) if row else None

    def add_item(self, user: str, title: str, body: str, task: str | None, mode: str, lang: str,
                 saved: bool = False) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO items (user, title, body, task, mode, lang, saved, ts) VALUES (?,?,?,?,?,?,?,?)",
                (user, title, body, task, mode, lang, int(saved), int(time.time())))
            item_id = cur.lastrowid
            self._db.execute(  # keep recent unsaved answers only
                "DELETE FROM items WHERE user=? AND saved=0 AND id NOT IN "
                "(SELECT id FROM items WHERE user=? AND saved=0 ORDER BY id DESC LIMIT ?)",
                (user, user, ITEMS_KEPT))
        return item_id

    def item(self, user: str, item_id) -> dict | None:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return None
        with self._lock:
            row = self._db.execute("SELECT id, title, body, task, mode, lang, saved, ts FROM items "
                                   "WHERE user=? AND id=?", (user, item_id)).fetchone()
        return dict(zip(("id", "title", "body", "task", "mode", "lang", "saved", "ts"), row)) if row else None

    def update_item_body(self, user: str, item_id: int, title: str, body: str) -> None:
        with self._lock:
            self._db.execute("UPDATE items SET title=?, body=?, ts=? WHERE user=? AND id=?",
                             (title, body, int(time.time()), user, item_id))

    def set_saved(self, user: str, item_id: int, saved: bool) -> None:
        with self._lock:
            self._db.execute("UPDATE items SET saved=? WHERE user=? AND id=?", (int(saved), user, item_id))
            if saved:  # library is capped: drop the oldest saved item beyond the limit
                self._db.execute(
                    "UPDATE items SET saved=0 WHERE user=? AND saved=1 AND id NOT IN "
                    "(SELECT id FROM items WHERE user=? AND saved=1 ORDER BY id DESC LIMIT ?)",
                    (user, user, LIBRARY_MAX))

    def library(self, user: str, limit: int = 8, offset: int = 0) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, title, task, ts FROM items WHERE user=? AND saved=1 "
                                    "ORDER BY id DESC LIMIT ? OFFSET ?", (user, limit, offset)).fetchall()
        return [dict(zip(("id", "title", "task", "ts"), r)) for r in rows]

    def library_count(self, user: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM items WHERE user=? AND saved=1", (user,)).fetchone()[0]

    # ---------------------------------------------------------------- dedupe
    def first_time_seen(self, msg_id: str) -> bool:
        if not msg_id:
            return True
        now = int(time.time())
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO seen (msg_id, ts) VALUES (?, ?)", (msg_id, now))
            if cur.rowcount and now % 50 == 0:
                self._db.execute("DELETE FROM seen WHERE ts < ?", (now - 7 * 86400,))
            return cur.rowcount == 1

    # ---------------------------------------------------------------- answers we sent
    def remember_outbound(self, msg_id: str, user: str, mode: str, lang: str, task: str | None) -> None:
        if not msg_id:
            return
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO outbound VALUES (?, ?, ?, ?, ?, ?)",
                             (msg_id, user, mode, lang, task, int(time.time())))

    def outbound(self, msg_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT mode, lang, task FROM outbound WHERE msg_id=?", (msg_id,)).fetchone()
        return dict(zip(("mode", "lang", "task"), row)) if row else None

    def count_profiles(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM sessions WHERE profile NOT IN ('{}', '')").fetchone()[0]
