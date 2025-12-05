import logging
import re
import asyncio

from utils import temp
from info import ADMINS
from pyrogram import Client, filters, enum
from pyrogram.enums import ChatMemberStatus, MessageMediaType
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.errors.exceptions.bad_request_400 import (
    ChannelInvalid,
    ChatAdminRequired,
    UsernameInvalid,
    UsernameNotModified,
)
from info import INDEX_REQ_CHANNEL as LOG_CHANNEL
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
lock = asyncio.Lock()

# ---------- Callback handler for moderator buttons ----------
@Client.on_callback_query(filters.regex(r'^index'))
async def index_files(bot, query):
    # cancel pressed from progress dialog
    if query.data.startswith('index_cancel'):
        temp.CANCEL = True
        return await query.answer("Cancelling Indexing")

    # expected format: index#action#chat#last_msg_id#from_user
    try:
        _, action, chat, lst_msg_id, from_user = query.data.split("#")
    except Exception:
        return await query.answer("Invalid data", show_alert=True)

    if action == 'reject':
        # moderator rejected the request
        await query.message.delete()
        await bot.send_message(
            int(from_user),
            f'**__Your Submission For Indexing {chat} Has Been Decliened By Our Moderators__**.',
            reply_to_message_id=int(lst_msg_id)
        )
        return

    # only one indexing at a time
    if lock.locked():
        return await query.answer('Wait until previous process complete.', show_alert=True)

    msg = query.message
    await query.answer('Processing...⏳', show_alert=True)

    # notify submitter if not admin
    if int(from_user) not in ADMINS:
        await bot.send_message(
            int(from_user),
            f'**__Your Submission For Indexing {chat} Has been Accepted By Our Moderators And Will Be Added Soon.__**',
            reply_to_message_id=int(lst_msg_id)
        )

    # update moderator message
    await msg.edit(
        "Starting Indexing",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton('Cancel', callback_data='index_cancel')]]
        )
    )

    # ensure chat is int if possible
    try:
        chat_obj = int(chat)
    except Exception:
        chat_obj = chat

    # start indexing
    # pass lst_msg_id as int (it's the last message id forwarded by user)
    await index_files_to_db(int(lst_msg_id), chat_obj, msg, bot)


# ---------- /index command for users ----------
@Client.on_message(filters.private & filters.command('index'))
async def send_for_index(bot, message):
    neo = await bot.ask(
        message.chat.id,
        "**__Now Send Me Your Channel Last Post Link Or Forward A Last Message From Your Index Channel.\n\nAnd You Can Skip Number By__ \n/setskip __YᴏᴜʀSᴋɪᴘNᴜᴍʙᴇʀ__**"
    )

    # forwarded message from a channel
    if getattr(neo, "forward_from_chat", None) and neo.forward_from_chat.type == enums.ChatType.CHANNEL:
        last_msg_id = neo.forward_from_message_id
        chat_id = neo.forward_from_chat.username or neo.forward_from_chat.id

    # text link
    elif getattr(neo, "text", None):
        regex = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")
        match = regex.match(neo.text.strip())
        if not match:
            return await neo.reply('**__Invalid Link 🚫\n\nTry Again By__ /index**')
        chat_id = match.group(4)
        last_msg_id = int(match.group(5))
        if chat_id.isnumeric():
            chat_id = int("-100" + chat_id)

    else:
        return

    # verify bot can access chat
    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await neo.reply('**__This May Be a Private Channel / Group. Make Me Admin Over There To Index The Files__**')
    except (UsernameInvalid, UsernameNotModified):
        return await neo.reply('Invalid Link specified.')
    except Exception as e:
        logger.exception(e)
        return await neo.reply(f'Errors - {e}')

    # ensure message exists
    try:
        k = await bot.get_messages(chat_id, last_msg_id)
    except Exception:
        return await message.reply('**__Make Sure That I am An Admin In The Channel, If Channel Is Private__**')
    if k.empty:
        return await message.reply('**__This May Be Group And I Am Not Am Admin Of The Group__**')

    # if admin — confirm right away in the same chat
    if message.from_user.id in ADMINS:
        buttons = [[
            InlineKeyboardButton('Cᴏɴғɪʀᴍ ✅', callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')
        ], [
            InlineKeyboardButton('Dᴇᴄʟɪɴᴇ ❌', callback_data='close_data')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        return await message.reply(
            f'**__Do you Want To Index This Channel or Group ?\n\n🆔 Chat ID/ Username :__** \n<code>▶️ {chat_id} ◀️</code>\n\n**📄 __Last Message ID :__ ** <code>{last_msg_id}</code>',
            reply_markup=reply_markup
        )

    # if not admin — send to log channel for approval
    if isinstance(chat_id, int):
        try:
            link = (await bot.create_chat_invite_link(chat_id)).invite_link
        except ChatAdminRequired:
            return await message.reply('**__Make Sure I am An Admin in the Chat and have Permission to Invite Users.__**')
    else:
        link = f"@{neo.forward_from_chat.username}" if getattr(neo, "forward_from_chat", None) else f"@{chat_id}"

    buttons = [[
        InlineKeyboardButton('Aᴄᴄᴇᴘᴛ Iɴᴅᴇx', callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')
    ], [
        InlineKeyboardButton('Rᴇjᴇᴄᴛ Iɴᴅᴇx', callback_data=f'index#reject#{chat_id}#{message.id}#{message.from_user.id}'),
    ]]
    reply_markup = InlineKeyboardMarkup(buttons)

    await bot.send_message(
        LOG_CHANNEL,
        f'#IndexRequest\n\n**__By__ : {message.from_user.mention} (<code>{message.from_user.id}</code>)\n__Chat ID/ Username__ - <code> {chat_id}</code>\n__Last Message ID__ - <code>{last_msg_id}</code>\nInviteLink - {link}',
        reply_markup=reply_markup
    )
    await message.reply('**__ThankYou For the Contribution, Wait For My Moderators to Verify the Files.__**')


# ---------- /setskip command ----------
@Client.on_message(filters.command('setskip') & filters.user(ADMINS))
async def set_skip_number(bot, message):
    if ' ' in message.text:
        _, skip = message.text.split(" ", 1)
        try:
            skip = int(skip)
        except Exception:
            return await message.reply("**__Skip Number Should Be An Integer__**")
        temp.CURRENT = int(skip)
        await message.reply(f"**__Successfully Set SKIP Number As__** {skip}")
    else:
        await message.reply("**__Give Me a Skip Number__**")


# ---------- Core indexing function (fixed for New -> Old) ----------
async def index_files_to_db(lst_msg_id, chat, msg, bot):
    """
    lst_msg_id: integer message id to start from (the last/most recent message forwarded by user)
    chat: chat id or username
    msg: the moderator message object used for progress updates
    bot: pyrogram client
    """
    total_files = 0
    duplicate = 0
    errors = 0
    deleted = 0
    no_media = 0
    unsupported = 0

    # we lock to ensure single indexing at a time
    async with lock:
        try:
            temp.CANCEL = False

            # ---- Process the starting/fwd message first (if it exists) ----
            try:
                start_msg = await bot.get_messages(chat, lst_msg_id)
            except Exception as e:
                logger.exception("Failed to fetch starting message: %s", e)
                await msg.edit(f"**__Failed to fetch start message: {e}__**")
                return

            # helper to process a single message object
            async def _process_message(message):
                nonlocal total_files, duplicate, errors, deleted, no_media, unsupported
                if temp.CANCEL:
                    return False  # caller will handle cancellation

                if not message or message.empty:
                    deleted += 1
                    return True

                if not message.media:
                    no_media += 1
                    return True

                if message.media not in [
                    enums.MessageMediaType.VIDEO,
                    enums.MessageMediaType.AUDIO,
                    enums.MessageMediaType.DOCUMENT,
                ]:
                    unsupported += 1
                    return True

                media = getattr(message, message.media.value, None)
                if not media:
                    unsupported += 1
                    return True

                # keep caption on the media object as your save_file expects
                media.caption = message.caption

                try:
                    ok, status = await save_file(media)
                except Exception as e:
                    logger.exception("save_file error: %s", e)
                    errors += 1
                    return True

                if ok:
                    total_files += 1
                elif status == 0:
                    duplicate += 1
                else:
                    errors += 1

                return True

            # process the starting message explicitly (so indexing starts exactly from forwarded message)
            if start_msg and not start_msg.empty:
                await _process_message(start_msg)

            # current_offset will be decreased as we move older
            # start from (lst_msg_id - 1) to fetch older messages
            current_offset_id = lst_msg_id - 1 if isinstance(lst_msg_id, int) else None

            # batching loop: fetch older messages in chunks of 200
            while True:
                if temp.CANCEL:
                    await msg.edit(
                        f"**__Sᴜᴄᴄᴇssғᴜʟʟʏ Cᴀɴᴄᴇʟʟᴇᴅ 🥹\n\nSᴀᴠᴇᴅ__ <code>{total_files}</code> __Fɪʟᴇs Tᴏ Dᴀᴛᴀʙᴀsᴇ !\n__Dᴜᴘʟɪᴄᴀᴛᴇ Fɪʟᴇs :__ <code>{duplicate}</code>\n__Dᴇʟᴇᴛᴇᴅ :__ <code>{deleted}</code>\n__Nᴏɴ-Mᴇᴅɪᴀ :__ <code>{no_media + unsupported}</code>(Unsupported `{unsupported}` )\n__Eʀʀᴏʀs :__ <code>{errors}</code>**"
                    )
                    return

                # fetch a batch of older messages
                try:
                    # offset_id points to the message id from which pyrogram will fetch older messages
                    batch = bot.iter_messages(
                        chat_id=chat,
                        offset_id=current_offset_id,
                        limit=200
                    )
                except Exception as e:
                    logger.exception("iter_messages failed: %s", e)
                    await msg.edit(f"**__Failed while fetching messages: {e}__**")
                    return

                count = 0
                last_id_in_batch = None

                async for message in batch:
                    # safety: stop if cancellation requested
                    if temp.CANCEL:
                        break

                    # iterate older messages (message.message_id should be <= current_offset_id)
                    count += 1
                    last_id_in_batch = message.message_id

                    await _process_message(message)

                # if no messages returned, we're done
                if count == 0:
                    break

                # prepare next offset: continue from older than last_id_in_batch
                # subtract 1 to avoid reprocessing last message (ids are integers)
                current_offset_id = last_id_in_batch - 1 if last_id_in_batch else None

                # progress update (non-blocking)
                try:
                    can = [[InlineKeyboardButton('Cancel', callback_data='index_cancel')]]
                    await msg.edit_text(
                        f"**Processed:** <code>{lst_msg_id - (current_offset_id if current_offset_id else 0)}</code>\n"
                        f"**Saved:** <code>{total_files}</code>\n"
                        f"**Duplicate:** <code>{duplicate}</code>\n"
                        f"**Deleted:** <code>{deleted}</code>\n"
                        f"**No Media:** <code>{no_media + unsupported}</code>\n"
                        f"**Errors:** <code>{errors}</code>",
                        reply_markup=InlineKeyboardMarkup(can)
                    )
                except Exception:
                    # ignore UI update failures
                    pass

                # small safety sleep to avoid FloodWait spikes
                await asyncio.sleep(0.1)

            # finished
            await msg.edit(
                f'**__Finished__ ✅ : <code>{total_files}</code>\n'
                f'__Duplicates__ : <code>{duplicate}</code>\n'
                f'__Deleted__ : <code>{deleted}</code>\n'
                f'__Non-Media__ : <code>{no_media + unsupported}</code>\n'
                f'__Errors__ : <code>{errors}</code>**'
            )
        finally:
            temp.CANCEL = False

