import sys, glob, importlib, logging, logging.config, pytz, asyncio
from pathlib import Path

# Get logging configurations
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("cinemagoer").setLevel(logging.ERROR)

from pyrogram import Client, idle
from database.users_chats_db import db
from info import *
from utils import temp
from typing import Union, Optional, AsyncGenerator
from Script import script
from datetime import date, datetime
from aiohttp import web
from plugins import web_server
from plugins.clone import restart_bots

from Neon.bot import NeonBot
from Neon.util.keepalive import ping_server
from Neon.bot.clients import initialize_clients

# ------------------- Added Keep-Alive Function -------------------
from info import KEEP_ALIVE_URL
import aiohttp

async def keep_alive():
    """Send a request every 100 seconds to keep the bot alive (if required)."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await session.get(KEEP_ALIVE_URL)
                logging.info("Sent keep-alive request.")
            except Exception as e:
                logging.error(f"Keep-alive request failed: {e}")
            await asyncio.sleep(100)
# ----------------------------------------------------------------


# ------------------- Updated plugin loader -------------------
def get_all_plugin_files(root="plugins"):
    """Recursively get all .py files in the plugins folder and subfolders."""
    files = []
    for path in Path(root).rglob("*.py"):
        if path.name != "__init__.py":  # skip __init__.py
            files.append(path)
    return files

files = get_all_plugin_files()
# -------------------------------------------------------------

NeonBot.start()
loop = asyncio.get_event_loop()


async def start():
    print("\nInitializing Your Bot...\n")

    bot_info = await NeonBot.get_me()
    await initialize_clients()

    # ------------------- Import plugins -------------------
    for plugin_path in files:
        plugin_name = plugin_path.stem
        import_path = ".".join(plugin_path.with_suffix("").parts)
        spec = importlib.util.spec_from_file_location(import_path, plugin_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[import_path] = mod
        print(f"✨ Neon Imported => {plugin_name}")
    # -------------------------------------------------------

    if ON_HEROKU:
        asyncio.create_task(ping_server())

    # Start keep-alive task
    if KEEP_ALIVE_URL:
        asyncio.create_task(keep_alive())

    # ---------------- Load banned users/chats ----------------
    b_users, b_chats = await db.get_banned()
    temp.BANNED_USERS = b_users
    temp.BANNED_CHATS = b_chats

    # ---------------- Bot Info ----------------
    me = await NeonBot.get_me()
    temp.BOT = NeonBot
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name

    logging.info(script.LOGO)

    # ---------------- Timezone ----------------
    tz = pytz.timezone("Asia/Kolkata")
    today = date.today()
    now = datetime.now(tz)
    time = now.strftime("%I:%M:%S %p")

    # ---------------- Restart Message ----------------
    bot_name = me.first_name
    try:
        await NeonBot.send_message(
            chat_id=LOG_CHANNEL,
            text=script.RESTART_TXT.format(bot_name, today, time),
        )
    except Exception as e:
        print("Restart message failed:", e)

    # -------------------------------------------------
    # Notify Channels
    for ch in CHANNELS:
        try:
            k = await NeonBot.send_message(chat_id=ch, text="**Bot Restarted**")
            await k.delete()
        except:
            print("Make Your Bot Admin In File Channels With Full Rights")

    try:
        k = await NeonBot.send_message(chat_id=AUTH_CHANNEL, text="**Bot Restarted**")
        await k.delete()
    except:
        print("Make Your Bot Admin In Force Subscribe Channel With Full Rights")

    # ---------------- Clone Mode ----------------
    if CLONE_MODE is True:
        print("Restarting All Clone Bots.......")
        await restart_bots()
        print("Restarted All Clone Bots.")

    # ---------------- Web Server ----------------
    app = web.AppRunner(await web_server())
    await app.setup()
    await web.TCPSite(app, "0.0.0.0", PORT).start()

    await idle()


if __name__ == "__main__":
    try:
        loop.run_until_complete(start())
    except KeyboardInterrupt:
        logging.info("Service Stopped Bye 👋")
