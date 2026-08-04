# ©️ Dan Gazizullin, 2021-2023
# This file is a part of Hikka Userbot
# 🌐 https://github.com/hikariatama/Hikka
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import time
import typing

import git
from git import GitCommandError, Repo
from legacytl.extensions.html import CUSTOM_EMOJIS
from legacytl.tl.functions.messages import (
    GetDialogFiltersRequest,
    UpdateDialogFilterRequest,
)
from legacytl.tl.types import (
    DialogFilter,
    Message,
    TextWithEntities,
    DialogFilterDefault,
)

from .. import loader, main, utils, version
from .._internal import restart
from ..inline.types import InlineCall

logger = logging.getLogger(__name__)


@loader.tds
class UpdaterMod(loader.Module):
    strings = {"name": "Updater"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "GIT_ORIGIN_URL",
                "https://github.com/ziwupa/Legacy-NewGen",
                lambda: self.strings("origin_cfg_doc"),
                validator=loader.validators.Link(),
            )
        )

    @loader.command()
    async def restart(self, message: Message):
        args = utils.get_args(message)
        try:
            if (
                "-f" in args
                or not self.inline.init_complete
                or not await self.inline.form(
                    message=message,
                    text=self.strings["restart_confirm"],
                    reply_markup=[
                        {
                            "text": self.strings("btn_restart"),
                            "callback": self.inline_restart,
                        },
                        {"text": self.strings("cancel"), "action": "close"},
                    ],
                )
            ):
                raise
        except Exception:
            await self.restart_common(message)

    async def inline_restart(self, call: InlineCall):
        await self.restart_common(call)

    @staticmethod
    def _dump_restart_target(msg_obj) -> typing.Union[str, dict]:
        """Return a JSON-serializable handle for the message to edit after the
        restart. Inline messages carry a TL InputBotInlineMessageID(64), which is
        not JSON-serializable, so persist its (all-int) fields as a tagged dict —
        the DB now rejects any non-JSON value."""
        imi = getattr(msg_obj, "inline_message_id", None)
        if imi is not None:
            if isinstance(imi, str):
                return imi
            return {
                "_tl": type(imi).__name__,
                "dc_id": getattr(imi, "dc_id", None),
                "owner_id": getattr(imi, "owner_id", None),
                "id": getattr(imi, "id", None),
                "access_hash": getattr(imi, "access_hash", None),
            }
        return f"{utils.get_chat_id(msg_obj)}:{msg_obj.id}"

    @staticmethod
    def _load_restart_target(ms):
        """Rebuild what _dump_restart_target stored: a tagged dict becomes the TL
        inline id again; anything else (a "chat:msg" string, a bot-api string, or
        None) is returned unchanged."""
        if not isinstance(ms, dict) or not ms.get("_tl"):
            return ms
        from legacytl.tl import types as _types

        if ms["_tl"] == "InputBotInlineMessageID":
            return _types.InputBotInlineMessageID(
                dc_id=ms["dc_id"],
                id=ms["id"],
                access_hash=ms["access_hash"],
            )
        return _types.InputBotInlineMessageID64(
            dc_id=ms["dc_id"],
            owner_id=ms["owner_id"],
            id=ms["id"],
            access_hash=ms["access_hash"],
        )

    async def process_restart_message(self, msg_obj: typing.Union[InlineCall, Message]):
        self.set("selfupdatemsg", self._dump_restart_target(msg_obj))

    async def restart_common(
        self,
        msg_obj: typing.Union[InlineCall, Message],
    ):
        if (
            hasattr(msg_obj, "form")
            and isinstance(msg_obj.form, dict)
            and "uid" in msg_obj.form
            and msg_obj.form["uid"] in self.inline._units
            and "message" in self.inline._units[msg_obj.form["uid"]]
        ):
            message = self.inline._units[msg_obj.form["uid"]]["message"]
        else:
            message = msg_obj

        msg_obj = await utils.answer(
            msg_obj,
            self.strings("restarting_caption").format(
                utils.get_platform_emoji()
                if self._client.legacy_me.premium
                and CUSTOM_EMOJIS
                and isinstance(msg_obj, Message)
                else "Legacy"
            ),
        )

        await self.process_restart_message(msg_obj)

        self.set("restart_ts", time.time())

        with contextlib.suppress(Exception):
            await main.legacy.web.stop()

        handler = logging.getLogger().handlers[0]
        handler.setLevel(logging.CRITICAL)

        for client in self.allclients:
            # Terminate main loop of all running clients
            # Won't work if not all clients are ready
            if client is not message.client:
                await client.disconnect()

        await message.client.disconnect()
        restart()

    async def download_common(self):
        try:
            repo = Repo(os.path.dirname(utils.get_base_dir()))
            origin = repo.remote("origin")
            r = origin.pull(rebase=True)
            new_commit = repo.head.commit
            for info in r:
                if info.old_commit:
                    for d in new_commit.diff(info.old_commit):
                        if d.b_path == "requirements.txt":
                            return True
            return False
        except git.exc.InvalidGitRepositoryError:
            repo = Repo.init(os.path.dirname(utils.get_base_dir()))
            origin = repo.create_remote("origin", self.config["GIT_ORIGIN_URL"])
            origin.fetch()
            repo.create_head(version.branch, origin.refs[version.branch])
            repo.heads[version.branch].set_tracking_branch(
                origin.refs[version.branch]
            )
            repo.heads[version.branch].checkout(True)
            return False

    @staticmethod
    def req_common():
        # Now we have downloaded new code, install requirements
        logger.debug("Installing new requirements...")
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    os.path.join(
                        os.path.dirname(utils.get_base_dir()),
                        "requirements.txt",
                    ),
                    "--user",
                ],
                check=True,
            )
        except subprocess.CalledProcessError:
            logger.exception("Req install failed")

    @staticmethod
    def _vcs_requirements() -> typing.List[str]:
        """Requirement lines that point at a VCS (git+...). These are branch refs
        that pip treats as already-satisfied once installed, so a plain .update
        would never refresh them — we have to force them explicitly."""
        path = os.path.join(
            os.path.dirname(utils.get_base_dir()),
            "requirements.txt",
        )
        try:
            with open(path, encoding="utf-8") as f:
                return [
                    line.strip()
                    for line in f
                    if "git+" in line and not line.lstrip().startswith("#")
                ]
        except OSError:
            return []

    @classmethod
    def refresh_core(cls):
        """Force-reinstall the git-based core libs (e.g. legacytl) so pushes to
        their branch reach the running bot through a normal .update, not just the
        userbot code. --no-deps keeps it to the core itself; --force-reinstall
        defeats pip's already-satisfied shortcut for branch refs. No --user, so
        it installs into the active (venv) environment where the core lives."""
        reqs = cls._vcs_requirements()
        if not reqs:
            return
        logger.debug("Refreshing core libs: %s", reqs)
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "--force-reinstall",
                    "--no-deps",
                    *reqs,
                ],
                check=True,
            )
        except subprocess.CalledProcessError:
            logger.exception("Core lib refresh failed")

    @loader.command()
    async def update(self, message: Message):
        try:
            args = utils.get_args(message)
            current = utils.get_git_hash()
            upcoming = next(
                git.Repo().iter_commits(f"origin/{version.branch}", max_count=1)
            ).hexsha
            if (
                "-f" in args
                or not self.inline.init_complete
                or not await self.inline.form(
                    message=message,
                    text=(
                        self.strings("update_confirm").format(
                            current, current[:8], upcoming, upcoming[:8]
                        )
                        if upcoming != current
                        else self.strings("no_update")
                    ),
                    reply_markup=[
                        {
                            "text": self.strings("btn_update"),
                            "callback": self.inline_update,
                        },
                        {"text": self.strings("cancel"), "action": "close"},
                    ],
                )
            ):
                raise
        except Exception:
            await self.inline_update(message)

    async def inline_update(
        self,
        msg_obj: typing.Union[InlineCall, Message],
        hard: bool = False,
    ):
        # We don't really care about asyncio at this point, as we are shutting down
        if hard:
            os.system(f"cd {utils.get_base_dir()} && cd .. && git reset --hard HEAD")

        try:
            with contextlib.suppress(Exception):
                msg_obj = await utils.answer(msg_obj, self.strings("downloading"))

            req_update = await self.download_common()

            with contextlib.suppress(Exception):
                msg_obj = await utils.answer(msg_obj, self.strings("installing"))

            if req_update:
                self.req_common()

            # A plain .update only pulls the userbot repo; the core TL lib is a
            # git branch ref that pip treats as already-satisfied, so it would
            # never refresh on its own. Force it every update so pushes to
            # legacytl@beta actually reach the running bot.
            self.refresh_core()

            await self.restart_common(msg_obj)
        except GitCommandError:
            if not hard:
                await self.inline_update(msg_obj, True)
                return

            logger.critical("Got update loop. Update manually via .terminal")

    @loader.command()
    async def source(self, message: Message):
        await utils.answer(
            message,
            self.strings("source").format(self.config["GIT_ORIGIN_URL"]),
        )

    def _get_recent_commits(self, count=3) -> typing.List:
        repo = Repo()

        commits = list(repo.iter_commits("HEAD", max_count=count))

        return commits

    def _rollback_to_commit(self, commit) -> bool:
        repo = Repo()

        try:
            repo.git.reset("--hard", commit)
            return True
        except Exception:
            return False

    async def _cb_rollback(self, call: InlineCall, args):
        res = self._rollback_to_commit(args)

        if res:
            await utils.answer(call, self.strings("rollback_ok"))
            await self.invoke("restart", "-f", peer=self.inline.bot.id)
        else:
            await utils.answer(call, self.strings("rollback_err"))

    @loader.command()
    async def rollback(self, message: Message):
        args = utils.get_args_raw(message)

        if not args:
            commits = self._get_recent_commits(4)
            commits.pop(0)

            await self.inline.form(
                text=self.strings("rollback_no_args"),
                message=message,
                reply_markup=[
                    [
                        {
                            "text": c.message.split("\n", 1)[0],
                            "callback": self._cb_rollback,
                            "args": [c.hexsha],
                        }
                    ]
                    for c in commits
                ]
                + [[{"text": self.strings("cancel"), "action": "close"}]],
            )
            return

        res = self._rollback_to_commit(args)

        if res:
            await utils.answer(message, self.strings("rollback_ok"))
            await self.invoke("restart", "-f", peer=message.peer_id)
        else:
            await utils.answer(message, self.strings("rollback_err"))

    async def client_ready(self):
        if self.get("selfupdatemsg") is not None:
            try:
                await self.update_complete()
            except Exception:
                logger.exception("Failed to complete update!")

        if self.get("do_not_create", False):
            return

        try:
            await self._add_folder()
        except Exception:
            logger.exception("Failed to add folder!")

        self.set("do_not_create", True)

    async def _add_folder(self):
        folders = await self._client(GetDialogFiltersRequest())

        for folder in folders.filters:
            if isinstance(folder, DialogFilterDefault):
                continue
            if folder.title.text == "legacy":
                return

        try:
            folder_id = (
                max(
                    (folder for folder in folders.filters if hasattr(folder, "id")),
                    key=lambda x: x.id,
                ).id
                + 1
            )
        except ValueError:
            folder_id = 2

        try:
            await self._client(
                UpdateDialogFilterRequest(
                    folder_id,
                    DialogFilter(
                        folder_id,
                        title=TextWithEntities(text="legacy", entities=[]),
                        pinned_peers=(
                            [
                                await self._client.get_input_entity(
                                    self._client.loader.inline.bot_id
                                )
                            ]
                            if self._client.loader.inline.init_complete
                            else []
                        ),
                        include_peers=[
                            await self._client.get_input_entity(dialog.entity)
                            async for dialog in self._client.iter_dialogs(
                                None,
                                archived=True,
                                ignore_migrated=False,
                            )
                            if dialog.name
                            in {
                                "legacy-logs",
                                "legacy-assets",
                                "legacy-backups",
                                "silent-tags",
                            }
                            and dialog.is_channel
                            and (
                                dialog.entity.participants_count == 1
                                or dialog.entity.participants_count == 2
                                and dialog.name in {"legacy-logs", "silent-tags"}
                            )
                            or (
                                self._client.loader.inline.init_complete
                                and dialog.entity.id
                                == self._client.loader.inline.bot_id
                            )
                            or dialog.entity.id in [2577311568]  # official legacy chat
                        ],
                        emoticon="⭐️",
                        exclude_peers=[],
                        contacts=False,
                        non_contacts=False,
                        groups=False,
                        broadcasts=False,
                        bots=False,
                        exclude_muted=False,
                        exclude_read=False,
                        exclude_archived=False,
                    ),
                )
            )
        except Exception as e:
            logger.critical(e)
            logger.critical(
                "Can't create Legacy folder. Possible reasons are:\n"
                "- User reached the limit of folders in Telegram\n"
                "- User got floodwait\n"
                "Ignoring error and adding folder addition to ignore list"
            )

    async def update_complete(self):
        logger.debug("Self update successful! Edit message")
        start = self.get("restart_ts")
        try:
            took = round(time.time() - start)
        except Exception:
            took = "n/a"

        msg = self.strings("success").format(utils.ascii_face(), took)
        ms = self._load_restart_target(self.get("selfupdatemsg"))

        if isinstance(ms, str) and ":" in ms:
            chat_id, message_id = ms.split(":")
            chat_id, message_id = int(chat_id), int(message_id)
            await self._client.edit_message(chat_id, message_id, msg)
            return

        await self.inline.bot.edit_message_text(
            inline_message_id=ms,
            text=self.inline.sanitise_text(msg),
        )

    async def full_restart_complete(self):
        start = self.get("restart_ts")

        try:
            took = round(time.time() - start)
        except Exception:
            took = "n/a"

        self.set("restart_ts", None)

        ms = self._load_restart_target(self.get("selfupdatemsg"))
        msg = self.strings("full_success").format(utils.ascii_face(), took)

        if ms is None:
            return

        self.set("selfupdatemsg", None)

        if isinstance(ms, str) and ":" in ms:
            chat_id, message_id = ms.split(":")
            chat_id, message_id = int(chat_id), int(message_id)
            await self._client.edit_message(chat_id, message_id, msg)
            await asyncio.sleep(60)
            await self._client.delete_messages(chat_id, message_id)
            return

        await self.inline.bot.edit_message_text(
            inline_message_id=ms,
            text=self.inline.sanitise_text(msg),
        )
