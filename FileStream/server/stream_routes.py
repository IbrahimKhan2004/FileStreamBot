import re
import time
import math
import logging
import mimetypes
import traceback

from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine

from FileStream.bot import multi_clients, work_loads, FileStream
from FileStream.config import Telegram, Server
from FileStream.server.exceptions import FIleNotFound, InvalidHash
from FileStream.utils.file_properties import get_media_from_message
from FileStream import utils, StartTime, __version__
from FileStream.utils.render_template import render_page
from FileStream.utils.link_utils import is_old_style_id, verify_new_link
from pyrogram.file_id import FileId

routes = web.RouteTableDef()

# new-style path: {integer_msg_id}/{filename}
_NEW_LINK_RE = re.compile(r"^(\d+)/(.+)$")


@routes.get("/status", allow_head=True)
async def root_route_handler(_):
    return web.json_response({
        "server_status": "running",
        "uptime": utils.get_readable_time(time.time() - StartTime),
        "telegram_bot": "@" + FileStream.username,
        "connected_bots": len(multi_clients),
        "loads": dict(
            ("bot" + str(c + 1), l)
            for c, (_, l) in enumerate(
                sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
            )
        ),
        "version": __version__,
    })


@routes.get("/watch/{path}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        return web.Response(text=await render_page(path), content_type="text/html")
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass


@routes.get("/dl/{path:.*}", allow_head=True)
async def stream_handler(request: web.Request):
    try:
        path = request.match_info["path"]

        if is_old_style_id(path):
            # ── OLD links: DB-based (unchanged) ──────────────────────────────
            return await _stream_old(request, path)

        m = _NEW_LINK_RE.match(path)
        if m:
            # ── NEW links: message_id + HMAC (no DB) ─────────────────────────
            msg_id = int(m.group(1))
            token = request.rel_url.query.get("hash", "")
            if not token or not verify_new_link(msg_id, token):
                raise InvalidHash()
            return await _stream_new(request, msg_id)

        raise FIleNotFound()

    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass
    except Exception as e:
        traceback.print_exc()
        logging.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))


# ── Client picker ────────────────────────────────────────────────────────────

def _pick_client():
    index = min(work_loads, key=work_loads.get)
    return index, multi_clients[index]


# ── Cache per streamer ───────────────────────────────────────────────────────
_class_cache = {}

def _get_streamer(client):
    if client not in _class_cache:
        _class_cache[client] = utils.ByteStreamer(client)
    return _class_cache[client]


# ── OLD-style (DB lookup) ────────────────────────────────────────────────────

async def _stream_old(request: web.Request, db_id: str):
    range_header = request.headers.get("Range", 0)
    index, client = _pick_client()
    tg = _get_streamer(client)
    file_id = await tg.get_file_properties(db_id, multi_clients)
    return _build_response(request, tg, file_id, index, range_header)


# ── NEW-style (message_id direct, no DB) ─────────────────────────────────────

async def _stream_new(request: web.Request, msg_id: int):
    range_header = request.headers.get("Range", 0)
    index, client = _pick_client()
    tg = _get_streamer(client)

    cache_key = f"msg:{msg_id}"
    if cache_key not in tg.cached_file_ids:
        msg = await client.get_messages(Telegram.FLOG_CHANNEL, msg_id)
        if not msg or msg.empty:
            raise FIleNotFound()
        media = get_media_from_message(msg)
        if not media:
            raise FIleNotFound()
        raw_id = getattr(media, "file_id", "")
        if not raw_id:
            raise FIleNotFound()
        file_id = FileId.decode(raw_id)
        setattr(file_id, "file_size", getattr(media, "file_size", 0))
        setattr(file_id, "mime_type", getattr(media, "mime_type", "application/octet-stream"))
        file_name = getattr(media, "file_name", None) or f"file_{msg_id}"
        setattr(file_id, "file_name", file_name)
        setattr(file_id, "unique_id", getattr(media, "file_unique_id", ""))
        tg.cached_file_ids[cache_key] = file_id

    return _build_response(request, tg, tg.cached_file_ids[cache_key], index, range_header)


# ── Shared response builder ──────────────────────────────────────────────────

def _build_response(request, tg, file_id, index, range_header):
    file_size = file_id.file_size

    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1

    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            body="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 1024 * 1024
    until_bytes = min(until_bytes, file_size - 1)
    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1
    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)

    body = tg.yield_file(
        file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
    )

    mime_type = file_id.mime_type
    file_name = utils.get_name(file_id)
    if not mime_type:
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers={
            "Content-Type": mime_type,
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Accept-Ranges": "bytes",
        },
    )
