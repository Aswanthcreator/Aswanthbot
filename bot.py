import sys, importlib, logging, logging.config, pytz, asyncio
from pathlib import Path

# ---------------- Logging ----------------
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("cinemagoer").setLevel(logging.ERROR)

from pyrogram import idle
from database.users_chats_db import db
from info import *
from utils import temp
from Script import script
from datetime import date, datetime
from aiohttp import web
from plugins import web_server
from plugins.clone import restart_bots

from Neon.bot import NeonBot
from Neon.util.keepalive import ping_server
from Neon.bot.clients import initialize_clients

# ---------------- Keep Alive ----------------
import aiohttp
async def keep_alive():
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await session.get(KEEP_ALIVE_URL)
                logging.info("Sent keep-alive request.")
            except Exception as e:
                logging.error("Keep-alive failed: %s", e)
            await asyncio.sleep(100)
# -------------------------------------------------------


# ---------------- Plugin Loader ----------------
def get_all_plugin_files(root="plugins"):
    files = []
    for path in Path(root).rglob("*.py"):
        if path.name != "__init__.py":
            files.append(path)
    return files

files = get_all_plugin_files()
# -------------------------------------------------------

loop = asyncio.get_event_loop()


# ============================================================
#                     BOT START FUNCTION
# ============================================================
async def start():
    print("\nInitializing Your Bot...\n")

    # ⭐ CORRECT BOT START (FIXES YOUR RESTART MESSAGE ISSUE)
    await NeonBot.start()

    # Load clients
    await initialize_clients()

    # ------------------- Import Plugins -------------------
    for plugin_path in files:
        plugin_name = plugin_path.stem
        import_path = ".".join(plugin_path.with_suffix("").parts)

        spec = importlib.util.spec_from_file_location(import_path, plugin_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        sys.modules[import_path] = mod
        print(f"✨ Neon Imported => {plugin_name}")
    # -------------------------------------------------------

    # Heroku keepalive
    if ON_HEROKU:
        asyncio.create_task(ping_server())

    # User keep-alive
    if KEEP_ALIVE_URL:
        asyncio.create_task(keep_alive())

    # Load banned list
    b_users, b_chats = await db.get_banned()
    temp.BANNED_USERS = b_users
    temp.BANNED_CHATS = b_chats

    # Bot info
    me = await NeonBot.get_me()
    temp.BOT = NeonBot
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name

    logging.info(script.LOGO)

    # Time
    tz = pytz.timezone("Asia/Kolkata")
    today = date.today()
    now = datetime.now(tz)
    time = now.strftime("%I:%M:%S %p")

    # Restart message
    try:
        await NeonBot.send_message(
            chat_id=LOG_CHANNEL,
            text=script.RESTART_TXT.format(me.first_name, today, time),
        )
    except Exception as e:
        print("Restart message failed:", e)

    # Notify channels
    for ch in CHANNELS:
        try:
            msg = await NeonBot.send_message(ch, "**Bot Restarted**")
            await msg.delete()
        except:
            print("Bot needs admin rights in File Channel:", ch)

    try:
        msg = await NeonBot.send_message(AUTH_CHANNEL, "**Bot Restarted**")
        await msg.delete()
    except:
        print("Bot needs admin rights in AUTH_CHANNEL")

    # Clone bots restart
    if CLONE_MODE:
        print("Restarting clone bots...")
        await restart_bots()
        print("Clone bots restarted.")

    # Web server
    app = web.AppRunner(await web_server())
    await app.setup()
    await web.TCPSite(app, "0.0.0.0", PORT).start()

    await idle()


# ============================================================
#                          MAIN
# ============================================================
if __name__ == "__main__":
    try:
        loop.run_until_complete(start())
    except KeyboardInterrupt:
        logging.info("Service Stopped Bye 👋")


