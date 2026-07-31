# ©️ Dan Gazizullin, 2021-2023
# This file is a part of Hikka Userbot
# 🌐 https://github.com/hikariatama/Hikka
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import contextlib
import logging
import re
import typing
from pathlib import Path

import ujson

from .. import loader, main, utils
from ..inline.types import InlineCall
from ..types import Message

logger = logging.getLogger(__name__)

# Forks this module is able to migrate from. The name of the package directory
# doubles as the database namespace prefix of that fork
KNOWN_FORKS = {
    "hikka": "🌘 Hikka",
    "heroku": "🪐 Heroku",
    "legacy": "🌙 Legacy",
}

# Directory names to look through when searching for an installation. Combined
# with the parents of the current one, so a sibling checkout is found too
SEARCH_NAMES = (
    "Hikka",
    "hikka",
    "Heroku",
    "heroku",
    "Legacy",
    "legacy",
    "Legacy-NewGen",
    "legacy-newgen",
    "userbot",
)

# Keys of this account's own infrastructure. They describe the content channel,
# its topics and the inline bot of THIS installation, so they must not be taken
# from the one being migrated — otherwise topics and the bot are recreated
LOCAL_KEYS = (
    ("legacy.forums", None),
    ("legacy.inline", "bot_token"),
)


class Installation(typing.NamedTuple):
    """A userbot installation found on this machine"""

    path: Path
    fork: str
    db_file: Path
    tg_id: int

    @property
    def title(self) -> str:
        return KNOWN_FORKS.get(self.fork, self.fork.capitalize())

    @property
    def modules_dir(self) -> Path:
        return self.path / "loaded_modules"

    def count_modules(self) -> int:
        if not self.modules_dir.is_dir():
            return 0

        return sum(
            1
            for file in self.modules_dir.iterdir()
            if file.suffix == ".py" and file.name.endswith(f"{self.tg_id}.py")
        )


@loader.tds
class LegacyMigrateMod(loader.Module):
    strings = {"name": "LegacyMigrate"}

    async def client_ready(self, client, db):
        self._client = client
        self._db = db

    # ======================================================================= #
    #  Discovery                                                             #
    # ======================================================================= #

    def _read_installation(self, path: Path) -> typing.Optional[Installation]:
        """
        Recognises a userbot installation in `path`. An installation is a
        directory which holds a package of a known fork next to at least one
        `config-<tg_id>.json` database of that fork
        """
        with contextlib.suppress(OSError):
            if not path.is_dir():
                return None

            fork = next(
                (name for name in KNOWN_FORKS if (path / name / "main.py").is_file()),
                None,
            )

            if not fork:
                return None

            # A database named after this account is preferred, so that a
            # multi-account installation migrates the right one
            databases = sorted(
                (
                    (file, int(match.group(1)))
                    for file in path.glob("config-*.json")
                    if (match := re.fullmatch(r"config-(\d+)\.json", file.name))
                ),
                key=lambda item: item[1] != self.tg_id,
            )

            if not databases:
                return None

            db_file, tg_id = databases[0]

            return Installation(path=path, fork=fork, db_file=db_file, tg_id=tg_id)

        return None

    def _candidates(self) -> typing.Iterator[Path]:
        """Yields directories which may hold another installation"""
        own = Path(main.BASE_DIR).resolve()

        roots = {Path.home(), own.parent, own.parent.parent, Path("/opt"), Path("/root")}

        for root in roots:
            for name in SEARCH_NAMES:
                yield root / name

            # A checkout of another fork often sits right next to ours under a
            # name we cannot guess, so shallow-scan the parent directories
            if root in (own.parent, own.parent.parent):
                with contextlib.suppress(OSError):
                    yield from (item for item in root.iterdir() if item.is_dir())

    def _discover(self) -> typing.List[Installation]:
        """Finds every installation on this machine except our own"""
        own = Path(main.BASE_DIR).resolve()

        found = {}

        for candidate in self._candidates():
            with contextlib.suppress(OSError):
                resolved = candidate.resolve()

                if resolved == own or resolved in found:
                    continue

                if installation := self._read_installation(resolved):
                    found[resolved] = installation

        return sorted(found.values(), key=lambda item: item.path.as_posix())

    # ======================================================================= #
    #  Migration                                                             #
    # ======================================================================= #

    @staticmethod
    def _convert_namespaces(db: dict) -> dict:
        """
        Renames the database namespaces of any known fork to `legacy.` ones.
        Unlike the regex of `LegacyBackup`, this works on the parsed database,
        so a value which happens to contain `hikka.` is left alone
        """
        converted = {}

        for key, value in db.items():
            if isinstance(key, str):
                for fork in KNOWN_FORKS:
                    if key.startswith(f"{fork}."):
                        key = f"legacy.{key[len(fork) + 1:]}"
                        break

            # A namespace may already exist under its `legacy.` name, in which
            # case the values of both are merged
            if isinstance(value, dict) and isinstance(converted.get(key), dict):
                converted[key].update(value)
            else:
                converted[key] = value

        return converted

    def _prepare_db(self, installation: Installation) -> dict:
        """Reads the database of `installation` and adapts it to this one"""
        new_db = ujson.loads(installation.db_file.read_text(encoding="utf-8"))

        if not isinstance(new_db, dict) or not new_db:
            raise RuntimeError("The database of that installation is empty or broken")

        new_db = self._convert_namespaces(new_db)

        # The inline bot of that installation belongs to it, not to us
        with contextlib.suppress(KeyError, AttributeError):
            new_db["legacy.inline"].pop("bot_token")

        for owner, key in LOCAL_KEYS:
            if not isinstance(local := dict.get(self._db, owner), dict):
                continue

            if key is None:
                new_db[owner] = dict(local)
            elif key in local:
                new_db.setdefault(owner, {})[key] = local[key]

        if not self._db.process_db_autofix(new_db):
            raise RuntimeError("The database of that installation is broken")

        return new_db

    def _copy_modules(self, installation: Installation) -> int:
        """
        Copies the external modules of `installation`, renaming them after this
        account — the loader ignores files which do not end with our own tg_id
        """
        if not installation.modules_dir.is_dir():
            return 0

        copied = 0

        for file in installation.modules_dir.iterdir():
            if file.suffix != ".py" or not file.name.endswith(f"{installation.tg_id}.py"):
                continue

            with contextlib.suppress(OSError):
                stem = re.sub(r"_\d+$", "", file.stem)
                (loader.LOADED_MODULES_PATH / f"{stem}_{self.tg_id}.py").write_bytes(
                    file.read_bytes()
                )
                copied += 1

        return copied

    def _migrate(self, installation: Installation) -> int:
        """Applies `installation` over this one and returns the amount of modules"""
        new_db = self._prepare_db(installation)
        copied = self._copy_modules(installation)

        self._db.clear()
        self._db.update(**new_db)
        self._db.save()

        return copied

    # ======================================================================= #
    #  Commands                                                              #
    # ======================================================================= #

    def _describe(self, installation: Installation) -> str:
        return self.strings("installation").format(
            title=utils.escape_html(installation.title),
            path=utils.escape_html(installation.path.as_posix()),
            modules=installation.count_modules(),
            id=installation.tg_id,
        )

    async def _confirm(
        self,
        message: Message,
        installation: Installation,
    ):
        async def confirmed(call: InlineCall):
            await utils.answer(call, self.strings("migrating"))

            try:
                copied = self._migrate(installation)
            except Exception as e:
                logger.exception("Unable to migrate from %s", installation.path)
                await utils.answer(
                    call,
                    self.strings("failed").format(utils.escape_html(str(e))),
                )
                return

            await utils.answer(
                call,
                self.strings("migrated").format(
                    title=utils.escape_html(installation.title),
                    modules=copied,
                ),
            )
            await self.invoke("restart", "-f", peer=message.peer_id)

        text = self.strings("confirm").format(self._describe(installation))

        if not await self.inline.form(
            message=message,
            text=text,
            reply_markup=[
                {"text": self.strings("btn_migrate"), "callback": confirmed},
                {"text": self.strings("btn_cancel"), "action": "close"},
            ],
        ):
            await utils.answer(message, text)

    @loader.command()
    async def migrate(self, message: Message):
        args = utils.get_args_raw(message)

        if args:
            installation = self._read_installation(Path(args).expanduser())

            if not installation:
                await utils.answer(
                    message,
                    self.strings("not_an_installation").format(
                        utils.escape_html(args)
                    ),
                )
                return

            await self._confirm(message, installation)
            return

        message = await utils.answer(message, self.strings("searching"))

        installations = await utils.run_sync(self._discover)

        if not installations:
            await utils.answer(
                message,
                self.strings("nothing_found").format(self.get_prefix()),
            )
            return

        if len(installations) == 1:
            await self._confirm(message, installations[0])
            return

        await utils.answer(
            message,
            self.strings("found").format(
                "\n\n".join(map(self._describe, installations)),
                self.get_prefix(),
            ),
        )
