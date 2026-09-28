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

# External modules are compiled with a pseudo-filename, real core files never are.
_BRACKETED_FILE = re.compile(r"^<file (.+)>$")

# Filesystem fallback (tests, manually executed scripts).
_EXTERNAL_FILE = re.compile(r"^(.+)_(\d+)\.py$")


def _resolve_module_file(classname: str) -> typing.Optional[str]:
    """Find the on-disk source of a loaded module for the forensic copy."""
    try:
        from .. import loader as _loader

        directory = _loader.LOADED_MODULES_DIR
    except Exception:
        return None
    try:
        for entry in os.listdir(directory):
            if entry.startswith(classname + "_") and entry.endswith(".py"):
                return os.path.join(directory, entry)
    except Exception:
        pass
    return None

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


# Bound late in SessionGuardMod.client_ready. Before that (e.g. a stealer
# running at import time) trips are still blocked, just without quarantine.
_STATE = {
    "installed": False,
    "mod": None,
    "core_dir": os.path.abspath(utils.get_base_dir()),
}


def _is_session_path(path: str, sessions_dir: str = "") -> bool:
    """Whether `path` points at a Telegram session file worth stealing."""
    try:
        abs_path = os.path.abspath(path)
    except Exception:
        return False

    if sessions_dir and abs_path.startswith(sessions_dir + os.sep):
        return abs_path.endswith(".session")

    name = os.path.basename(abs_path)
    parts = abs_path.split(os.sep)
    if name.endswith(".session") and "sessions" in parts:
        return True
    if re.match(r"^(legacy|ratko|heroku)-.*\.session$", name):
        return True
    return False


def _attribute(core_dir: str) -> typing.Optional[typing.Tuple[str, typing.Optional[str], int, str]]:
    """Nearest externally-loaded module in the call stack, if any.

    Production modules are compiled by StringLoader with a pseudo-filename
    like `<file legacy.modules.StealerMod_8243127223>` — the class part (sans
    tg suffix) is what the loader unloads by. `<string>` (eval), `<frozen>`
    and core files never attribute.
    """
    for frame_info in inspect.stack(0):
        filename = frame_info.filename or ""
        bracketed = _BRACKETED_FILE.match(filename)
        if bracketed:
            inner = bracketed.group(1)
            if inner.startswith("legacy.modules."):
                dotted = inner.rsplit(".", 1)[1]
                classname = re.sub(r"_\d+$", "", dotted)
                if classname:
                    return (
                        classname,
                        _resolve_module_file(classname),
                        frame_info.lineno,
                        frame_info.function,
                    )
            continue
        if filename.startswith("<") and filename.endswith(">"):
            continue
        try:
            abs_name = os.path.abspath(filename)
        except Exception:
            continue
        if core_dir and abs_name.startswith(core_dir + os.sep):
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
    path: str, sessions_dir: str = ""
) -> typing.Optional[typing.Tuple[str, typing.Optional[str], int, str]]:
    if not _is_session_path(path, sessions_dir):
        return None
    return _attribute(_STATE["core_dir"])


def _guarded_open(file, *args, **kwargs):
    path = _normalize_path(file)
    if path is not None:
        mod = _STATE["mod"]
        if (mod is None or mod._guard_on()) and _check_access(
            path, mod._sessions_dir if mod is not None else ""
        ) is not None:
            culprit = _check_access(
                path, mod._sessions_dir if mod is not None else ""
            )
            if mod is not None:
                mod._on_trip(*culprit, path)
            logger.warning(
                "SessionGuard: blocked session read with no instance bound (%s)", path
            )
            raise PermissionError(f"SessionGuard: reading {path} is not allowed")
    return _REAL_OPEN(file, *args, **kwargs)


def _guarded_os_open(path, flags, *args, **kwargs):
    norm = _normalize_path(path)
    if norm is not None:
        mod = _STATE["mod"]
        if (mod is None or mod._guard_on()) and _check_access(
            norm, mod._sessions_dir if mod is not None else ""
        ) is not None:
            culprit = _check_access(
                norm, mod._sessions_dir if mod is not None else ""
            )
            if mod is not None:
                mod._on_trip(*culprit, norm)
            logger.warning(
                "SessionGuard: blocked session read with no instance bound (%s)", norm
            )
            raise PermissionError(f"SessionGuard: reading {norm} is not allowed")
    return _REAL_OS_OPEN(path, flags, *args, **kwargs)


def _guarded_sqlite_connect(database=None, *args, **kwargs):
    norm = _normalize_path(database)
    if norm is not None:
        mod = _STATE["mod"]
        if (mod is None or mod._guard_on()) and _check_access(
            norm, mod._sessions_dir if mod is not None else ""
        ) is not None:
            culprit = _check_access(
                norm, mod._sessions_dir if mod is not None else ""
            )
            if mod is not None:
                mod._on_trip(*culprit, norm)
            logger.warning(
                "SessionGuard: blocked session read with no instance bound (%s)", norm
            )
            raise PermissionError(f"SessionGuard: reading {norm} is not allowed")
    return _REAL_SQLITE_CONNECT(database, *args, **kwargs)


def _install_global_wrappers():
    if _STATE["installed"]:
        return
    builtins.open = _guarded_open
    io.open = _guarded_open
    os.open = _guarded_os_open
    sqlite3.connect = _guarded_sqlite_connect
    _STATE["installed"] = True


def _remove_global_wrappers():
    if not _STATE["installed"]:
        return
    builtins.open = _REAL_OPEN
    io.open = _REAL_IO_OPEN
    os.open = _REAL_OS_OPEN
    sqlite3.connect = _REAL_SQLITE_CONNECT
    _STATE["installed"] = False
    _STATE["mod"] = None


try:
    _install_global_wrappers()
except Exception:
    logger.debug("SessionGuard: global tripwire install failed", exc_info=True)


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

        # Bind this instance to the tripwires installed at import time, so
        # trips schedule quarantine and alerts instead of just blocking.
        _STATE["mod"] = self
        _STATE["core_dir"] = self._core_dir = os.path.abspath(os.path.join(base))
        if not _STATE["installed"]:
            _install_global_wrappers()
        self._installed = True
        logger.debug("SessionGuard bound to tripwires")

    async def on_unload(self):
        # Core modules are not unloadable in practice; unbind anyway so a
        # forced reload never leaves stale wrappers behind.
        if _STATE.get("mod") is self:
            _STATE["mod"] = None

    def _guard_on(self) -> bool:
        try:
            return bool(self.config["session_guard"])
        except Exception:
            return True

    def _is_session_path(self, path: str) -> bool:
        return _is_session_path(path, self._sessions_dir)

    def _attribute(self) -> typing.Optional[typing.Tuple[str, typing.Optional[str], int, str]]:
        return _attribute(self._core_dir or _STATE["core_dir"])

    def _check_access(
        self, path: str
    ) -> typing.Optional[typing.Tuple[str, typing.Optional[str], int, str]]:
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

    async def _quarantine(
        self, classname: str, filepath: typing.Optional[str], evidence: str
    ):
        quarantined = False
        if self.config["auto_quarantine"]:
            self._local.busy = True
            try:
                if filepath and os.path.isfile(filepath):
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

    def _quarantine_files(self) -> typing.List[str]:
        """Quarantined copies on disk, newest first."""
        try:
            entries = [
                os.path.join(self._quarantine_dir, entry)
                for entry in os.listdir(self._quarantine_dir)
                if entry.endswith(".quarantined")
            ]
        except Exception:
            return []
        entries.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        return entries

    @staticmethod
    def _quarantine_name(path: str) -> str:
        """Human name of a quarantined copy: ClassName (+ tg id kept raw)."""
        name = os.path.basename(path)
        if name.endswith(".quarantined"):
            name = name[: -len(".quarantined")]
        name = re.sub(r"\.\d+$", "", name)
        return name

    @loader.command()
    async def sessionguard(self, message):
        """- Check session-theft protection status and rescan modules"""
        args = utils.get_args_raw(message).strip()
        low = args.lower()
        if low == "off":
            self.config["session_guard"] = False
            await utils.answer(message, self.strings("disabled"))
            return
        if low == "on":
            self.config["session_guard"] = True
            await utils.answer(message, self.strings("enabled"))
            return
        if low.startswith("send "):
            want = args[5:].strip().lower()
            for path in self._quarantine_files():
                if self._quarantine_name(path).lower().startswith(want):
                    await utils.answer(
                        message,
                        self.strings("q_sent").format(
                            utils.escape_html(self._quarantine_name(path))
                        ),
                        file=path,
                    )
                    return
            await utils.answer(
                message, self.strings("q_not_found").format(utils.escape_html(args[5:].strip()))
            )
            return
        hits = await self.rescan()
        text = self.strings("status").format(
            self.strings("on" if self._guard_on() else "off"),
            len(hits),
        )
        files = self._quarantine_files()
        if files:
            text += self.strings("q_list_header")
            for path in files[:20]:
                text += self.strings("q_item").format(
                    utils.escape_html(self._quarantine_name(path))
                )
        else:
            text += self.strings("q_empty")
        await utils.answer(message, text)
