# ©️ Undefined & XDesai, 2025
# This file is a part of Legacy Userbot
# 🌐 https://github.com/ziwupa/Legacy-NewGen
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import contextlib
import logging
import typing

from legacytl.tl.types import (
    DataJSON,
    InputBotInlineMessageMediaInvoice,
    Invoice as TLInvoice,
    LabeledPrice,
    Message,
)

from .. import utils
from .types import InlineMessage, InlineUnit

logger = logging.getLogger(__name__)


class Invoice(InlineUnit):
    async def invoice(
        self,
        message: typing.Union[Message, int],
        title: str,
        description: str,
        id: str,
        payload: str,
        label: str,
        amount: int,
        currency: str,
        *,
        photo_url: typing.Optional[str] = None,
        max_tip_amount: typing.Optional[int] = None,
        suggested_tip_amounts: typing.Optional[typing.List[int]] = None,
    ) -> typing.Union[InlineMessage, bool]:
        if not isinstance(message, Message):
            logger.error(
                "Invalid type for `message`. Expected Message, got %s",
                type(message),
            )
            return False

        if not isinstance(title, str):
            logger.error("Invalid type for `title`. Expected str, got %s", type(title))
            return False

        if not isinstance(description, str):
            logger.error(
                "Invalid type for `description`. Expected str, got %s",
                type(description),
            )
            return False

        if not isinstance(currency, str):
            logger.error(
                "Invalid type for `currency`. Expected str, got %s", type(currency)
            )
            return False

        if not isinstance(amount, int):
            logger.error(
                "Invalid type for `amount`. Expected int, got %s",
                type(amount),
            )
            return False

        if not isinstance(label, str):
            logger.error(
                "Invalid type for `label`. Expected str, got %s",
                type(label),
            )
            return False

        unit_id = utils.rand(16)
        self._units[unit_id] = {
            "type": "invoice",
            "title": title,
            "id": id,
            "payload": payload,
            "currency": currency,
            "prices": {"label": label, "amount": amount},
            "description": description,
            "photo_url": photo_url,
            "max_tip_amount": max_tip_amount,
            "suggested_tip_amounts": suggested_tip_amounts,
            "uid": unit_id,
        }
        return self._units[unit_id]

    async def _invoice_inline_handler(self, inline_query):
        unit = inline_query.query

        if unit not in self._units or self._units[unit]["type"] != "invoice":
            return

        form = self._units[unit]
        with contextlib.suppress(Exception):
            invoice_tl = TLInvoice(
                currency=form["currency"],
                prices=[
                    LabeledPrice(
                        label=form["prices"]["label"],
                        amount=form["prices"]["amount"],
                    )
                ],
                test=False,
                name_requested=False,
                phone_requested=False,
                email_requested=False,
                shipping_address_requested=False,
                flexible=False,
                phone_to_provider=False,
                email_to_provider=False,
            )

            msg = InputBotInlineMessageMediaInvoice(
                title=form["title"],
                description=form["description"],
                invoice=invoice_tl,
                payload=form["payload"].encode(),
                provider="",
                provider_data=DataJSON(data="{}"),
            )

            results = [
                await inline_query.builder.article(
                    title=form["title"],
                    description=form["description"],
                    text="🌙",
                    parse_mode="HTML",
                    link_preview=False,
                    thumb=self._web_document(form["photo_url"]),
                    id=utils.rand(20),
                )
            ]
            await inline_query.answer(results, cache_time=0)
        return
