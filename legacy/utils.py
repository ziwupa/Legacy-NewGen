"""Utilities"""

#    Friendly Telegram (telegram userbot)
#    Copyright (C) 2018-2021 The Authors

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.

#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

# ©️ Dan Gazizullin, 2021-2023
# This file is a part of Hikka Userbot
# 🌐 https://github.com/hikariatama/Hikka
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import ast
import asyncio
import atexit as _atexit
import contextlib
import functools
import inspect
import io
import logging
import os
import random
import re
import shlex
import signal
import socket
import string
import time
import typing
from datetime import timedelta
from platform import uname
from urllib.parse import urlparse

import git
import grapheme
import legacytl
import requests
import ujson
from aiogram.types import Message as AiogramMessage
from legacytl import hints
from legacytl.tl.custom.message import Message
from legacytl.tl.functions.account import UpdateNotifySettingsRequest
from legacytl.tl.functions.channels import (
    CreateChannelRequest,
    EditAdminRequest,
    EditPhotoRequest,
    InviteToChannelRequest,
)
from legacytl.tl.functions.messages import (
    CreateForumTopicRequest,
    EditForumTopicRequest,
    GetDialogFiltersRequest,
    GetForumTopicsByIDRequest,
    SendReactionRequest,
    SetHistoryTTLRequest,
    UpdateDialogFilterRequest,
)
from legacytl.tl.types import (
    Channel,
    Chat,
    ChatAdminRights,
    InputDocument,
    InputMediaWebPage,
    InputPeerNotifySettings,
    MessageEntityBankCard,
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityBotCommand,
    MessageEntityCashtag,
    TypeInputMedia,
    MessageEntityCode,
    MessageEntityCustomEmoji,
    MessageEntityEmail,
    MessageEntityHashtag,
    MessageEntityItalic,
    MessageEntityMention,
    MessageEntityMentionName,
    MessageEntityPhone,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
    MessageEntityUnknown,
    MessageEntityUrl,
    MessageMediaWebPage,
    PeerChannel,
    PeerChat,
    PeerUser,
    ReactionCustomEmoji,
    ReactionEmoji,
    UpdateNewChannelMessage,
    User,
    ForumTopic,
    ForumTopicDeleted,
)

from ._internal import fw_protect
from .inline.types import BotInlineCall, InlineCall, InlineMessage
from .tl_cache import CustomTelegramClient
from .types import LegacyReplyMarkup, ListLike, Module

FormattingEntity = typing.Union[
    MessageEntityUnknown,
    MessageEntityMention,
    MessageEntityHashtag,
    MessageEntityBotCommand,
    MessageEntityUrl,
    MessageEntityEmail,
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntityTextUrl,
    MessageEntityMentionName,
    MessageEntityPhone,
    MessageEntityCashtag,
    MessageEntityUnderline,
    MessageEntityStrike,
    MessageEntityBlockquote,
    MessageEntityBankCard,
    MessageEntitySpoiler,
]

emoji_pattern = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map symbols
    "\U0001f1e0-\U0001f1ff"  # flags (iOS)
    "]+",
    flags=re.UNICODE,
)

parser = legacytl.utils.sanitize_parse_mode("html")
logger = logging.getLogger(__name__)

# Bot API dialect of custom emojis, used by Heroku and by Telegram itself
TG_EMOJI_TAG_PATTERN = re.compile(
    r"<tg-emoji\s+emoji-id=(?:\"([^\"]+)\"|'([^']+)'|([^\s>]+))>(.*?)</tg-emoji>",
    flags=re.IGNORECASE | re.DOTALL,
)

# Dialect of Hikka and Legacy
LEGACY_EMOJI_TAG_PATTERN = re.compile(
    r"<emoji\s+document_id=(?:\"([^\"]+)\"|'([^']+)'|([^\s>]+))>(.*?)</emoji>",
    flags=re.IGNORECASE | re.DOTALL,
)


def use_exteragram_emoji_links(message: typing.Any) -> bool:
    """
    Whether custom emojis should be turned into `tg://emoji` links. Telegram
    only renders real custom emojis for premium accounts, while third-party
    clients (exteraGram and alike) render such links as emojis for everyone
    :param message: Message, client or any object holding a client
    :return: Whether the fallback is needed
    """
    if isinstance(message, Message):
        client = getattr(message, "client", None)
    elif hasattr(message, "loader") and hasattr(message, "legacy_me"):
        client = message
    else:
        client = getattr(message, "client", None)

    if client is None or getattr(
        getattr(client, "legacy_me", None), "premium", False
    ):
        return False

    loader = getattr(client, "loader", None)
    db = getattr(loader, "db", None)
    if db is None:
        return False

    return bool(db.get("LegacySettingsMod", "exteragram_emoji", True))


def replace_tg_emoji_tags(response: str, message: typing.Any) -> str:
    """
    Replaces custom emoji tags of both dialects with `tg://emoji` links, if the
    account is not premium and the fallback is enabled. Premium accounts get
    the tags untouched, so that they are parsed into real custom emojis
    :param response: Text to process
    :param message: Message, client or any object holding a client
    :return: Processed text
    """
    if not isinstance(response, str) or (
        "<tg-emoji" not in response and "<emoji" not in response
    ):
        return response

    if not use_exteragram_emoji_links(message):
        return response

    def replace(match: re.Match) -> str:
        emoji_id = next(group for group in match.group(1, 2, 3) if group is not None)
        return f'<a href="tg://emoji?id={emoji_id}">{match.group(4)}</a>'

    response = TG_EMOJI_TAG_PATTERN.sub(replace, response)
    return LEGACY_EMOJI_TAG_PATTERN.sub(replace, response)


def get_args(message: typing.Union[Message, str]) -> typing.List[str]:
    """
    Get arguments from message
    :param message: Message or string to get arguments from
    :return: List of arguments
    """
    prefix = message.client.loader.get_prefix(message.sender_id)

    if not (message := getattr(message, "message", message)):
        return False

    if message.startswith(prefix):
        message = message[len(prefix):].strip()

    if len(message := message.split(maxsplit=1)) <= 1:
        return []
    
    message = message[1]

    try:
        split = shlex.split(message)
    except ValueError:
        return message  # Cannot split, let's assume that it's just one long message

    return list(filter(lambda x: len(x) > 0, split))


def get_args_raw(message: typing.Union[Message, str]) -> str:
    """
    Get the parameters to the command as a raw string (not split)
    :param message: Message or string to get arguments from
    :return: Raw string of arguments
    """
    prefix = message.client.loader.get_prefix(message.sender_id)
    
    if not (message := getattr(message, "message", message)):
        return False
    
    if message.startswith(prefix):
        message = message[len(prefix):].strip()

    return args[1] if len(args := message.split(maxsplit=1)) > 1 else ""


def get_args_html(message: Message) -> str:
    """
    Get the parameters to the command as string with HTML (not split)
    :param message: Message to get arguments from
    :return: String with HTML arguments
    """
    prefix = message.client.loader.get_prefix()

    if not (message := message.text):
        return False

    if prefix not in message:
        return message

    raw_text, entities = parser.parse(message)

    raw_text = parser._add_surrogate(raw_text)

    try:
        command = raw_text[
            raw_text.index(prefix) : raw_text.index(" ", raw_text.index(prefix) + 1)
        ]
    except ValueError:
        return ""

    command_len = len(command) + 1

    return parser.unparse(
        parser._del_surrogate(raw_text[command_len:]),
        relocate_entities(entities, -command_len, raw_text[command_len:]),
    )


def get_args_split_by(
    message: typing.Union[Message, str],
    separator: typing.Union[str, typing.List[str]],
) -> typing.List[str]:
    """
    Split args with a specific separator
    :param message: Message or string to get arguments from
    :param separator: Separator to split by
    :return: List of arguments
    """
    raw_args = get_args_raw(message)
    if isinstance(separator, str):
        sections = raw_args.split(separator)
    else:
        sections = [raw_args]
        for sep in separator:
            new_sections = []
            for section in sections:
                new_sections.extend(section.split(sep))
            sections = new_sections
    return [section.strip() for section in sections if section.strip()]


def get_chat_id(message: typing.Union[Message, AiogramMessage]) -> int:
    """
    Get the chat ID, but without -100 if its a channel
    :param message: Message to get chat ID from
    :return: Chat ID
    """
    return legacytl.utils.resolve_id(
        getattr(message, "chat_id", None)
        or getattr(getattr(message, "chat", None), "id", None)
    )[0]


def get_entity_id(entity: hints.Entity) -> int:
    """
    Get entity ID
    :param entity: Entity to get ID from
    :return: Entity ID
    """
    return legacytl.utils.get_peer_id(entity)


def escape_html(text: str, /) -> str:  # sourcery skip
    """
    Pass all untrusted/potentially corrupt input here
    :param text: Text to escape
    :return: Escaped text
    """
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_quotes(text: str, /) -> str:
    """
    Escape quotes to html quotes
    :param text: Text to escape
    :return: Escaped text
    """
    return escape_html(text).replace('"', "&quot;")


def get_base_dir() -> str:
    """
    Get directory of this file
    :return: Directory of this file
    """
    return get_dir(__file__)


def get_dir(mod: str) -> str:
    """
    Get directory of given module
    :param mod: Module's `__file__` to get directory of
    :return: Directory of given module
    """
    return os.path.abspath(os.path.dirname(os.path.abspath(mod)))


async def get_user(message: Message) -> typing.Optional[User]:
    """
    Get user who sent message, searching if not found easily
    :param message: Message to get user from
    :return: User who sent message
    """
    try:
        return await message.get_sender()
    except ValueError:  # Not in database. Lets go looking for them.
        logger.debug("User not in session cache. Searching...")

    if isinstance(message.peer_id, PeerUser):
        await message.client.get_dialogs()
        return await message.get_sender()

    if isinstance(message.peer_id, (PeerChannel, PeerChat)):
        async for user in message.client.iter_participants(
            message.peer_id,
            aggressive=True,
        ):
            if user.id == message.sender_id:
                return user

        logger.error("User isn't in the group where they sent the message")
        return None

    logger.error("`peer_id` is not a user, chat or channel")
    return None


def run_sync(func, *args, **kwargs):
    """
    Run a non-async function in a new thread and return an awaitable
    :param func: Sync-only function to execute
    :return: Awaitable coroutine
    """
    return asyncio.get_event_loop().run_in_executor(
        None,
        functools.partial(func, *args, **kwargs),
    )


def run_async(loop: asyncio.AbstractEventLoop, coro: typing.Awaitable) -> typing.Any:
    """
    Run an async function as a non-async function, blocking till it's done
    :param loop: Event loop to run the coroutine in
    :param coro: Coroutine to run
    :return: Result of the coroutine
    """
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


def censor(
    obj: typing.Any,
    to_censor: typing.Optional[typing.Iterable[str]] = None,
    replace_with: str = "redacted_{count}_chars",
):
    """
    May modify the original object, but don't rely on it
    :param obj: Object to censor, preferrably legacytl
    :param to_censor: Iterable of strings to censor
    :param replace_with: String to replace with, {count} will be replaced with the number of characters
    :return: Censored object
    """
    if to_censor is None:
        to_censor = ["phone"]

    for k, v in vars(obj).items():
        if k in to_censor:
            setattr(obj, k, replace_with.format(count=len(v)))
        elif k[0] != "_" and hasattr(v, "__dict__"):
            setattr(obj, k, censor(v, to_censor, replace_with))

    return obj


def relocate_entities(
    entities: typing.List[FormattingEntity],
    offset: int,
    text: typing.Optional[str] = None,
) -> typing.List[FormattingEntity]:
    """
    Move all entities by offset (truncating at text)
    :param entities: List of entities
    :param offset: Offset to move by
    :param text: Text to truncate at
    :return: List of entities
    """
    length = len(text) if text is not None else 0

    for ent in entities.copy() if entities else ():
        ent.offset += offset
        if ent.offset < 0:
            ent.length += ent.offset
            ent.offset = 0
        if text is not None and ent.offset + ent.length > length:
            ent.length = length - ent.offset
        if ent.length <= 0:
            entities.remove(ent)

    return entities


async def answer_file(
    message: typing.Union[Message, InlineCall, InlineMessage],
    file: typing.Union[str, bytes, io.IOBase, InputDocument],
    caption: typing.Optional[str] = "",
    **kwargs,
):
    """
    Use this to answer a message with a document
    :param message: Message to answer
    :param file: File to send - url, path or bytes
    :param caption: Caption to send
    :param kwargs: Extra kwargs to pass to `send_file`
    :return: Sent message

    :example:
        >>> await utils.answer_file(message, "test.txt")
        >>> await utils.answer_file(
            message,
            "https://mods.hikariatama.ru/badges/artai.jpg",
            "This is the cool module, check it out!",
        )
    """
    if isinstance(message, (InlineCall, InlineMessage)):
        message = message.form["caller"]

    try:
        if message.out:
            response = await message.edit(
                text=caption,
                file=file,
                **kwargs,
            )
        else:
            if topic := get_topic(message):
                kwargs.setdefault("reply_to", topic)
            response = await message.client.send_file(
                message.peer_id,
                file,
                caption=caption,
                **kwargs,
            )
    except Exception:
        if caption:
            logger.warning(
                "Failed to send file, sending plain text instead", exc_info=True
            )
            return await answer(message, caption, **kwargs)

        raise

    return response


async def answer(
    message: typing.Union[Message, InlineCall, InlineMessage],
    response: str,
    *,
    reply_markup: typing.Optional[LegacyReplyMarkup] = None,
    file: typing.Optional[TypeInputMedia] = None,
    **kwargs,
) -> typing.Union[InlineCall, InlineMessage, Message]:
    """
    Use this to give the response to a command
    :param message: Message to answer to. Can be a tl message or hikka inline object
    :param response: Response to send
    :param reply_markup: Reply markup to send. If specified, inline form will be used
    :return: Message or inline object

    :example:
        >>> await utils.answer(message, "Hello world!")
        >>> await utils.answer(
            message,
            "https://some-url.com/photo.jpg",
            caption="Hello, this is your photo!",
            asfile=True,
        )
        >>> await utils.answer(
            message,
            "Hello world!",
            reply_markup={"text": "Hello!", "data": "world"},
            silent=True,
            disable_security=True,
        )
    """
    # Compatibility with FTG\GeekTG

    if isinstance(message, list) and message:
        message = message[0]

    if reply_markup is not None:
        if not isinstance(reply_markup, (list, dict)):
            raise ValueError("reply_markup must be a list or dict")

        if reply_markup:
            kwargs.pop("message", None)
            if isinstance(message, (InlineMessage, InlineCall, BotInlineCall)):
                await message.edit(response, reply_markup, **kwargs)
                return

            reply_markup = message.client.loader.inline._normalize_markup(reply_markup)
            result = await message.client.loader.inline.form(
                response,
                message=message if message.out else get_chat_id(message),
                reply_markup=reply_markup,
                **kwargs,
            )
            return result

    if isinstance(message, (InlineMessage, InlineCall, BotInlineCall)):
        await message.edit(response, **kwargs)
        return message

    kwargs.setdefault("link_preview", False)

    if not (edit := (message.out and not message.via_bot_id and not message.fwd_from)):
        kwargs.setdefault(
            "reply_to",
            getattr(message, "reply_to_msg_id", None),
        )
    elif "reply_to" in kwargs:
        kwargs.pop("reply_to")

    parse_mode = legacytl.utils.sanitize_parse_mode(
        kwargs.pop(
            "parse_mode",
            message.client.parse_mode,
        )
    )

    if isinstance(response, str):
        response = replace_tg_emoji_tags(response, message)

    if isinstance(response, str) and not kwargs.pop("asfile", False):
        text, entities = parse_mode.parse(response)

        if len(text) >= 4096 and not hasattr(message, "legacy_grepped"):
            try:
                if not message.client.loader.inline.init_complete:
                    raise

                entities = [
                    e for e in entities if not isinstance(e, MessageEntityCustomEmoji)
                ]

                strings = list(smart_split(text, entities, 4096))

                if len(strings) > 10:
                    raise

                list_ = await message.client.loader.inline.list(
                    message=message,
                    strings=strings,
                )

                if not list_:
                    raise

                return list_
            except Exception:
                file = io.BytesIO(text.encode("utf-8"))
                file.name = "command_result.txt"

                result = await answer_file(
                    message,
                    file,
                    message.client.loader.lookup("translations").strings("too_long"),
                )

                return result

        if edit:
            result = await message.edit(
                text,
                parse_mode=lambda t: (t, entities),
                file=file,
                **kwargs,
            )
        else:
            result = await message.respond(
                text,
                parse_mode=lambda t: (t, entities),
                **kwargs,
            )
    elif isinstance(response, Message):
        if edit:
            result = await message.edit(
                response.message,
                parse_mode=lambda t: (t, response.entities or []),
                file=(
                    response.media
                    if not isinstance(response.media, MessageMediaWebPage)
                    else InputMediaWebPage(response.media.webpage.url)
                ),
                invert_media=response.invert_media,
                **kwargs,
            )
        else:
            result = await message.respond(
                response.message,
                parse_mode=lambda t: (t, response.entities or []),
                file=(
                    response.media
                    if not isinstance(response.media, MessageMediaWebPage)
                    else InputMediaWebPage(response.media.webpage.url)
                ),
                invert_media=response.invert_media,
                **kwargs,
            )
    else:
        if isinstance(response, bytes):
            response = io.BytesIO(response)
        elif isinstance(response, str):
            response = io.BytesIO(response.encode("utf-8"))

        if name := kwargs.pop("filename", None):
            response.name = name

        if message.media is not None and edit:
            await message.edit(file=response, **kwargs)
        else:
            kwargs.setdefault(
                "reply_to",
                getattr(message, "reply_to_msg_id", get_topic(message)),
            )
            result = await message.client.send_file(message.peer_id, response, **kwargs)
            if message.out:
                await message.delete()

    return result


async def get_target(message: Message, arg_no: int = 0) -> typing.Optional[int]:
    """
    Get target from message
    :param message: Message to get target from
    :param arg_no: Argument number to get target from
    :return: Target
    """

    if any(
        isinstance(entity, MessageEntityMentionName)
        for entity in message.entities or []
    ):
        e = sorted(
            filter(lambda x: isinstance(x, MessageEntityMentionName), message.entities),
            key=lambda x: x.offset,
        )[0]
        return e.user_id

    if len(get_args(message)) > arg_no:
        user = get_args(message)[arg_no]
        if user.isdigit():
            user = int(user)
    elif message.is_reply:
        return (await message.get_reply_message()).sender_id
    elif hasattr(message.peer_id, "user_id"):
        user = message.peer_id.user_id
    else:
        return None

    try:
        entity = await message.client.get_entity(user)
    except ValueError:
        return None
    else:
        if isinstance(entity, User):
            return entity.id


def merge(a: dict, b: dict, /) -> dict:
    """
    Merge with replace dictionary a to dictionary b
    :param a: Dictionary to merge
    :param b: Dictionary to merge to
    :return: Merged dictionary
    """
    for key in a:
        if key in b:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                b[key] = merge(a[key], b[key])
            elif isinstance(a[key], list) and isinstance(b[key], list):
                b[key] = list(set(b[key] + a[key]))
            else:
                b[key] = a[key]

        b[key] = a[key]

    return b


async def set_avatar(
    client: CustomTelegramClient,
    peer: hints.Entity,
    avatar: str,
) -> bool:
    """
    Sets an entity avatar
    :param client: Client to use
    :param peer: Peer to set avatar to
    :param avatar: Avatar to set
    :return: True if avatar was set, False otherwise
    """
    if isinstance(avatar, str) and check_url(avatar):
        f = (
            await run_sync(
                requests.get,
                avatar,
            )
        ).content
    elif isinstance(avatar, str) and os.path.exists(avatar):
        f = avatar
    elif isinstance(avatar, bytes):
        f = avatar
    else:
        return False

    await fw_protect()
    res = await client(
        EditPhotoRequest(
            channel=peer,
            photo=await client.upload_file(f, file_name="photo.png"),
        )
    )

    await fw_protect()

    try:
        await client.delete_messages(
            peer,
            message_ids=[
                next(
                    update
                    for update in res.updates
                    if isinstance(update, UpdateNewChannelMessage)
                ).message.id
            ],
        )
    except Exception:
        pass

    return True


async def invite_inline_bot(
    client: CustomTelegramClient,
    peer: hints.EntityLike,
) -> None:
    """
    Invites inline bot to a chat
    :param client: Client to use
    :param peer: Peer to invite bot to
    :return: None
    :raise RuntimeError: If error occurred while inviting bot
    """

    try:
        await client(InviteToChannelRequest(peer, [client.loader.inline.bot_username]))
    except Exception as e:
        raise RuntimeError(
            "Can't invite inline bot to old asset chat, which is required by module"
        ) from e

    with contextlib.suppress(Exception):
        await client(
            EditAdminRequest(
                channel=peer,
                user_id=client.loader.inline.bot_username,
                admin_rights=ChatAdminRights(ban_users=True),
                rank="Legacy",
            )
        )


async def asset_channel(
    client: CustomTelegramClient,
    title: str,
    description: str,
    *,
    channel: bool = False,
    silent: bool = False,
    archive: bool = False,
    invite_bot: bool = False,
    avatar: typing.Optional[str] = None,
    ttl: typing.Optional[int] = None,
    forum: bool = False,
    hide_general: bool = False,
    _folder: typing.Optional[str] = None,
) -> typing.Tuple[Channel, bool]:
    """
    Create new channel (if needed) and return its entity
    :param client: Telegram client to create channel by
    :param title: Channel title
    :param description: Description
    :param channel: Whether to create a channel or supergroup
    :param silent: Automatically mute channel
    :param archive: Automatically archive channel
    :param invite_bot: Add inline bot and assure it's in chat
    :param avatar: Url to an avatar to set as pfp of created peer
    :param ttl: Time to live for messages in channel
    :param forum: Whether to create a forum channel
    :param hide_general: Hide '#General' topic
    :return: Peer and bool: is channel new or pre-existent
    """
    if not hasattr(client, "_channels_cache"):
        client._channels_cache = {}

    if (
        title in client._channels_cache
        and client._channels_cache[title]["exp"] > time.time()
    ):
        return client._channels_cache[title]["peer"], False

    # legacytl heroku / hikka chats conversion to legacy
    if title.startswith("hikka-"):
        title = title.replace("hikka-", "legacy-")
    if title.startswith("heroku-"):
        title = title.replace("heroku-", "legacy-")

    async for d in client.iter_dialogs():
        if d.title == title:
            client._channels_cache[title] = {"peer": d.entity, "exp": int(time.time())}
            if invite_bot:
                if all(
                    participant.id != client.loader.inline.bot_id
                    for participant in await client.get_participants(
                        d.entity, limit=100
                    )
                ):
                    await fw_protect()
                    await invite_inline_bot(client, d.entity)

            return d.entity, False

    await fw_protect()

    peer = (
        await client(
            CreateChannelRequest(
                title,
                description,
                megagroup=not channel,
                forum=forum,
            )
        )
    ).chats[0]

    if invite_bot:
        await fw_protect()
        await invite_inline_bot(client, peer)

    if silent:
        await fw_protect()
        await dnd(client, peer, archive)
    elif archive:
        await fw_protect()
        await client.edit_folder(peer, 1)

    if avatar:
        await fw_protect()
        await set_avatar(client, peer, avatar)

    if hide_general and forum:
        await fw_protect()
        await client(EditForumTopicRequest(peer=peer, topic_id=1, hidden=True))

    if ttl:
        await fw_protect()
        await client(SetHistoryTTLRequest(peer=peer, period=ttl))

    if _folder:
        folders = (await client(GetDialogFiltersRequest())).filters

        try:
            folder = next(
                folder
                for folder in folders
                if not isinstance(folder, legacytl.tl.types.DialogFilterDefault)
                and folder.title.text.lower() == _folder.lower()
            )
        except Exception:
            folder = None

        if folder and not any(
            peer.id == getattr(folder_peer, "channel_id", None)
            for folder_peer in folder.include_peers
        ):
            print(len(folder.include_peers))
            folder.include_peers.append(await client.get_input_entity(peer))
            print(len(folder.include_peers))

            await client(
                UpdateDialogFilterRequest(
                    folder.id,
                    folder,
                )
            )

    client._channels_cache[title] = {"peer": peer, "exp": int(time.time())}
    return peer, True

if typing.TYPE_CHECKING:
    from .database import Database

async def asset_forum_topic(
    client: CustomTelegramClient,
    db: 'Database',
    peer: hints.Entity,
    title: str,
    description: typing.Optional[str] = None,
    icon_emoji_id: typing.Optional[int] = None,
    invite_bot: bool = False,
) -> ForumTopic:
    entity = await client.get_entity(peer)

    if not isinstance(entity, Channel):
        raise TypeError(f"Expected entity to be 'Channel', but got '{type(entity).__name__}'")

    # The bot has to be a member before it can post here, and it only learns the
    # channel's access_hash from the update it receives when added — without that
    # every send raises "Could not find the input entity for PeerChannel".
    #
    # This has to run even when the topic is already cached below: a channel
    # carried over from hikka/heroku or restored from someone else's backup was
    # shared with a *different* inline bot than the one this session uses.
    if invite_bot:
        await fw_protect()
        if all(
            p.id != client.loader.inline.bot_id
            for p in await client.get_participants(entity, limit=100)
        ):
            await fw_protect()
            await invite_inline_bot(client, entity)

    async def create_topic() -> ForumTopic:
        result = await client(CreateForumTopicRequest(
            peer=entity,
            title=title,
            icon_emoji_id=(icon_emoji_id if client.legacy_me.premium else None)
        ))

        await fw_protect()

        await client.send_message(entity=entity, message=(description if description else f"<emoji document_id=5258503720928288433>ℹ️</emoji> <b>Content related to <i>'{title}'</i> will be here</b>"), reply_to=result.updates[0].id)

        await fw_protect()

        result = await client(GetForumTopicsByIDRequest(peer=entity, topics=[result.updates[0].id]))

        return result.topics[0]
    
    forums_cache = db.get("legacy.forums", "forums_cache", {})

    # Topics used to be cached by channel title, which does not survive a rename
    # of the channel nor a restore of another userbot's backup — the id does
    cache_key = str(entity.id)

    if cache_key not in forums_cache and entity.title in forums_cache:
        forums_cache[cache_key] = forums_cache.pop(entity.title)

    if (topic_id := forums_cache.get(cache_key, {}).get(title)):
        await fw_protect()
        topic = await client(GetForumTopicsByIDRequest(peer=entity, topics=[topic_id]))
        topic = topic.topics[0]

        if not isinstance(topic, ForumTopicDeleted):
            return topic
        else:
            logger.warning(f"Topic: '{title}' was found in the database but does not exist in the channel and will be recreated")
            await fw_protect()
            new_topic = await create_topic()
            forums_cache[cache_key][title] = new_topic.id

    else:
        await fw_protect()
        new_topic = await create_topic()
        forums_cache.setdefault(cache_key, {})[title] = new_topic.id
    
    db.set("legacy.forums", "forums_cache", forums_cache)

    return new_topic

async def wait_for_content_channel(db: 'Database', delay: float = 10) -> int:
    cid = db.get("legacy.forums", "channel_id", None)
    
    while not cid:
        logger.warning("Legacy content channel not found in database. Sleeping 10 seconds...")
        await asyncio.sleep(delay)
        cid = db.get("legacy.forums", "channel_id", None)
    
    return cid
    

async def dnd(
    client: CustomTelegramClient,
    peer: hints.Entity,
    archive: bool = True,
) -> bool:
    """
    Mutes and optionally archives peer
    :param peer: Anything entity-link
    :param archive: Archive peer, or just mute?
    :return: `True` on success, otherwise `False`
    """
    try:
        await client(
            UpdateNotifySettingsRequest(
                peer=peer,
                settings=InputPeerNotifySettings(
                    show_previews=False,
                    silent=True,
                    mute_until=2**31 - 1,
                ),
            )
        )

        if archive:
            await fw_protect()
            await client.edit_folder(peer, 1)
    except Exception:
        logger.exception("utils.dnd error")
        return False

    return True


def get_link(user: typing.Union[User, Channel], /) -> str:
    """
    Get telegram permalink to entity
    :param user: User or channel
    :return: Link to entity
    """
    return (
        f"tg://user?id={user.id}"
        if isinstance(user, User)
        else (
            f"tg://resolve?domain={user.username}"
            if getattr(user, "username", None)
            else ""
        )
    )


def chunks(_list: ListLike, n: int, /) -> typing.List[typing.List[typing.Any]]:
    """
    Split provided `_list` into chunks of `n`
    :param _list: List to split
    :param n: Chunk size
    :return: List of chunks
    """
    return [_list[i : i + n] for i in range(0, len(_list), n)]


def get_named_platform() -> str:
    """
    Returns formatted platform name
    :return: Platform name
    """

    host = get_current_platform() or _platforms.get("vds")
    if host:
        return f"{host.get('emoji')} {host.get('display_name')}"
    return "💎 VDS"


_platforms = {
    "raspberry": {
        "display_name": "Raspberry Pi",
        "emoji": "🍇",
        "emoji_document_id": 5467541303938019154,
    },
    "banana": {
        "display_name": "Banana Pi",
        "emoji": "🍌",
        "emoji_document_id": 5467541303938019154,
    },
    "orange": {
        "display_name": "Orange Pi",
        "emoji": "🍊",
        "emoji_document_id": 5467541303938019154,
    },
    "hikkahost": {
        "display_name": "HikkaHost",
        "emoji": "🌼",
        "emoji_document_id": 5458807006905264299,
    },
    "docker": {
        "display_name": "Docker",
        "emoji": "🐳",
        "emoji_document_id": 5456574628933693253,
    },
    "wsl": {
        "display_name": "WSL",
        "emoji": "🍀",
        "emoji_document_id": 5467541303938019154,
    },
    "aeza": {
        "display_name": "Aeza",
        "emoji": "🛡",
        "emoji_document_id": 5467541303938019154,
    },
    "oracle": {
        "display_name": "Oracle",
        "emoji": "🧨",
        "emoji_document_id": 5380110961090788815,
    },
    "userland": {
        "display_name": "Userland",
        "emoji": "🐧",
        "emoji_document_id": 5458508523858062696,
    },
    "railway": {
        "display_name": "Railway",
        "emoji": "🚂",
        "emoji_document_id": 5456525163795344370,
    },
    "vds": {
        "display_name": "VDS",
        "emoji": "💎",
        "emoji_document_id": 5467541303938019154,
    },
}


def get_platform(host_name):
    return _platforms.get(host_name.lower())


def _detect_by_uname():
    for platform in _platforms:
        if platform.lower() in uname().release.lower():
            return get_platform(platform)
    return None


def _detect_by_device_tree():
    try:
        with open("/proc/device-tree/model") as f:
            model = f.read()
    except OSError:
        # File is absent on non-ARM hosts, and present-but-unreadable in some
        # Android userlands (restricted /proc -> PermissionError). isfile()
        # can't distinguish the latter, so just fall through to the next
        # detector instead of crashing the caller (e.g. the web /can_add).
        return None
    for platform in _platforms:
        if platform.lower() in model.lower():
            return get_platform(platform)
    return None


def _detect_by_hostname():
    for platform in _platforms:
        if platform.lower() in socket.gethostname().lower():
            return get_platform(platform)
    return None


def _detect_by_env_vars():
    for platform in _platforms:
        if os.environ.get(platform.upper()) or os.environ.get(platform.lower()):
            return get_platform(platform)
    return None


def _get_default_platform():
    return get_platform("vds")


def get_current_platform():
    detection_chain = [
        _detect_by_uname,
        _detect_by_device_tree,
        _detect_by_hostname,
        _detect_by_env_vars,
        _get_default_platform,
    ]
    for detect in detection_chain:
        try:
            host = detect()
        except Exception:
            # Platform detection is best-effort; a single failing probe must
            # never break callers (this runs inside the web /can_add handler).
            host = None
        if host:
            return host


def get_platform_emoji() -> str:
    """
    Returns custom emoji for current platform
    :return: Emoji entity in string
    """
    BASE = "".join(
        (
            "<emoji document_id={}>🌙</emoji>",
            "<emoji document_id=5456453854453327393>🌙</emoji>",
            "<emoji document_id=5458918203608563276>🌙</emoji>",
            "<emoji document_id=5456290924868954389>🌙</emoji>",
        )
    )

    host = get_current_platform()
    if host:
        return BASE.format(host.get("emoji_document_id", 5467541303938019154))
    return BASE.format(5467541303938019154)


def uptime() -> int:
    """
    Returns userbot uptime in seconds
    """
    current_uptime = round(time.perf_counter() - init_ts)
    return current_uptime


def formatted_uptime() -> str:
    """
    Returns formatted uptime including days if applicable.
    :return: Formatted uptime
    """
    total_seconds = uptime()
    days, remainder = divmod(total_seconds, 86400)
    time_formatted = str(timedelta(seconds=remainder))
    if days > 0:
        return f"{days} day(s), {time_formatted}"
    return time_formatted


async def add_uptime(minutes: int) -> str:
    """
    Adds a custom uptime in minutes to the current uptime.
    :param minutes: The custom uptime in minutes to add
    """

    global init_ts
    seconds = minutes * 60
    init_ts -= seconds
    return "Added uptime!"


async def set_uptime(minutes: int) -> str:
    """
    Sets a custom uptime in minutes. This will adjust the init_ts accordingly.
    :param minutes: The custom uptime in minutes to set
    """
    global init_ts
    seconds = minutes * 60
    init_ts = time.perf_counter() - seconds

    return " Uptime is on offer!"


def ascii_face() -> str:
    """
    Returnes cute ASCII-art face
    :return: ASCII-art face
    """
    return escape_html(
        random.choice(
            [
                "ヽ(๑◠ܫ◠๑)ﾉ",
                "(◕ᴥ◕ʋ)",
                "ᕙ(`▽´)ᕗ",
                "(✿◠‿◠)",
                "(▰˘◡˘▰)",
                "(˵ ͡° ͜ʖ ͡°˵)",
                "ʕっ•ᴥ•ʔっ",
                "( ͡° ᴥ ͡°)",
                "(๑•́ ヮ •̀๑)",
                "٩(^‿^)۶",
                "(っˆڡˆς)",
                "ψ(｀∇´)ψ",
                "⊙ω⊙",
                "٩(^ᴗ^)۶",
                "(´・ω・)っ由",
                "( ͡~ ͜ʖ ͡°)",
                "✧♡(◕‿◕✿)",
                "โ๏௰๏ใ ื",
                "∩｡• ᵕ •｡∩ ♡",
                "(♡´౪`♡)",
                "(◍＞◡＜◍)⋈。✧♡",
                "╰(✿´⌣`✿)╯♡",
                "ʕ•ᴥ•ʔ",
                "ᶘ ◕ᴥ◕ᶅ",
                "▼・ᴥ・▼",
                "ฅ^•ﻌ•^ฅ",
                "(΄◞ิ౪◟ิ‵)",
                "٩(^ᴗ^)۶",
                "ᕴｰᴥｰᕵ",
                "ʕ￫ᴥ￩ʔ",
                "ʕᵕᴥᵕʔ",
                "ʕᵒᴥᵒʔ",
                "ᵔᴥᵔ",
                "(✿╹◡╹)",
                "(๑￫ܫ￩)",
                "ʕ·ᴥ·　ʔ",
                "(ﾉ≧ڡ≦)",
                "(≖ᴗ≖✿)",
                "（〜^∇^ )〜",
                "( ﾉ･ｪ･ )ﾉ",
                "~( ˘▾˘~)",
                "(〜^∇^)〜",
                "ヽ(^ᴗ^ヽ)",
                "(´･ω･`)",
                "₍ᐢ•ﻌ•ᐢ₎*･ﾟ｡",
                "(。・・)_且",
                "(=｀ω´=)",
                "(*•‿•*)",
                "(*ﾟ∀ﾟ*)",
                "(☉⋆‿⋆☉)",
                "ɷ◡ɷ",
                "ʘ‿ʘ",
                "(。-ω-)ﾉ",
                "( ･ω･)ﾉ",
                "(=ﾟωﾟ)ﾉ",
                "(・ε・`*) …",
                "ʕっ•ᴥ•ʔっ",
                "(*˘︶˘*)",
                "ಥ_ಥ",
                "･ﾟ･(｡>д<｡)･ﾟ･",
                "(┬┬＿┬┬)",
                "(◞‸◟ㆀ)",
                " ˚‧º·(˚ ˃̣̣̥⌓˂̣̣̥ )‧º·˚",
            ]
        )
    )


def array_sum(
    array: typing.List[typing.List[typing.Any]], /
) -> typing.List[typing.Any]:
    """
    Performs basic sum operation on array
    :param array: Array to sum
    :return: Sum of array
    """
    result = []
    for item in array:
        result += item

    return result


def rand(size: int, /) -> str:
    """
    Return random string of len `size`
    :param size: Length of string
    :return: Random string
    """
    return "".join(
        [random.choice("abcdefghijklmnopqrstuvwxyz1234567890") for _ in range(size)]
    )


def smart_split(
    text: str,
    entities: typing.List[FormattingEntity],
    length: int = 4096,
    split_on: ListLike = ("\n", " "),
    min_length: int = 1,
) -> typing.Iterator[str]:
    """
    Split the message into smaller messages.
    A grapheme will never be broken. Entities will be displaced to match the right location. No inputs will be mutated.
    The end of each message except the last one is stripped of characters from [split_on]
    :param text: the plain text input
    :param entities: the entities
    :param length: the maximum length of a single message
    :param split_on: characters (or strings) which are preferred for a message break
    :param min_length: ignore any matches on [split_on] strings before this number of characters into each message
    :return: iterator, which returns strings

    :example:
        >>> utils.smart_split(
            *legacytl.extensions.html.parse(
                "<b>Hello, world!</b>"
            )
        )
        <<< ["<b>Hello, world!</b>"]
    """

    # Authored by @bsolute
    # https://t.me/LonamiWebs/27777

    encoded = text.encode("utf-16le")
    pending_entities = entities
    text_offset = 0
    bytes_offset = 0
    text_length = len(text)
    bytes_length = len(encoded)

    while text_offset < text_length:
        if bytes_offset + length * 2 >= bytes_length:
            yield parser.unparse(
                text[text_offset:],
                list(sorted(pending_entities, key=lambda x: x.offset)),
            )
            break

        codepoint_count = len(
            encoded[bytes_offset : bytes_offset + length * 2].decode(
                "utf-16le",
                errors="ignore",
            )
        )

        for search in split_on:
            search_index = text.rfind(
                search,
                text_offset + min_length,
                text_offset + codepoint_count,
            )
            if search_index != -1:
                break
        else:
            search_index = text_offset + codepoint_count

        split_index = grapheme.safe_split_index(text, search_index)

        split_offset_utf16 = (
            len(text[text_offset:split_index].encode("utf-16le"))
        ) // 2
        exclude = 0

        while (
            split_index + exclude < text_length
            and text[split_index + exclude] in split_on
        ):
            exclude += 1

        current_entities = []
        entities = pending_entities.copy()
        pending_entities = []

        for entity in entities:
            if (
                entity.offset < split_offset_utf16
                and entity.offset + entity.length > split_offset_utf16 + exclude
            ):
                # spans boundary
                current_entities.append(
                    _copy_tl(
                        entity,
                        length=split_offset_utf16 - entity.offset,
                    )
                )
                pending_entities.append(
                    _copy_tl(
                        entity,
                        offset=0,
                        length=entity.offset
                        + entity.length
                        - split_offset_utf16
                        - exclude,
                    )
                )
            elif entity.offset < split_offset_utf16 < entity.offset + entity.length:
                # overlaps boundary
                current_entities.append(
                    _copy_tl(
                        entity,
                        length=split_offset_utf16 - entity.offset,
                    )
                )
            elif entity.offset < split_offset_utf16:
                # wholly left
                current_entities.append(entity)
            elif (
                entity.offset + entity.length
                > split_offset_utf16 + exclude
                > entity.offset
            ):
                # overlaps right boundary
                pending_entities.append(
                    _copy_tl(
                        entity,
                        offset=0,
                        length=entity.offset
                        + entity.length
                        - split_offset_utf16
                        - exclude,
                    )
                )
            elif entity.offset + entity.length > split_offset_utf16 + exclude:
                # wholly right
                pending_entities.append(
                    _copy_tl(
                        entity,
                        offset=entity.offset - split_offset_utf16 - exclude,
                    )
                )

        current_text = text[text_offset:split_index]
        yield parser.unparse(
            current_text,
            list(sorted(current_entities, key=lambda x: x.offset)),
        )

        text_offset = split_index + exclude
        bytes_offset += len(current_text.encode("utf-16le"))


def _copy_tl(o, **kwargs):
    d = o.to_dict()
    del d["_"]
    d.update(kwargs)
    return o.__class__(**d)


def check_url(url: str) -> bool:
    """
    Statically checks url for validity
    :param url: URL to check
    :return: True if valid, False otherwise
    """
    try:
        return bool(urlparse(url).netloc)
    except Exception:
        return False


def normalize_banner_url(url: typing.Any) -> typing.Optional[str]:
    """
    Normalizes banner url config value into a single valid url
    :param url: Config value - a url, a list of urls or a stringified list of them
    :return: Valid url or `None`, if there is nothing usable
    """
    if isinstance(url, (list, tuple)):
        url = next(iter(url), None)

    if not isinstance(url, str):
        return None

    url = url.strip()

    # `validators.String` casts any value to `str`, so a list, which was set by
    # user or migrated from another userbot, is stored as its own repr
    if url.startswith(("[", "(")):
        with contextlib.suppress(ValueError, SyntaxError):
            if isinstance(parsed := ast.literal_eval(url), (list, tuple)):
                url = str(next(iter(parsed), "")).strip()

    return url if check_url(url) else None


def get_git_hash() -> typing.Union[str, bool]:
    """
    Get current Hikka git hash
    :return: Git commit hash
    """
    try:
        return git.Repo().head.commit.hexsha
    except Exception:
        return False


def get_commit_url() -> str:
    """
    Get current Legacy git commit url
    :return: Git commit url
    """
    try:
        hash_ = get_git_hash()
        return f'<a href="https://github.com/ziwupa/Legacy-NewGen/commit/{hash_}">#{hash_[:7]}</a>'
    except Exception:
        return "Unknown"


def is_serializable(x: typing.Any, /) -> bool:
    """
    Checks if object is JSON-serializable
    :param x: Object to check
    :return: True if object is JSON-serializable, False otherwise
    """
    try:
        ujson.dumps(x)
        return True
    except Exception:
        return False


def get_lang_flag(countrycode: str) -> str:
    """
    Gets an emoji of specified countrycode
    :param countrycode: 2-letter countrycode
    :return: Emoji flag
    """
    if (
        len(
            code := [
                c
                for c in countrycode.lower()
                if c in string.ascii_letters + string.digits
            ]
        )
        == 2
    ):
        return "".join([chr(ord(c.upper()) + (ord("🇦") - ord("A"))) for c in code])

    return countrycode


def get_entity_url(
    entity: typing.Union[User, Channel],
    openmessage: bool = False,
) -> str:
    """
    Get link to object, if available
    :param entity: Entity to get url of
    :param openmessage: Use tg://openmessage link for users
    :return: Link to object or empty string
    """
    return (
        (
            f"tg://openmessage?id={entity.id}"
            if openmessage
            else f"tg://user?id={entity.id}"
        )
        if isinstance(entity, User)
        else (
            f"tg://resolve?domain={entity.username}"
            if getattr(entity, "username", None)
            else ""
        )
    )


async def get_message_link(
    message: Message,
    chat: typing.Optional[typing.Union[Chat, Channel]] = None,
) -> str:
    """
    Get link to message
    :param message: Message to get link of
    :param chat: Chat, where message was sent
    :return: Link to message
    """
    if message.is_private:
        return (
            f"tg://openmessage?user_id={get_chat_id(message)}&message_id={message.id}"
        )

    if not chat and not (chat := message.chat):
        chat = await message.get_chat()

    topic_affix = (
        f"?topic={message.reply_to.reply_to_msg_id}"
        if getattr(message.reply_to, "forum_topic", False)
        else ""
    )

    return (
        f"https://t.me/{chat.username}/{message.id}{topic_affix}"
        if getattr(chat, "username", False)
        else f"https://t.me/c/{chat.id}/{message.id}{topic_affix}"
    )


def remove_html(text: str, escape: bool = False, keep_emojis: bool = False) -> str:
    """
    Removes HTML tags from text
    :param text: Text to remove HTML from
    :param escape: Escape HTML
    :param keep_emojis: Keep custom emojis
    :return: Text without HTML
    """
    return (escape_html if escape else str)(
        re.sub(
            (
                r"(<\/?a.*?>|<\/?b>|<\/?i>|<\/?u>|<\/?strong>|<\/?em>|<\/?code>|<\/?strike>|<\/?del>|<\/?pre.*?>)"
                if keep_emojis
                else r"(<\/?a.*?>|<\/?b>|<\/?i>|<\/?u>|<\/?strong>|<\/?em>|<\/?code>|<\/?strike>|<\/?del>|<\/?pre.*?>|<\/?emoji.*?>)"
            ),
            "",
            text,
        )
    )


def get_kwargs() -> typing.Dict[str, typing.Any]:
    """
    Get kwargs of function, in which is called
    :return: kwargs
    """
    # https://stackoverflow.com/a/65927265/19170642
    keys, _, _, values = inspect.getargvalues(inspect.currentframe().f_back)
    return {key: values[key] for key in keys if key != "self"}


def mime_type(message: Message) -> str:
    """
    Get mime type of document in message
    :param message: Message with document
    :return: Mime type or empty string if not present
    """
    return (
        ""
        if not isinstance(message, Message) or not getattr(message, "media", False)
        else getattr(
            getattr(getattr(message, "media", False), "document", False), "mime_type"
        )
        or ""
    )


def find_caller(
    stack: typing.Optional[typing.List[inspect.FrameInfo]] = None,
) -> typing.Any:
    """
    Attempts to find command in stack
    :param stack: Stack to search in
    :return: Command-caller or None
    """
    caller = next(
        (
            frame_info
            for frame_info in stack or inspect.stack()
            if hasattr(frame_info, "function")
            and any(
                inspect.isclass(cls_)
                and issubclass(cls_, Module)
                and cls_ is not Module
                for cls_ in frame_info.frame.f_globals.values()
            )
        ),
        None,
    )

    if not caller:
        return next(
            (
                frame_info.frame.f_locals["func"]
                for frame_info in stack or inspect.stack()
                if hasattr(frame_info, "function")
                and frame_info.function == "future_dispatcher"
                and (
                    "CommandDispatcher"
                    in getattr(getattr(frame_info, "frame", None), "f_globals", {})
                )
            ),
            None,
        )

    return next(
        (
            getattr(cls_, caller.function, None)
            for cls_ in caller.frame.f_globals.values()
            if inspect.isclass(cls_) and issubclass(cls_, Module)
        ),
        None,
    )


def validate_html(html: str) -> str:
    """
    Removes broken tags from html
    :param html: HTML to validate
    :return: Valid HTML
    """
    text, entities = legacytl.extensions.html.parse(html)
    return legacytl.extensions.html.unparse(escape_html(text), entities)


def iter_attrs(obj: typing.Any, /) -> typing.List[typing.Tuple[str, typing.Any]]:
    """
    Returns list of attributes of object
    :param obj: Object to iterate over
    :return: List of attributes and their values
    """
    return ((attr, getattr(obj, attr)) for attr in dir(obj))


def atexit(
    func: typing.Callable,
    use_signal: typing.Optional[int] = None,
    *args,
    **kwargs,
) -> None:
    """
    Calls function on exit
    :param func: Function to call
    :param use_signal: If passed, `signal` will be used instead of `atexit`
    :param args: Arguments to pass to function
    :param kwargs: Keyword arguments to pass to function
    :return: None
    """
    if use_signal:
        signal.signal(use_signal, lambda *_: func(*args, **kwargs))
        return

    _atexit.register(functools.partial(func, *args, **kwargs))


def get_topic(message: Message) -> typing.Optional[int]:
    """
    Get topic id of message
    :param message: Message to get topic of
    :return: int or None if not present
    """
    return (
        (message.reply_to.reply_to_top_id or message.reply_to.reply_to_msg_id)
        if (
            isinstance(message, Message)
            and message.reply_to
            and message.reply_to.forum_topic
        )
        else (
            message.form["top_msg_id"]
            if isinstance(message, (InlineCall, InlineMessage))
            else None
        )
    )


def get_ram_usage() -> float:
    """Returns current process tree memory usage in MB"""
    try:
        import psutil

        current_process = psutil.Process(os.getpid())
        mem = current_process.memory_info()[0] / 2.0**20
        for child in current_process.children(recursive=True):
            mem += child.memory_info()[0] / 2.0**20

        return round(mem, 1)
    except Exception:
        return 0


async def get_cpu_usage_async() -> float:
    from aiopsutil import AsyncPSUtil

    aiops = AsyncPSUtil()

    cpu_usage = await aiops.cpu_percent(interval=0.5)

    return cpu_usage


def get_cpu_usage() -> float:
    try:
        import subprocess

        result = subprocess.run(
            ["ps", "-p", str(os.getpid()), "-o", "%cpu"], capture_output=True, text=True
        )
        cpu_usage = float(result.stdout.splitlines()[1].strip())
        return round(cpu_usage, 2)
    except Exception as e:
        logging.error(f"{e}")
        return 0.0


init_ts = time.perf_counter()


# GeekTG Compatibility
def get_git_info() -> typing.Tuple[str, str]:
    """
    Get git info
    :return: Git info
    """
    hash_ = get_git_hash()
    return (
        hash_,
        f"https://github.com/ziwupa/Legacy-NewGen/commit/{hash_}" if hash_ else "",
    )


def get_version_raw() -> str:
    """
    Get the version of the userbot
    :return: Version in format %s.%s.%s
    """
    from . import version

    return version.__version__


async def send_reaction(
    client: CustomTelegramClient, message: Message, emoji: typing.Union[int, str]
) -> None:
    """
    Send reaction to specified message

    Parameters:
    - client (CustomTelegramClient): An instance of the CustomTelegramClient used to interact with the Telegram API
    - message (Message): The message to which the reaction will be sent. This should contain the chat ID and message ID
    - emoji (Union[int, str]): The emoji to be used as a reaction. This can be either an integer representing a custom emoji's document ID (if the user has a premium account) or a string representing a standard emoji

    Returns: None
    """
    try:
        me = await client.get_me()
        if isinstance(emoji, int) and me.premium:
            await client(
                SendReactionRequest(
                    peer=message.chat_id,
                    msg_id=message.id,
                    reaction=[ReactionCustomEmoji(document_id=emoji)],
                )
            )
        elif isinstance(emoji, str):
            await client(
                SendReactionRequest(
                    peer=message.chat_id,
                    msg_id=message.id,
                    reaction=[ReactionEmoji(emoticon=emoji)],
                )
            )
    except Exception as e:
        logger.error(f"Unable to send reaction to the specified message: {e}")
        return
