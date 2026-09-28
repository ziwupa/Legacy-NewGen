# Session theft guard for Legacy NewGen
# Watches reads of Telegram session files and quarantines offending modules

import asyncio
import builtins
import contextlib
import inspect
import io
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import typing

from .. import loader, utils

logger = logging.getLogger(__name__)

# Kept as plain builtins so the wrappers below can be verified/restored.
_REAL_OPEN = builtins.open
_REAL_IO_OPEN = io.open
_REAL_OS_OPEN = os.open
_REAL_SQLITE_CONNECT = sqlite3.connect

# loaded_modules files look like {ClassName}_{tg_id}.py — core files never do.
_EXTERNAL_FILE = re.compile(r"^(.+)_(\d+)\.py$")

# Source-scan rules, applied to code with string literals and comments
# masked out (signatures and docs must not count). Network-send verbs only
# count when session material is present too — moving files around alone is
# what downloaders, backups and chats do all day.
_SCAN_CODE_RES = (
    ("auth_key", re.compile(r"(?<![\w])auth_key\b"), 4),
    ("StringSession", re.compile(r"(?<![\w])StringSession\b"), 4),
    ("acceptLoginToken", re.compile(r"(?<![\w])acceptLoginToken\b"), 4),
    ("sessions/", re.compile(r"sessions/"), 3),
)

# Quoted session-file literals and session file operations are checked on
# a comments-stripped (but strings-kept) view: masking eats string literals,
# while comments must never trigger anything.
_SCAN_PATH_LITERAL = re.compile(r"""['"][^'"]*sessions/[^'"]*\.session""")
_SCAN_OPEN_SESSION = re.compile(
    r"(open|read_bytes|read_text|connect|copyfile|copy)\s*\([^)]*\.session"
)

_SCAN_EXFIL_WEIGHTS = (
    ("requests.post", 2),
    ("requests.get", 2),
    ("urlopen", 2),
    ("send_file", 2),
    ("aiohttp", 1),
)

_SCAN_DESTRUCTIVE = (
    ("deleteAccount", 2),
    ("resetAuthorizations", 2),
    ("changePhone", 2),
)

# Score at which a scanned module is quarantined outright.
_QUARANTINE_SCORE = 5

# How often loaded modules are re-scanned for theft patterns.
_RESCAN_INTERVAL = 600


def _normalize_path(file: typing.Any) -> typing.Optional[str]:
    if isinstance(file, int):
        return None
    if isinstance(file, (bytes, bytearray)):
        try:
            file = bytes(file).decode("utf-8", "replace")
        except Exception:
            return None
    if isinstance(file, os.PathLike):
        file = os.fspath(file)
    if not isinstance(file, str):
        return None
    return file


@loader.tds
class SessionGuardMod(loader.Module):
    strings = {"name": "SessionGuard"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "session_guard",
                True,
                lambda: self.strings("_cfg_doc_session_guard"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "auto_quarantine",
                True,
                lambda: self.strings("_cfg_doc_auto_quarantine"),
                validator=loader.validators.Boolean(),
            ),
        )
        self._installed = False
        self._local = threading.local()
        self._loop: typing.Optional[asyncio.AbstractEventLoop] = None
        self._sessions_dir = ""
        self._core_dir = ""
        self._quarantine_dir = ""
        self._last_alert = 0.0

    async def client_ready(self, client, db):
        from .. import main as _main

        base = utils.get_base_dir()
        self._sessions_dir = os.path.abspath(
            getattr(_main, "SESSIONS_DIR", os.path.join(base, "..", "sessions"))
        )
        self._core_dir = os.path.abspath(os.path.join(base))
        self._quarantine_dir = os.path.abspath(
            os.path.join(base, "..", "quarantined")
        )
        with contextlib.suppress(Exception):
            os.makedirs(self._quarantine_dir, exist_ok=True)

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        self._install_wrappers()

    def _install_wrappers(self):
        if self._installed:
            return

        mod = self

        def _guarded_open(file, *args, **kwargs):
            path = _normalize_path(file)
            if path is not None:
                culprit = mod._check_access(path) if mod._guard_on() else None
                if culprit is not None:
                    mod._on_trip(*culprit, path)
            return _REAL_OPEN(file, *args, **kwargs)

        def _guarded_os_open(path, flags, *args, **kwargs):
            norm = _normalize_path(path)
            if norm is not None:
                culprit = mod._check_access(norm) if mod._guard_on() else None
                if culprit is not None:
                    mod._on_trip(*culprit, norm)
            return _REAL_OS_OPEN(path, flags, *args, **kwargs)

        def _guarded_sqlite_connect(database=None, *args, **kwargs):
            norm = _normalize_path(database)
            if norm is not None:
                culprit = mod._check_access(norm) if mod._guard_on() else None
                if culprit is not None:
                    mod._on_trip(*culprit, norm)
            return _REAL_SQLITE_CONNECT(database, *args, **kwargs)

        builtins.open = _guarded_open
        io.open = _guarded_open
        os.open = _guarded_os_open
        sqlite3.connect = _guarded_sqlite_connect
        self._installed = True
        logger.debug("SessionGuard file tripwires installed")

    async def on_unload(self):
        if not self._installed:
            return
        builtins.open = _REAL_OPEN
        io.open = _REAL_IO_OPEN
        os.open = _REAL_OS_OPEN
        sqlite3.connect = _REAL_SQLITE_CONNECT
        self._installed = False
        logger.debug("SessionGuard file tripwires removed")

    def _guard_on(self) -> bool:
        try:
            return bool(self.config["session_guard"])
        except Exception:
            return True

    def _is_session_path(self, path: str) -> bool:
        """Whether `path` points at a Telegram session file worth stealing."""
        try:
            abs_path = os.path.abspath(path)
        except Exception:
            return False

        if self._sessions_dir and abs_path.startswith(self._sessions_dir + os.sep):
            return abs_path.endswith(".session")

        name = os.path.basename(abs_path)
        parts = abs_path.split(os.sep)
        if name.endswith(".session") and "sessions" in parts:
            return True
        if re.match(r"^(legacy|ratko|heroku)-.*\.session$", name):
            return True
        return False

    def _attribute(self) -> typing.Optional[typing.Tuple[str, str, int, str]]:
        """Find the nearest externally-loaded module in the call stack.

        Returns (classname, filepath, lineno, funcname) or None when the
        access comes from core code, stdlib, or eval (owner) context.
        """
        for frame_info in inspect.stack(0):
            filename = frame_info.filename or ""
            if filename.startswith("<") and filename.endswith(">"):
                # <string>, <frozen ...> — look further out for the caller
                # that exec'd it instead of blaming thin air.
                continue
            try:
                abs_name = os.path.abspath(filename)
            except Exception:
                continue
            if self._core_dir and abs_name.startswith(self._core_dir + os.sep):
                continue
            if "site-packages" in abs_name.split(os.sep):
                continue
            match = _EXTERNAL_FILE.match(os.path.basename(abs_name))
            if match:
                return (
                    match.group(1),
                    abs_name,
                    frame_info.lineno,
                    frame_info.function,
                )
        return None

    def _check_access(
        self, path: str
    ) -> typing.Optional[typing.Tuple[str, str, int, str]]:
        """Path matched a session file — attribute it or let it through."""
        if getattr(self._local, "busy", False):
            return None
        if not self._is_session_path(path):
            return None
        return self._attribute()

    def _on_trip(self, classname: str, filepath: str, lineno: int, func: str, path: str):
        """A foreign module touched a session file: block the read now,
        quarantine asynchronously (open() is sync and cannot await)."""
        self._schedule(self._quarantine(classname, filepath, f"{path}:{func}:{lineno}"))
        raise PermissionError(
            f"SessionGuard: module {classname} is not allowed to read {path}"
        )

    def _schedule(self, coro):
        try:
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = self._loop
            if loop is not None and not loop.is_closed():
                # The callback runs on the loop thread, so ensure_future
                # finds a running loop there by itself.
                loop.call_soon_threadsafe(asyncio.ensure_future, coro)
                return
        except Exception:
            logger.debug("SessionGuard: no loop to schedule quarantine on", exc_info=True)
        # No loop (sync-only context): block already happened above; at least
        # leave a loud trace so the owner sees it in logs.
        logger.warning("SessionGuard trip with no event loop (read was blocked)")

    async def _quarantine(self, classname: str, filepath: str, evidence: str):
        quarantined = False
        if self.config["auto_quarantine"]:
            self._local.busy = True
            try:
                if os.path.isfile(filepath):
                    with contextlib.suppress(Exception):
                        os.makedirs(self._quarantine_dir, exist_ok=True)
                    dst = os.path.join(
                        self._quarantine_dir,
                        f"{os.path.basename(filepath)}.{int(time.time())}.quarantined",
                    )
                    with contextlib.suppress(Exception):
                        shutil.copyfile(filepath, dst)
                with contextlib.suppress(Exception):
                    worked = await self.allmodules.unload_module(classname)
                    quarantined = bool(worked)
            finally:
                self._local.busy = False

        action = (
            self.strings("quarantined") if quarantined else self.strings("blocked")
        )
        await self._alert(classname, evidence, action)

    async def _alert(self, classname: str, evidence: str, action: str):
        # Debounce: one alert per minute max, a persistent attacker must not
        # be able to spam the owner.
        now = time.monotonic()
        if now - self._last_alert < 60:
            logger.warning("SessionGuard: %s (%s) — %s", classname, evidence, action)
            return
        self._last_alert = now
        text = self.strings("trip_alert").format(classname, evidence, action)
        logger.warning("SessionGuard: %s (%s) — %s", classname, evidence, action)
        try:
            await self.inline.bot.send_message(self.tg_id, text)
        except Exception:
            logger.exception("SessionGuard: unable to deliver alert")

    def _mask_source(self, source: str) -> str:
        """Blank out string literals and comments, keeping line layout.

        Detection signatures and docs mention scary tokens without acting on
        them; only live code counts. String *contents* are replaced with
        spaces so column-sensitive regexes keep working.
        """
        out = []
        i, n = 0, len(source)
        while i < n:
            ch = source[i]
            # comments
            if ch == "#":
                while i < n and source[i] != "\n":
                    out.append(" ")
                    i += 1
                continue
            # strings (possibly prefixed, possibly triple-quoted)
            if ch in "'\"":
                quote = ch
                triple = source.startswith(quote * 3, i)
                j = i + (3 if triple else 1)
                while j < n:
                    if source[j] == "\\":
                        j += 2
                        continue
                    if triple and source.startswith(quote * 3, j):
                        j += 3
                        break
                    if not triple and source[j] == quote:
                        j += 1
                        break
                    if not triple and source[j] == "\n":
                        break
                    j += 1
                out.append(" " * (j - i))
                i = j
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    def _strip_comments(self, source: str) -> str:
        """Blank out `#` comments outside of string literals."""
        out = []
        i, n = 0, len(source)
        in_str = None
        triple = False
        while i < n:
            ch = source[i]
            if in_str is not None:
                if ch == "\\":
                    out.append("  ")
                    i += 2
                    continue
                if triple and source.startswith(in_str * 3, i):
                    out.append(in_str * 3)
                    i += 3
                    in_str = None
                    triple = False
                    continue
                if not triple and ch == in_str:
                    out.append(ch)
                    i += 1
                    in_str = None
                    continue
                out.append(ch)
                i += 1
                continue
            if ch == "#":
                while i < n and source[i] != "\n":
                    out.append(" ")
                    i += 1
                continue
            if ch in "'\"":
                # Keep the quote characters themselves: path-literal
                # regexes below rely on them, only comments are blanked.
                if source.startswith(ch * 3, i):
                    in_str, triple = ch, True
                    out.append(ch * 3)
                    i += 3
                else:
                    in_str, triple = ch, False
                    out.append(ch)
                    i += 1
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    def _score_source(self, source: str) -> typing.Tuple[int, typing.List[str]]:
        masked = self._mask_source(source)
        code_only = self._strip_comments(source)
        found = []
        session_material = 0
        for name, pattern, weight in _SCAN_CODE_RES:
            if pattern.search(masked):
                session_material += weight
                found.append(name)
        if _SCAN_PATH_LITERAL.search(code_only):
            session_material += 4
            found.append("sessions/*.session literal")
        if _SCAN_OPEN_SESSION.search(code_only):
            session_material += 3
            found.append("open(*.session)")
        score = session_material
        if session_material:
            for token, weight in _SCAN_EXFIL_WEIGHTS:
                if token in masked:
                    score += weight
                    found.append(token)
            for token, weight in _SCAN_DESTRUCTIVE:
                if token in masked:
                    score += weight
                    found.append(token)
        return score, found

    async def rescan(self) -> typing.List[typing.Tuple[str, int, typing.List[str]]]:
        """Score every externally loaded module; quarantine blatant stealers."""
        from .. import loader as _loader

        hits = []
        try:
            entries = os.listdir(_loader.LOADED_MODULES_DIR)
        except Exception:
            return hits
        for entry in entries:
            if not entry.endswith(".py"):
                continue
            full = os.path.join(_loader.LOADED_MODULES_DIR, entry)
            try:
                with _REAL_OPEN(full, encoding="utf-8", errors="replace") as f:
                    source = f.read()
            except Exception:
                continue
            score, tokens = self._score_source(source)
            if score < _QUARANTINE_SCORE:
                continue
            match = _EXTERNAL_FILE.match(entry)
            classname = match.group(1) if match else entry
            hits.append((classname, score, tokens))
            if self._guard_on():
                await self._quarantine(classname, full, "source-scan: " + ", ".join(tokens))
        return hits

    @loader.loop(interval=_RESCAN_INTERVAL, autostart=True)
    async def rescan_loop(self):
        if not self._guard_on():
            return
        try:
            await self.rescan()
        except Exception:
            logger.exception("SessionGuard rescan failed")

    @loader.command()
    async def sessionguard(self, message):
        """- Check session-theft protection status and rescan modules"""
        args = utils.get_args_raw(message).strip().lower()
        if args == "off":
            self.config["session_guard"] = False
            await utils.answer(message, self.strings("disabled"))
            return
        if args == "on":
            self.config["session_guard"] = True
            await utils.answer(message, self.strings("enabled"))
            return
        hits = await self.rescan()
        await utils.answer(
            message,
            self.strings("status").format(
                self.strings("on" if self._guard_on() else "off"),
                len(hits),
            ),
        )
