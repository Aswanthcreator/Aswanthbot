import logging
import time
import re
import asyncio
from math import ceil

from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import (
    ChannelInvalid,
    ChatAdminRequired,
    UsernameInvalid,
    UsernameNotModified,
)

from info import ADMINS, INDEX_REQ_CHANNEL as LOG_CHANNEL
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp, get_readable_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

lock = asyncio.Lock()

# --- CONFIG ---
BATCH_SIZE = 200          # files per batch (as requested)
SAVE_CONCURRENCY = 50     # number of concurrent save_file tasks at once (safe default)
UI_UPDATE_EVERY_BATCH = True

# -------------------------
# Helper: progress bar
# -------------------------
def get_progress_bar(percent: float, length: int = 12) -> str:
    """Return an emoji progress bar (length blocks)."""
    p = max(0.0, min(100.0, percent))
    filled = int(length * p / 100)
    return "▰" * filled + "▱" * (length - filled)

# -------------------------
# Callback handler
# -------------------------
@Client.on_callback_query(filters.regex(r"^index"))
async def index_files(bot, query):
    if query.data.startswith("index_cancel"):
        temp.CANCEL = True
        return await query.answer("Cancelling indexing…")

    _, decision, chat, lst_msg_id, from_user = query.data.split("#")
    if decision == "reject":
        # moderator rejected indexing
        await query.message.delete()
        try:
            await bot.send_message(
                int(from_user),
                f"Your submission for indexing {chat} has been declined by moderators.",
                reply_to_message_id=int(lst_msg_id),
            )
        except Exception:
            pass
        return

    if lock.locked():
        return await query.answer("Wait until the previous process completes.", show_alert=True)

    msg = query.message
    await query.answer("Processing…", show_alert=True)

    if int(from_user) not in ADMINS:
        try:
            await bot.send_message(
                int(from_user),
                f"Your submission for indexing {chat} has been accepted by moderators and will be added soon.",
                reply_to_message_id=int(lst_msg_id),
            )
        except Exception:
            pass

    await msg.edit(
        "Starting indexing…",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="index_cancel")]]),
    )

    try:
        chat = int(chat)
    except Exception:
        chat = chat

    await index_files_to_db(int(lst_msg_id), chat, msg, bot)

# -------------------------
# Command handler (forward or link)
# -------------------------
@Client.on_message(
    (filters.forwarded | (filters.regex(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[A-Za-z0-9_]+)/(\d+)$") & filters.text))
    & filters.private
    & filters.incoming
)
async def send_for_index(bot, message):
    # Accept either forwarded message or t.me link
    if message.text:
        regex = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[A-Za-z0-9_]+)/(\d+)$")
        match = regex.match(message.text.strip())
        if not match:
            return await message.reply("Invalid link. Send a channel message link or forward the channel's last message.")
        chat_id = match.group(4)
        last_msg_id = int(match.group(5))
        if chat_id.isnumeric():
            chat_id = int("-100" + chat_id)
    elif message.forward_from_chat and message.forward_from_chat.type == enums.ChatType.CHANNEL:
        last_msg_id = message.forward_from_message_id
        chat_id = message.forward_from_chat.username or message.forward_from_chat.id
    else:
        return

    # validate chat access
    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await message.reply("This may be a private channel/group. Make me an admin there to index files.")
    except (UsernameInvalid, UsernameNotModified):
        return await message.reply("Invalid link specified.")
    except Exception as e:
        logger.exception(e)
        return await message.reply(f"Error: {e}")

    try:
        k = await bot.get_messages(chat_id, last_msg_id)
    except Exception:
        return await message.reply("Make sure I am admin in the channel (if private).")

    if k is None or getattr(k, "empty", False):
        return await message.reply("This may be a group or I am not an admin of the channel.")

    # If admin user: quick confirm UI
    if message.from_user.id in ADMINS:
        buttons = [
            [InlineKeyboardButton("Yes", callback_data=f"index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}")],
            [InlineKeyboardButton("Close", callback_data="close_data")],
        ]
        return await message.reply(
            f"Do you want to index this channel/group?\n\nChat: <code>{chat_id}</code>\nLast message id: <code>{last_msg_id}</code>\n\nSet skip with /setskip",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    # For regular users, post to moderators channel with invite if possible
    if isinstance(chat_id, int):
        try:
            link = (await bot.create_chat_invite_link(chat_id)).invite_link
        except ChatAdminRequired:
            return await message.reply("Make sure I am admin and can invite users.")
    else:
        link = f"@{message.forward_from_chat.username}" if message.forward_from_chat else "N/A"

    buttons = [
        [InlineKeyboardButton("Accept Index", callback_data=f"index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}")],
        [InlineKeyboardButton("Reject Index", callback_data=f"index#reject#{chat_id}#{message.id}#{message.from_user.id}")],
    ]
    await bot.send_message(
        LOG_CHANNEL,
        f"#IndexRequest\n\nBy: {message.from_user.mention} (<code>{message.from_user.id}</code>)\nChat: <code>{chat_id}</code>\nLast Msg ID: <code>{last_msg_id}</code>\nInvite: {link}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await message.reply("Thanks for the contribution. Wait for moderators to verify the files.")

# -------------------------
# setskip command
# -------------------------
@Client.on_message(filters.command("setskip") & filters.user(ADMINS))
async def set_skip_number(bot, message):
    try:
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) == 2:
            skip = int(parts[1])
            temp.CURRENT = skip
            return await message.reply(f"Successfully set SKIP number to {skip}")
    except Exception:
        pass
    await message.reply("Usage: /setskip <number>")

# -------------------------
# Main indexing logic
# -------------------------
async def index_files_to_db(lst_msg_id, chat, msg, bot):
    """
    lst_msg_id : last message id in the target channel (int)
    chat       : chat id or username
    msg        : message object used to edit progress
    bot        : pyrogram client
    """
    total_files = 0
    duplicate = 0
    errors = 0
    deleted = 0
    no_media = 0
    unsupported = 0

    start_time = time.time()

    async with lock:
        try:
            current = int(temp.CURRENT or 0)
            temp.CANCEL = False

            total_messages = int(lst_msg_id)
            total_fetch = max(0, total_messages - current)
            if total_messages <= 0 or total_fetch <= 0:
                await msg.edit(
                    "🚫 Nothing to index. Check the provided last message id or /setskip value.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="close_data")]]),
                )
                return

            batches = ceil(total_fetch / BATCH_SIZE)
            batch_times = []

            # initial UI
            try:
                await msg.edit(
                    f"🚀 Indexing started\n\n"
                    f"📁 Total messages (last id): <code>{total_messages}</code>\n"
                    f"📥 To fetch (from skip): <code>{total_fetch}</code>\n"
                    f"⏱ Elapsed: <code>{get_readable_time(0)}</code>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="index_cancel")]]),
                )
            except Exception:
                pass

            # bounded semaphore for concurrent saves (protects DB overload)
            sem = asyncio.Semaphore(SAVE_CONCURRENCY)

            async def sem_save(media):
                async with sem:
                    return await save_file(media)

            # iterate batches (by message ID ranges)
            fetched_count = 0
            for batch_index in range(batches):
                if temp.CANCEL:
                    break

                batch_start_time = time.time()

                # compute id range for this batch
                start_id = current + 1
                end_id = min(current + BATCH_SIZE, total_messages)
                message_ids = list(range(start_id, end_id + 1))

                # fetch messages in batch (handle FloodWait)
                try:
                    messages = await bot.get_messages(chat, message_ids)
                    if messages is None:
                        messages = []
                    elif not isinstance(messages, list):
                        messages = [messages]
                except FloodWait as fw:
                    wait_s = int(getattr(fw, "x", 5))
                    logger.warning(f"FloodWait {wait_s}s while fetching messages — sleeping.")
                    await asyncio.sleep(wait_s)
                    try:
                        messages = await bot.get_messages(chat, message_ids)
                        if messages is None:
                            messages = []
                        elif not isinstance(messages, list):
                            messages = [messages]
                    except Exception as e:
                        logger.exception(e)
                        errors += len(message_ids)
                        # move current pointer forward to avoid infinite loop
                        current += len(message_ids)
                        fetched_count += len(message_ids)
                        continue
                except Exception as e:
                    logger.exception(e)
                    errors += len(message_ids)
                    current += len(message_ids)
                    fetched_count += len(message_ids)
                    continue

                # prepare save tasks
                save_coros = []
                # messages may be returned in arbitrary order; keep iterating message_ids order
                # iterate through the fetched messages mapping by id to ensure count matches
                # build a mapping from id -> message for quick lookup
                id_to_msg = {}
                try:
                    for m in messages:
                        if getattr(m, "message_id", None) is not None:
                            id_to_msg[int(m.message_id)] = m
                except Exception:
                    # fallback to raw list
                    pass

                for mid in message_ids:
                    current += 1
                    fetched_count += 1
                    m = id_to_msg.get(mid) if id_to_msg else (messages.pop(0) if messages else None)
                    try:
                        if not m:
                            deleted += 1
                            continue
                        if getattr(m, "empty", False):
                            deleted += 1
                            continue
                        if not getattr(m, "media", None):
                            no_media += 1
                            continue
                        if m.media not in (
                            enums.MessageMediaType.VIDEO,
                            enums.MessageMediaType.AUDIO,
                            enums.MessageMediaType.DOCUMENT,
                        ):
                            unsupported += 1
                            continue
                        media = getattr(m, m.media.value, None)
                        if not media:
                            unsupported += 1
                            continue

                        # attach helpful attributes expected by save_file
                        try:
                            media.file_type = m.media.value
                        except Exception:
                            pass
                        media.caption = getattr(m, "caption", None)

                        # append coroutine to be run concurrently (bounded by semaphore)
                        save_coros.append(sem_save(media))

                    except FloodWait as fw:
                        wait_s = int(getattr(fw, "x", 5))
                        logger.warning(f"FloodWait {wait_s}s inside message loop — sleeping.")
                        await asyncio.sleep(wait_s)
                        errors += 1
                        continue
                    except Exception:
                        errors += 1
                        continue

                # run save coroutines for this batch
                if save_coros:
                    try:
                        results = await asyncio.gather(*save_coros, return_exceptions=True)
                    except Exception as e:
                        logger.exception(e)
                        # count them as errors
                        errors += len(save_coros)
                        results = []

                    for res in results:
                        if isinstance(res, Exception):
                            if isinstance(res, FloodWait):
                                wait_s = int(getattr(res, "x", 5))
                                logger.warning(f"FloodWait {wait_s}s during save_file — sleeping.")
                                await asyncio.sleep(wait_s)
                                errors += 1
                            else:
                                errors += 1
                        else:
                            # expected save_file to return (ok, code)
                            try:
                                ok, code = res
                                if ok:
                                    total_files += 1
                                elif code == 0:
                                    duplicate += 1
                                elif code == 2:
                                    errors += 1
                                else:
                                    # unknown codes treated as non-fatal (but count separately if needed)
                                    pass
                            except Exception:
                                errors += 1

                # batch timing & UI update
                batch_time = time.time() - batch_start_time
                batch_times.append(batch_time)
                elapsed = time.time() - start_time
                progress = fetched_count
                percentage = (progress / total_fetch) * 100 if total_fetch else 100.0
                avg_batch_time = sum(batch_times) / len(batch_times) if batch_times else 0.0
                remaining = max(0, total_fetch - progress)
                eta_seconds = (remaining / BATCH_SIZE) * avg_batch_time if avg_batch_time and BATCH_SIZE else 0
                try:
                    if UI_UPDATE_EVERY_BATCH:
                        await msg.edit(
                            f"📊 Indexing Progress — Batch {batch_index + 1}/{batches}\n\n"
                            f"{get_progress_bar(percentage)}  <code>{percentage:.1f}%</code>\n\n"
                            f"📁 Total (last id): <code>{total_messages}</code>\n"
                            f"📥 To fetch: <code>{total_fetch}</code>\n"
                            f"🔎 Fetched: <code>{progress}</code>\n"
                            f"✅ Saved: <code>{total_files}</code>\n"
                            f"♻️ Duplicates: <code>{duplicate}</code>\n"
                            f"🗑 Deleted: <code>{deleted}</code>\n"
                            f"📦 Non-media: <code>{no_media + unsupported}</code> (Unsupported: <code>{unsupported}</code>)\n"
                            f"⚠️ Errors: <code>{errors}</code>\n\n"
                            f"⏱ Elapsed: <code>{get_readable_time(elapsed)}</code>\n"
                            f"⏰ ETA: <code>{get_readable_time(eta_seconds)}</code>",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="index_cancel")]]),
                        )
                except Exception:
                    # ignore edit errors (MessageNotModified etc.)
                    pass

            # final summary
            elapsed = time.time() - start_time
            try:
                await msg.edit(
                    f"✅ Indexing Completed!\n\n"
                    f"📁 Total (last id): <code>{total_messages}</code>\n"
                    f"📥 To fetch: <code>{total_fetch}</code>\n"
                    f"🔎 Fetched: <code>{fetched_count}</code>\n"
                    f"✅ Saved: <code>{total_files}</code>\n"
                    f"♻️ Duplicates: <code>{duplicate}</code>\n"
                    f"🗑 Deleted: <code>{deleted}</code>\n"
                    f"📦 Non-media: <code>{no_media + unsupported}</code> (Unsupported: <code>{unsupported}</code>)\n"
                    f"⚠️ Errors: <code>{errors}</code>\n\n"
                    f"⏱ Total elapsed: <code>{get_readable_time(elapsed)}</code>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="close_data")]]),
                )
            except Exception:
                pass

        except Exception as e:
            logger.exception(e)
            try:
                await msg.edit(
                    f"❌ Error during indexing: <code>{e}</code>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="close_data")]]),
                )
            except Exception:
                pass
