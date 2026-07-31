"""Responsible for web init and mandatory ops"""

#    Friendly Telegram (telegram userbot)
#    Copyright (C) 2018-2021 The Authors

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.

#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

# ©️ Dan Gazizullin, 2021-2023
# This file is a part of Hikka Userbot
# 🌐 https://github.com/hikariatama/Hikka
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
import contextlib
import inspect
import logging
import socket
import os
import requests

import aiohttp_jinja2
import jinja2
from aiohttp import web

from .. import utils
from ..database import Database
from ..loader import Modules
from ..tl_cache import CustomTelegramClient
from . import proxypass, root

logger = logging.getLogger(__name__)


class Web(root.Web):
    def __init__(self, **kwargs):
        self.runner = None
        self.url = None
        self.port = None
        self.running = asyncio.Event()
        self.ready = asyncio.Event()
        self.client_data = {}
        self.app = web.Application()
        self.proxypasser = proxypass.ProxyPasser()
        aiohttp_jinja2.setup(
            self.app,
            filters={"getdoc": inspect.getdoc, "ascii": ascii},
            loader=jinja2.FileSystemLoader("web-resources"),
        )
        self.app["static_root_url"] = "/static"

        super().__init__(**kwargs)
        self.app.router.add_get("/favicon.ico", self.favicon)
        self.app.router.add_static("/static/", "web-resources/static")

    async def start_if_ready(
        self,
        total_count: int,
        port: int,
        proxy_pass: bool = False,
    ):
        if total_count <= len(self.client_data):
            if not self.running.is_set():
                await self.start(port, proxy_pass=proxy_pass)

            self.ready.set()

    async def get_url(self, proxy_pass: bool) -> str:
        url = None

        if proxy_pass:
            with contextlib.suppress(Exception):
                url = await asyncio.wait_for(
                    self.proxypasser.get_url(self.port),
                    timeout=10,
                )

        if not url:
            platform = utils.get_named_platform()

            if any(keyword in platform for keyword in ["WSL", "UserLand"]):
                ip = "127.0.0.1"
            else:
                try:
                    original_getaddrinfo = socket.getaddrinfo

                    def getaddrinfo_ipv4_only(
                        host, port, family=0, type=0, proto=0, flags=0
                    ):
                        return original_getaddrinfo(
                            host, port, socket.AF_INET, type, proto, flags
                        )

                    socket.getaddrinfo = getaddrinfo_ipv4_only

                    resp = requests.get("http://ifconfig.me/ip", timeout=5)
                    resp.raise_for_status()
                    ip = resp.text.strip()
                except (requests.exceptions.RequestException, ValueError) as e:
                    ip = "127.0.0.1"
                finally:
                    socket.getaddrinfo = original_getaddrinfo

            url = f"http://{ip}:{self.port}"

        self.url = url
        return url

    async def start(self, port: int, proxy_pass: bool = False):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.port = os.environ.get("PORT", port)
        site = web.TCPSite(self.runner, None, self.port)
        await site.start()

        await self.get_url(proxy_pass)

        self.running.set()

    async def stop(self):
        await self.runner.shutdown()
        await self.runner.cleanup()
        self.running.clear()
        self.ready.clear()

    async def add_loader(
        self,
        client: CustomTelegramClient,
        loader: Modules,
        db: Database,
    ):
        self.client_data[client.tg_id] = (loader, client, db)

    @staticmethod
    async def favicon(_):
        return web.Response(
            status=301,
            headers={"Location": "https://i.imgur.com/IRAiWBo.jpeg"},
        )
