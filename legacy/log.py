"""Main logging part"""

# пасхалка номер 3
# ©️ Dan Gazizullin, 2021-2023
# This file is a part of Hikka Userbot
# 🌐 https://github.com/hikariatama/Hikka
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
import contextlib
import importlib.util
import inspect
import io
import linecache
import logging
import re
import sys
import traceback
import typing
from logging.handlers import RotatingFileHandler

import legacytl
from aiogram.exceptions import TelegramNetworkError as NetworkError
from aiogram.exceptions import TelegramRetryAfter as RetryAfter
from legacytl.errors.rpcbaseerrors import RPCError, ServerError

from . import utils
from .tl_cache import CustomTelegramClient
from .types import BotInlineCall, Module

old = linecache.getlines


def getlines(filename: str, module_globals=None) -> str:
    """
    Get the lines for a Python source file from the cache.
    Update the cache if it doesn't contain an entry for this file already.
    """

    try:
        if filename.startswith("<") and filename.endswith(">"):
            module = filename[1:-1].split(maxsplit=1)[-1]
            if (module.startswith("legacy.modules")) and module in sys.modules:
                return list(
                    map(
                        lambda x: f"{x}\n",
                        sys.modules[module].__loader__.get_source().splitlines(),
                    )
                )
    except Exception:
        logging.debug("Can't get lines for %s", filename, exc_info=True)

    return old(filename, module_globals)


linecache.getlines = getlines

logger = logging.getLogger(__name__)


def override_text(exception: Exception) -> typing.Optional[str]:
    """Returns error-specific description if available, else `None`"""
    if isinstance(exception, NetworkError):
        return "✈️ <b>You have problems with internet connection on your server.</b>"
    if isinstance(exception, ServerError):
        return "📡 <b>Telegram servers are currently experiencing issues. Please try again later.</b>"
    if isinstance(exception, RPCError) and "TRANSLATION_TIMEOUT" in str(exception):
        return (
            "🕓 <b>Telegram translation service timed out. Please try again later.</b>"
        )
    if isinstance(exception, RetryAfter):
        return f"🕓 <b>{traceback.format_exception_only(type(exception), exception)[0].split(':')[1].strip()}</b>"
    if isinstance(exception, ModuleNotFoundError):
        return f"📦 <b>{traceback.format_exception_only(type(exception), exception)[0].split(':')[1].strip()}</b>"
    if isinstance(exception, asyncio.InvalidStateError):
        return "🔄 <b>Internal task was in invalid state.</b>"
    return None


class LegacyException:
    def __init__(
        self,
        message: str,
        full_stack: str,
        sysinfo: typing.Optional[
            typing.Tuple[object, Exception, traceback.TracebackException]
        ] = None,
    ):
        self.message = message
        self.full_stack = full_stack
        self.sysinfo = sysinfo

    @classmethod
    def from_exc_info(
        cls,
        exc_type: object,
        exc_value: Exception,
        tb: traceback.TracebackException,
        stack: typing.Optional[typing.List[inspect.FrameInfo]] = None,
        comment: typing.Optional[typing.Any] = None,
    ) -> "LegacyException":
        def to_hashable(dictionary: dict) -> dict:
            dictionary = dictionary.copy()
            for key, value in dictionary.items():
                if isinstance(value, dict):
                    dictionary[key] = to_hashable(value)
                else:
                    try:
                        if (
                            getattr(getattr(value, "__class__", None), "__name__", None)
                            == "Database"
                        ):
                            dictionary[key] = "<Database>"
                        elif isinstance(
                            value,
                            (legacytl.TelegramClient, CustomTelegramClient),
                        ):
                            dictionary[key] = f"<{value.__class__.__name__}>"
                        elif len(str(value)) > 512:
                            dictionary[key] = f"{str(value)[:512]}..."
                        else:
                            dictionary[key] = str(value)
                    except Exception:
                        dictionary[key] = f"<{value.__class__.__name__}>"

            return dictionary

        full_traceback = traceback.format_exc().replace(
            "Traceback (most recent call last):\n",
            "",
        )

        line_regex = re.compile(r'  File "(.*?)", line ([0-9]+), in (.+)')

        def format_line(line: str) -> str:
            filename_, lineno_, name_ = line_regex.search(line).groups()

            return (
                f"👉 <code>{utils.escape_html(filename_)}:{lineno_}</code> <b>in</b>"
                f" <code>{utils.escape_html(name_)}</code>"
            )

        filename, lineno, name = next(
            (
                line_regex.search(line).groups()
                for line in reversed(full_traceback.splitlines())
                if line_regex.search(line)
            ),
            (None, None, None),
        )

        full_traceback = "\n".join(
            [
                (
                    format_line(line)
                    if line_regex.search(line)
                    else f"<code>{utils.escape_html(line)}</code>"
                )
                for line in full_traceback.splitlines()
            ]
        )

        caller = utils.find_caller(stack or inspect.stack())

        return cls(
            message=override_text(exc_value)
            or (
                "{}<b>🎯 Source:</b> <code>{}:{}</code><b> in"
                " </b><code>{}</code>\n<b>❓ Error:</b> <code>{}</code>{}"
            ).format(
                (
                    (
                        "🔮 <b>Cause: method </b><code>{}</code><b> of"
                        " </b><code>{}</code>\n\n"
                    ).format(
                        utils.escape_html(caller.__name__),
                        utils.escape_html(caller.__self__.__class__.__name__),
                    )
                    if (
                        caller
                        and hasattr(caller, "__self__")
                        and hasattr(caller, "__name__")
                    )
                    else ""
                ),
                utils.escape_html(filename),
                lineno,
                utils.escape_html(name),
                utils.escape_html(
                    "".join(
                        traceback.format_exception_only(exc_type, exc_value)
                    ).strip()
                ),
                (
                    "\n💭 <b>Message:</b>"
                    f" <code>{utils.escape_html(str(comment))}</code>"
                    if comment
                    else ""
                ),
            ),
            full_stack=full_traceback,
            sysinfo=(exc_type, exc_value, tb),
        )


# The name a library is imported by and the name it is published on PyPI under
# are two different things. Handing pip the import name silently installs a
# squatted package of that name (`pytgcalls` 2.1.0, `google` 3.0.0) or nothing
# at all, which is why the install button used to report success and change
# nothing. Only names which really differ belong here
PIP_ALIASES = {
    "PIL": "pillow",
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python-headless",
    "dateutil": "python-dateutil",
    "docx": "python-docx",
    "dotenv": "python-dotenv",
    "fake_useragent": "fake-useragent",
    "git": "GitPython",
    "gtts": "gTTS",
    "magic": "python-magic",
    "markdown_it": "markdown-it-py",
    "mpl_toolkits": "matplotlib",
    "nacl": "PyNaCl",
    "OpenSSL": "pyOpenSSL",
    "pptx": "python-pptx",
    "pymorphy3": "pymorphy3",
    "pytgcalls": "py-tgcalls",
    "serial": "pyserial",
    "skimage": "scikit-image",
    "sklearn": "scikit-learn",
    "socks": "PySocks",
    "speech_recognition": "SpeechRecognition",
    "telethon": "",  # served by legacytl, must never be installed
    "tgcalls": "py-tgcalls",
    "usb": "pyusb",
    "yaml": "PyYAML",
    "zoneinfo": "backports.zoneinfo",
}

# Imports which are a part of a userbot rather than a PyPI package. `heroku`,
# `hikka` and friends are rewritten onto `legacy` by `patched_import`, so a
# failure on one of these is a bug in a module, not a missing dependency
NEVER_INSTALL = frozenset({
    "legacy",
    "legacytl",
    "heroku",
    "herokutl",
    "hikka",
    "hikkatl",
    "telethon",
})


# Some wheels are tens of megabytes, so pip is given room to work — but not
# forever, otherwise a hung resolver leaves the button spinning for good
PIP_TIMEOUT = 600


def _normalize_dist_name(name: str) -> str:
    """Brings a distribution name to the form PyPI compares by (PEP 503)"""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _requirement_name(requirement: str) -> str:
    """
    The bare distribution name of a `# requires:` entry, without extras, version
    specifiers or markers. A URL requirement has no name to speak of and comes
    out as something which matches nothing, which is exactly what we want
    """
    return _normalize_dist_name(re.split(r"[<>=!~;\[\s]", requirement, 1)[0])


def _importable(lib: str) -> bool:
    """Whether `lib` can be imported right now"""
    importlib.invalidate_caches()

    # `find_spec` of a dotted name imports every parent package of it, so this
    # runs third party code and may raise absolutely anything
    with contextlib.suppress(BaseException):
        return importlib.util.find_spec(lib) is not None

    return False


def _pip_report(output: str, limit: int = 12) -> str:
    """The tail of pip's output, trimmed to something a message can hold"""
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return utils.escape_html("\n".join(lines[-limit:])) or "no output"


def _pip_installed(output: str) -> str:
    """
    What pip says it actually did. `Successfully installed` names the versions
    which ended up on disk, which is the only trustworthy account of the run —
    the exit code says nothing about *which* distribution took the name
    """
    for line in reversed(output.splitlines()):
        line = line.strip()

        if line.startswith("Successfully installed "):
            return utils.escape_html(line[len("Successfully installed ") :])

        if line.startswith("Requirement already satisfied"):
            return "nothing, it was already there"

    return ""


def _pip_conflicts(output: str) -> typing.List[str]:
    """
    The dependency conflicts pip prints *after* a successful install. pip
    happily replaces a pinned version another library needs and still exits 0,
    so these lines are the difference between a working install and a broken
    userbot — they have to reach the user
    """
    conflicts = []
    collecting = False

    for line in output.splitlines():
        line = line.strip()

        if line.startswith("ERROR: pip's dependency resolver"):
            collecting = True
            continue

        if not collecting:
            continue

        # The block runs until the summary line or the end of the output
        if not line or line.startswith("Successfully installed"):
            break

        conflicts.append(utils.escape_html(line))

    return conflicts


def pip_name_of(lib: str) -> typing.Optional[str]:
    """
    Guesses the PyPI name of an import. `None` means the import cannot come
    from PyPI at all
    """
    root = lib.split(".", 1)[0]

    if root in NEVER_INSTALL:
        return None

    if root in PIP_ALIASES:
        return PIP_ALIASES[root] or None

    return root


def lib_of_message(message: str) -> typing.Optional[str]:
    """
    The import which a log message is complaining about, if it is complaining
    about one at all. `override_text` has already reduced the traceback to the
    quoted module name by the time this runs
    """
    if "No module named" not in message:
        return None

    match = re.search(r"'([^']+)'", message)

    return match[1] if match else None


def requirements_of_module(tb: typing.Optional[traceback.TracebackException]) -> list:
    """
    Digs the `# requires:` header out of the module which failed to import.
    The module itself knows the real names of its dependencies, so its own
    header is a far better source than any guess made from the import name
    """
    # `loader` sits in an import cycle with `main` and `dispatcher`, so it is
    # taken out of `sys.modules` rather than imported: a traceback belonging to
    # a module can only exist once the loader which ran it is up, which means it
    # is always there by the time this is reached
    loader = sys.modules.get(f"{__package__}.loader")

    if tb is None or loader is None:
        return []

    source = None
    prefix = f"{__package__}.{loader.MODULES_NAME}."

    # The deepest frame belonging to a module is the one which ran the import
    while tb is not None:
        frame = tb.tb_frame
        name = frame.f_globals.get("__name__") or ""

        if name.startswith(prefix):
            loader_ = frame.f_globals.get("__loader__")

            with contextlib.suppress(Exception):
                if hasattr(loader_, "get_source"):
                    source = loader_.get_source(name)

        tb = tb.tb_next

    if not source:
        return []

    match = loader.VALID_PIP_PACKAGES.search(source)

    if not match:
        return []

    return [
        requirement
        for requirement in map(str.strip, match[1].split())
        if requirement and not requirement.startswith(("-", "_", "."))
    ]


# At INFO `legacytl` also reports every file download, every update difference
# and every entity cache flush, so its records below WARNING are dropped. These
# are the exception: they say whether a closed connection was picked back up,
# which is what makes the closures themselves readable instead of alarming
_LEGACYTL_LIFECYCLE = (
    "Server closed the connection: %s",
    "The server closed the connection while sending",
    "Connection closed while receiving data: %s",
    "Connection closed while sending data",
    "Closing current connection to begin reconnect...",
    "Connecting to %s...",
    "Connection to %s complete!",
    "Failed reconnection attempt %d with %s",
    "Asking for the current state after reconnect...",
    "Successfully fetched missed updates",
    "Reconnecting to new data center %s",
    "Server does not know about the current auth key; the session may need to be recreated",
)


class LegacytlLifecycleFilter(logging.Filter):
    """
    Lets the connection lifecycle of `legacytl` through the level it is muted at

    Belongs on the handler rather than on the `legacytl` logger: a logger checks
    its own level before anything else, so muting it there would drop these
    records before a filter ever saw them, and a filter of a parent logger is
    not consulted for the records of its children anyway
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # The match is on a whole logger name, never on a prefix of one. The
        # names themselves are doubled up — `legacytl.legacytl.network...` —
        # because the library passes its children their own `__name__`
        if record.levelno >= logging.WARNING or not (
            record.name == "legacytl" or record.name.startswith("legacytl.")
        ):
            return True

        if record.msg not in _LEGACYTL_LIFECYCLE:
            return False

        # Belongs in the log file, not in the log chat: a reconnect happens
        # dozens of times an hour on a healthy client, and the chat is there
        # for what needs a person to look at it
        record.legacy_no_tg = True
        return True


class TelegramLogsHandler(logging.Handler):
    """
    Keeps 2 buffers.
    One for dispatched messages.
    One for unused messages.
    When the length of the 2 together is 100
    truncate to make them 100 together,
    first trimming handled then unused.
    """

    def __init__(self, targets: list, capacity: int):
        super().__init__(0)
        self.buffer = []
        self.handledbuffer = []
        self._queue = []
        self._mods = {}
        self.tg_buff = []
        self.force_send_all = False
        self.tg_level = 20
        self.ignore_common = False
        self.targets = targets
        self.capacity = capacity
        self.lvl = logging.NOTSET
        self._send_lock = asyncio.Lock()

    def install_tg_log(self, mod: Module):
        if getattr(self, "_task", False):
            self._task.cancel()

        self._mods[mod.tg_id] = mod

        self._task = asyncio.ensure_future(self.queue_poller())

    async def queue_poller(self):
        while True:
            with contextlib.suppress(Exception):
                await self.sender()
            await asyncio.sleep(3)

    def setLevel(self, level: int):
        self.lvl = level

    def dump(self):
        """Return a list of logging entries"""
        return self.handledbuffer + self.buffer

    def dumps(
        self,
        lvl: int = 0,
        client_id: typing.Optional[int] = None,
    ) -> typing.List[str]:
        """Return all entries of minimum level as list of strings"""
        return [
            self.targets[0].format(record)
            for record in self.buffer + self.handledbuffer
            if record.levelno >= lvl
            and (not record.legacy_caller or client_id == record.legacy_caller)
        ]

    def _log_markup(
        self,
        bot: "aiogram.Bot",  # type: ignore  # noqa: F821
        item: LegacyException,
        lib: typing.Optional[str] = None,
    ) -> list:
        """
        The buttons of a log message. An edit which does not pass the markup
        again silently drops the buttons, so every edit rebuilds them from here
        """
        buttons = [
            {
                "text": "🌙 Full traceback",
                "callback": self._show_full_trace,
                "args": (bot, item),
                "disable_security": True,
            },
        ]

        # No button for imports which cannot come from PyPI — offering to
        # install `legacy` or `telethon` only invites breaking the venv
        if lib and pip_name_of(lib):
            buttons.append(
                {
                    "text": "⬇️ Install",
                    "callback": self._install_pylib,
                    "args": (bot, item, lib),
                }
            )

        return buttons

    async def _install_pylib(
        self,
        call: BotInlineCall,
        bot: "aiogram.Bot",  # type: ignore  # noqa: F821
        item: LegacyException,
        lib: str,
    ):
        # The `# requires:` header of the module which failed is authoritative:
        # it carries the real PyPI names, extras and pins its author intended.
        # The import name is only a fallback for modules without a header
        requirements = requirements_of_module(
            item.sysinfo[2] if item.sysinfo else None
        )

        guess = pip_name_of(lib)

        if guess and not any(
            _requirement_name(requirement) == _normalize_dist_name(guess)
            for requirement in requirements
        ):
            requirements.append(guess)

        if not requirements:
            return await call.answer(
                f"🚫 {lib} is not a PyPI package — this is a bug in the module,"
                " not a missing dependency",
                show_alert=True,
            )

        await call.answer(f"⏳ Installing {', '.join(requirements)}...")

        with contextlib.suppress(Exception):
            await call.edit(
                f"{item.message}\n\n⏳ <b>Installing"
                f" <code>{utils.escape_html(' '.join(requirements))}</code>...</b>",
                reply_markup=self._log_markup(bot, item),
            )

        try:
            output, returncode = await self._run_pip(requirements)
        except asyncio.TimeoutError:
            await call.answer("🚫 pip timed out", show_alert=True)
            await self._install_failed(
                call,
                bot,
                item,
                lib,
                requirements,
                f"pip did not finish in {PIP_TIMEOUT} seconds",
            )
            return
        except Exception as e:
            logger.exception("Unable to run pip for %s", requirements)
            await call.answer("🚫 Unable to run pip", show_alert=True)
            await self._install_failed(
                call,
                bot,
                item,
                lib,
                requirements,
                f"{type(e).__name__}: {e}",
            )
            return

        if returncode != 0:
            await call.answer("🚫 pip failed", show_alert=True)
            await self._install_failed(
                call, bot, item, lib, requirements, _pip_report(output)
            )
            return

        # pip exiting 0 is not evidence of anything. A squatted package of the
        # same name installs cleanly and leaves the import just as missing, so
        # the only real check is whether the import works now
        if not await utils.run_sync(_importable, lib):
            await call.answer("🚫 Installed, but the import still fails", show_alert=True)
            await self._install_failed(
                call,
                bot,
                item,
                lib,
                requirements,
                (
                    f"pip reports success, but <code>{utils.escape_html(lib)}</code>"
                    " still cannot be imported. The name on PyPI most likely belongs"
                    " to a different project than the one this module needs.\n\npip"
                    f" installed: {_pip_installed(output) or 'nothing'}"
                ),
            )
            return

        text = (
            f"{item.message}\n\n✅ <b>Installed"
            f" <code>{utils.escape_html(' '.join(requirements))}</code></b>\n<i>Restart"
            " Legacy to load the module</i>"
        )

        if installed := _pip_installed(output):
            text += f"\n\n<b>pip installed:</b> <code>{installed}</code>"

        if conflicts := _pip_conflicts(output):
            text += (
                "\n\n⚠️ <b>pip broke the versions other libraries pinned:</b>\n<code>"
                + "\n".join(conflicts)
                + "</code>"
            )

        await call.answer("✅ Installed")

        with contextlib.suppress(Exception):
            # The Install button is gone from the markup: the library is there
            # and pressing it again would only churn the environment
            await call.edit(text, reply_markup=self._log_markup(bot, item))

    @staticmethod
    async def _run_pip(requirements: typing.List[str]) -> typing.Tuple[str, int]:
        """
        Runs pip for `requirements` and returns everything it said together with
        its exit code. `-q` is deliberately absent: the output is the evidence
        """
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-warn-script-location",
            *requirements,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=PIP_TIMEOUT
            )
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()

            with contextlib.suppress(Exception):
                await process.wait()

            raise

        return stdout.decode("utf-8", "replace"), process.returncode

    async def _install_failed(
        self,
        call: BotInlineCall,
        bot: "aiogram.Bot",  # type: ignore  # noqa: F821
        item: LegacyException,
        lib: str,
        requirements: typing.List[str],
        reason: str,
    ):
        """
        Reports a failed install in place of the log message, keeping the button
        so it can be pressed again once the cause is dealt with
        """
        text = (
            f"{item.message}\n\n🚫 <b>Unable to install"
            f" <code>{utils.escape_html(' '.join(requirements))}</code></b>\n"
            f"<code>{reason}</code>"
        )

        for chunk in utils.smart_split(*legacytl.extensions.html.parse(text), 4096):
            text = chunk
            break

        with contextlib.suppress(Exception):
            await call.edit(text, reply_markup=self._log_markup(bot, item, lib))
            return

        with contextlib.suppress(Exception):
            await bot.send_message(chat_id=call.chat_id, text=text)

    async def _show_full_trace(
        self,
        call: BotInlineCall,
        bot: "aiogram.Bot",  # type: ignore  # noqa: F821
        item: LegacyException,
    ):
        chunks = item.message + "\n\n<b>🌙 Full traceback:</b>\n" + item.full_stack

        chunks = list(utils.smart_split(*legacytl.extensions.html.parse(chunks), 4096))

        # The markup has to be passed again, otherwise reading the traceback
        # throws away the Install button along with it
        await call.edit(
            chunks[0],
            reply_markup=self._log_markup(bot, item, lib_of_message(item.message)),
        )

        for chunk in chunks[1:]:
            await bot.send_message(chat_id=call.chat_id, text=chunk)

    def get_logid_by_client(self, client_id: int) -> int:
        return self._mods[client_id].logchat
    
    def get_logs_topic_id_by_client(self, client_id: int) -> int:
        return self._mods[client_id]._logs_topic.id

    async def sender(self):
        async with self._send_lock:
            self._queue = {
                client_id: utils.chunks(
                    utils.escape_html(
                        "".join(
                            [
                                item[0]
                                for item in self.tg_buff
                                if isinstance(item[0], str)
                                and (
                                    not item[1]
                                    or item[1] == client_id
                                    or self.force_send_all
                                )
                            ]
                        )
                    ),
                    4096,
                )
                for client_id in self._mods
            }
            for client_id in self._mods:
                for item in self.tg_buff:
                    if isinstance(item[0], LegacyException) and (
                        not item[1] or item[1] == client_id or self.force_send_all
                    ):
                        lib = lib_of_message(item[0].message)

                        await self._mods[client_id].inline.bot.send_message(
                            self._mods[client_id].logchat,
                            item[0].message,
                            reply_markup=self._mods[client_id].inline.generate_markup(
                                self._log_markup(
                                    self._mods[client_id].inline.bot,
                                    item[0],
                                    lib,
                                )
                            ),
                            message_thread_id=self._mods[client_id]._logs_topic.id,
                        )

            self.tg_buff = []

            for client_id in self._mods:
                if client_id not in self._queue:
                    continue

                if len(self._queue[client_id]) > 5:
                    logfile = io.BytesIO(
                        "".join(self._queue[client_id]).encode("utf-8")
                    )
                    logfile.name = "legacy-logs.txt"
                    logfile.seek(0)
                    await self._mods[client_id].inline.bot.send_document(
                        self._mods[client_id].logchat,
                        logfile,
                        caption=(
                            "<b>🧳 Journals are too big to be sent as separate"
                            " messages</b>"
                        ),
                        message_thread_id=self._mods[client_id]._logs_topic.id,
                    )

                    self._queue[client_id] = []
                    continue

                while self._queue[client_id]:
                    if chunk := self._queue[client_id].pop(0):
                        asyncio.ensure_future(
                            self._mods[client_id].inline.bot.send_message(
                                self._mods[client_id].logchat,
                                f"<code>{chunk}</code>",
                                disable_notification=True,
                                message_thread_id=self._mods[client_id]._logs_topic.id,
                            )
                        )

    def emit(self, record: logging.LogRecord):
        try:
            caller = next(
                (
                    frame_info.frame.f_locals["_legacy_client_id_logging_tag"]
                    for frame_info in inspect.stack()
                    if isinstance(
                        getattr(getattr(frame_info, "frame", None), "f_locals", {}).get(
                            "_legacy_client_id_logging_tag"
                        ),
                        int,
                    )
                ),
                False,
            )

            if not isinstance(caller, int):
                caller = None
        except Exception:
            caller = None

        record.legacy_caller = caller

        if record.levelno >= self.tg_level and not getattr(
            record, "legacy_no_tg", False
        ):
            if record.exc_info:
                exc = LegacyException.from_exc_info(
                    *record.exc_info,
                    stack=record.__dict__.get("stack", None),
                    comment=record.msg % record.args,
                )

                if not self.ignore_common or all(
                    field not in exc.message
                    for field in [
                        "InputPeerEmpty() does not have any entity type",
                        "https://docs.legacytl.dev/en/stable/concepts/entities.html",
                    ]
                ):
                    self.tg_buff += [(exc, caller)]
            else:
                self.tg_buff += [
                    (
                        _tg_formatter.format(record),
                        caller,
                    )
                ]

        if len(self.buffer) + len(self.handledbuffer) >= self.capacity:
            if self.handledbuffer:
                del self.handledbuffer[0]
            else:
                del self.buffer[0]

        self.buffer.append(record)

        if record.levelno >= self.lvl >= 0:
            self.acquire()
            try:
                for precord in self.buffer:
                    for target in self.targets:
                        if record.levelno >= target.level:
                            target.handle(precord)

                self.handledbuffer = (
                    self.handledbuffer[-(self.capacity - len(self.buffer)) :]
                    + self.buffer
                )
                self.buffer = []
            finally:
                self.release()


_main_formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    style="%",
)
_tg_formatter = logging.Formatter(
    fmt="[%(levelname)s] %(name)s: %(message)s\n",
    datefmt=None,
    style="%",
)

rotating_handler = RotatingFileHandler(
    filename="legacy.log",
    mode="a",
    maxBytes=10 * 1024 * 1024,
    backupCount=1,
    encoding="utf-8",
    delay=0,
)

rotating_handler.setFormatter(_main_formatter)


def init():
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(_main_formatter)
    telegram_handler = TelegramLogsHandler((handler, rotating_handler), 7000)
    telegram_handler.addFilter(LegacytlLifecycleFilter())
    logging.getLogger().handlers = []
    logging.getLogger().addHandler(telegram_handler)
    logging.getLogger().setLevel(logging.NOTSET)
    # Not muted outright, `LegacytlLifecycleFilter` sorts its records out
    logging.getLogger("legacytl").setLevel(logging.INFO)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.captureWarnings(True)
