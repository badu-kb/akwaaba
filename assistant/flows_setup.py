"""Register Akwaaba's WhatsApp Flows (native in-chat forms) with Meta.

Run once (and again after editing anything in flows/):
    source ~/.akwaaba-venv/bin/activate
    python flows_setup.py            # create/update as drafts and try to publish
    python flows_setup.py --draft    # keep them as drafts (fine for testing)

Needs META_ACCESS_TOKEN (with whatsapp_business_management) and META_WABA_ID
in .env. Writes flows/registry.json, which the assistant reads at start-up.
Drafts can be sent to your test numbers; publishing makes them available to all.
"""

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
FLOWS = HERE / "flows"
REGISTRY = FLOWS / "registry.json"
NAMES = {"setup": "Akwaaba health setup", "danger": "Akwaaba danger signs", "settings": "Akwaaba settings"}
CATEGORIES = {"setup": ["SIGN_UP"], "danger": ["OTHER"], "settings": ["OTHER"]}
VERSIONS = ["7.3", "7.2", "7.1", "7.0", "6.3"]  # newest first; Meta retires old ones

load_dotenv(HERE / ".env")  # values already set in the shell win (handy for a one-off token)
TOKEN = os.getenv("META_ACCESS_TOKEN", "")
WABA = os.getenv("META_WABA_ID", "")
BASE = f"https://graph.facebook.com/{os.getenv('META_GRAPH_VERSION', 'v23.0')}"
H = {"Authorization": f"Bearer {TOKEN}"}


def fail(msg):
    print(f"\n❌ {msg}")
    sys.exit(1)


def call(method, url, **kw):
    r = httpx.request(method, url, headers=H, timeout=60, **kw)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:300]}
    if r.status_code >= 400:
        err = data.get("error", {})
        raise RuntimeError(f"{err.get('message', data)} (code {err.get('code')}, {err.get('error_user_msg', '')})")
    return data


def upload(flow_id, flow):
    return call("POST", f"{BASE}/{flow_id}/assets",
                files={"file": ("flow.json", json.dumps(flow).encode(), "application/json")},
                data={"name": "flow.json", "asset_type": "FLOW_JSON"})


def token_info():
    """Which permissions this token has, and which WhatsApp accounts they cover."""
    try:
        d = call("GET", f"{BASE}/debug_token", params={"input_token": TOKEN}).get("data", {})
    except RuntimeError as e:
        print(f"  (couldn't inspect the token: {e})")
        return [], []
    scopes = d.get("scopes") or []
    targets = []
    for g in d.get("granular_scopes") or []:
        if g.get("scope") in ("whatsapp_business_management", "whatsapp_business_messaging"):
            targets += [t for t in (g.get("target_ids") or []) if t not in targets]
    return scopes, targets


def pick_waba() -> str:
    scopes, targets = token_info()
    print(f"Token permissions: {', '.join(scopes) or 'unknown'}")
    if "whatsapp_business_management" not in scopes:
        print("\u26A0\ufe0f  This token lacks whatsapp_business_management, which Flows need.")
    candidates = [WABA] if WABA else []
    candidates += [t for t in targets if t not in candidates]
    if targets:
        print(f"WhatsApp accounts this token covers: {', '.join(targets)}")
    for waba in candidates:
        try:
            call("GET", f"{BASE}/{waba}/flows", params={"limit": 1})
            if waba != WABA:
                print(f"\u2705 Using WhatsApp account {waba}. Put META_WABA_ID={waba} in .env to save this.")
            return waba
        except RuntimeError as e:
            print(f"  account {waba}: {e}")
    fail("None of the WhatsApp accounts allowed Flows with this token. Paste the lines above to Claude.")


def main():
    global WABA
    draft_only = "--draft" in sys.argv
    if not TOKEN:
        fail("Set META_ACCESS_TOKEN in .env first.")
    WABA = pick_waba()
    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    existing = {f["name"]: f for f in call("GET", f"{BASE}/{WABA}/flows", params={"limit": 100}).get("data", [])}

    for key, title in NAMES.items():
        flow = json.loads((FLOWS / f"{key}.json").read_text())
        print(f"\n▶ {title}")
        current = existing.get(title)
        if current and current.get("status") == "PUBLISHED":
            # Published flows can't be edited: make a fresh draft alongside it.
            title_v = f"{title} {len([n for n in existing if n.startswith(title)]) + 1}"
            current = None
        else:
            title_v = title
        flow_id = current["id"] if current else call("POST", f"{BASE}/{WABA}/flows", json={
            "name": title_v, "categories": CATEGORIES[key]})["id"]

        errors = None
        for v in VERSIONS:
            flow["version"] = v
            res = upload(flow_id, flow)
            errors = res.get("validation_errors") or []
            if not any("version" in (e.get("error") or e.get("message") or "").lower() for e in errors):
                print(f"  uploaded (Flow JSON {v})")
                break
        if errors:
            print("  ⚠️ validation errors:")
            for e in errors:
                print(f"   - {e.get('error_type', '')}: {e.get('message', e)} {e.get('pointers', '')}")
            print("  Left as a draft. Fix the file in flows/ and run again.")
            status = "DRAFT"
        elif draft_only:
            status = "DRAFT"
        else:
            try:
                call("POST", f"{BASE}/{flow_id}/publish")
                status = "PUBLISHED"
                print("  ✅ published")
            except RuntimeError as e:
                status = "DRAFT"
                print(f"  kept as draft (publish said: {e})")
                print("  Drafts still work for your test numbers.")
        registry[key] = {"id": flow_id, "status": status, "name": title_v}

    REGISTRY.write_text(json.dumps(registry, indent=2))
    print(f"\nSaved {REGISTRY.name}. Restart the assistant (bash start.sh) to use the new forms.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        fail(f"Meta returned an error: {e}")
