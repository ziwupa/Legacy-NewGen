"""Rich inline screens: HTML with in-text buttons, rendered by Telegram."""

import contextlib
import html
import logging
import re
import time
import typing
from html.parser import HTMLParser

from legacytl.extensions.html import CUSTOM_EMOJIS

from .. import utils

if typing.TYPE_CHECKING:
    from .core import InlineManager

logger = logging.getLogger(__name__)

# name: (custom emoji id, emoji the custom one replaces, plain fallback)
ICONS: typing.Dict[str, typing.Tuple[int, str, str]] = {
    "back": (6039519841256214245, "⬅️", "‹"),
    "close": (6030757850274336631, "❌", "✕"),
    "search": (6032850693348399258, "🔎", "🔍"),
    "settings": (5341715473882955310, "⚙️", "⚙"),
    "edit": (6039779802741739617, "✏️", "✎"),
    "copy": (6034969813032374911, "📄", "📋"),
    "reset": (5769248574499983619, "🔄", "↺"),
    "check": (5774022692642492953, "✅", "✓"),
    "on": (6041919344995209164, "✅", "●"),
    "off": (5776428312414917091, "⭕️", "○"),
    "add": (6032924188828767321, "➕", "➕"),
    "remove": (5774077015388852135, "❌", "✕"),
    "eye": (6037397706505195857, "👁", "👁"),
    "hide": (5812150667812280629, "🫥", "🙈"),
    "lock": (6037249452824072506, "🔒", "🔒"),
    "secret": (5776227595708273545, "🔒", "🔐"),
    "info": (6028435952299413210, "ℹ️", "ℹ️"),
    "warning": (6030563507299160824, "❗️", "⚠"),
    "module": (5274164885381459901, "🧩", "🧩"),
    "core": (5823537588186647980, "⛈️", "⛈"),
    "external": (5778672437122045013, "📦", "📦"),
    "library": (5431736674147114227, "🗂", "📚"),
    "folder": (6037475557082403885, "📁", "📁"),
    "list": (5766994197705921104, "🗂", "☰"),
    "number": (5924498929147189381, "#️⃣", "#"),
    "text": (5771851822897566479, "🔡", "🔤"),
    "choice": (5776424837786374634, "🎛", "🎛"),
    "link": (6028171274939797252, "🔗", "🔗"),
    "user": (5879770735999717115, "👤", "👤"),
    "bot": (5372981976804366741, "🤖", "🤖"),
    "guest": (6032609071373226027, "👥", "🫂"),
    "command": (6039404727542747508, "⌨️", "⌨"),
    "placeholder": (5197195523794157505, "▫️", "▫️"),
    "page": (6050643982646513651, "📄", "📄"),
    "help": (6037286673010660132, "📖", "📖"),
}

STYLES = {"primary", "success", "danger", "link"}
COPY_LIMIT = 256
ROW_LIMIT = 8
KEYBOARD_LIMIT = 100

_BLOCK = r"details|summary|table|tr|th|td|blockquote|tg-button-row|img|h[1-6]|hr|pre|ul|ol|li"
_BLOCK_RE = re.compile(rf"\n*(</?(?:{_BLOCK})\b[^>]*>)\n*", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def escape(value: typing.Any) -> str:
    return html.escape(str(value), quote=False)


def attr(value: typing.Any) -> str:
    return html.escape(str(value), quote=True)


def plural(count: int, forms: str) -> str:
    """
    :param forms: `one|few|many` (ru) or `one|many` (en), each with `{}`
    """
    variants = forms.split("|")
    n = abs(int(count))
    if len(variants) >= 3:
        if n % 100 in range(11, 15):
            index = 2
        elif n % 10 == 1:
            index = 0
        else:
            index = 1 if n % 10 in (2, 3, 4) else 2
    else:
        index = 0 if n == 1 else len(variants) - 1

    return variants[index].format(count)


def to_rich(source: str) -> str:
    """Line breaks of the screen source to rich HTML, blocks break lines themselves"""
    return _BLOCK_RE.sub(r"\1", source.strip("\n")).replace("\n", "<br>")


def strip_tags(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


class _Plain(HTMLParser):
    """Rich HTML to Telegram HTML, in-text buttons are moved to the keyboard"""

    KEEP = {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "s",
        "del",
        "code",
        "pre",
        "a",
        "tg-spoiler",
        "tg-emoji",
    }

    def __init__(self, taps: typing.Dict[str, dict]):
        super().__init__(convert_charrefs=True)
        self.taps = taps
        self.out: typing.List[str] = []
        self.rows: typing.List[typing.List[dict]] = []
        self.loose: typing.List[dict] = []
        self.button: typing.Optional[dict] = None
        self.row: typing.Optional[typing.List[dict]] = None
        self.details: typing.List[bool] = []
        self.quotes: typing.List[bool] = []
        self.cells = 0
        self.header = False
        self.summary: typing.Optional[int] = None

    def emit(self, chunk: str):
        if self.button is not None:
            self.button["label"].append(chunk)
        else:
            self.out.append(chunk)

    def newline(self):
        if self.button is None and self.out and not self.out[-1].endswith("\n"):
            self.out.append("\n")

    def unquote(self):
        while self.out and self.out[-1].endswith("\n"):
            self.out[-1] = self.out[-1][:-1]
            if not self.out[-1]:
                self.out.pop()

        self.emit("</blockquote>")

    def flush(self):
        self.rows += utils.chunks(self.loose, 2)
        self.loose = []

    def handle_starttag(self, tag: str, attrs: list):
        keys = {key for key, _ in attrs}
        match tag:
            case "tg-button":
                self.button = {"attrs": dict(attrs), "label": []}
            case "tg-button-row":
                self.flush()
                self.row = []
            case _ if tag in self.KEEP:
                self.emit(self.get_starttag_text())
            case "br":
                self.emit("\n")
            case "details":
                self.flush()
                self.newline()
                shown = not any(self.quotes)
                self.quotes.append(shown)
                if shown:
                    self.emit(
                        "<blockquote expandable>"
                        if "expandable" in keys
                        else "<blockquote>"
                    )
            case "summary":
                self.summary = len(self.out)
            case "blockquote":
                self.flush()
                self.newline()
                shown = not any(self.quotes)
                self.quotes.append(shown)
                if shown:
                    self.emit(
                        "<blockquote expandable>"
                        if "expandable" in keys
                        else "<blockquote>"
                    )
            case "table" | "tr":
                if tag == "table":
                    self.flush()
                self.newline()
                self.cells = 0
            case "th" | "td":
                if self.cells:
                    self.emit(": " if self.header else " · ")
                self.header = tag == "th" and not self.cells
                self.cells += 1
                if tag == "th":
                    self.emit("<b>")
            case "h1" | "h2" | "h3" | "h4" | "h5" | "h6":
                self.newline()
                self.emit("<b>")
            case "hr" | "p" | "div":
                self.newline()
            case "li":
                self.newline()
                self.emit("• ")

    def handle_startendtag(self, tag: str, attrs: list):
        if tag == "br":
            self.emit("\n")
        elif tag == "hr":
            self.newline()

    def handle_endtag(self, tag: str):
        match tag:
            case "tg-button":
                self.finish()
            case "tg-button-row":
                if self.row:
                    self.rows += utils.chunks(self.row, ROW_LIMIT)
                self.row = None
                self.newline()
            case _ if tag in self.KEEP:
                self.emit(f"</{tag}>")
            case "summary":
                if self.summary is not None and "<b>" not in "".join(
                    self.out[self.summary :]
                ):
                    self.out.insert(self.summary, "<b>")
                    self.emit("</b>")

                self.summary = None
                self.emit("\n")
                if self.details and not any(self.quotes):
                    self.quotes[-1] = True
                    self.emit(
                        "<blockquote>"
                        if self.details[-1]
                        else "<blockquote expandable>"
                    )
            case "details":
                if self.details:
                    self.details.pop()
                if self.quotes and self.quotes.pop():
                    self.unquote()
                self.newline()
            case "blockquote":
                if self.quotes and self.quotes.pop():
                    self.unquote()
                self.newline()
            case "th":
                self.emit("</b>")
            case "table" | "tr" | "p" | "div" | "li":
                self.newline()
            case "h1" | "h2" | "h3" | "h4" | "h5" | "h6":
                self.emit("</b>")
                self.newline()

    def handle_data(self, data: str):
        self.emit(escape(data))

    def finish(self):
        button, self.button = self.button, None
        attrs, label = button["attrs"], "".join(button["label"])
        text = strip_tags(label) or "·"
        kind = attrs.get("type")
        match kind:
            case "callback_data":
                data = attrs.get("data", "")
                key = self.taps.get(data) or {"text": text, "data": data}
            case "copy_text":
                value = attrs.get("text") or " "
                key = {
                    "text": f"{text} {value}"[:64] if len(text) <= 2 else text,
                    "copy": value,
                }
            case "url":
                key = {"text": text, "url": attrs.get("url", "")}
            case _:
                key = {"text": text, "disabled": True}

        if self.row is not None:
            self.row.append(key)
        elif kind == "callback_data":
            self.emit(label)
            self.loose.append(key)
        elif kind in {"copy_text", "url"}:
            self.loose.append(key)
        else:
            self.emit(f"⟨{label}⟩")

    def result(self) -> typing.Tuple[str, typing.List[typing.List[dict]]]:
        self.close()
        self.flush()
        text = re.sub(r"[ \t]+\n", "\n", "".join(self.out))
        return re.sub(r"\n{3,}", "\n\n", text).strip(), self.rows


def to_plain(
    source: str,
    taps: typing.Optional[typing.Dict[str, dict]] = None,
) -> typing.Tuple[str, typing.List[typing.List[dict]]]:
    """
    Readable Telegram HTML out of a rich screen
    :return: Text and keyboard rows made of its in-text buttons
    """
    parser = _Plain(taps or {})
    parser.feed(_BLOCK_RE.sub(r"\1", source))
    return parser.result()


def fit_keyboard(
    rows: typing.List[typing.List[dict]],
    markup: typing.List[typing.List[dict]],
) -> typing.List[typing.List[dict]]:
    """Puts `rows` above `markup`, dropping what Telegram would not accept"""
    room = KEYBOARD_LIMIT - sum(map(len, markup))
    fitted = []
    for row in rows:
        row = row[: max(room, 0)]
        room -= len(row)
        if row:
            fitted.append(row)

    return fitted + markup


class Screen:
    """
    Rich message with in-text buttons and a bottom keyboard

    Build the body out of fragments and pass it to `inline.form(rich_html=...)`
    or `call.edit(rich_html=...)`. In-text taps are ordinary form callbacks,
    they are tied to the unit and pass the same security checks
    """

    def __init__(
        self,
        *,
        premium: bool = False,
        translate: typing.Optional[typing.Callable[[str], typing.Any]] = None,
    ):
        self.premium = premium
        self._translate = translate
        self.body: typing.List[str] = []
        self.markup: typing.List[typing.List[dict]] = []
        self.taps: typing.Dict[str, dict] = {}
        self.notice: typing.Optional[str] = None
        self.toast: typing.Optional[str] = None
        self.alert = False
        self.reply_input: typing.Optional[dict] = None

    def t(self, key: str, default: str) -> str:
        value = self._translate(f"inline.{key}") if self._translate else None
        return value if isinstance(value, str) else default

    def icon(self, name: typing.Optional[str]) -> str:
        if not name:
            return ""

        emoji_id, emoji, fallback = ICONS.get(name, (None, name, name))
        if self.premium and emoji_id:
            return f'<tg-emoji emoji-id="{emoji_id}">{emoji}</tg-emoji>'

        return escape(fallback)

    def fallback(self, name: typing.Optional[str]) -> str:
        return ICONS[name][2] if name in ICONS else (name or "")

    @staticmethod
    def _style(style: typing.Optional[str]) -> str:
        if not style:
            return ""

        if style not in STYLES:
            raise ValueError(f"Unknown button style: {style}")

        return f' style="{style}"'

    def _label(
        self,
        text: str,
        icon: typing.Optional[str] = None,
        style: typing.Optional[str] = None,
    ) -> str:
        colored = style in {"primary", "success", "danger"}
        mark = escape(self.fallback(icon)) if colored and icon else self.icon(icon)
        body = escape(text)
        return f"{mark} {body}" if mark and body else (mark or body or "·")

    def tap(
        self,
        label: str,
        callback: typing.Callable,
        *args,
        style: typing.Optional[str] = "link",
        icon: typing.Optional[str] = None,
        kwargs: typing.Optional[dict] = None,
        **extra,
    ) -> str:
        """
        In-text callback button
        :param extra: Anything a form button accepts (`force_me`, `always_allow`, ...)
        """
        data = utils.rand(30)
        self.taps[data] = {
            **self.key(label, icon, style if style != "link" else None),
            "callback": callback,
            "args": args,
            "kwargs": kwargs or {},
            "_callback_data": data,
            **extra,
        }
        return (
            f'<tg-button type="callback_data"{self._style(style)} data="{data}">'
            f"{self._label(label, icon, style)}</tg-button>"
        )

    def copy(
        self, label: str, value: typing.Any, *, icon: typing.Optional[str] = "copy"
    ) -> str:
        value = str(value)[:COPY_LIMIT] or " "
        return (
            f'<tg-button type="copy_text" text="{attr(value)}">'
            f"{self._label(label, icon)}</tg-button>"
        )

    def badge(
        self,
        text: typing.Any,
        style: typing.Optional[str] = None,
        *,
        icon: typing.Optional[str] = None,
    ) -> str:
        return (
            f'<tg-button type="disabled"{self._style(style)}>'
            f"{self._label(str(text), icon, style)}</tg-button>"
        )

    @staticmethod
    def row(*buttons: str, align: str = "left") -> str:
        items = [button for button in buttons if button]
        return "".join(
            f'<tg-button-row align="{align}">{"".join(chunk)}</tg-button-row>'
            for chunk in utils.chunks(items, ROW_LIMIT)
        )

    @staticmethod
    def details(summary: str, body: str, *, open: bool = False) -> str:
        return f"<details{' open' if open else ''}><summary>{summary}</summary>{body}</details>"

    @staticmethod
    def table(rows: typing.List[typing.List[str]], *, keys: bool = True) -> str:
        body = "".join(
            "<tr>"
            + "".join(
                f"<th>{cell}</th>" if keys and index == 0 else f"<td>{cell}</td>"
                for index, cell in enumerate(row)
            )
            + "</tr>"
            for row in rows
        )
        return f"<table bordered compact>{body}</table>"

    @staticmethod
    def quote(body: str, *, expandable: bool = False) -> str:
        return f"<blockquote{' expandable' if expandable else ''}>{body}</blockquote>"

    @staticmethod
    def image(url: str) -> str:
        return f'<img src="{attr(url)}"/>'

    def key(
        self,
        label: str,
        icon: typing.Optional[str] = None,
        style: typing.Optional[str] = None,
    ) -> dict:
        button = {"text": label}
        if style:
            button["style"] = style

        if icon in ICONS:
            if self.premium:
                button["emoji_id"] = str(ICONS[icon][0])
            else:
                button["text"] = f"{ICONS[icon][2]} {label}".strip()

        return button

    def button(
        self,
        label: str,
        callback: typing.Callable,
        *args,
        style: typing.Optional[str] = None,
        icon: typing.Optional[str] = None,
        kwargs: typing.Optional[dict] = None,
        **extra,
    ) -> dict:
        return {
            **self.key(label, icon, style),
            "callback": callback,
            "args": args,
            "kwargs": kwargs or {},
            **extra,
        }

    def input(
        self,
        label: str,
        handler: typing.Callable,
        prompt: str,
        *args,
        style: typing.Optional[str] = None,
        icon: typing.Optional[str] = None,
        kwargs: typing.Optional[dict] = None,
    ) -> dict:
        """Button asking for a value, `handler(call, value, *args, **kwargs)`"""
        return {
            **self.key(label, icon, style),
            "input": prompt,
            "handler": handler,
            "args": args,
            "kwargs": kwargs or {},
        }

    def copy_button(self, label: str, value: typing.Any, *, icon: str = "copy") -> dict:
        return {**self.key(label, icon), "copy": str(value)[:COPY_LIMIT] or " "}

    @staticmethod
    def label(text: str) -> dict:
        return {"text": text, "disabled": True}

    def back(
        self, callback: typing.Callable, *args, kwargs: typing.Optional[dict] = None
    ) -> dict:
        return self.button(
            self.t("screen_back", "Back"), callback, *args, icon="back", kwargs=kwargs
        )

    def close(self) -> dict:
        return {**self.key(self.t("screen_close", "Close"), "close"), "action": "close"}

    def footer(
        self,
        back: typing.Optional[typing.Callable] = None,
        *args,
        kwargs: typing.Optional[dict] = None,
    ) -> typing.List[dict]:
        return ([self.back(back, *args, kwargs=kwargs)] if back else []) + [
            self.close()
        ]

    def pager(
        self,
        callback: typing.Callable,
        page: int,
        pages: int,
        *args,
        kwargs: typing.Optional[dict] = None,
    ) -> typing.List[dict]:
        """‹ 1 / N ›, `callback(call, *args, page=..., **kwargs)`"""
        if pages <= 1:
            return []

        kwargs = kwargs or {}

        def move(label: str, target: int) -> dict:
            if not 0 <= target < pages:
                return self.label("·")

            return {
                "text": label,
                "callback": callback,
                "args": args,
                "kwargs": {**kwargs, "page": target},
            }

        return [
            move("‹", page - 1),
            self.label(f"{page + 1} / {pages}"),
            move("›", page + 1),
        ]

    @staticmethod
    def page(items: list, page: int, size: int) -> typing.Tuple[list, int, int]:
        pages = max(1, -(-len(items) // size))
        page = max(0, min(int(page or 0), pages - 1))
        return items[page * size : (page + 1) * size], page, pages

    def add(self, *lines: typing.Optional[str]) -> "Screen":
        self.body += [line for line in lines if line]
        return self

    def keyboard(self, *rows: typing.Optional[typing.List[dict]]) -> "Screen":
        self.markup += [list(row) for row in rows if row]
        return self

    def warn(self, text: str) -> "Screen":
        self.notice = f"{self.icon('warning')} {escape(text)}"
        return self

    def reply(
        self, handler: typing.Callable, *args, kwargs: typing.Optional[dict] = None
    ) -> "Screen":
        """Accept a reply to the screen message, `handler(call, text, *args, **kwargs)`"""
        self.reply_input = {"handler": handler, "args": args, "kwargs": kwargs or {}}
        return self

    @property
    def source(self) -> str:
        return "\n".join(([self.notice] if self.notice else []) + self.body)

    def html(self) -> str:
        return to_rich(self.source)

    def plain(self) -> typing.Tuple[str, typing.List[typing.List[dict]]]:
        return to_plain(self.source, self.taps)


class Screens:
    """Rich screens mixin for InlineManager (see Screen)."""

    def screen(self: "InlineManager") -> Screen:
        """Empty screen, aware of the owner's Premium for custom emoji"""
        return Screen(premium=self._premium(), translate=self.translator.getkey)

    def _premium(self: "InlineManager") -> bool:
        me = getattr(self._client, "legacy_me", None)
        return bool(getattr(me, "premium", False)) and CUSTOM_EMOJIS

    async def show(self: "InlineManager", call: typing.Any, screen: Screen) -> bool:
        """Edit the unit of `call` to `screen` and answer with its toast"""

        async def answer():
            if getattr(call, "data", None) is not None and hasattr(call, "answer"):
                with contextlib.suppress(Exception):
                    await call.answer(
                        screen.toast or None, show_alert=screen.alert or None
                    )

        result = await call.edit(rich_html=screen)
        await answer()
        return result

    def _bind_screen(
        self: "InlineManager",
        unit_id: typing.Optional[str],
        screen: typing.Optional[Screen],
        *,
        fresh: bool = False,
    ):
        """Registers in-text taps of `screen` as callbacks of the unit"""
        unit = self._units.get(unit_id) if unit_id is not None else None
        if unit is not None and not fresh:
            for data, entry in self._custom_map.copy().items():
                if entry.get("unit_id") == unit_id:
                    self._custom_map.pop(data, None)

        taps = list(screen.taps.values()) if screen is not None else []
        for tap in taps:
            self._custom_map[tap["_callback_data"]] = {
                "handler": tap["callback"],
                "always_allow": tap.get("always_allow", []),
                "args": tap.get("args", {}),
                "kwargs": tap.get("kwargs", {}),
                "force_me": tap.get("force_me", False),
                "disable_security": tap.get("disable_security", False),
                "unit_id": unit_id,
            }

        if unit is not None:
            unit["rich_buttons"] = taps
            unit["reply_input"] = screen.reply_input if screen is not None else None

    def _rich_to_plain(
        self: "InlineManager",
        source: str,
        taps: typing.Optional[typing.Dict[str, dict]],
        markup: typing.List[typing.List[dict]],
    ) -> typing.Tuple[str, typing.List[typing.List[dict]]]:
        text, rows = to_plain(source, taps)
        return text or "­", fit_keyboard(rows, markup)

    def _rich_fallback(self: "InlineManager", unit_id: str) -> bool:
        """Turns a rich form into plain text one, if it is rich"""
        unit = self._units.get(unit_id)
        if not unit or unit.get("rich_html") is None:
            return False

        unit.pop("rich_html")
        unit["text"], unit["buttons"] = self._rich_to_plain(
            unit.pop("rich_source", ""),
            {tap["_callback_data"]: tap for tap in unit.get("rich_buttons", [])},
            unit.get("buttons", []),
        )
        unit["rich_failed"] = True
        return True

    async def _reply_input_handler(self: "InlineManager", message: typing.Any) -> bool:
        """Passes owner's reply to a screen, which asked for one, to its handler"""
        reply_to = getattr(message, "reply_to_msg_id", None)
        if not reply_to:
            return False

        chat = utils.get_chat_id(message)
        for unit_id, unit in self._units.copy().items():
            entry = unit.get("reply_input")
            if (
                not entry
                or unit.get("message_id") != reply_to
                or unit.get("chat") != chat
                or unit.get("ttl", time.time() + 1) < time.time()
            ):
                continue

            with contextlib.suppress(Exception):
                await message.delete()

            from .types import InlineMessage

            call = InlineMessage(self, unit_id, unit.get("inline_message_id"))
            try:
                await entry["handler"](
                    call,
                    getattr(message, "raw_text", None) or getattr(message, "text", ""),
                    *entry.get("args", ()),
                    **entry.get("kwargs", {}),
                )
            except Exception:
                logger.exception("Error while processing reply to screen %s", unit_id)

            return True

        return False
