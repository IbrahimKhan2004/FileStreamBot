import asyncio
from urllib.parse import quote_plus

from pyrogram import filters, Client
from pyrogram.errors import FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums.parse_mode import ParseMode

from FileStream.bot import FileStream, multi_clients
from FileStream.config import Telegram
from FileStream.utils.bot_utils import (
    is_user_banned, is_user_exist, is_user_joined,
    is_channel_banned, is_channel_exist, is_user_authorized,
)
from FileStream.utils.database import Database
from FileStream.utils.file_properties import get_name, get_media_from_message
from FileStream.utils.link_utils import gen_new_link
from FileStream.utils.human_readable import humanbytes
from FileStream.utils.translation import LANG

db = Database(Telegram.DATABASE_URL, Telegram.SESSION_NAME)


async def _forward_and_gen_link(message: Message):
    """
    Forward file to FLOG_CHANNEL → get message_id → gen HMAC link.
    NO database write. New-style permanent link.
    """
    log_msg = await message.forward(chat_id=Telegram.FLOG_CHANNEL)
    media = get_media_from_message(message)
    file_name = get_name(message)
    file_size = humanbytes(getattr(media, "file_size", 0))
    mime_type = getattr(media, "mime_type", "")
    stream_link = gen_new_link(log_msg.id, file_name)
    return stream_link, file_name, file_size, mime_type


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
        stream_link, file_name, file_size, mime_type = await _forward_and_gen_link(message)

        if "video" in mime_type:
            stream_text = LANG.STREAM_TEXT.format(file_name, file_size, stream_link, stream_link, "")
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("sᴛʀᴇᴀᴍ", url=stream_link),
                 InlineKeyboardButton("ᴅᴏᴡɴʟᴏᴀᴅ", url=stream_link)],
                [InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close")]
            ])
        else:
            stream_text = LANG.STREAM_TEXT_X.format(file_name, file_size, stream_link, "")
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("ᴅᴏᴡɴʟᴏᴀᴅ", url=stream_link)],
                [InlineKeyboardButton("ᴄʟᴏsᴇ", callback_data="close")]
            ])

        await message.reply_text(
            text=stream_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            quote=True,
        )

    except FloodWait as e:
        print(f"Sleeping for {str(e.value)}s")
        await asyncio.sleep(e.value)
        await bot.send_message(
            chat_id=Telegram.ULOG_CHANNEL,
            text=(
                f"Gᴏᴛ FʟᴏᴏᴅWᴀɪᴛ ᴏғ {str(e.value)}s ғʀᴏᴍ "
                f"[{message.from_user.first_name}](tg://user?id={message.from_user.id})\n\n"
                f"**ᴜsᴇʀ ɪᴅ :** `{str(message.from_user.id)}`"
            ),
            disable_web_page_preview=True,
            parse_mode=ParseMode.MARKDOWN,
        )


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
        stream_link, _, _, _ = await _forward_and_gen_link(message)

        await bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=message.id,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Dᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋ 📥", url=stream_link)]
            ]),
        )

    except FloodWait as w:
        print(f"Sleeping for {str(w.x)}s")
        await asyncio.sleep(w.x)
        await bot.send_message(
            chat_id=Telegram.ULOG_CHANNEL,
            text=f"ɢᴏᴛ ғʟᴏᴏᴅᴡᴀɪᴛ ᴏғ {str(w.x)}s ғʀᴏᴍ {message.chat.title}\n\n**ᴄʜᴀɴɴᴇʟ ɪᴅ :** `{str(message.chat.id)}`",
            disable_web_page_preview=True,
        )
    except Exception as e:
        await bot.send_message(
            chat_id=Telegram.ULOG_CHANNEL,
            text=f"**#EʀʀᴏʀTʀᴀᴄᴋʙᴀᴄᴋ:** `{e}`",
            disable_web_page_preview=True,
        )
