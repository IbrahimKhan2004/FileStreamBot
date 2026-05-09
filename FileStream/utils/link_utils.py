"""
link_utils.py

New-style links (new files — NO DB):
    dl/{msg_id}/{filename}?hash=HMAC[:24]
    → Forward to FLOG_CHANNEL → get message_id → sign with HASH_KEY → done
    No database entry ever created for new files.

Old-style links (existing 4000+ files — DB untouched):
    dl/69fd9cb2335701c5c4a1a1ce
    → DB lookup as before, nothing changed.
"""

from __future__ import annotations
import hmac
import hashlib
from urllib.parse import quote_plus
from FileStream.config import Telegram, Server


def _sign(msg_id: int) -> str:
    key = Telegram.HASH_KEY.encode()
    msg = str(msg_id).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:24]


def gen_new_link(msg_id: int, file_name: str) -> str:
    safe_name = quote_plus(file_name)
    token = _sign(msg_id)
    return f"{Server.URL}dl/{msg_id}/{safe_name}?hash={token}"


def verify_new_link(msg_id: int, token: str) -> bool:
    return hmac.compare_digest(_sign(msg_id), token)


def is_old_style_id(path: str) -> bool:
    """24-char hex = MongoDB ObjectId = old-style link."""
    return len(path) == 24 and all(c in "0123456789abcdefABCDEF" for c in path)
