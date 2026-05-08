from __future__ import annotations
import hashlib
import logging
from datetime import datetime
from pyrogram import Client
from typing import Any, Optional

from pyrogram.enums import ParseMode, ChatType
from pyrogram.types import Message
from pyrogram.file_id import FileId
from FileStream.bot import FileStream
from FileStream.utils.database import Database
from FileStream.config import Telegram, Server

db = Database(Telegram.DATABASE_URL, Telegram.SESSION_NAME)

async def get_file_ids(client: Client | bool, db_id: str, multi_clients, message, log_msg_id: int = None) -> Optional[FileId]:
    logging.debug("Starting of get_file_ids")
    if log_msg_id:
        logging.debug(f"Fetching file_id from log_msg_id: {log_msg_id}")
        msg = await client.get_messages(Telegram.FLOG_CHANNEL, log_msg_id)
        if msg.empty:
            raise Exception("Message not found in log channel")
        media = get_media_from_message(msg)
        file_id = FileId.decode(getattr(media, "file_id", ""))
        setattr(file_id, "file_size", getattr(media, "file_size", 0))
        setattr(file_id, "mime_type", getattr(media, "mime_type", ""))
        setattr(file_id, "file_name", getattr(media, "file_name", ""))
        setattr(file_id, "unique_id", getattr(media, "file_unique_id", ""))
        return file_id

    file_info = await db.get_file(db_id)

    if "log_msg_id" in file_info:
        return await get_file_ids(client, db_id, multi_clients, message, log_msg_id=file_info["log_msg_id"])

    # Fallback to old logic for files without log_msg_id
    if (not "file_ids" in file_info) or not client:
        logging.debug("Storing file_id of all clients in DB")
        log_msg = await send_file(FileStream, db_id, file_info["file_id"], message)
        await db.update_log_msg_id(db_id, log_msg.id)
        await db.update_file_ids(db_id, await update_file_id(log_msg.id, multi_clients))
        logging.debug("Stored file_id of all clients in DB")
        if not client:
            return
        file_info = await db.get_file(db_id)

    file_id_info = file_info.setdefault("file_ids", {})
    if not str(client.id) in file_id_info:
        logging.debug("Storing file_id in DB")
        log_msg = await send_file(FileStream, db_id, file_info["file_id"], message)
        await db.update_log_msg_id(db_id, log_msg.id)
        msg = await client.get_messages(Telegram.FLOG_CHANNEL, log_msg.id)
        media = get_media_from_message(msg)
        file_id_info[str(client.id)] = getattr(media, "file_id", "")
        await db.update_file_ids(db_id, file_id_info)
        logging.debug("Stored file_id in DB")

    logging.debug("Middle of get_file_ids")
    try:
        file_id_str = file_id_info.get(str(client.id))
        if not file_id_str:
             # Refresh using Main Bot if possible
             log_msg = await send_file(FileStream, db_id, file_info["file_id"], message)
             await db.update_log_msg_id(db_id, log_msg.id)
             return await get_file_ids(client, db_id, multi_clients, message, log_msg_id=log_msg.id)

        file_id = FileId.decode(file_id_str)
    except Exception:
        # If decode fails, try to refresh
        log_msg = await send_file(FileStream, db_id, file_info["file_id"], message)
        await db.update_log_msg_id(db_id, log_msg.id)
        return await get_file_ids(client, db_id, multi_clients, message, log_msg_id=log_msg.id)

    setattr(file_id, "file_size", file_info["file_size"])
    setattr(file_id, "mime_type", file_info["mime_type"])
    setattr(file_id, "file_name", file_info["file_name"])
    setattr(file_id, "unique_id", file_info["file_unique_id"])
    logging.debug("Ending of get_file_ids")
    return file_id

def get_media_from_message(message: "Message") -> Any:
    media_types = (
        "audio",
        "document",
        "photo",
        "sticker",
        "animation",
        "video",
        "voice",
        "video_note",
    )
    for attr in media_types:
        media = getattr(message, attr, None)
        if media:
            return media


def get_media_file_size(m):
    media = get_media_from_message(m)
    return getattr(media, "file_size", "None")


def get_name(media_msg: Message | FileId) -> str:
    if isinstance(media_msg, Message):
        media = get_media_from_message(media_msg)
        file_name = getattr(media, "file_name", "")

    elif isinstance(media_msg, FileId):
        file_name = getattr(media_msg, "file_name", "")

    if not file_name:
        if isinstance(media_msg, Message) and media_msg.media:
            media_type = media_msg.media.value
        elif media_msg.file_type:
            media_type = media_msg.file_type.name.lower()
        else:
            media_type = "file"

        formats = {
            "photo": "jpg", "audio": "mp3", "voice": "ogg",
            "video": "mp4", "animation": "mp4", "video_note": "mp4",
            "sticker": "webp"
        }

        ext = formats.get(media_type)
        ext = "." + ext if ext else ""

        date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        file_name = f"{media_type}-{date}{ext}"

    return file_name


def get_file_info(message):
    media = get_media_from_message(message)
    if message.chat.type == ChatType.PRIVATE:
        user_idx = message.from_user.id
    else:
        user_idx = message.chat.id
    return {
        "user_id": user_idx,
        "file_id": getattr(media, "file_id", ""),
        "file_unique_id": getattr(media, "file_unique_id", ""),
        "file_name": get_name(message),
        "file_size": getattr(media, "file_size", 0),
        "mime_type": getattr(media, "mime_type", "None/unknown")
    }


async def update_file_id(msg_id, multi_clients):
    file_ids = {}
    for client_id, client in multi_clients.items():
        log_msg = await client.get_messages(Telegram.FLOG_CHANNEL, msg_id)
        media = get_media_from_message(log_msg)
        file_ids[str(client.id)] = getattr(media, "file_id", "")

    return file_ids


async def send_file(client: Client, db_id, file_id: str, message):
    file_caption = getattr(message, 'caption', None) or get_name(message)
    log_msg = await client.send_cached_media(chat_id=Telegram.FLOG_CHANNEL, file_id=file_id,
                                             caption=f'**{file_caption}**')

    if message.chat.type == ChatType.PRIVATE:
        await log_msg.reply_text(
            text=f"**RᴇQᴜᴇꜱᴛᴇᴅ ʙʏ :** [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n**Uꜱᴇʀ ɪᴅ :** `{message.from_user.id}`\n**Fɪʟᴇ ɪᴅ :** `{db_id}`",
            disable_web_page_preview=True, parse_mode=ParseMode.MARKDOWN, quote=True)
    else:
        await log_msg.reply_text(
            text=f"**RᴇQᴜᴇꜱᴛᴇᴅ ʙʏ :** {message.chat.title} \n**Cʜᴀɴɴᴇʟ ɪᴅ :** `{message.chat.id}`\n**Fɪʟᴇ ɪᴅ :** `{db_id}`",
            disable_web_page_preview=True, parse_mode=ParseMode.MARKDOWN, quote=True)

    return log_msg

def get_hash(media_msg: Message | str, length: int) -> str:
    if isinstance(media_msg, Message):
        media = get_media_from_message(media_msg)
        unique_id = getattr(media, "file_unique_id", "")
    else:
        unique_id = media_msg
    long_hash = hashlib.sha256(unique_id.encode("UTF-8")).hexdigest()
    return long_hash[:length]
