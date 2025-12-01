import logging
import asyncio
import re
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import FloodWait, ChatAdminRequired

from info import INDEX_REQ_CHANNEL as LOG_CHANNEL, ADMINS
from database.ia_filterdb import unpack_new_file_id, save_file, get_bad_files

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

BATCH_SIZE = 200   # 200 files in one stretch


# ============================
# /index Command
# ============================
@Client.on_message(filters.command("index") & filters.user(ADMINS))
async def index_handler(bot: Client, message: Message):

    if not message.reply_to_message:
        return await message.reply(
            "Reply to a forwarded file or send the channel link.\n\nExample:\n`/index https://t.me/c/xxx/1-500`"
        )

    r = message.reply_to_message

    # Case 1: Forwarded message (FAST INDEX)
    if r.forward_from_chat:
        channel = r.forward_from_chat
        return await confirm_index(
            bot, message, channel.id, 1, "forwarded message"
        )

    # Case 2: URL link indexing
    if r.text:
        match = re.findall(r"(https://t\.me/[\w_/\-]+)", r.text)
        if not match:
            return await message.reply("No valid Telegram link found.")

        link = match[0]

        # Parse: https://t.me/c/123456/100-500
        pattern = r"https://t\.me\/(?:c\/)?([a-zA-Z0-9_]+)/(\d+)(?:-(\d+))?"
        parts = re.findall(pattern, link)

        if not parts:
            return await message.reply("Invalid Telegram link format.")

        chat, start, end = parts[0]
        start = int(start)
        end = int(end) if end else start

        try:
            chat_info = await bot.get_chat(chat)
        except Exception:
            return await message.reply("Cannot access chat. Make sure bot is admin.")

        return await confirm_index(
            bot, message, chat_info.id, start, end
        )


# ============================
# Confirm Index Buttons
# ============================
async def confirm_index(bot, message, chat_id, start, end):
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ ACCEPT", callback_data=f"index_yes|{chat_id}|{start}|{end}"),
                InlineKeyboardButton("❌ CANCEL", callback_data="index_no")
            ]
        ]
    )

    return await message.reply(
        f"**Index Request**\n\n"
        f"Chat ID: `{chat_id}`\n"
        f"Messages: `{start}` to `{end}`\n\n"
        f"Do you want to index?",
        reply_markup=kb,
    )


# ============================
# Callback Handling
# ============================
@Client.on_callback_query(filters.regex("index_no"))
async def cancel_index(_, query):
    await query.message.edit("❌ **Indexing Cancelled.**")


@Client.on_callback_query(filters.regex(r"index_yes"))
async def start_index(bot, query):
    _, chat_id, start, end = query.data.split("|")
    chat_id = int(chat_id)
    start = int(start)
    end = int(end)

    await query.message.edit("⏳ **Indexing started...**")

    await index_files_to_db(bot, query, chat_id, start, end)


# ============================
# Indexing Core Function
# ============================
async def index_files_to_db(bot, query, chat_id, start, end):

    total = end - start + 1
    done = 0
    bad_files = []

    progress = await query.message.reply(
        f"🔄 Indexing **{total}** files...\n\nPlease wait..."
    )

    for msg_id in range(start, end + 1):

        try:
            msg = await bot.get_messages(chat_id, msg_id)

            # Skip non-files
            if not msg or not msg.media:
                bad_files.append(msg_id)
                continue

            # Extract file details
            file_data = unpack_new_file_id(msg)
            if not file_data:
                bad_files.append(msg_id)
                continue

            await save_file(file_data)

            done += 1

            # Batch progress every 200 files
            if done % BATCH_SIZE == 0:
                await progress.edit(
                    f"⚡ **Batch Completed**: {done}/{total}\n"
                    f"📦 Saved 200 files..."
                )

        except FloodWait as e:
            await asyncio.sleep(e.value)
        except ChatAdminRequired:
            return await progress.edit("❌ Bot is not admin in the channel.")
        except Exception:
            bad_files.append(msg_id)

    # Final summary
    await progress.edit(
        f"🎉 **Index Completed!**\n\n"
        f"📚 Total: `{total}`\n"
        f"✅ Indexed: `{done}`\n"
        f"❌ Bad Files: `{len(bad_files)}`"
    )

    if bad_files:
        await bot.send_message(
            LOG_CHANNEL,
            f"⚠️ **Bad Files Found:**\n{bad_files}"
)
