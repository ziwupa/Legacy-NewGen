# ©️ Dan Gazizullin, 2021-2023
# This file is a part of Hikka Userbot
# 🌐 https://github.com/hikariatama/Hikka
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
import io
import ujson
import logging
import random
import sys
import time
import typing

from legacytl.tl import functions
from legacytl.tl import tlobject as _tlobject
from legacytl.tl.tlobject import TLRequest
from legacytl.tl.types import Message
from legacytl.utils import is_list_like

from .. import loader, utils
from ..inline.types import InlineCall

logger = logging.getLogger(__name__)

GROUPS = [
    "account",
    "auth",
    "bots",
    "channels",
    "chatlists",
    "contacts",
    "folders",
    "fragment",
    "help",
    "langpack",
    "messages",
    "payments",
    "phone",
    "photos",
    "premium",
    "smsjobs",
    "stats",
    "stickers",
    "stories",
    "upadates",
    "upload",
    "users",
]


def get_methods_from_group(group_obj):
    return [
        method
        for method_name in dir(group_obj)
        if isinstance((method := getattr(group_obj, method_name, None)), type)
        and issubclass(method, TLRequest)
    ]


CONSTRUCTORS = {}

for group in GROUPS:
    group_obj = getattr(functions, group, None)
    if group_obj:
        methods = get_methods_from_group(group_obj)
        for method in methods:
            constructor_name = method.__name__.rsplit("Request", 1)[0].lower()
            CONSTRUCTORS[constructor_name] = method.CONSTRUCTOR_ID


# The dangerous constructors that legacytl blocks unconditionally. The watchdog
# verifies these stay forbidden; if any of them silently drops out of the active
# set (because a module cleared it, or rebound the guard), that is tampering.
_CANONICAL_FORBIDDEN = frozenset({
    0xA2C0CF74,  # account.deleteAccount
    0x9308CE1B,  # account.resetPassword
    0x9FAB0D1A,  # auth.resetAuthorizations
    0xE894AD4D,  # auth.acceptLoginToken
})

# How often the watchdog re-checks the protection, and how long it waits before
# re-alerting about the same tampering so a persistent attacker can't spam you.
_WATCHDOG_INTERVAL = 4
_ALERT_DEBOUNCE = 60

# Symbols that only protection-tampering code has any reason to touch. When the
# watchdog trips, we scan the source of every externally-loaded module for these
# to name a likely culprit in the alert instead of just "something".
_PROTECTION_TOKENS = (
    "_CORE_FORBIDDEN_CONSTRUCTORS",
    "_EXTRA_FORBIDDEN_CONSTRUCTORS",
    "FORBIDDEN_CONSTRUCTORS",
    "_raise_if_forbidden",
    "_get_forbid_constructors",
    "forbid_constructors",
)


@loader.tds
class APIRatelimiterMod(loader.Module):
    strings = {"name": "APILimiter"}

    def __init__(self):
        self._ratelimiter: typing.List[tuple] = []
        self._suspend_until = 0
        self._lock = False
        self._watchdog_task: typing.Optional[asyncio.Task] = None
        self._watchdog_refs: dict = {}
        self._last_alert = 0
        # True only if the legacytl in memory already had the hardened frozenset
        # core when the watchdog captured its baseline. If it was an older core,
        # the core checks can never pass and _restore can't help — that's a
        # restart-required version mismatch, not an attack.
        self._core_hardened_at_capture = None
        self._stale_core_warned = False
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "time_sample",
                15,
                lambda: self.strings("_cfg_time_sample"),
                validator=loader.validators.Integer(minimum=1),
            ),
            loader.ConfigValue(
                "threshold",
                100,
                lambda: self.strings("_cfg_threshold"),
                validator=loader.validators.Integer(minimum=10),
            ),
            loader.ConfigValue(
                "local_floodwait",
                30,
                lambda: self.strings("_cfg_local_floodwait"),
                validator=loader.validators.Integer(minimum=10, maximum=3600),
            ),
            loader.ConfigValue(
                "forbidden_methods",
                [
                    "joinChannel",
                    "importChatInvite",
                    "changePhone",
                    "resetPassword",
                    "deleteAccount",
                ],
                lambda: self.strings("_cfg_forbidden_methods"),
                validator=loader.validators.MultiChoice(
                    [
                        "getUserPhotos",
                        "sendReaction",
                        "joinChannel",
                        "importChatInvite",
                        "exportChatInvite",
                        "setPrivacy",
                        "changePhone",
                        "resetPassword",
                        "deleteAccount",
                        "acceptLoginToken",
                    ]
                ),
                on_change=lambda: (
                    self._client.forbid_constructors(
                        list(
                            map(
                                lambda x: CONSTRUCTORS.get(x.lower(), None),
                                self.config["forbidden_methods"],
                            ),
                        ),
                    ),
                ),
            ),
        )

    async def client_ready(self):
        asyncio.ensure_future(self._install_protection())
        self._client.forbid_constructors(
            list(
                map(
                    lambda x: CONSTRUCTORS.get(x.lower(), None),
                    self.config["forbidden_methods"],
                ),
            ),
        )

    async def _install_protection(self):
        await asyncio.sleep(30)  # Restart lock
        if hasattr(self._client._call, "_old_call_rewritten"):
            raise loader.SelfUnload("Already installed")

        old_call = self._client._call

        async def new_call(
            sender: "MTProtoSender",  # type: ignore  # noqa: F821
            request: TLRequest,
            ordered: bool = False,
            flood_sleep_threshold: int = None,
        ):
            await asyncio.sleep(random.randint(1, 5) / 100)
            req = (request,) if not is_list_like(request) else request
            for r in req:
                if (
                    time.perf_counter() > self._suspend_until
                    and not self.get(
                        "disable_protection",
                        True,
                    )
                    and (
                        r.__module__.rsplit(".", maxsplit=1)[1]
                        in {"messages", "account", "channels"}
                    )
                ):
                    request_name = type(r).__name__
                    self._ratelimiter += [(request_name, time.perf_counter())]

                    self._ratelimiter = list(
                        filter(
                            lambda x: time.perf_counter() - x[1]
                            < int(self.config["time_sample"]),
                            self._ratelimiter,
                        )
                    )

                    if (
                        len(self._ratelimiter) > int(self.config["threshold"])
                        and not self._lock
                    ):
                        self._lock = True
                        report = io.BytesIO(
                            ujson.dumps(
                                self._ratelimiter,
                                indent=4,
                            ).encode()
                        )
                        report.name = "local_fw_report.json"

                        await self.inline.bot.send_document(
                            self.tg_id,
                            report,
                            caption=self.inline.sanitise_text(
                                self.strings("warning").format(
                                    self.config["local_floodwait"],
                                    prefix=utils.escape_html(self.get_prefix()),
                                )
                            ),
                        )

                        # It is intented to use time.sleep instead of asyncio.sleep
                        time.sleep(int(self.config["local_floodwait"]))
                        self._lock = False

            return await old_call(sender, request, ordered, flood_sleep_threshold)

        self._client._call = new_call
        self._client._old_call_rewritten = old_call
        self._client._call._legacy_overwritten = True
        self._call_wrapper = new_call
        logger.debug("Successfully installed ratelimiter")

        self._install_watchdog()

    def _install_watchdog(self):
        """Capture the current, known-good protection objects and start a
        background task that keeps verifying they have not been swapped out or
        emptied by a loaded module."""
        self._watchdog_refs = {
            "raise_ctor": _tlobject._raise_if_forbidden_constructor,
            "raise_serialized": _tlobject._raise_if_forbidden_serialized_request,
            "get_forbid": _tlobject._get_forbid_constructors,
            "core": getattr(_tlobject, "_CORE_FORBIDDEN_CONSTRUCTORS", None),
        }
        # Snapshot the active forbidden set as it stands right now (known-good).
        # The watchdog trips if it later *shrinks* below this baseline — that
        # catches a dropped constructor whether the core is new or old, without
        # demanding ids the running core never had (which would false-positive).
        try:
            self._watchdog_refs["active_baseline"] = frozenset(
                _tlobject._get_forbid_constructors()
            )
        except Exception:
            self._watchdog_refs["active_baseline"] = frozenset()
        self._core_hardened_at_capture = self._watchdog_refs["core"] is not None
        if not self._core_hardened_at_capture:
            logger.warning(
                "api_protection: legacytl in memory predates the hardened core"
                " (no _CORE_FORBIDDEN_CONSTRUCTORS) — a restart is required to"
                " load it; core-set checks are advisory until then"
            )
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.ensure_future(self._watchdog_loop())

    async def _watchdog_loop(self):
        while True:
            try:
                await asyncio.sleep(_WATCHDOG_INTERVAL)
                # An old core in memory is a restart-required version mismatch,
                # not an attack: say so once, then keep watching only the pieces
                # that are still meaningful (guard identity, ratelimiter, config).
                if not self._core_hardened_at_capture:
                    await self._warn_stale_core_once()
                problems = self._check_integrity()
                if problems:
                    self._restore()
                    await self._alert(problems)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("api_protection watchdog iteration failed")

    def _check_integrity(self) -> typing.List[str]:
        """Return human-readable names of protection pieces that changed since
        the watchdog captured its baseline. Checks are baseline-relative: a piece
        is "tampered" only if it differs from the known-good snapshot taken at
        install, never from an absolute ideal the running core may never have met.
        Empty list means everything is intact."""
        refs = self._watchdog_refs
        if not refs:
            return []

        problems = []
        t = _tlobject

        if t._raise_if_forbidden_constructor is not refs["raise_ctor"]:
            problems.append("legacytl._raise_if_forbidden_constructor")
        if t._raise_if_forbidden_serialized_request is not refs["raise_serialized"]:
            problems.append("legacytl._raise_if_forbidden_serialized_request")
        if t._get_forbid_constructors is not refs["get_forbid"]:
            problems.append("legacytl._get_forbid_constructors")

        # The core set is only checkable if we captured a hardened one. When we
        # did, tampering = it stopped being that exact frozenset object.
        if self._core_hardened_at_capture:
            core = getattr(t, "_CORE_FORBIDDEN_CONSTRUCTORS", None)
            if core is not refs["core"] or not isinstance(core, frozenset):
                problems.append("legacytl._CORE_FORBIDDEN_CONSTRUCTORS")

        # Active forbidden set: trip only if it SHRANK below the captured
        # baseline (a constructor was removed), not if it merely lacks ids the
        # running core never carried.
        try:
            active = set(refs["get_forbid"]())
        except Exception:
            active = set()
        if not refs["active_baseline"] <= active:
            problems.append("active forbidden constructor set")

        call = getattr(self._client, "_call", None)
        if not getattr(call, "_legacy_overwritten", False):
            problems.append("ratelimiter (client._call)")

        configured = {
            CONSTRUCTORS.get(x.lower())
            for x in self.config["forbidden_methods"]
        } - {None}
        if configured and not configured <= set(self._client._forbidden_constructors):
            problems.append("configured forbidden methods (client._forbidden_constructors)")

        return problems

    def _restore(self):
        """Put every tampered protection piece back to its captured good state."""
        refs = self._watchdog_refs
        t = _tlobject

        t._raise_if_forbidden_constructor = refs["raise_ctor"]
        t._raise_if_forbidden_serialized_request = refs["raise_serialized"]
        t._get_forbid_constructors = refs["get_forbid"]
        if refs["core"] is not None and (
            getattr(t, "_CORE_FORBIDDEN_CONSTRUCTORS", None) is not refs["core"]
        ):
            t._CORE_FORBIDDEN_CONSTRUCTORS = refs["core"]
            t.FORBIDDEN_CONSTRUCTORS = refs["core"]

        # Re-arm the ratelimiter wrapper if it was unwrapped.
        call = getattr(self._client, "_call", None)
        wrapper = getattr(self, "_call_wrapper", None)
        if wrapper is not None and not getattr(call, "_legacy_overwritten", False):
            self._client._call = wrapper

        # Re-assert the user-configured forbidden methods.
        self._client.forbid_constructors(
            list(
                map(
                    lambda x: CONSTRUCTORS.get(x.lower(), None),
                    self.config["forbidden_methods"],
                ),
            ),
        )

    async def _warn_stale_core_once(self):
        """The running process holds a legacytl older than the hardened core, so
        the core-level protection (immutable frozenset, acceptLoginToken block)
        is NOT active in memory even though it's installed on disk. Tell the
        owner once — only a full restart loads it; no restore can."""
        if self._stale_core_warned:
            return
        self._stale_core_warned = True
        try:
            missing = _CANONICAL_FORBIDDEN - set(_tlobject._get_forbid_constructors())
        except Exception:
            missing = _CANONICAL_FORBIDDEN
        logger.warning(
            "api_protection: hardened legacytl core is on disk but not in memory"
            " — restart the userbot process to activate it; %d canonical"
            " protection(s) not enforced right now",
            len(missing),
        )
        try:
            await self.inline.bot.send_message(
                self.tg_id,
                self.inline.sanitise_text(self.strings("core_outdated")),
            )
        except Exception:
            logger.exception("api_protection: failed to deliver core-outdated notice")

    def _blame_modules(self) -> typing.List[str]:
        """Best-effort attribution: name loaded modules whose *source* references
        the protection internals — the realistic culprits when something neuters
        the guard, since disabling it means naming these symbols. Only external
        (`<file …>`) modules are reported; core modules legitimately touch them.
        This is a heuristic (a source mention, not proof), so the alert frames it
        as a suspect."""
        suspects = []
        try:
            modules = list(getattr(self.allmodules, "modules", []))
        except Exception:
            return suspects

        for mod in modules:
            if mod is self:
                continue
            origin = getattr(mod, "__origin__", "") or ""
            if origin.startswith("<core"):
                continue
            try:
                loader = sys.modules[mod.__class__.__module__].__loader__
                src = loader.get_source() or ""
            except Exception:
                src = ""
            if src and any(tok in src for tok in _PROTECTION_TOKENS):
                name = getattr(mod, "name", None) or mod.__class__.__name__
                suspects.append(f"{name} {origin}".strip())

        return suspects

    async def _alert(self, problems: typing.List[str]):
        now = time.perf_counter()
        if now - self._last_alert < _ALERT_DEBOUNCE:
            return
        self._last_alert = now

        detail = ", ".join(problems)
        blamed = self._blame_modules()
        culprit = "; ".join(blamed) if blamed else self.strings("tamper_no_module")
        logger.warning(
            "api_protection: tampering detected and restored: %s | suspect: %s",
            detail,
            culprit,
        )
        try:
            await self.inline.bot.send_message(
                self.tg_id,
                self.inline.sanitise_text(
                    self.strings("tamper_alert").format(detail, culprit)
                ),
            )
        except Exception:
            logger.exception("api_protection: failed to deliver tamper alert")

    async def on_unload(self):
        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if hasattr(self._client, "_old_call_rewritten"):
            self._client._call = self._client._old_call_rewritten
            delattr(self._client, "_old_call_rewritten")
            logger.debug("Successfully uninstalled ratelimiter")

    @loader.command()
    async def suspend_api_protect(self, message: Message):
        if not (args := utils.get_args_raw(message)) or not args.isdigit():
            await utils.answer(message, self.strings("args_invalid"))
            return

        self._suspend_until = time.perf_counter() + int(args)
        await utils.answer(message, self.strings("suspended_for").format(args))

    @loader.command()
    async def api_fw_protection(self, message: Message):
        await self.inline.form(
            message=message,
            text=self.strings("u_sure"),
            reply_markup=[
                {"text": self.strings("btn_no"), "action": "close"},
                {"text": self.strings("btn_yes"), "callback": self._finish},
            ],
        )

    async def _finish(self, call: InlineCall):
        state = self.get("disable_protection", True)
        self.set("disable_protection", not state)
        await call.edit(self.strings("on" if state else "off"))
