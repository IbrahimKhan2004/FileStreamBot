import asyncio
import logging
from typing import Dict, Union
from FileStream.bot import work_loads
from pyrogram import Client, utils, raw
from .file_properties import get_file_ids
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from pyrogram.types import Message

class ByteStreamer:
    def __init__(self, client: Client):
        self.clean_timer = 30 * 60
        self.client: Client = client
        self.cached_file_ids: Dict[str, FileId] = {}
        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, db_id: str = None, multi_clients = None, message_id: int = None) -> FileId:
        """
        Returns the properties of a media of a specific message in a FIleId class.
        if the properties are cached, then it'll return the cached results.
        or it'll generate the properties from the Message ID and cache them.
        """
        cache_id = db_id if db_id else str(message_id)
        if not cache_id in self.cached_file_ids:
            logging.debug("Before Calling generate_file_properties")
            await self.generate_file_properties(db_id, multi_clients, message_id)
            logging.debug(f"Cached file properties for file with ID {cache_id}")
        return self.cached_file_ids[cache_id]
    
    async def generate_file_properties(self, db_id: str = None, multi_clients = None, message_id: int = None) -> FileId:
        """
        Generates the properties of a media file on a specific message.
        returns ths properties in a FIleId class.
        """
        logging.debug("Before calling get_file_ids")
        file_id = await get_file_ids(self.client, db_id, multi_clients, Message, log_msg_id=message_id)
        logging.debug(f"Generated file ID and Unique ID for file with ID {db_id or message_id}")
        cache_id = db_id if db_id else str(message_id)
        self.cached_file_ids[cache_id] = file_id
        logging.debug(f"Cached media file with ID {cache_id}")
        return self.cached_file_ids[cache_id]

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        """
        Generates the media session for the DC that contains the media file.
        This is required for getting the bytes from Telegram servers.
        """

        media_session = client.media_sessions.get(file_id.dc_id, None)

        if media_session is None:
            if file_id.dc_id != await client.storage.dc_id():
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await Auth(
                        client, file_id.dc_id, await client.storage.test_mode()
                    ).create(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()

                for _ in range(6):
                    exported_auth = await client.invoke(
                        raw.functions.auth.ExportAuthorization(dc_id=file_id.dc_id)
                    )

                    try:
                        await media_session.invoke(
                            raw.functions.auth.ImportAuthorization(
                                id=exported_auth.id, bytes=exported_auth.bytes
                            )
                        )
                        break
                    except AuthBytesInvalid:
                        logging.debug(
                            f"Invalid authorization bytes for DC {file_id.dc_id}"
                        )
                        continue
                else:
                    await media_session.stop()
                    raise AuthBytesInvalid
            else:
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await client.storage.auth_key(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()
            logging.debug(f"Created media session for DC {file_id.dc_id}")
            client.media_sessions[file_id.dc_id] = media_session
        else:
            logging.debug(f"Using cached media session for DC {file_id.dc_id}")
        return media_session


    @staticmethod
    async def get_location(file_id: FileId) -> Union[raw.types.InputPhotoFileLocation,
                                                     raw.types.InputDocumentFileLocation,
                                                     raw.types.InputPeerPhotoFileLocation,]:
        """
        Returns the file location for the media file.
        """
        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id, access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash,
                    )

            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG,
            )
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        return location

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
    ) -> Union[str, None]:
        """
        Custom generator that yields the bytes of the media file.
        Modded from <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py#L20>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        client = self.client
        work_loads[index] += 1
        logging.debug(f"Starting to yielding file with client {index}.")
        media_session = await self.generate_media_session(client, file_id)

        current_part = 1

        location = await self.get_location(file_id)

        semaphore = asyncio.Semaphore(3)

        async def download_chunk(current_offset):
            async with semaphore:
                try:
                    r = await media_session.invoke(
                        raw.functions.upload.GetFile(
                            location=location, offset=current_offset, limit=chunk_size
                        ),
                    )
                    return r.bytes
                except Exception:
                    r1 = await media_session.invoke(
                        raw.functions.upload.GetFile(
                            location=location, offset=current_offset, limit=512 * 1024
                        ),
                    )
                    r2 = await media_session.invoke(
                        raw.functions.upload.GetFile(
                            location=location, offset=current_offset + 512 * 1024, limit=512 * 1024
                        ),
                    )
                    return r1.bytes + r2.bytes

        try:
            tasks = []
            for i in range(part_count):
                tasks.append(asyncio.create_task(download_chunk(offset + i * chunk_size)))
                if len(tasks) >= 3:
                    chunk = await tasks.pop(0)
                    if part_count == 1:
                        yield chunk[first_part_cut:last_part_cut]
                    elif current_part == 1:
                        yield chunk[first_part_cut:]
                    elif current_part == part_count:
                        yield chunk[:last_part_cut]
                    else:
                        yield chunk
                    current_part += 1

            for task in tasks:
                chunk = await task
                if part_count == 1:
                    yield chunk[first_part_cut:last_part_cut]
                elif current_part == 1:
                    yield chunk[first_part_cut:]
                elif current_part == part_count:
                    yield chunk[:last_part_cut]
                else:
                    yield chunk
                current_part += 1
        except (TimeoutError, AttributeError):
            pass
        finally:
            logging.debug(f"Finished yielding file with {current_part} parts.")
            work_loads[index] -= 1

    
    async def clean_cache(self) -> None:
        """
        function to clean the cache to reduce memory usage
        """
        while True:
            await asyncio.sleep(self.clean_timer)
            self.cached_file_ids.clear()
            logging.debug("Cleaned the cache")
