# ©️ Undefined & XDesai, 2025
# This file is a part of Legacy Userbot
# 🌐 https://github.com/ziwupa/Legacy-NewGen
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

# ©️ Based on Dan Gazizullin's work
# 🌐 https://github.com/hikariatama/Hikka

import re

def compat(code: str) -> str:
    """
    Reformats modules, built for Hikka or Heroku to work with Legacy
    :param code: code to reformat
    :return: reformatted code
    :rtype: str
    """

    # utils.get_platform_name → utils.get_named_platform
    code = re.sub(
        r"\butils\.get_platform_name\b",
        "utils.get_named_platform",
        code,
    )

    # import hikka.something → import legacy.something
    code = re.sub(
        r"\bimport\s+hikka(\.[\w\.]*)",
        r"import legacy\1",
        code,
    )

    # from hikka import ... → from legacy import ...
    code = re.sub(
        r"\bfrom\s+hikka\b",
        "from legacy",
        code,
    )

    # hikka. → legacy.
    code = re.sub(
        r"\bhikka\.",
        "legacy.",
        code,
    )

    # import heroku.something → import legacy.something
    code = re.sub(
        r"\bimport\s+heroku(\.[\w\.]*)",
        r"import legacy\1",
        code,
    )

    # from heroku import ... → from legacy import ...
    code = re.sub(
        r"\bfrom\s+heroku\b",
        "from legacy",
        code,
    )

    # heroku.loader → legacy.loader. Unlike `hikka.`, a bare `heroku.` is not
    # renamed blindly: `heroku.com` is a real website which modules do link to
    code = re.sub(
        r"\bheroku\.(?="
        r"(?:loader|utils|main|types|security|database|translations|dispatcher"
        r"|tl_cache|validators|version|inline|log|configurator|qr)\b)",
        "legacy.",
        code,
    )

    # *.hikka_me / *.heroku_me → *.legacy_me
    code = re.sub(
        r"\b([\w\.-]+)\.(?:hikka|heroku)_me\b",
        r"\1.legacy_me",
        code,
    )

    # *.hikka_inline / *.heroku_inline → *.legacy_inline
    code = re.sub(
        r"\b([\w\.-]+)\.(?:hikka|heroku)_inline\b",
        r"\1.legacy_inline",
        code,
    )

    return code
