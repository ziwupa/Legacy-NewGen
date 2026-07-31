# ©️ Dan Gazizullin, 2021-2023
# This file is a part of Hikka Userbot
# 🌐 https://github.com/hikariatama/Hikka
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
import contextlib
import copy
import datetime
import io
import logging
import os
import re
import time
import typing
import zipfile
from pathlib import Path

import ujson
from aiogram.types import BufferedInputFile

from .. import loader, main, utils
from ..inline.types import BotInlineCall

logger = logging.getLogger(__name__)

# Names of a database dump inside an archive. `db-backup.json` is used by
# Legacy and Hikka, `db.json` — by Heroku
DB_NAMES = ("db-backup.json", "db.json")

# Name of the `LoaderMod.loaded_modules` dump inside an archive
LINKS_NAME = "db_mods.json"

# Heroku keeps modules in a nested archive with this name
NESTED_NAME = "mods.zip"

# Everything inside an archive which is not a module
SERVICE_FILES = frozenset({*DB_NAMES, LINKS_NAME, NESTED_NAME})

# Namespaces of other userbots, which are renamed to `legacy.` on restore
FOREIGN_PREFIXES = ("heroku", "hikka")

# Keys, which describe THIS account's own infrastructure (content channel and
# its topics, inline bot). They are not transferable, so they survive a restore
# instead of being overwritten by the ones from the backup — otherwise topics
# and the bot are created anew on every restore
LOCAL_KEYS = (
    ("legacy.forums", None),
    ("legacy.inline", "bot_token"),
)


@loader.tds
class LegacyBackupMod(loader.Module):
    strings = {"name": "LegacyBackup"}

    async def client_ready(self, client, db):
        self._client = client
        self._db = db
        
        if not self.get("period"):
            await self.inline.bot.send_photo(
                self.tg_id,
                photo="https://i.postimg.cc/8PPXPyK5/legacy-unit-alpha.png",
                caption=self.strings["period"],
                reply_markup=self.inline.generate_markup(
                    utils.chunks(
                        [
                            {
                                "text": f"🕰 {i} h",
                                "callback": self._set_backup_period,
                                "args": (i,),
                            }
                            for i in [1, 2, 4, 6, 8, 12, 24, 48, 168]
                        ],
                        3,
                    )
                    + [
                        [
                            {
                                "text": "🚫 Never",
                                "callback": self._set_backup_period,
                                "args": (0,),
                            }
                        ]
                    ]
                ),
            )

        self._content_channel_id = await utils.wait_for_content_channel(self._db)

        await utils.fw_protect()
        
        self._backup_topic = await utils.asset_forum_topic(
            client=self._client,
            db=self._db,
            peer=self._content_channel_id,
            title="Backups",
            description="📼 Your database backups will appear here",
            icon_emoji_id=6024106569430472546,
        )

    # ======================================================================= #
    #  Compatibility layer: allows restoring backups of Heroku, Hikka and     #
    #  Legacy itself, in any of their layouts                                 #
    # ======================================================================= #

    @staticmethod
    def _convert_prefixes(raw: str) -> str:
        """Renames `heroku.` / `hikka.` database namespaces to `legacy.`"""
        for prefix in FOREIGN_PREFIXES:
            raw = re.sub(
                rf"({prefix}\.)(\S+?\":)",
                lambda m: f"legacy.{m.group(2)}",
                raw,
            )

        return raw

    def _load_db(self, raw: bytes) -> dict:
        """Parses a database dump of any known userbot into a `legacy.` one"""
        decoded = raw.decode()

        if any(f'"{prefix}.' in decoded for prefix in FOREIGN_PREFIXES):
            logger.info("Foreign database detected, converting its namespaces")
            decoded = self._convert_prefixes(decoded)

        new_db = ujson.loads(decoded)

        if not isinstance(new_db, dict) or not new_db:
            raise RuntimeError("Attempted to restore broken database")

        # Inline bot token belongs to the author of the backup and is of no
        # use here, so it is dropped whichever userbot it came from
        for prefix in ("legacy", *FOREIGN_PREFIXES):
            with contextlib.suppress(KeyError, AttributeError):
                new_db[f"{prefix}.inline"].pop("bot_token")

        return new_db

    def _preserve_local_keys(self, new_db: dict):
        """
        Carries this account's own infrastructure over into the database being
        restored, so that the content channel topics and the inline bot are
        reused instead of being created from scratch
        """
        for owner, key in LOCAL_KEYS:
            if not isinstance(local := dict.get(self._db, owner), dict):
                continue

            if key is None:
                new_db[owner] = copy.deepcopy(local)
            elif key in local:
                new_db.setdefault(owner, {})[key] = local[key]

    def _apply_db(self, new_db: dict):
        if not self._db.process_db_autofix(new_db):
            raise RuntimeError("Attempted to restore broken database")

        self._preserve_local_keys(new_db)

        self._db.clear()
        self._db.update(**new_db)
        self._db.save()

    def _create_backup(self) -> io.BytesIO:
        """
        Creates a backup archive. Its layout is the flat one, understood by
        Legacy and Hikka, extended with `db_mods.json` of Heroku
        """
        result = io.BytesIO()

        with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(loader.LOADED_MODULES_DIR):
                for file in files:
                    if file.endswith(f"{self.tg_id}.py"):
                        with open(os.path.join(root, file), "rb") as f:
                            zipf.writestr(file, f.read())

            zipf.writestr("db-backup.json", ujson.dumps(self._db).encode())

            if loader_mod := self.lookup("LoaderMod"):
                zipf.writestr(
                    LINKS_NAME,
                    ujson.dumps(loader_mod.get("loaded_modules", {})).encode(),
                )

        outfile = io.BytesIO(result.getvalue())
        outfile.name = f"legacy-{datetime.datetime.now():%d-%m-%Y-%H-%M}.backup"
        return outfile

    def _write_module(self, name: str, data: bytes):
        """
        Saves a single module file, making sure its name ends with the tg_id of
        this account — otherwise the loader will simply ignore it
        """
        name = Path(name).name

        if not name.endswith(".py"):
            return

        # `SomeMod_123456789.py` of another account -> `SomeMod_<our id>.py`
        stem = re.sub(r"_\d+$", "", name[: -len(".py")])
        (loader.LOADED_MODULES_PATH / f"{stem}_{self.tg_id}.py").write_bytes(data)

    def _restore_links(self, raw: bytes) -> bool:
        """Restores the list of installed modules of `LoaderMod`"""
        links = ujson.loads(raw.decode())

        if not isinstance(links, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in links.items()
        ):
            raise RuntimeError("Invalid backup")

        loader_mod = self.lookup("LoaderMod")
        if not loader_mod:
            logger.warning("LoaderMod is not available, module links are skipped")
            return False

        loader_mod.set(
            "loaded_modules",
            {
                key: value
                for key, value in links.items()
                if utils.check_url(value)
            },
        )
        return True

    def _restore_mods_zip(self, raw: bytes) -> int:
        """Restores modules from a `mods.zip`-like archive"""
        archive = io.BytesIO(raw)
        archive.name = NESTED_NAME

        restored = 0

        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                if Path(name).name == LINKS_NAME:
                    with contextlib.suppress(Exception), zf.open(name, "r") as links:
                        self._restore_links(links.read())

                    continue

                if not name.endswith(".py"):
                    continue

                with zf.open(name, "r") as module:
                    self._write_module(name, module.read())

                restored += 1

        return restored

    def _restore(self, file: bytes) -> typing.Tuple[bool, int]:
        """
        Restores a backup of any known format:
          • Heroku — nested archive: `db.json` + `mods.zip`
          • Legacy / Hikka — flat archive: `db-backup.json` + `*.py`
          • a bare database dump (`.json`)
          • a bare `mods.zip` or a bare dump of module links
        Returns whether the database was restored and the amount of modules
        """
        if not zipfile.is_zipfile(io.BytesIO(file)):
            # Not an archive at all — the only thing it can be is json
            try:
                if self._restore_links(file):
                    return False, 0
            except Exception:
                pass

            self._apply_db(self._load_db(file))
            return True, 0

        container = io.BytesIO(file)
        container.name = "backup.zip"

        db_restored = False
        modules = 0

        with zipfile.ZipFile(container) as zf:
            names = {Path(name).name: name for name in zf.namelist()}

            for db_name in DB_NAMES:
                if db_name not in names:
                    continue

                with zf.open(names[db_name], "r") as db_file:
                    self._apply_db(self._load_db(db_file.read()))

                db_restored = True
                break

            # Heroku keeps modules in a nested archive
            if NESTED_NAME in names:
                with zf.open(names[NESTED_NAME], "r") as nested:
                    modules += self._restore_mods_zip(nested.read())

            if LINKS_NAME in names:
                with contextlib.suppress(Exception), zf.open(
                    names[LINKS_NAME], "r"
                ) as links:
                    self._restore_links(links.read())

            # ...while Legacy and Hikka — right next to the database
            for name in zf.namelist():
                if Path(name).name in SERVICE_FILES or not name.endswith(".py"):
                    continue

                with zf.open(name, "r") as module:
                    self._write_module(name, module.read())

                modules += 1

        if not db_restored and not modules:
            raise RuntimeError("Nothing to restore")

        return db_restored, modules

    async def _set_backup_period(self, call: BotInlineCall, value: int):
        if not value:
            self.set("period", "disabled")
            await call.answer(
                self.strings["never"].format(self.get_prefix()),
                show_alert=True,
            )
            await call.delete()
            return

        self.set("period", value * 60 * 60)
        self.set("last_backup", round(time.time()))

        await call.answer(
            self.strings["saved"].format(self.get_prefix()),
            show_alert=True,
        )
        await call.delete()

    @loader.command()
    async def set_backup_period(self, message):
        if (
            not (args := utils.get_args_raw(message))
            or not args.isdigit()
            or int(args) not in range(200)
        ):
            await utils.answer(message, self.strings["invalid_args"])
            return

        if not int(args):
            self.set("period", "disabled")
            await utils.answer(
                message,
                self.strings["never"].format(self.get_prefix(message.sender_id)),
            )
            return

        period = int(args) * 60 * 60
        self.set("period", period)
        self.set("last_backup", round(time.time()))
        await utils.answer(
            message, self.strings["saved"].format(self.get_prefix(message.sender_id))
        )

    @loader.loop(interval=1, autostart=True)
    async def handler(self):
        try:
            if self.get("period") == "disabled":
                raise loader.StopLoop

            if not self.get("period"):
                await asyncio.sleep(3)
                return

            if not self.get("last_backup"):
                self.set("last_backup", round(time.time()))
                await asyncio.sleep(self.get("period"))
                return

            await asyncio.sleep(
                self.get("last_backup") + self.get("period") - time.time()
            )

            outfile = self._create_backup()

            await self.inline.bot.send_document(
                int(f"-100{self._content_channel_id}"),
                BufferedInputFile(outfile.getvalue(), outfile.name),
                caption=self.strings["backup_caption"].format(
                    prefix=self.get_prefix(),
                ),
                reply_markup=self.inline.generate_markup(
                    [
                        [
                            {
                                "text": self.strings["restore_this"],
                                "data": "legacy/backup/restore/confirm",
                            },
                        ],
                    ],
                ),
                message_thread_id=self._backup_topic.id,
            )

            self.set("last_backup", round(time.time()))
        except loader.StopLoop:
            raise
        except Exception:
            logger.exception("LegacyBackup failed")
            await asyncio.sleep(60)

    @loader.callback_handler()
    async def restore_inl(self, call: BotInlineCall):
        if not call.data.startswith("legacy/backup/restore"):
            return

        if call.data == "legacy/backup/restore/confirm":
            await utils.answer(
                call,
                self.strings["confirm"],
                reply_markup=[
                    {
                        "text": self.strings["_btn_yes"],
                        "data": "legacy/backup/restore",
                    },
                    {
                        "text": self.strings["_btn_no"],
                        "data": "legacy/backup/restore/cancel",
                    },
                ],
            )
            return

        if call.data == "legacy/backup/restore/cancel":
            await utils.answer(
                call,
                self.strings["backup_caption"].format(
                    prefix=self.get_prefix(),
                ),
                reply_markup=[
                    {
                        "text": self.strings["restore_this"],
                        "data": "legacy/backup/restore/confirm",
                    },
                ],
            )
            return

        file = await (
            await self._client.get_messages(
                self._content_channel_id, ids=call.message.message_id
            )
        ).download_media(bytes)

        try:
            self._restore(file)
        except Exception:
            logger.exception("Unable to restore backup")
            await call.answer(self.strings["invalid_backup"], show_alert=True)
            return

        await call.answer(self.strings["backup_restored"], show_alert=True)
        await self.invoke("restart", "-f", peer=self.inline.bot_id)

    @loader.command()
    async def backup(self, message):
        outfile = self._create_backup()

        backup_msg = await self.inline.bot.send_document(
            int(f"-100{self._content_channel_id}"),
            BufferedInputFile(outfile.getvalue(), outfile.name),
            caption=self.strings["backup_caption"].format(
                prefix=self.get_prefix(message.sender_id),
            ),
            reply_markup=self.inline.generate_markup(
                [
                    [
                        {
                            "text": self.strings["restore_this"],
                            "data": "legacy/backup/restore/confirm",
                        },
                    ],
                ],
            ),
            message_thread_id=self._backup_topic.id,
        )

        await utils.answer(
            message,
            self.strings["backup_sent"].format(
                f"https://t.me/c/{self._content_channel_id}/{self._backup_topic.id}/{backup_msg.message_id}"
            ),
        )

    @loader.command()
    async def restore(self, message):
        if not (reply := await message.get_reply_message()) or not reply.media:
            await utils.answer(message, self.strings["reply_to_file"])
            return

        logger.info("📚 Trying to restore backup")

        message = await utils.answer(message, self.strings["restoring"])

        file = await reply.download_media(bytes)

        try:
            db_restored, modules = self._restore(file)
        except Exception:
            logger.exception("Unable to restore backup")
            await utils.answer(message, self.strings["invalid_backup"])
            return

        logger.info(
            "Backup restored (database: %s, modules: %s)",
            db_restored,
            modules,
        )

        await utils.answer(message, self.strings["backup_restored"])
        await self.invoke("restart", "-f", peer=message.peer_id)
