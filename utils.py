import logging, asyncio, os, re, random, pytz, aiohttp, requests, string, json, http.client
from info import *
from imdb import Cinemagoer 
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram import enums
from pyrogram.errors import *
from typing import Union, List
from Script import script
from datetime import datetime, date
from database.users_chats_db import db
from database.join_reqs import JoinReqs
from bs4 import BeautifulSoup
from shortzy import Shortzy

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

join_db = JoinReqs
BTN_URL_REGEX = re.compile(r"(\[([^\[]+?)\]\((buttonurl|buttonalert):(?:/{0,2})(.+?)(:same)?\))")

imdb = Cinemagoer() 
TOKENS = {}
VERIFIED = {}
BANNED = {}
SECOND_SHORTENER = {}
SMART_OPEN = '“'
SMART_CLOSE = '”'
START_CHAR = ('\'', '"', SMART_OPEN)

# temp variables used across the bot
class temp(object):
    BANNED_USERS = []
    BANNED_CHATS = []
    ME = None
    BOT = None
    CURRENT = int(os.environ.get("SKIP", 2))
    CANCEL = False
    MELCOW = {}
    U_NAME = None
    B_NAME = None
    GETALL = {}
    SHORT = {}
    SETTINGS = {}
    IMDB_CAP = {}


async def pub_is_subscribed(bot, query, channel):
    btn = []
    for id in channel:
        chat = await bot.get_chat(int(id))
        try:
            await bot.get_chat_member(id, query.from_user.id)
        except UserNotParticipant:
            btn.append([InlineKeyboardButton(f'Join {chat.title}', url=chat.invite_link)])
        except:
            pass
    return btn


async def is_subscribed(bot, query):
    if REQUEST_TO_JOIN_MODE and join_db().isActive():
        try:
            user = await join_db().get_user(query.from_user.id)
            if user and user["user_id"] == query.from_user.id:
                return True
            else:
                try:
                    user_data = await bot.get_chat_member(AUTH_CHANNEL, query.from_user.id)
                except UserNotParticipant:
                    pass
                except Exception as e:
                    logger.exception(e)
                else:
                    if user_data.status != enums.ChatMemberStatus.BANNED:
                        return True
        except Exception as e:
            logger.exception(e)
        return False
    else:
        try:
            user = await bot.get_chat_member(AUTH_CHANNEL, query.from_user.id)
        except UserNotParticipant:
            pass
        except Exception as e:
            logger.exception(e)
        else:
            if user.status != enums.ChatMemberStatus.BANNED:
                return True
        return False


async def get_poster(query, bulk=False, id=False, file=None):
    query = query.strip().lower()
    id = id
    if not id:
        title = query
        year = re.findall(r'[1-2]\d{3}$', query)
        if year:
            year = year[0]
            title = query.replace(year, "").strip()
        movieid = imdb.search_movie(title, results=10)
        if not movieid:
            return None
        if year:
            filtered = list(filter(lambda k: str(k.get('year')) == str(year), movieid))
            if not filtered:
                filtered = movieid
        else:
            filtered = movieid
        movieid = list(filter(lambda k: k.get('kind') in ['movie', 'tv series'], filtered))
        movieid = (movieid or filtered)[0].movieID
    else:
        movieid = query

    movie = imdb.get_movie(movieid)
    if not movie:
        return None

    if movie.get("original air date"):
        datex = movie["original air date"]
    else:
        datex = movie.get("year", "N/A")

    plot = movie.get('plot')
    if plot and len(plot) > 0:
        plot = plot[0][:800] + "..."

    return {
        'title': movie.get('title'),
        'votes': movie.get('votes'),
        "aka": list_to_str(movie.get("akas")),
        "seasons": movie.get("number of seasons"),
        "box_office": movie.get('box office'),
        'localized_title': movie.get('localized title'),
        'kind': movie.get("kind"),
        "imdb_id": f"tt{movie.get('imdbID')}",
        "cast": list_to_str(movie.get("cast")),
        "runtime": list_to_str(movie.get("runtimes")),
        "countries": list_to_str(movie.get("countries")),
        "certificates": list_to_str(movie.get("certificates")),
        "languages": list_to_str(movie.get("languages")),
        "director": list_to_str(movie.get("director")),
        "writer": list_to_str(movie.get("writer")),
        "producer": list_to_str(movie.get("producer")),
        "composer": list_to_str(movie.get("composer")),
        "cinematographer": list_to_str(movie.get("cinematographer")),
        "music_team": list_to_str(movie.get("music department")),
        "distributors": list_to_str(movie.get("distributors")),
        'release_date': datex,
        'year': movie.get('year'),
        'genres': list_to_str(movie.get("genres")),
        'poster': movie.get('full-size cover url'),
        'plot': plot,
        'rating': str(movie.get("rating")),
        'url': f'https://www.imdb.com/title/tt{movieid}'
    }


async def broadcast_messages(user_id, message):
    try:
        await message.copy(chat_id=user_id)
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.x)
        return await broadcast_messages(user_id, message)
    except (InputUserDeactivated, UserIsBlocked, PeerIdInvalid):
        await db.delete_user(int(user_id))
        return False, "Deleted"
    except:
        return False, "Error"


async def broadcast_messages_group(chat_id, message):
    try:
        kd = await message.copy(chat_id=chat_id)
        try:
            await kd.pin()
        except:
            pass
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.x)
        return await broadcast_messages_group(chat_id, message)
    except:
        return False, "Error"


async def search_gagala(text):
    usr_agent = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/61.0.3163.100 Safari/537.36'
    }
    text = text.replace(" ", '+')
    url = f'https://www.google.com/search?q={text}'
    response = requests.get(url, headers=usr_agent)
    soup = BeautifulSoup(response.text, 'html.parser')
    titles = soup.find_all('h3')
    return [title.getText() for title in titles]


async def get_settings(group_id):
    return await db.get_settings(group_id)


async def save_group_settings(group_id, key, value):
    current = await get_settings(group_id)
    current.update({key: value})
    await db.update_settings(group_id, current)


def get_size(size):
    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units):
        i += 1
        size /= 1024.0
    return "%.2f %s" % (size, units[i])


def split_list(l, n):
    for i in range(0, len(l), n):
        yield l[i:i + n]


def get_file_id(msg: Message):
    if msg.media:
        for message_type in ("photo", "animation", "audio", "document", "video", "video_note", "voice", "sticker"):
            obj = getattr(msg, message_type)
            if obj:
                setattr(obj, "message_type", message_type)
                return obj


def extract_user(message: Message) -> Union[int, str]:
    if message.reply_to_message:
        return message.reply_to_message.from_user.id, message.reply_to_message.from_user.first_name
    elif len(message.command) > 1:
        if len(message.entities) > 1 and message.entities[1].type == enums.MessageEntityType.TEXT_MENTION:
            required_entity = message.entities[1]
            return required_entity.user.id, required_entity.user.first_name
        return message.command[1], message.command[1]
    else:
        return message.from_user.id, message.from_user.first_name


def list_to_str(k):
    if not k:
        return "N/A"
    elif len(k) == 1:
        return str(k[0])
    elif MAX_LIST_ELM:
        k = k[:int(MAX_LIST_ELM)]
        return ", ".join(k)
    else:
        return ", ".join(k)


def last_online(from_user):
    if from_user.is_bot:
        return "🤖 Bot"
    status = from_user.status
    if status == enums.UserStatus.RECENTLY:
        return "Recently"
    if status == enums.UserStatus.LAST_WEEK:
        return "Within last week"
    if status == enums.UserStatus.LAST_MONTH:
        return "Within last month"
    if status == enums.UserStatus.LONG_AGO:
        return "A long time ago"
    if status == enums.UserStatus.ONLINE:
        return "Online now"
    if status == enums.UserStatus.OFFLINE:
        return from_user.last_online_date.strftime("%a, %d %b %Y, %H:%M:%S")
    return ""


def split_quotes(text: str) -> List:
    if not any(text.startswith(char) for char in START_CHAR):
        return text.split(None, 1)
    counter = 1
    while counter < len(text):
        if text[counter] == "\\":
            counter += 1
        elif text[counter] == text[0] or (text[0] == SMART_OPEN and text[counter] == SMART_CLOSE):
            break
        counter += 1

    key = remove_escapes(text[1:counter].strip())
    rest = text[counter + 1:].strip()
    if not key:
        key = text[0] + text[0]
    return [key, rest]


def gfilterparser(text, keyword):
    if "buttonalert" in text:
        text = text.replace("\n", "\\n").replace("\t", "\\t")
    buttons, note_data, prev, i = [], "", 0, 0
    alerts = []

    for match in BTN_URL_REGEX.finditer(text):
        n_escapes, to_check = 0, match.start(1) - 1
        while to_check > 0 and text[to_check] == "\\":
            n_escapes += 1
            to_check -= 1

        if n_escapes % 2 == 0:
            note_data += text[prev:match.start(1)]
            prev = match.end(1)
            label, url = match.group(2), match.group(4)

            if match.group(3) == "buttonalert":
                if match.group(5) and buttons:
                    buttons[-1].append(InlineKeyboardButton(text=label, callback_data=f"gfilteralert:{i}:{keyword}"))
                else:
                    buttons.append([InlineKeyboardButton(text=label, callback_data=f"gfilteralert:{i}:{keyword}")])
                alerts.append(url)
                i += 1
            elif match.group(5) and buttons:
                buttons[-1].append(InlineKeyboardButton(text=label, url=url.replace(" ", "")))
            else:
                buttons.append([InlineKeyboardButton(text=label, url=url.replace(" ", ""))])
        else:
            note_data += text[prev:to_check]
            prev = match.start(1) - 1
    note_data += text[prev:]
    return note_data, buttons, alerts


def parser(text, keyword):
    if "buttonalert" in text:
        text = text.replace("\n", "\\n").replace("\t", "\\t")
    buttons, note_data, prev, i = [], "", 0, 0
    alerts = []

    for match in BTN_URL_REGEX.finditer(text):
        n_escapes, to_check = 0, match.start(1) - 1
        while to_check > 0 and text[to_check] == "\\":
            n_escapes += 1
            to_check -= 1

        if n_escapes % 2 == 0:
            note_data += text[prev:match.start(1)]
            prev = match.end(1)
            label, url = match.group(2), match.group(4)

            if match.group(3) == "buttonalert":
                if match.group(5) and buttons:
                    buttons[-1].append(InlineKeyboardButton(text=label, callback_data=f"alertmessage:{i}:{keyword}"))
                else:
                    buttons.append([InlineKeyboardButton(text=label, callback_data=f"alertmessage:{i}:{keyword}")])
                alerts.append(url)
                i += 1
            elif match.group(5) and buttons:
                buttons[-1].append(InlineKeyboardButton(text=label, url=url.replace(" ", "")))
            else:
                buttons.append([InlineKeyboardButton(text=label, url=url.replace(" ", ""))])
        else:
            note_data += text[prev:to_check]
            prev = match.start(1) - 1
    note_data += text[prev:]
    return note_data, buttons, alerts


def remove_escapes(text: str) -> str:
    res, is_escaped = "", False
    for ch in text:
        if is_escaped:
            res += ch
            is_escaped = False
        elif ch == "\\":
            is_escaped = True
        else:
            res += ch
    return res


def humanbytes(size):
    if not size:
        return ""
    power, n = 2**10, 0
    Dic_powerN = {0: '', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + "B"


async def get_clone_shortlink(link, url, api):
    shortzy = Shortzy(api_key=api, base_site=url)
    return await shortzy.convert(link)


async def get_shortlink(chat_id, link):
    settings = await get_settings(chat_id)
    URL = settings.get('shortlink', SHORTLINK_URL)
    API = settings.get('shortlink_api', SHORTLINK_API)

    if URL.startswith(("shorturllink", "terabox.in", "urlshorten.in")):
        URL, API = SHORTLINK_URL, SHORTLINK_API

    if URL == "api.shareus.io":
        url = f'https://{URL}/easy_api'
        params = {"key": API, "link": link}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, raise_for_status=True, ssl=False) as response:
                    return await response.text()
        except Exception as e:
            logger.error(e)
            return link
    else:
        shortzy = Shortzy(api_key=API, base_site=URL)
        return await shortzy.convert(link)


async def get_tutorial(chat_id):
    settings = await get_settings(chat_id)
    return settings['tutorial']


async def get_verify_shorted_link(link, url, api):
    if url == "api.shareus.io":
        url = f'https://{url}/easy_api'
        params = {"key": api, "link": link}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, raise_for_status=True, ssl=False) as response:
                    return await response.text()
        except Exception as e:
            logger.error(e)
            return link
    else:
        shortzy = Shortzy(api_key=api, base_site=url)
        return await shortzy.convert(link)


async def check_token(bot, userid, token):
    user = await bot.get_users(userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))

    if user.id in TOKENS:
        TKN = TOKENS[user.id]
        if token in TKN:
            return not TKN[token]
    return False


async def get_token(bot, userid, link):
    user = await bot.get_users(userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))

    token = ''.join(random.choices(string.ascii_letters + string.digits, k=7))
    TOKENS[user.id] = {token: False}
    link = f"{link}verify-{user.id}-{token}"

    shortened_verify_url = await get_verify_shorted_link(link, VERIFY_SHORTLINK_URL, VERIFY_SHORTLINK_API)
    if VERIFY_SECOND_SHORTNER:
        return str(
            await get_verify_shorted_link(shortened_verify_url, VERIFY_SND_SHORTLINK_URL, VERIFY_SND_SHORTLINK_API)
        )
    return str(shortened_verify_url)


async def verify_user(bot, userid, token):
    user = await bot.get_users(userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))

    TOKENS[user.id] = {token: True}
    VERIFIED[user.id] = str(date.today())


async def check_verification(bot, userid):
    user = await bot.get_users(userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))

    today = date.today()
    if userid in VERIFIED:
        years, month, day = VERIFIED[user.id].split('-')
        comp = date(int(years), int(month), int(day))
        return comp >= today
    return False


async def send_all(bot, userid, files, ident, chat_id, user_name, query):
    settings = await get_settings(chat_id)
    ENABLE_SHORTLINK = settings.get('is_shortlink', False)

    try:
        if ENABLE_SHORTLINK:
            for file in files:
                title = file["file_name"]
                size = get_size(file["file_size"])
                if not await db.has_premium_access(userid) and SHORTLINK_MODE:
                    link = await get_shortlink(chat_id, f"https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}")
                    await bot.send_message(
                        chat_id=userid,
                        text=f"<b>Hᴇʏ {user_name} 👋🏽\n\n🗃️ Fɪʟᴇ: {title}\n📦 Sɪᴢᴇ: {size}</b>",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download", url=link)]])
                    )
        else:
            for file in files:
                f_caption = file["caption"]
                title = file["file_name"]
                size = get_size(file["file_size"])

                if CUSTOM_FILE_CAPTION:
                    try:
                        f_caption = CUSTOM_FILE_CAPTION.format(
                            file_name=title or '',
                            file_size=size or '',
                            file_caption=f_caption or ''
                        )
                    except:
                        pass

                if f_caption is None:
                    f_caption = f"{title}"

                await bot.send_cached_media(
                    chat_id=userid,
                    file_id=file["file_id"],
                    caption=f_caption,
                    protect_content=True if ident == "filep" else False,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton('Sᴜᴘᴘᴏʀᴛ', url=GRP_LNK),
                                InlineKeyboardButton('Uᴘᴅᴀᴛᴇs', url=CHNL_LNK),
                            ],
                            [
                                InlineKeyboardButton('Bᴏᴛ Oᴡɴᴇʀ', url=OWNER_LNK)
                            ]
                        ]
                    )
                )
    except UserIsBlocked:
        await query.answer('Unblock bot first!', show_alert=True)
    except PeerIdInvalid:
        await query.answer('Start bot first, then click Send All', show_alert=True)
    except:
        await query.answer('Start bot first, then click Send All', show_alert=True)


async def get_cap(settings, remaining_seconds, files, query, total_results, search):
    if settings["imdb"]:
        IMDB_CAP = temp.IMDB_CAP.get(query.from_user.id)

        if IMDB_CAP:
            cap = IMDB_CAP + "<b>\n\n<i>🍿 Your Files 👇</i></b>\n\n"
            for file in files:
                cap += (
                    f"<b>📁 <a href='https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}'>"
                    f"[{get_size(file['file_size'])}] {file['file_name']}</a></b>\n\n"
                )

        else:
            imdb_data = await get_poster(search, file=files[0]["file_name"])
            if imdb_data:
                TEMPLATE = script.IMDB_TEMPLATE_TXT
                cap = TEMPLATE.format(**imdb_data, search=search)
                cap += "<b>\n\n<i>🍿 Your Files 👇</i></b>\n\n"

                for file in files:
                    cap += (
                        f"<b>📁 <a href='https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}'>"
                        f"[{get_size(file['file_size'])}] {file['file_name']}</a></b>\n\n"
                    )
            else:
                cap = f"<b><i>No IMDB results found for {search}</i></b>\n\n"

    else:
        cap = (
            f"<b><i>The Results for: {search}\n"
            f"Requested by {query.from_user.mention}\n\n"
            f"Shown in {remaining_seconds}s</i></b>"
        )

    return cap



async def get_seconds(time_string):
    def extract_value_and_unit(ts):
        value = "".join([c for c in ts if c.isdigit()])
        unit = ts[len(value):]
        return int(value) if value else 0, unit

    value, unit = extract_value_and_unit(time_string)

    if unit == 's':
        return value
    if unit == 'min':
        return value * 60
    if unit == 'hour':
        return value * 3600
    if unit == 'day':
        return value * 86400
    if unit == 'month':
        return value * 86400 * 30
    if unit == 'year':
        return value * 86400 * 365

    return 0



# -------------------------------------------------------------------
# 🔥 REQUIRED BY YOUR INDEXER
# -------------------------------------------------------------------
def get_readable_time(seconds: int) -> str:
    """Convert seconds into a readable time format: 1h 2m 30s"""
    count = 0
    time_list = []
    time_suffix_list = ["s", "m", "h", "days"]

    while count < 4:
        if count == 3:
            remainder = seconds
        else:
            remainder, result = divmod(seconds, 60 if count < 2 else 24)

        seconds = remainder
        time_list.append(f"{int(result)}{time_suffix_list[count]}")
        count += 1

    time_list.reverse()
    return " ".join(time_list)
