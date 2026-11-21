"""One-way codes for phone numbers in the usage log, and the dashboard key.

The code is a keyed hash (HMAC-SHA256), so someone holding the log can't hash a list
of likely phone numbers and match them without also having the key. The key comes from
LOG_SALT in .env, or is created once and kept beside the database (~/.akwaaba/log_key).
"""

import hashlib
import hmac
import os
import secrets
from pathlib import Path

_cache: dict[str, bytes] = {}


def _home() -> Path:
    return Path(os.getenv("AKWAABA_DB", str(Path.home() / ".akwaaba" / "akwaaba.db"))).parent


def _secret(name: str, env: str) -> bytes:
    if os.getenv(env):
        return os.environ[env].encode()
    path = _home() / name
    if str(path) in _cache:
        return _cache[str(path)]
    try:
        value = path.read_bytes().strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(24).encode()
        path.write_bytes(value)
        os.chmod(path, 0o600)
    _cache[str(path)] = value
    return value


def user_code(phone: str) -> str:
    return hmac.new(_secret("log_key", "LOG_SALT"), phone.encode(), hashlib.sha256).hexdigest()[:12]


def dashboard_key() -> str:
    """DASHBOARD_KEY from .env, or one made for you (shown once in the Terminal at start-up)."""
    return _secret("dashboard_key", "DASHBOARD_KEY").decode()
