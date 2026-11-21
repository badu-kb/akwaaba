"""Thin client for Meta's WhatsApp Cloud API with retries and clear errors."""

import logging
import time

import re
from urllib.parse import urlparse

import httpx

from i18n import fit, tr

log = logging.getLogger("akwaaba")

TEXT_LIMIT = 3500        # Meta allows 4096 per text message; leave headroom
BUTTON_BODY_LIMIT = 1024  # Meta's limit for interactive message bodies
BUTTON_TITLE_LIMIT = 20
MAX_MEDIA_BYTES = 10 * 1024 * 1024
MEDIA_HOSTS = (".fbsbx.com", ".facebook.com", ".whatsapp.net", ".fbcdn.net")


class TokenError(RuntimeError):
    """The Meta access token is missing, expired or revoked."""


class SendError(RuntimeError):
    pass


class WhatsApp:
    def __init__(self, token: str, phone_number_id: str, graph_version: str = "v23.0",
                 client: httpx.Client | None = None):
        self.token = token
        self.phone_number_id = phone_number_id
        self.base = f"https://graph.facebook.com/{graph_version}"
        self.http = client or httpx.Client(timeout=30)

    # ---------------------------------------------------------------- core
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        """Retries network errors, rate limits and Meta server errors with backoff."""
        last = None
        for attempt in range(4):
            try:
                resp = self.http.request(method, url, headers=self._headers(), **kw)
            except httpx.HTTPError as e:
                last = e
                log.warning("Network problem talking to Meta (attempt %d): %s", attempt + 1, e)
            else:
                if resp.status_code < 400:
                    return resp
                err = _error(resp)
                if resp.status_code == 401 or err.get("code") == 190:
                    log.error(
                        "META ACCESS TOKEN REJECTED (%s). It has expired or been replaced. "
                        "Generate a new one (a system user token never expires), put it in "
                        ".env as META_ACCESS_TOKEN, and restart.", err.get("message", ""))
                    raise TokenError(err.get("message", "token rejected"))
                if resp.status_code == 429 or resp.status_code >= 500 or err.get("code") in (4, 80007, 130429):
                    last = SendError(f"{resp.status_code}: {err.get('message')}")
                    log.warning("Meta busy or rate limited (attempt %d): %s", attempt + 1, err.get("message"))
                else:
                    raise SendError(f"{resp.status_code}: {err.get('message')} "
                                    f"(code {err.get('code')}) {err.get('error_data', '')}")
            time.sleep(min(8, 0.5 * 2 ** attempt))
        raise SendError(f"Gave up after retries: {last}")

    def _send(self, to: str, payload: dict) -> str:
        """Sends one message; returns its WhatsApp message ID (or "" if Meta didn't say)."""
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **payload}
        resp = self._request("POST", f"{self.base}/{self.phone_number_id}/messages", json=body)
        try:
            return (resp.json().get("messages") or [{}])[0].get("id", "")
        except Exception:
            return ""

    # ---------------------------------------------------------------- messages
    def send_text(self, to: str, text: str, preview_url: bool = False) -> str:
        text = tr(text)
        last = ""
        for chunk in split_text(text, TEXT_LIMIT):
            last = self._send(to, {"type": "text", "text": {"body": chunk, "preview_url": preview_url}})
        return last

    def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]],
                     header: str | None = None, footer: str | None = None,
                     image_url: str | None = None) -> str:
        """Up to 3 tap-to-reply buttons. A long body is sent as text first, then
        the buttons under a short prompt, so buttons are never lost."""
        body = tr(body)
        if len(body) > BUTTON_BODY_LIMIT:
            first = self.send_text(to, body)
            self.send_buttons(to, "What next?", buttons, footer=footer)
            return first
        interactive = {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": bid, "title": fit(title, BUTTON_TITLE_LIMIT)[:BUTTON_TITLE_LIMIT]}}
                for bid, title in buttons[:3]
            ]},
        }
        if image_url:
            interactive["header"] = {"type": "image", "image": {"link": image_url}}
        elif header:
            interactive["header"] = {"type": "text", "text": fit(header, 60)[:60]}
        if footer:
            interactive["footer"] = {"text": fit(footer, 60)[:60]}
        try:
            return self._send(to, {"type": "interactive", "interactive": interactive})
        except SendError:
            if not image_url:
                raise
            log.warning("Image header failed; sending without it")
            return self.send_buttons(to, body, buttons, header=header, footer=footer)

    def send_template(self, to: str, name: str, lang: str, params: list[str] | None = None) -> str:
        """An approved message template: the only way to start a chat after WhatsApp's
        24-hour window. Create it in WhatsApp Manager (see README); English is used
        unless a template in the person's language exists (WA_TEMPLATE_LANGS=en,tw,ee)."""
        import os
        langs = {x.strip() for x in os.getenv("WA_TEMPLATE_LANGS", "en").split(",")}
        code = lang if lang in langs else "en"
        tpl = {"name": name, "language": {"code": code}}
        if params:
            tpl["components"] = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]
        return self._send(to, {"type": "template", "template": tpl})

    def send_list(self, to: str, body: str, button: str,
                  sections: list[tuple[str, list[tuple[str, str, str]]]],
                  header: str | None = None, footer: str | None = None) -> str:
        """A tappable menu of up to 10 rows: sections = [(title, [(id, title, description)])]."""
        interactive = {
            "type": "list",
            "body": {"text": tr(body)[:BUTTON_BODY_LIMIT]},
            "action": {"button": fit(button, 20)[:20], "sections": [
                {"title": fit(title, 24)[:24], "rows": [
                    {"id": rid, "title": fit(rt, 24)[:24], **({"description": fit(rd, 72)[:72]} if rd else {})}
                    for rid, rt, rd in rows]}
                for title, rows in sections]},
        }
        if header:
            interactive["header"] = {"type": "text", "text": fit(header, 60)[:60]}
        if footer:
            interactive["footer"] = {"text": fit(footer, 60)[:60]}
        return self._send(to, {"type": "interactive", "interactive": interactive})

    def send_flow(self, to: str, body: str, cta: str, flow_id: str, flow_token: str, screen: str,
                  mode: str = "published", header: str | None = None, footer: str | None = None,
                  data: dict | None = None) -> str:
        """Open a native WhatsApp form (a Flow) from a button in the chat."""
        interactive = {
            "type": "flow",
            "body": {"text": tr(body)[:BUTTON_BODY_LIMIT]},
            "action": {"name": "flow", "parameters": {
                "flow_message_version": "3", "flow_token": flow_token, "flow_id": flow_id,
                "flow_cta": fit(cta, 30)[:30], "mode": mode, "flow_action": "navigate",
                "flow_action_payload": {"screen": screen, **({"data": data} if data else {})}}},
        }
        if header:
            interactive["header"] = {"type": "text", "text": fit(header, 60)[:60]}
        if footer:
            interactive["footer"] = {"text": fit(footer, 60)[:60]}
        return self._send(to, {"type": "interactive", "interactive": interactive})

    def send_image(self, to: str, url: str, caption: str = "") -> str:
        """A picture message. Best effort: a failed picture never blocks the conversation."""
        try:
            return self._send(to, {"type": "image", "image": {"link": url, **({"caption": tr(caption)} if caption else {})}})
        except SendError as e:
            log.warning("Image couldn't be sent: %s", e)
            return ""

    def send_link_button(self, to: str, body: str, label: str, url: str, footer: str | None = None) -> str:
        """A message with a single tappable link button."""
        interactive = {"type": "cta_url", "body": {"text": tr(body)[:BUTTON_BODY_LIMIT]},
                       "action": {"name": "cta_url", "parameters": {"display_text": fit(label, 20)[:20], "url": url}}}
        if footer:
            interactive["footer"] = {"text": fit(footer, 60)[:60]}
        return self._send(to, {"type": "interactive", "interactive": interactive})

    # ---------------------------------------------------------------- media we create
    def upload_media(self, data: bytes, mime: str, filename: str) -> str:
        """Uploads a file to Meta and returns its media ID (valid for 30 days)."""
        resp = self._request("POST", f"{self.base}/{self.phone_number_id}/media",
                             data={"messaging_product": "whatsapp", "type": mime},
                             files={"file": (filename, data, mime)})
        media_id = resp.json().get("id", "")
        if not media_id:
            raise SendError("upload returned no media id")
        return media_id

    def send_voice(self, to: str, ogg: bytes) -> str:
        """A voice note (plays inline with the waveform, like one recorded on a phone)."""
        media_id = self.upload_media(ogg, "audio/ogg", "akwaaba.ogg")
        try:
            return self._send(to, {"type": "audio", "audio": {"id": media_id, "voice": True}})
        except SendError:  # older API versions don't know "voice": send as a plain audio file
            return self._send(to, {"type": "audio", "audio": {"id": media_id}})

    def send_document(self, to: str, data: bytes, filename: str, caption: str = "",
                      mime: str = "application/pdf") -> str:
        media_id = self.upload_media(data, mime, filename)
        return self._send(to, {"type": "document", "document": {
            "id": media_id, "filename": filename, **({"caption": tr(caption)} if caption else {})}})

    def send_image_bytes(self, to: str, png: bytes, caption: str = "") -> str:
        media_id = self.upload_media(png, "image/png", "akwaaba.png")
        return self._send(to, {"type": "image", "image": {"id": media_id, **({"caption": tr(caption)} if caption else {})}})

    def send_location(self, to: str, lat: float, lng: float, name: str = "", address: str = "") -> str:
        """A map pin people can tap for directions."""
        return self._send(to, {"type": "location", "location": {
            "latitude": lat, "longitude": lng, **({"name": name} if name else {}),
            **({"address": address} if address else {})}})

    def send_location_request(self, to: str, body: str) -> str:
        """A message with a 'Send location' button."""
        return self._send(to, {"type": "interactive", "interactive": {
            "type": "location_request_message", "body": {"text": tr(body)[:BUTTON_BODY_LIMIT]},
            "action": {"name": "send_location"}}})

    def react(self, to: str, message_id: str, emoji: str) -> None:
        """React to the person's message (e.g. \U0001F440 while reading a photo). Best effort."""
        if not message_id:
            return
        try:
            self._send(to, {"type": "reaction", "reaction": {"message_id": message_id, "emoji": emoji}})
        except TokenError:
            raise
        except Exception as e:
            log.debug("Reaction failed: %s", e)

    def typing(self, message_id: str) -> None:
        """Blue ticks plus a 'typing…' bubble (lasts ~25s or until we reply). Best effort."""
        if not message_id:
            return
        try:
            self._request("POST", f"{self.base}/{self.phone_number_id}/messages", json={
                "messaging_product": "whatsapp", "status": "read", "message_id": message_id,
                "typing_indicator": {"type": "text"},
            })
        except TokenError:
            raise
        except Exception as e:  # never let a cosmetic call break the reply
            log.debug("Typing indicator failed: %s", e)

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(media_id or "")):
            raise ValueError("unexpected media id")
        meta = self._request("GET", f"{self.base}/{media_id}").json()
        host = (urlparse(str(meta.get("url") or "")).hostname or "").lower()
        if not host.endswith(MEDIA_HOSTS) or not str(meta["url"]).startswith("https://"):
            raise ValueError("media link is not on a Meta host")
        if int(meta.get("file_size") or 0) > MAX_MEDIA_BYTES:
            raise ValueError("file too large")
        data = self._request("GET", meta["url"], follow_redirects=True)
        if len(data.content) > MAX_MEDIA_BYTES:
            raise ValueError("file too large")
        return data.content, (meta.get("mime_type") or "").split(";")[0]

    def display_number(self) -> str:
        info = self._request("GET", f"{self.base}/{self.phone_number_id}",
                             params={"fields": "display_phone_number"}).json()
        return "".join(ch for ch in info.get("display_phone_number", "") if ch.isdigit())

    def check(self) -> str:
        """Confirms the token and phone number ID work. Returns the display number."""
        info = self._request("GET", f"{self.base}/{self.phone_number_id}",
                             params={"fields": "display_phone_number,verified_name"}).json()
        return f"{info.get('display_phone_number', '?')} ({info.get('verified_name', '')})"


def _error(resp: httpx.Response) -> dict:
    try:
        return resp.json().get("error", {}) or {}
    except Exception:
        return {"message": resp.text[:200]}


def split_text(text: str, limit: int) -> list[str]:
    """Split on paragraph, then line, then sentence boundaries; never mid-word."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks, current = [], ""
    for para in text.split("\n"):
        candidate = (current + "\n" + para) if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(para) > limit:
            cut = max(para.rfind(". ", 0, limit), para.rfind(" ", 0, limit))
            cut = cut if cut > limit // 3 else limit
            chunks.append(para[: cut + 1].strip())
            para = para[cut + 1 :].strip()
        current = para
    if current.strip():
        chunks.append(current)
    return [c.strip() for c in chunks if c.strip()]
