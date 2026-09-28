# GitHub repository notifications for Legacy NewGen ( polling a'la
# vsecoder/github-notifi-bot, but userbot-native: no webhooks needed )
#
# Configure a personal access token, add repos, point it at a chat with
# .sendth — and get commit / issue / PR / release / CI notifications.

import asyncio
import contextlib
import logging
import re
import time
import typing

import requests

from .. import loader, utils
from ..types import Message

logger = logging.getLogger(__name__)

_API = "https://api.github.com"

# How many items to pull per repo per cycle (seen-state caps traffic anyway).
_PAGE = 10

_CI_ICONS = {
    "success": "✅",
    "failure": "❌",
    "cancelled": "🚫",
    "skipped": "⏭️",
    "timed_out": "⌛",
    "action_required": "❗",
    "neutral": "⚪",
}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _normalize_repo(raw: str) -> typing.Optional[str]:
    """owner/repo out of a short name, URL or SSH remote. None if garbage."""
    text = (raw or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if "://" in text:
        # Full URLs are only accepted for github.com.
        host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", lowered).split("/", 1)[0]
        host = host.split("@")[-1].split(":")[0]
        if host not in ("github.com", "www.github.com"):
            return None
        text = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]+/", "", text)
    elif "@" in text.split("/")[0] and ":" in text:
        # git@github.com:owner/repo(.git) — host must be github as well.
        pre, _, rest = text.partition(":")
        if "github.com" not in pre.lower():
            return None
        text = rest
    else:
        # Schemeless host prefix: github.com/owner/repo.
        text = re.sub(r"^(?:www\.)?github\.com/", "", text, flags=re.I)
    text = text.strip().strip("/")
    if text.lower().endswith(".git"):
        text = text[: -len(".git")].strip("/")
    parts = [part for part in text.split("/") if part]
    if len(parts) != 2:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", "/".join(parts)):
        return None
    return "/".join(parts).lower()


@loader.tds
class GitNotifyMod(loader.Module):
    strings = {"name": "GitNotify"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "git_token",
                "",
                lambda: self.strings("_cfg_doc_token"),
            ),
            loader.ConfigValue(
                "poll_interval",
                5,
                lambda: self.strings("_cfg_doc_interval"),
                validator=loader.validators.Integer(minimum=1, maximum=1440),
            ),
            loader.ConfigValue(
                "track_commits",
                True,
                lambda: self.strings("_cfg_doc_commits"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "track_issues",
                True,
                lambda: self.strings("_cfg_doc_issues"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "track_releases",
                True,
                lambda: self.strings("_cfg_doc_releases"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "track_ci",
                True,
                lambda: self.strings("_cfg_doc_ci"),
                validator=loader.validators.Boolean(),
            ),
        )
        self._last_poll = 0.0
        self._token_warned = False
        self._unavailable: typing.Set[str] = set()

    # -- state helpers (db-backed, per account via self.get/set) --

    def _repos(self) -> dict:
        return self.get("repos", {})

    def _save_repos(self, repos: dict):
        self.set("repos", repos)

    def _seen(self) -> dict:
        return self.get("seen", {})

    def _save_seen(self, seen: dict):
        self.set("seen", seen)

    def _targets(self) -> list:
        return self.get("targets", [])

    # -- github api --

    async def _api(self, path: str) -> typing.Tuple[int, typing.Any]:
        token = self.config["git_token"] or ""

        def _call():
            try:
                response = requests.get(
                    f"{_API}{path}",
                    headers=_headers(token),
                    timeout=20,
                )
                try:
                    return response.status_code, response.json()
                except Exception:
                    return response.status_code, None
            except Exception as e:
                return 0, e

        return await utils.run_sync(_call)

    async def _send_all(self, text: str):
        for target in self._targets():
            try:
                await self._client.send_message(
                    target["chat"],
                    text,
                    parse_mode="HTML",
                    link_preview=False,
                    reply_to=target.get("topic"),
                )
            except Exception:
                logger.exception(
                    "GitNotify: unable to deliver to %s", target.get("chat")
                )

    def _fmt_commit(self, repo: str, commit: dict) -> str:
        sha = commit.get("sha", "")[:7]
        url = commit.get("html_url", f"https://github.com/{repo}/commit/{sha}")
        info = commit.get("commit", {}) or {}
        author = ((info.get("author") or {}).get("name")) or (
            (commit.get("author") or {}).get("login", "?")
        )
        title = (info.get("message") or "").splitlines()[0][:120] if info.get(
            "message"
        ) else ""
        return (
            f'🔨 <a href="{url}"><code>{utils.escape_html(sha)}</code></a>'
            f" {utils.escape_html(title)}\n"
            f"👤 {utils.escape_html(str(author))}"
        )

    # -- commands --

    @loader.command()
    async def ghtoken(self, message: Message):
        """<token> - Save GitHub personal access token"""
        token = utils.get_args_raw(message).strip()
        if not token:
            await utils.answer(message, self.strings("token_empty"))
            return
        self.config["git_token"] = token
        self._token_warned = False
        status, data = await self._api("/user")
        if status == 200 and isinstance(data, dict) and data.get("login"):
            await utils.answer(
                message,
                self.strings("token_ok").format(utils.escape_html(data["login"])),
            )
        else:
            await utils.answer(message, self.strings("token_bad"))
        with contextlib.suppress(Exception):
            await message.delete()

    @loader.command()
    async def ghadd(self, message: Message):
        """<owner/repo | URL> [branch] - Track repository (baseline, no spam)"""
        args = utils.get_args(message)
        if not args:
            await utils.answer(message, self.strings("add_usage"))
            return
        full = _normalize_repo(args[0])
        if not full:
            await utils.answer(message, self.strings("add_usage"))
            return
        branch = args[1].strip() if len(args) > 1 else ""
        repos = self._repos()
        if full in repos:
            await utils.answer(message, self.strings("already").format(full))
            return
        status, _ = await self._api(f"/repos/{full}")
        if status == 404:
            await utils.answer(message, self.strings("no_repo").format(full))
            return
        if status == 401:
            await utils.answer(message, self.strings("token_bad"))
            return
        if status != 200:
            await utils.answer(message, self.strings("gh_error").format(status))
            return
        repos[full] = {"branch": branch}
        self._save_repos(repos)
        await self._baseline(full, repos[full])
        await utils.answer(
            message, self.strings("added").format(full, branch or "default")
        )

    @loader.command()
    async def ghdel(self, message: Message):
        """<owner/repo | URL> - Stop tracking repository"""
        args = utils.get_args(message)
        if not args:
            await utils.answer(message, self.strings("del_usage"))
            return
        full = _normalize_repo(args[0])
        if not full:
            await utils.answer(message, self.strings("del_usage"))
            return
        repos = self._repos()
        if full not in repos:
            await utils.answer(message, self.strings("not_tracked").format(full))
            return
        del repos[full]
        self._save_repos(repos)
        seen = self._seen()
        seen.pop(full, None)
        self._save_seen(seen)
        await utils.answer(message, self.strings("deleted").format(full))

    @loader.command()
    async def ghlist(self, message: Message):
        """- List tracked repositories"""
        repos = self._repos()
        if not repos:
            await utils.answer(message, self.strings("empty"))
            return
        lines = [
            f"• <code>{name}</code> ({info.get('branch') or 'default'})"
            for name, info in sorted(repos.items())
        ]
        await utils.answer(message, self.strings("listed").format("\n".join(lines)))

    @loader.command()
    async def sendth(self, message: Message):
        """[clear] - Send GitHub notifications here (works in forum topics)"""
        args = utils.get_args(message)
        if args and args[0].lower() == "clear":
            self.set("targets", [])
            await utils.answer(message, self.strings("targets_cleared"))
            return
        try:
            entity = await message.get_chat()
            chat = utils.get_entity_id(entity)
        except Exception:
            await utils.answer(message, self.strings("target_fail"))
            return
        topic = None
        with contextlib.suppress(Exception):
            topic = utils.get_topic(message)
        targets = self._targets()
        entry = {"chat": chat, "topic": topic}
        if entry not in targets:
            targets.append(entry)
            self.set("targets", targets)
        await utils.answer(
            message,
            self.strings("target_set").format(
                utils.escape_html(getattr(entity, "title", None) or str(chat)),
                f" (topic {topic})" if topic else "",
            ),
        )

    # -- polling --

    async def _baseline(self, full: str, info: dict):
        """Remember current state without notifying, so .ghadd never spams."""
        seen = self._seen()
        state = seen.get(full, {})
        branch = info.get("branch") or ""
        path = f"/repos/{full}/commits?per_page=1"
        if branch:
            path += f"&sha={branch}"
        status, data = await self._api(path)
        if status == 200 and isinstance(data, list) and data:
            state["sha"] = data[0].get("sha")
        status, data = await self._api(
            f"/repos/{full}/issues?state=all&sort=created&direction=desc&per_page=20"
        )
        if status == 200 and isinstance(data, list):
            state["items"] = {
                str(item.get("number")): {
                    "state": item.get("state"),
                    "pr": "pull_request" in item,
                }
                for item in data
                if item.get("number") is not None
            }
            nums = [i.get("number") for i in data if i.get("number") is not None]
            state["max_no"] = max(nums) if nums else 0
        status, data = await self._api(f"/repos/{full}/releases?per_page=1")
        if status == 200 and isinstance(data, list) and data:
            state["release"] = data[0].get("id", 0)
        status, data = await self._api(f"/repos/{full}/actions/runs?per_page=5")
        if status == 200 and isinstance(data, dict):
            state["runs"] = {
                str(run.get("id")): run.get("status")
                for run in (data.get("workflow_runs") or [])
                if run.get("id") is not None
            }
        seen[full] = state
        self._save_seen(seen)

    @loader.loop(interval=60, autostart=True)
    async def poll_loop(self):
        token = (self.config["git_token"] or "").strip()
        if not token:
            return
        try:
            interval = int(self.config["poll_interval"] or 5)
        except Exception:
            interval = 5
        if time.monotonic() - self._last_poll < interval * 60:
            return
        self._last_poll = time.monotonic()
        repos = self._repos()
        if not repos or not self._targets():
            return
        for full, info in list(repos.items()):
            try:
                await self._poll_repo(full, info)
            except Exception:
                logger.exception("GitNotify: poll failed for %s", full)

    async def _poll_repo(self, full: str, info: dict):
        seen = self._seen()
        state = seen.get(full, {})
        branch = info.get("branch") or ""
        changed = False

        if self.config["track_commits"]:
            path = f"/repos/{full}/commits?per_page={_PAGE}"
            if branch:
                path += f"&sha={branch}"
            status, data = await self._api(path)
            if status == 200 and isinstance(data, list) and data:
                fresh = []
                for commit in data:
                    if commit.get("sha") == state.get("sha"):
                        break
                    fresh.append(commit)
                if fresh and state.get("sha"):
                    lines = [self._fmt_commit(full, c) for c in reversed(fresh[:10])]
                    extra = (
                        self.strings("more").format(len(fresh) - 10)
                        if len(fresh) > 10
                        else ""
                    )
                    await self._send_all(
                        self.strings("push").format(full, branch or "default", "\n".join(lines))
                        + extra
                    )
                if not state.get("sha"):
                    # No baseline yet (added before baselines existed).
                    pass
                state["sha"] = data[0].get("sha", state.get("sha"))
                changed = True

        if self.config["track_issues"]:
            status, data = await self._api(
                f"/repos/{full}/issues?state=all&sort=created&direction=desc&per_page=20"
            )
            if status == 200 and isinstance(data, list):
                known = state.get("items", {})
                max_no = state.get("max_no", 0)
                for item in reversed(data):
                    num = item.get("number")
                    if num is None:
                        continue
                    is_pr = "pull_request" in item
                    old = known.get(str(num))
                    if num > max_no and max_no:
                        await self._send_all(self._fmt_issue(full, item, is_pr, True))
                    elif old and old.get("state") == "open" and item.get("state") != "open":
                        await self._send_all(self._fmt_issue(full, item, is_pr, False))
                    known[str(num)] = {"state": item.get("state"), "pr": is_pr}
                nums = [i.get("number") for i in data if i.get("number") is not None]
                if nums:
                    max_no = max([max_no] + nums)
                state["items"] = dict(list(known.items())[-40:])
                state["max_no"] = max_no
                changed = True

        if self.config["track_releases"]:
            status, data = await self._api(f"/repos/{full}/releases?per_page=5")
            if status == 200 and isinstance(data, list):
                last = state.get("release", 0)
                for rel in reversed(data):
                    rid = rel.get("id", 0)
                    if rid and rid > last and last:
                        await self._send_all(self._fmt_release(full, rel))
                if data:
                    state["release"] = max(
                        [last] + [r.get("id", 0) for r in data]
                    )
                    changed = True

        if self.config["track_ci"]:
            status, data = await self._api(
                f"/repos/{full}/actions/runs?per_page=5"
            )
            if status == 200 and isinstance(data, dict):
                runs = {
                    str(run.get("id")): run.get("status")
                    for run in (data.get("workflow_runs") or [])
                    if run.get("id") is not None
                }
                known_runs = state.get("runs", {})
                lookup = {
                    str(run.get("id")): run
                    for run in (data.get("workflow_runs") or [])
                    if run.get("id") is not None
                }
                for rid, run in lookup.items():
                    if (
                        run.get("status") == "completed"
                        and known_runs.get(rid) != "completed"
                        and known_runs
                    ):
                        await self._send_all(self._fmt_run(full, run))
                state["runs"] = runs
                changed = True

        if changed:
            seen[full] = state
            self._save_seen(seen)

    def _fmt_issue(self, repo: str, item: dict, is_pr: bool, opened: bool) -> str:
        num = item.get("number")
        url = item.get("html_url", f"https://github.com/{repo}/issues/{num}")
        title = (item.get("title") or "")[:150]
        user = ((item.get("user") or {}).get("login")) or "?"
        kind = "PR" if is_pr else "issue"
        if opened:
            head = self.strings("opened").format(kind, url, num)
        elif is_pr and bool(item.get("pull_request", {}).get("merged_at")):
            head = self.strings("merged").format(url, num)
        else:
            head = self.strings("closed").format(kind, url, num)
        return (
            f"{head}\n<b>{utils.escape_html(title)}</b>\n"
            f"👤 {utils.escape_html(str(user))} | 📦 <code>{repo}</code>"
        )

    def _fmt_release(self, repo: str, rel: dict) -> str:
        tag = rel.get("tag_name", "?")
        name = rel.get("name") or tag
        url = rel.get("html_url", f"https://github.com/{repo}/releases")
        body = (rel.get("body") or "")[:300]
        text = self.strings("release").format(repo, url, utils.escape_html(name), tag)
        if body:
            text += f"\n<blockquote>{utils.escape_html(body)}</blockquote>"
        if rel.get("prerelease"):
            text += self.strings("pre")
        return text

    def _fmt_run(self, repo: str, run: dict) -> str:
        name = run.get("name") or run.get("displayTitle") or "workflow"
        conclusion = run.get("conclusion", "?")
        url = run.get("html_url", f"https://github.com/{repo}/actions")
        branch = (run.get("head_branch") or "")
        return self.strings("ci").format(
            _CI_ICONS.get(conclusion, "⚪"),
            utils.escape_html(str(name)),
            utils.escape_html(str(conclusion)),
            utils.escape_html(branch),
            url,
            repo,
        )

