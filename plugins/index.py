import logging
import time
import re
import asyncio
from math import ceil
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.errors.exceptions.bad_request_400 import (
    ChannelInvalid, ChatAdminRequired, UsernameInvalid, UsernameNotModified
)
from info import ADMINS, INDEX_REQ_CHANNEL as LOG_CHANNEL
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp, get_readable_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

lock = asyncio.Lock()


@Client.on_callback_query(filters.regex(r'^index'))
async def index_files(bot, query):
    # Cancel request
    if query.data.startswith('index_cancel'):
        temp.CANCEL = True
        return await query.answer("Cancelling Indexing")

    # Expected format: index#accept#<chat>#<lst_msg_id>#<from_user>
    try:
        _, raju, chat, lst_msg_id, from_user = query.data.split("#")
    except Exception:
        return await query.answer("Invalid callback data.", show_alert=True)

    # If moderator rejected
    if raju == 'reject':
        await query.message.delete()
        try:
            await bot.send_message(
                int(from_user),
                f'Your Submission for indexing {chat} has been declined by our moderators.',
                reply_to_message_id=int(lst_msg_id)
            )
        except Exception:
            # best-effort, ignore
            pass
        return

    # Prevent concurrent indexing
    if lock.locked():
        return await query.answer('Wait until previous process complete.', show_alert=True)

    msg = query.message
    await query.answer('Processing...⏳', show_alert=True)

    # Notify submitter (if not admin)
    try:
        if int(from_user) not in ADMINS:
            await bot.send_message(
                int(from_user),
                f'Your Submission for indexing {chat} has been accepted by our moderators and will be added soon.',
                reply_to_message_id=int(lst_msg_id)
            )
    except Exception:
        pass

    await msg.edit(
        "Starting Indexing",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Cancel', callback_data='index_cancel')]])
    )

    # convert chat to int if possible
    try:
        chat = int(chat)
    except Exception:
        chat = chat

    await index_files_to_db(int(lst_msg_id), chat, msg, bot)


# Accept forwarded messages or text links (private incoming)
INDEX_REGEX = r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?([0-9a-zA-Z_]+)/(\d+)$"

@Client.on_message(((filters.forwarded) | (filters.regex(INDEX_REGEX) & filters.text)) & filters.private & filters.incoming)
async def send_for_index(bot, message):
    # If user sent a link
    if message.text:
        match = re.search(INDEX_REGEX, message.text.strip())
        if not match:
            return await message.reply('**__Invalid link 🚫\n\nTry Again By__ /index**')
        chat_id = match.group(4)
        last_msg_id = int(match.group(5))
        # numeric channel ids in t.me/c/ are given without -100 prefix
        if chat_id.isnumeric():
            chat_id = int("-100" + chat_id)
    # If user forwarded a message from a channel
    elif message.forward_from_chat and message.forward_from_chat.type == enums.ChatType.CHANNEL:
        last_msg_id = message.forward_from_message_id
        chat_id = message.forward_from_chat.username or message.forward_from_chat.id
    else:
        return

    # Verify bot can access chat
    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await message.reply('**__This may be a private channel / group. Make me an admin over there to index the files.__**')
    except (UsernameInvalid, UsernameNotModified):
        return await message.reply('Invalid Link specified.')
    except Exception as e:
        logger.exception(e)
        return await message.reply(f'Errors - {e}')

    # Check that the referenced message exists and bot can read it
    try:
        k = await bot.get_messages(chat_id, last_msg_id)
    except Exception:
        return await message.reply('**__Make sure that I am an admin in the channel if the channel is private.__**')
    if not k or (hasattr(k, "empty") and k.empty):
        return await message.reply('**__This may be a group and I am not an admin of the group or the message is missing.__**')

    # If the requester is an admin — confirm directly
    if message.from_user.id in ADMINS:
        buttons = [
            [InlineKeyboardButton('Cᴏɴғɪʀᴍ ✅', callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')],
            [InlineKeyboardButton('Dᴇᴄʟɪɴᴇ ❌', callback_data='close_data')]
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        return await message.reply(
            f'**__Do you Want To Index This Channel or Group ?\n\n🆔 Chat ID/ Username :__** \n<code>▶️ {chat_id} ◀️</code>\n\n**📄 __Last Message ID :__ ** <code>{last_msg_id}</code>',
            reply_markup=reply_markup
        )

    # If requester is not admin — send to moderation channel with invite link if possible
    if isinstance(chat_id, int):
        try:
            link = (await bot.create_chat_invite_link(chat_id)).invite_link
        except ChatAdminRequired:
            return await message.reply('**__Make sure I am an admin in the chat and have permission to invite users.__**')
        except Exception:
            link = "Unable to create invite"
    else:
        # chat_id is username
        link = f"@{message.forward_from_chat.username}" if message.forward_from_chat else str(chat_id)

    buttons = [
        [InlineKeyboardButton('Aᴄᴄᴇᴘᴛ Iɴᴅᴇx', callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')],
        [InlineKeyboardButton('Rᴇjᴇᴄᴛ Iɴᴅᴇx', callback_data=f'index#reject#{chat_id}#{message.id}#{message.from_user.id}')]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await bot.send_message(
        LOG_CHANNEL,
        f'#IndexRequest\n\n**__By__ : {message.from_user.mention} (<code>{message.from_user.id}</code>)\n__Chat ID/ Username__ - <code>{chat_id}</code>\n__Last Message ID__ - <code>{last_msg_id}</code>\nInviteLink - {link}',
        reply_markup=reply_markup
    )
    await message.reply('**__Thank you for the contribution, wait for my moderators to verify the files.__**')


@Client.on_message(filters.command('setskip') & filters.user(ADMINS))
async def set_skip_number(bot, message):
    if ' ' in message.text:
        _, skip = message.text.split(" ", 1)
        try:
            skip = int(skip)
        except Exception:
            return await message.reply("**__Skip number should be an integer.__**")
        temp.CURRENT = int(skip)
        await message.reply(f"**__Successfully set SKIP number as__** {skip}")
    else:
        await message.reply("**__Give me a skip number__**")


def get_progress_bar(percent, length=10):
    """Creates an emoji-based progress bar."""
    filled = int(length * percent / 100)
    unfilled = length - filled
    return '🟩' * filled + '⬜️' * unfilled


async def index_files_to_db(lst_msg_id, chat, msg, bot):
    total_files = 0
    duplicate = 0
    errors = 0
    deleted = 0
    no_media = 0
    unsupported = 0
    BATCH_SIZE = 200
    start_time = time.time()

    async with lock:
        try:
            current = int(getattr(temp, "CURRENT", 0))
            temp.CANCEL = False
            total_messages = int(lst_msg_id)
            total_fetch = max(0, total_messages - current)
            if total_messages <= 0 or total_fetch <= 0:
                await msg.edit(
                    "🚫 No Messages To Index.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Close', callback_data='close_data')]])
                )
                return

            batches = ceil(total_messages / BATCH_SIZE)
            batch_times = []
            await msg.edit(
                f"📊 Indexing Starting......\n"
                f"💬 Total Messages: <code>{total_messages}</code>\n"
                f"📋 Total Fetch: <code>{total_fetch}</code>\n"
                f"⏰ Elapsed: <code>{get_readable_time(time.time() - start_time)}</code>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Cancel', callback_data='index_cancel')]])
            )

            for batch in range(batches):
                if temp.CANCEL:
                    break

                batch_start = time.time()
                start_id = current + 1
                end_id = min(current + BATCH_SIZE, lst_msg_id)
                if start_id > end_id:
                    break

                message_ids = list(range(start_id, end_id + 1))
                try:
                    messages = await bot.get_messages(chat, message_ids)
                    # bot.get_messages may return a single Message if list len == 1
                    if not isinstance(messages, list):
                        messages = [messages]
                except FloodWait as fw:
                    # Sleep and retry this batch
                    logger.warning(f"FloodWait sleeping for {fw.value} seconds")
                    await asyncio.sleep(fw.value)
                    # retry once
                    try:
                        messages = await bot.get_messages(chat, message_ids)
                        if not isinstance(messages, list):
                            messages = [messages]
                    except Exception as e:
                        logger.exception(e)
                        errors += len(message_ids)
                        current += len(message_ids)
                        continue
                except Exception as e:
                    logger.exception(e)
                    errors += len(message_ids)
                    current += len(message_ids)
                    continue

                save_tasks = []
                for message in messages:
                    current += 1
                    try:
                        if getattr(message, "empty", False):
                            deleted += 1
                            continue
                        if not message.media:
                            no_media += 1
                            continue
                        if message.media not in [enums.MessageMediaType.VIDEO,
                                                 enums.MessageMediaType.AUDIO,
                                                 enums.MessageMediaType.DOCUMENT]:
                            unsupported += 1
                            continue
                        media = getattr(message, message.media.value, None)
                        if not media:
                            unsupported += 1
                            continue
                        # attach extra attributes expected by save_file
                        media.file_type = message.media.value
                        media.caption = message.caption
                        save_tasks.append(save_file(media))

                    except Exception:
                        errors += 1
                        continue

                # run all save tasks concurrently and collect results
                results = []
                if save_tasks:
                    results = await asyncio.gather(*save_tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, Exception):
                        errors += 1
                    else:
                        ok, code = result
                        if ok:
                            total_files += 1
                        elif code == 0:
                            duplicate += 1
                        elif code == 2:
                            errors += 1

                batch_time = time.time() - batch_start
                batch_times.append(batch_time)
                elapsed = time.time() - start_time
                progress = current - int(getattr(temp, "CURRENT", 0))
                percentage = (progress / total_fetch) * 100 if total_fetch else 0
                avg_batch_time = (sum(batch_times) / len(batch_times)) if batch_times else 1
                eta = ((total_fetch - progress) / BATCH_SIZE) * avg_batch_time if BATCH_SIZE else 0
                progress_bar = get_progress_bar(int(percentage))

                try:
                    await msg.edit(
                        f"📊 Indexing Progress 📦 Batch {batch + 1}/{batches}\n"
                        f"{progress_bar} <code>{percentage:.1f}%</code>\n\n"
                        f"Total Messages: <code>{total_messages}</code>\n"
                        f"Total Fetched: <code>{total_fetch}</code>\n"
                        f"Fetched: <code>{current}</code>\n"
                        f"Saved: <code>{total_files}</code>\n"
                        f"Duplicates: <code>{duplicate}</code>\n"
                        f"Deleted: <code>{deleted}</code>\n"
                        f"Non-Media: <code>{no_media + unsupported}</code> (Unsupported: <code>{unsupported}</code>)\n"
                        f"Errors: <code>{errors}</code>\n"
                        f"⏱️ Elapsed: <code>{get_readable_time(elapsed)}</code>\n"
                        f"⏰ ETA: <code>{get_readable_time(eta)}</code>",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Cancel', callback_data='index_cancel')]])
                    )
                except MessageNotModified:
                    pass
                except Exception as e:
                    logger.exception(e)

            # final summary
            elapsed = time.time() - start_time
            await msg.edit(
                f"✅ Indexing Completed!\n"
                f"Total Messages: <code>{total_messages}</code>\n"
                f"Total Fetched: <code>{total_fetch}</code>\n"
                f"Fetched: <code>{current}</code>\n"
                f"Saved: <code>{total_files}</code>\n"
                f"Duplicates: <code>{duplicate}</code>\n"
                f"Deleted: <code>{deleted}</code>\n"
                f"Non-Media: <code>{no_media + unsupported}</code> (Unsupported: <code>{unsupported}</code>)\n"
                f"Errors: <code>{errors}</code>\n"
                f"⏱️ Elapsed: <code>{get_readable_time(elapsed)}</code>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Close', callback_data='close_data')]])
            )

        except Exception as e:
            logger.exception(e)
            try:
                await msg.edit(
                    f"❌ Error: <code>{e}</code>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Close', callback_data='close_data')]])
                )
            except Exception:
                pass
