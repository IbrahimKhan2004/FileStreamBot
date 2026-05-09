"""
link_utils.py

New-style links (new files — NO DB):
    dl/{msg_id}/{filename}?hash=HMAC[:24]

Old-style links (existing files — DB lookup):
    dl/{hex_id}   — any length hex string (MongoDB ObjectId or legacy IDs)
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
    """
    True if path segment is a hex-only string (old DB id — any length).
    Old: dl/fd8de3f24531018        (15 char hex)
    Old: dl/69fd9cb2335701c5c4a1a1ce  (24 char hex MongoDB ObjectId)
    New: dl/12345/movie.mp4        (contains '/' — handled before this)
    """
    return len(path) > 0 and all(c in "0123456789abcdefABCDEF" for c in path)
