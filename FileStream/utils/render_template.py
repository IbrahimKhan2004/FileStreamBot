import aiohttp
import jinja2
import urllib.parse
import logging
import re
from FileStream.config import Telegram, Server
from FileStream.utils.database import Database
from FileStream.utils.human_readable import humanbytes
from FileStream.utils.file_properties import get_file_ids, get_hash
from FileStream.bot import FileStream, multi_clients
from pyrogram.types import Message

db = Database(Telegram.DATABASE_URL, Telegram.SESSION_NAME)

async def render_page(path):
    import re
    match = re.search(r"^([0-9a-f]{10})(\d+)$", path)
    if match and len(path) != 24:
        secure_hash = match.group(1)
        message_id = int(match.group(2))

        try:
            file_id = await get_file_ids(FileStream, db_id=None, multi_clients=multi_clients, message=Message, log_msg_id=message_id)
            if get_hash(file_id.unique_id, 10) != secure_hash:
                return "Invalid Hash"

            file_name = file_id.file_name
            file_size = humanbytes(file_id.file_size)
            src = urllib.parse.urljoin(Server.URL, f'dl/{path}')
            mime_type = file_id.mime_type
        except Exception as e:
            logging.error(e)
            return "File Not Found"
    else:
        try:
            file_data = await db.get_file(path)
            file_name = file_data['file_name'].replace("_", " ")
            file_size = humanbytes(file_data['file_size'])
            mime_type = file_data['mime_type']

            if "log_msg_id" in file_data:
                secure_hash = get_hash(file_data['file_unique_id'], 10)
                link_id = f"{secure_hash}{file_data['log_msg_id']}"
            else:
                link_id = path

            src = urllib.parse.urljoin(Server.URL, f'dl/{link_id}')
        except Exception:
            return "File Not Found"

    if str(mime_type.split('/')[0].strip()) == 'video':
        template_file = "FileStream/template/play.html"
    else:
        template_file = "FileStream/template/dl.html"

    with open(template_file) as f:
        template = jinja2.Template(f.read())

    return template.render(
        file_name=file_name,
        file_url=src,
        file_size=file_size
    )
