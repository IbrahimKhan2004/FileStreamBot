import asyncio
import logging  # Added: enables structured diagnostic logging for silent errors
from FileStream.bot import FileStream, multi_clients
from FileStream.utils.bot_utils import is_user_banned, is_user_exist, is_user_joined, gen_link, is_channel_banned, is_channel_exist, is_user_authorized
from FileStream.utils.database import Database
from FileStream.utils.file_properties import get_file_ids, get_file_info, get_hash
from FileStream.config import Telegram, Server
from pyrogram import filters, Client
from pyrogram.errors import FloodWait, ButtonUrlInvalid  # Added ButtonUrlInvalid: root cause of [400 BUTTON_URL_INVALID], e.g. FQDN=0.0.0.0
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums.parse_mode import ParseMode

logger = logging.getLogger(__name__)  # Added: module-level logger so errors are traceable by file name
db = Database(Telegram.DATABASE_URL, Telegram.SESSION_NAME)

@FileStream.on_message(
    filters.private
    & (
            filters.document
            | filters.video
            | filters.video_note
            | filters.audio
            | filters.voice
            | filters.animation
            | filters.photo
    ),
    group=4,
)
async def private_receive_handler(bot: Client, message: Message):
    if not await is_user_authorized(message):
        return
    if await is_user_banned(message):
        return

    await is_user_exist(bot, message)
    if Telegram.FORCE_SUB:
        if not await is_user_joined(bot, message):
            return
    try:
        inserted_id = await db.add_file(get_file_info(message))  # Unchanged: insert/deduplicate file record in DB

        _, file_info = await asyncio.gather(  # Changed: run get_file_ids and db.get_file concurrently; previously sequential (2× network latency)
            get_file_ids(False, inserted_id, multi_clients, message),  # Unchanged logic: stores log_msg_id + file_ids in DB; result discarded (False client)
            db.get_file(inserted_id),  # Changed: fetch file_info in parallel instead of implicitly inside gen_link later
        )  # Why: both calls hit independent resources (Telegram API vs MongoDB); no dependency between them

        reply_markup, stream_text = await gen_link(_id=inserted_id, file_info=file_info)  # Changed: pass pre-fetched file_info to skip gen_link's own db.get_file call; saves 1 DB round-trip
        await message.reply_text(
            text=stream_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            quote=True
        )
    except ButtonUrlInvalid:  # Added: catches [400 BUTTON_URL_INVALID] — Telegram rejects URLs with invalid hosts (e.g. FQDN=0.0.0.0 or missing domain)
        bad_url = Server.URL  # Capture: surface the exact bad URL so the operator can fix FQDN env var
        logger.error(  # Added: structured log makes silent Telegram 400 visible with the offending URL value
            "BUTTON_URL_INVALID for user_id=%s inserted_id=%s — Server.URL=%r is not a public URL. "  # Changed: includes all variable states per user preference
            "Fix: set FQDN env var to your public domain or IP (not 0.0.0.0).",
            message.from_user.id, inserted_id, bad_url
        )
        await message.reply_text(  # Added: inform user gracefully instead of crashing silently
            "⚠️ Sᴇʀᴠᴇʀ ᴄᴏɴғɪɢᴜʀᴀᴛɪᴏɴ ᴇʀʀᴏʀ: ᴛʜᴇ sᴛʀᴇᴀᴍ ᴜʀʟ ɪs ɴᴏᴛ ᴘᴜʙʟɪᴄʟʏ ʀᴇᴀᴄʜᴀʙʟᴇ.\n"
            "Pʟᴇᴀsᴇ ᴄᴏɴᴛᴀᴄᴛ ᴛʜᴇ ʙᴏᴛ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀ.",
            quote=True
        )
    except FloodWait as e:
        print(f"Sleeping for {str(e.value)}s")
        await asyncio.sleep(e.value)
        await bot.send_message(chat_id=Telegram.ULOG_CHANNEL,
                               text=f"Gᴏᴛ FʟᴏᴏᴅWᴀɪᴛ ᴏғ {str(e.value)}s ғʀᴏᴍ [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n\n**ᴜsᴇʀ ɪᴅ :** `{str(message.from_user.id)}`",
                               disable_web_page_preview=True, parse_mode=ParseMode.MARKDOWN)


@FileStream.on_message(
    filters.channel
    & ~filters.forwarded
    & ~filters.media_group
    & (
            filters.document
            | filters.video
            | filters.video_note
            | filters.audio
            | filters.voice
            | filters.photo
    )
)
async def channel_receive_handler(bot: Client, message: Message):
    if await is_channel_banned(bot, message):
        return
    await is_channel_exist(bot, message)

    try:
        inserted_id = await db.add_file(get_file_info(message))
        await get_file_ids(False, inserted_id, multi_clients, message)

        file_info = await db.get_file(inserted_id)
        if "log_msg_id" in file_info:
            secure_hash = get_hash(file_info['file_unique_id'], 10)
            link_id = f"{secure_hash}{file_info['log_msg_id']}"
        else:
            link_id = inserted_id

        await bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=message.id,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Direct Download Link",
                                       url=f"{Server.URL}dl/{str(link_id)}")]])
        )

    except FloodWait as w:
        print(f"Sleeping for {str(w.x)}s")
        await asyncio.sleep(w.x)
        await bot.send_message(chat_id=Telegram.ULOG_CHANNEL,
                               text=f"ɢᴏᴛ ғʟᴏᴏᴅᴡᴀɪᴛ ᴏғ {str(w.x)}s ғʀᴏᴍ {message.chat.title}\n\n**ᴄʜᴀɴɴᴇʟ ɪᴅ :** `{str(message.chat.id)}`",
                               disable_web_page_preview=True)
    except Exception as e:
        await bot.send_message(chat_id=Telegram.ULOG_CHANNEL, text=f"**#EʀʀᴏʀTʀᴀᴄᴋᴇʙᴀᴄᴋ:** `{e}`",
                               disable_web_page_preview=True)
        print(f"Cᴀɴ'ᴛ Eᴅɪᴛ Bʀᴏᴀᴅᴄᴀsᴛ Mᴇssᴀɢᴇ!\nEʀʀᴏʀ:  **Gɪᴠᴇ ᴍᴇ ᴇᴅɪᴛ ᴘᴇʀᴍɪssɪᴏɴ ɪɴ ᴜᴘᴅᴀᴛᴇs ᴀɴᴅ ʙɪɴ Cʜᴀɴɴᴇʟ!{e}**")
