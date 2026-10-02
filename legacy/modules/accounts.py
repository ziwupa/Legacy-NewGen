# Multi-account manager for Legacy NewGen (system module)

import asyncio
import contextlib
import logging
import os
import time
import typing

from legacytl.errors.rpcerrorlist import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from legacytl.password import compute_check
from legacytl.sessions import MemorySession
from legacytl.tl.functions.account import GetPasswordRequest
from legacytl.tl.functions.auth import CheckPasswordRequest
from legacytl.utils import get_display_name

from .. import loader, main, utils
from .._internal import restart
from ..tl_cache import CustomTelegramClient
from ..types import Message

logger = logging.getLogger(__name__)

_FLOW_TTL = 300


@loader.tds
class AccountsMod(loader.Module):
    strings = {"name": "Accounts"}

    def __init__(self):
        # (chat_id, user_id) -> flow dict. Owner-only, in-memory.
        self._flows: dict = {}

    def _flow_key(self, message) -> tuple:
        return (
            getattr(message, "chat_id", None),
            getattr(message, "sender_id", None),
        )

    def _flow_get(self, message):
        flow = self._flows.get(self._flow_key(message))
        if not flow:
            return None
        if time.monotonic() - flow.get("ts", 0) > _FLOW_TTL:
            self._flows.pop(self._flow_key(message), None)
            return None
        return flow

    def _is_command(self, message) -> bool:
        try:
            prefixes = self.allmodules.get_prefix()
            text = (getattr(message, "raw_text", None) or getattr(message, "text", "") or "")
            if isinstance(prefixes, (list, tuple, set)):
                return any(text.startswith(p) for p in prefixes if p)
            return bool(prefixes) and text.startswith(prefixes)
        except Exception:
            return False

    def _clients(self) -> list:
        try:
            return list(self.allclients or [])
        except Exception:
            return []

    def _client_info(self, client) -> dict:
        me = getattr(client, "legacy_me", None)
        tg_id = getattr(client, "tg_id", None) or getattr(me, "id", None)
        name = "?"
        with contextlib.suppress(Exception):
            name = utils.get_display_name(me)
        return {"id": tg_id, "name": name, "premium": bool(getattr(me, "premium", False))}

    @loader.command()
    async def addacc(self, message: Message):
        """- Add another Telegram account (interactive login)"""
        key = self._flow_key(message)
        self._flows.pop(key, None)
        self._flows[key] = {"step": "phone", "ts": time.monotonic()}
        await utils.answer(
            message,
            self.strings("ask_phone"),
        )

    @loader.command()
    async def acclist(self, message: Message):
        """- List accounts with per-account actions"""
        await self._show_accounts(message)

    async def _show_accounts(self, target):
        infos = [self._client_info(c) for c in self._clients()]
        screen = self.inline.screen()
        screen.add(self.strings("list_title"))
        rows = []
        for info in infos:
            if not info["id"]:
                continue
            label = f"{'⭐ ' if info['premium'] else ''}{info['name']} <code>{info['id']}</code>"
            rows.append(
                [
                    {
                        "text": label,
                        "callback": self._acc_menu,
                        "args": (info["id"],),
                    }
                ]
            )
        screen.keyboard(
            *rows,
            [
                {
                    "text": self.strings("add_btn"),
                    "callback": self._acc_add_hint,
                }
            ],
        )
        if isinstance(target, Message):
            await self.inline.form("", target, rich_html=screen)
        else:
            await target.edit(rich_html=screen)

    @loader.command()
    async def delacc(self, message: Message):
        """<tg_id> - Remove account (deletes its session, restarts)"""
        args = utils.get_args(message)
        if not args or not str(args[0]).isdigit():
            await utils.answer(message, self.strings("del_usage"))
            return
        await self._confirm_del(message, int(args[0]))

    async def _acc_menu(self, call, account_id: int):
        infos = {i["id"]: i for i in (self._client_info(c) for c in self._clients())}
        info = infos.get(account_id) or {"id": account_id, "name": "?", "premium": False}
        screen = self.inline.screen()
        screen.add(self.strings("acc_title").format(info["id"], utils.escape_html(str(info["name"]))))
        screen.keyboard(
            [
                {
                    "text": self.strings("acc_info"),
                    "callback": self._acc_info,
                    "args": (account_id,),
                },
                {
                    "text": self.strings("acc_logout"),
                    "callback": self._acc_logout,
                    "args": (account_id,),
                },
            ],
            [{"text": self.strings("back"), "callback": self._acc_back}],
        )
        await call.edit(rich_html=screen)

    async def _acc_back(self, call):
        await self._show_accounts(call)

    async def _acc_add_hint(self, call):
        await call.answer(self.strings("add_hint"), show_alert=False)

    async def _acc_info(self, call, account_id: int):
        info = None
        for client in self._clients():
            if getattr(client, "tg_id", None) == account_id:
                info = self._client_info(client)
                me = getattr(client, "legacy_me", None)
                dc = getattr(getattr(client, "session", None), "dc_id", "?")
                break
        if info is None:
            await call.answer(self.strings("gone"), show_alert=True)
            return
        text = self.strings("acc_info_text").format(
            utils.escape_html(str(info["name"])),
            info["id"],
            dc,
            self.strings("yes") if info["premium"] else self.strings("no"),
        )
        await call.edit(text)

    async def _acc_logout(self, call, account_id: int):
        screen = self.inline.screen()
        screen.add(self.strings("logout_confirm").format(account_id))
        screen.keyboard(
            [
                {
                    "text": self.strings("yes_do"),
                    "callback": self._acc_logout_yes,
                    "args": (account_id,),
                },
                {"text": self.strings("back"), "callback": self._acc_menu, "args": (account_id,)},
            ]
        )
        await call.edit(rich_html=screen)

    def _account_known(self, account_id: int) -> bool:
        for client in self._clients():
            if getattr(client, "tg_id", None) == account_id:
                return True
        try:
            for name in os.listdir(main.SESSIONS_DIR):
                if name.startswith(f"legacy-{account_id}") and (
                    name.endswith(".session")
                    or name.endswith(".lsession")
                    or ".session.migrated" in name
                ):
                    return True
        except Exception:
            pass
        return False

    async def _acc_logout_yes(self, call, account_id: int):
        if not self._account_known(account_id):
            await call.edit(self.strings("unknown"))
            return
        await self._remove_account(account_id)
        await call.edit(self.strings("logged_out").format(account_id))
        await asyncio.sleep(2)
        restart()

    async def _confirm_del(self, message, account_id: int):
        if not self._account_known(account_id):
            await utils.answer(message, self.strings("unknown"))
            return
        screen = self.inline.screen()
        screen.add(self.strings("logout_confirm").format(account_id))
        screen.keyboard(
            [
                {
                    "text": self.strings("yes_do"),
                    "callback": self._acc_logout_yes,
                    "args": (account_id,),
                },
                {"text": self.strings("cancel"), "action": "close"},
            ]
        )
        await self.inline.form("", message, rich_html=screen)

    async def _remove_account(self, account_id: int):
        for client in self._clients():
            if getattr(client, "tg_id", None) == account_id:
                with contextlib.suppress(Exception):
                    await client.disconnect()
        removed = []
        try:
            base = main.SESSIONS_DIR
            for name in os.listdir(base):
                if name.startswith(f"legacy-{account_id}") and (
                    name.endswith(".session")
                    or name.endswith(".lsession")
                    or ".session.migrated" in name
                    or name.endswith(".session-journal")
                ):
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(base, name))
                        removed.append(name)
        except Exception:
            logger.exception("Accounts: cleanup failed for %s", account_id)
        logger.info("Accounts: removed %s (%s)", account_id, removed)

    @loader.watcher()
    async def watcher(self, message):
        if not getattr(message, "out", False):
            return
        flow = self._flow_get(message)
        if not flow:
            return
        if self._is_command(message):
            self._flows.pop(self._flow_key(message), None)
            return
        text = (getattr(message, "raw_text", None) or getattr(message, "text", "") or "").strip()
        if not text:
            return
        step = flow.get("step")
        try:
            if step == "phone":
                await self._flow_phone(message, flow, text)
            elif step == "code":
                await self._flow_code(message, flow, text)
            elif step == "password":
                await self._flow_password(message, flow, text)
        except Exception:
            logger.exception("Accounts: flow failed")
            self._flows.pop(self._flow_key(message), None)
            await utils.answer(message, self.strings("flow_fail"))

    def _new_client(self):
        return CustomTelegramClient(
            MemorySession(),
            self._client.api_id,
            self._client.api_hash,
            connection=main.legacy.conn,
            proxy=main.legacy.proxy,
            connection_retries=None,
            device_model=main.get_app_name(),
            system_version=main.generate_random_system_version(),
            app_version=main.version.__version__,
            lang_code="en",
            system_lang_code="en-US",
        )

    async def _flow_phone(self, message, flow, text: str):
        from legacytl.utils import parse_phone

        phone = parse_phone(text)
        if not phone:
            await utils.answer(message, self.strings("bad_phone"))
            return
        client = self._new_client()
        try:
            await client.connect()
        except Exception as e:
            await utils.answer(message, self.strings("connect_fail").format(e))
            return
        try:
            await client.send_code_request(phone)
        except FloodWaitError as e:
            await utils.answer(message, self.strings("flood").format(e.seconds))
            with contextlib.suppress(Exception):
                await client.disconnect()
            self._flows.pop(self._flow_key(message), None)
            return
        except Exception as e:
            await utils.answer(message, self.strings("code_fail").format(e))
            with contextlib.suppress(Exception):
                await client.disconnect()
            self._flows.pop(self._flow_key(message), None)
            return
        flow.update({"step": "code", "ts": time.monotonic(), "phone": phone, "client": client})
        await utils.answer(message, self.strings("ask_code"))

    async def _flow_code(self, message, flow, text: str):
        code = "".join(ch for ch in text if ch.isdigit())
        client = flow.get("client")
        if client is None:
            self._flows.pop(self._flow_key(message), None)
            return
        try:
            await client.sign_in(flow.get("phone"), code=code)
        except SessionPasswordNeededError:
            flow.update({"step": "password", "ts": time.monotonic()})
            await utils.answer(message, self.strings("ask_password"))
            return
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            await utils.answer(message, self.strings("bad_code"))
            return
        except FloodWaitError as e:
            await utils.answer(message, self.strings("flood").format(e.seconds))
            self._flows.pop(self._flow_key(message), None)
            return
        except Exception as e:
            await utils.answer(message, self.strings("signin_fail").format(e))
            self._flows.pop(self._flow_key(message), None)
            return
        await self._finish_login(message, flow)

    async def _flow_password(self, message, flow, text: str):
        client = flow.get("client")
        if client is None:
            self._flows.pop(self._flow_key(message), None)
            return
        try:
            password = await client(GetPasswordRequest())
            await client._on_login(
                (
                    await client(
                        CheckPasswordRequest(compute_check(password, text.strip()))
                    )
                ).user
            )
        except PasswordHashInvalidError:
            await utils.answer(message, self.strings("bad_password"))
            return
        except FloodWaitError as e:
            await utils.answer(message, self.strings("flood").format(e.seconds))
            self._flows.pop(self._flow_key(message), None)
            return
        except Exception as e:
            await utils.answer(message, self.strings("signin_fail").format(e))
            self._flows.pop(self._flow_key(message), None)
            return
        await self._finish_login(message, flow)

    async def _finish_login(self, message, flow):
        client = flow.pop("client", None)
        self._flows.pop(self._flow_key(message), None)
        if client is None:
            return
        try:
            me = await client.get_me()
        except Exception as e:
            await utils.answer(message, self.strings("signin_fail").format(e))
            return
        await utils.answer(
            message, self.strings("login_ok").format(me.id, utils.escape_html(getattr(me, "first_name", "") or ""))
        )
        await asyncio.sleep(1)
        try:
            await main.legacy.save_client_session(client)
        except Exception:
            logger.exception("Accounts: save failed, restarting anyway")
            restart()
