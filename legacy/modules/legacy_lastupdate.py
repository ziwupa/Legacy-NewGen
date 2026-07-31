# ©️ ziwupa, 2026
# This file is a part of Legacy NewGen Userbot
# 🌐 https://github.com/ziwupa/Legacy-NewGen
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import datetime
import logging
import typing

import git

from .. import loader, utils, version
from ..inline.types import InlineCall
from ..types import Message

logger = logging.getLogger(__name__)

REPO = "https://github.com/ziwupa/Legacy-NewGen"


@loader.tds
class LegacyLastUpdateMod(loader.Module):
    """Tells when the branch you are running was last updated"""

    strings = {
        "name": "LegacyLastUpdate",
        "checking": (
            "<emoji document_id=5328274090262275771>🕗</emoji> <b>Reading the"
            " branch...</b>"
        ),
        "result": (
            "<emoji document_id=5172453033245672405>🌱</emoji> <b>Branch:</b>"
            " <code>{branch}</code>\n\n"
            "<emoji document_id=5123230779593196220>⏰</emoji> <b>Last update:</b>"
            " {when}\n<b>Date:</b> <i>{date}</i>\n<b>Commit:</b> {commit}"
            " <i>{summary}</i>\n<b>Author:</b> <i>{author}</i>\n\n{status}"
        ),
        "up_to_date": (
            "<emoji document_id=5408909562919007848>✅</emoji> <b>This is the newest"
            " commit of the branch</b>"
        ),
        "behind": (
            "<emoji document_id=5292226786229236118>🔄</emoji> <b>{count} update(s)"
            " waiting</b>\n<b>Newest one:</b> {when} <i>({date})</i>\n\n{commits}\n\n"
            "<i>Pull them with</i> <code>{prefix}update</code>"
        ),
        "more": "<i>...and {} more</i>",
        "no_remote": (
            "<emoji document_id=5220053623211305785>❓</emoji> <b>Unable to read"
            " <code>origin/{}</code>, so there is nothing to compare with</b>"
        ),
        "stale": (
            "\n\n<i>origin could not be reached, so this comparison may be out of"
            " date</i>"
        ),
        "error": (
            "<emoji document_id=5210952531676504517>🚫</emoji> <b>Unable to read the"
            " repository:</b> <code>{}</code>"
        ),
        "btn_refresh": "🔄 Refresh",
        "btn_commit": "🌙 Commit",
        "btn_close": "🚫 Close",
        "just_now": "<b>just now</b>",
        "ago": "<b>{} ago</b>",
        "unit_d": "{} d",
        "unit_h": "{} h",
        "unit_m": "{} min",
        "unit_s": "{} s",
        "_cfg_doc_fetch": (
            "Fetch the remote before comparing. Turn this off if the machine has no"
            " network access"
        ),
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "fetch",
                True,
                doc=lambda: self.strings("_cfg_doc_fetch"),
                validator=loader.validators.Boolean(),
            ),
        )

    # ======================================================================= #
    #  Reading the repository                                                #
    # ======================================================================= #

    @staticmethod
    def _describe(commit: git.Commit) -> dict:
        message = str(commit.message or "").strip()

        return {
            "hexsha": commit.hexsha,
            "summary": message.splitlines()[0] if message else "",
            "author": str(getattr(commit.author, "name", None) or commit.author),
            # `committed_datetime` carries the committer's own offset, so it is
            # normalised here and converted back for display
            "date": commit.committed_datetime.astimezone(datetime.timezone.utc),
        }

    def _collect(self) -> dict:
        """Everything the message shows, gathered in one blocking pass"""
        try:
            repo = git.Repo(search_parent_directories=True)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

        try:
            # `version.branch` is resolved once at import time and falls back to
            # a hardcoded name, so the branch is read from the repo itself here
            branch = repo.active_branch.name
        except Exception:
            branch = version.branch

        info = {"branch": branch, "fetched": False}

        if self.config["fetch"]:
            try:
                for remote in repo.remotes:
                    remote.fetch()

                info["fetched"] = True
            except Exception:
                logger.debug("Unable to fetch the remote", exc_info=True)

        try:
            info["local"] = self._describe(repo.head.commit)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

        try:
            info["remote"] = self._describe(
                next(repo.iter_commits(f"origin/{info['branch']}", max_count=1))
            )
            behind = repo.git.log(
                [f"HEAD..origin/{info['branch']}", "--oneline"]
            )
            info["behind"] = behind.splitlines() if behind else []
        except Exception:
            logger.debug("Unable to read origin/%s", info["branch"], exc_info=True)
            info["remote"] = None
            info["behind"] = None

        return info

    # ======================================================================= #
    #  Rendering                                                             #
    # ======================================================================= #

    def _ago(self, delta: datetime.timedelta) -> str:
        """`delta` as the two coarsest units which are not zero"""
        seconds = max(round(delta.total_seconds()), 0)
        parts = []

        for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
            value, seconds = divmod(seconds, size)

            if value:
                parts.append(self.strings(f"unit_{unit}").format(value))

            if len(parts) == 2:
                break

        return (
            self.strings("ago").format(" ".join(parts))
            if parts
            else self.strings("just_now")
        )

    @staticmethod
    def _absolute(moment: datetime.datetime) -> str:
        return moment.astimezone().strftime("%d.%m.%Y %H:%M:%S %Z").strip()

    @staticmethod
    def _link(hexsha: str) -> str:
        return f'<a href="{REPO}/commit/{hexsha}">#{hexsha[:7]}</a>'

    def _status(self, info: dict) -> str:
        if info["behind"] is None:
            return self.strings("no_remote").format(
                utils.escape_html(info["branch"])
            )

        if not info["behind"]:
            return self.strings("up_to_date")

        commits = "\n".join(
            "<b>{}</b>: <i>{}</i>".format(
                line.split(maxsplit=1)[0],
                utils.escape_html(
                    line.split(maxsplit=1)[1] if " " in line.strip() else ""
                ),
            )
            for line in info["behind"][:10]
        )

        if len(info["behind"]) > 10:
            commits += "\n" + self.strings("more").format(len(info["behind"]) - 10)

        remote = info["remote"]
        now = datetime.datetime.now(datetime.timezone.utc)

        return self.strings("behind").format(
            count=len(info["behind"]),
            when=self._ago(now - remote["date"]) if remote else "",
            date=self._absolute(remote["date"]) if remote else "",
            commits=commits,
            prefix=utils.escape_html(self.get_prefix()),
        )

    def _render(self, info: dict) -> str:
        if error := info.get("error"):
            return self.strings("error").format(utils.escape_html(error))

        local = info["local"]
        now = datetime.datetime.now(datetime.timezone.utc)

        text = self.strings("result").format(
            branch=utils.escape_html(info["branch"]),
            when=self._ago(now - local["date"]),
            date=self._absolute(local["date"]),
            commit=self._link(local["hexsha"]),
            summary=utils.escape_html(local["summary"]),
            author=utils.escape_html(local["author"]),
            status=self._status(info),
        )

        if self.config["fetch"] and not info["fetched"]:
            text += self.strings("stale")

        return text

    def _markup(self, info: dict) -> typing.List[typing.List[dict]]:
        row = [
            {"text": self.strings("btn_refresh"), "callback": self._refresh},
            {"text": self.strings("btn_close"), "action": "close"},
        ]

        if local := info.get("local"):
            row.insert(
                1,
                {
                    "text": self.strings("btn_commit"),
                    "url": f"{REPO}/commit/{local['hexsha']}",
                },
            )

        return [row]

    # ======================================================================= #
    #  Entry points                                                          #
    # ======================================================================= #

    async def _refresh(self, call: InlineCall):
        # An edit which omits `reply_markup` drops the buttons, so the progress
        # state keeps a close button and nothing that can be pressed twice
        await call.edit(
            self.strings("checking"),
            reply_markup=[
                [{"text": self.strings("btn_close"), "action": "close"}],
            ],
        )

        info = await utils.run_sync(self._collect)

        await call.edit(self._render(info), reply_markup=self._markup(info))

    @loader.command()
    async def lastupdate(self, message: Message):
        """Shows when the branch you are running was last updated"""
        message = await utils.answer(message, self.strings("checking"))

        info = await utils.run_sync(self._collect)
        text = self._render(info)

        if not await self.inline.form(
            text,
            message=message,
            reply_markup=self._markup(info),
        ):
            await utils.answer(message, text)
