"""The manager in other languages: English, German, French, Polish, Russian and Simplified Chinese.

Text in the code is English, and is its own key: ``tr("Apply to game")``.
Values go in by name, ``tr("Removed {name}.", name=...)``, so a translation
can put them where its grammar wants them. Counted text gives both English
forms, ``ntr(n, "{n} file", "{n} files")``, and each language keeps as many
forms as it has: Russian and Polish three (1 файл, 2 файла, 5 файлов),
Chinese one. ``N_`` marks text kept in a table to be translated where it is
shown.

The catalogs are ``resources/locale/<code>.json``: ``strings`` maps English
to the translation, ``plurals`` maps ``"one|many"`` to the forms. Anything
missing shows in English. ``tests/test_i18n.py`` checks that every text the
code asks for is in every catalog, with the same ``{names}`` and markup.

No Qt here: the core's messages go through ``tr`` too. The command line
never sets a language, so it speaks English.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

LOCALE = Path(__file__).resolve().parent / "resources" / "locale"

#: Each language in its own name, in the order the choice lists them.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "pl": "Polski",
    "ru": "Русский",
    "zh": "简体中文",
}
#: Windows' primary language ids for the languages there are.
_WINDOWS = {0x09: "en", 0x07: "de", 0x0C: "fr", 0x15: "pl", 0x19: "ru", 0x04: "zh"}

_lang = "en"
_strings: dict[str, str] = {}
_plurals: dict[str, list[str]] = {}
PSEUDO = "qps"        # a language for finding text left untranslated: everything translated is [bracketed]


def N_(text: str) -> str:
    """Marks text to be translated where it is shown, not where it is written."""
    return text


def system_language() -> str:
    """The language Windows is shown in, if it is one of these; English otherwise."""
    if sys.platform == "win32":
        try:
            import ctypes
            primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            return _WINDOWS.get(primary, "en")
        except Exception:
            return "en"
    import locale
    code = (locale.getlocale()[0] or os.environ.get("LANG", "en"))[:2].lower()
    return code if code in LANGUAGES else "en"


def choose(preference: str = "system") -> str:
    """The language to show: ``LANGMOD_LANGUAGE`` if set (tests and screenshots), then the player's choice,
    then Windows'."""
    forced = os.environ.get("LANGMOD_LANGUAGE", "").lower()
    if forced in LANGUAGES or forced == PSEUDO:
        return forced
    if preference in LANGUAGES:
        return preference
    return system_language()


def set_language(code: str) -> str:
    """Use a language from now on; returns the one in use (English if there is no such catalog)."""
    global _lang, _strings, _plurals
    _strings, _plurals = {}, {}
    if code not in LANGUAGES and code != PSEUDO:
        code = "en"
    if code not in ("en", PSEUDO):
        try:
            raw = json.loads((LOCALE / f"{code}.json").read_text("utf-8"))
            _strings = {k: v for k, v in raw.get("strings", {}).items() if isinstance(v, str) and v}
            _plurals = {k: v for k, v in raw.get("plurals", {}).items() if isinstance(v, list) and v}
        except (OSError, ValueError, AttributeError):
            code = "en"
    _lang = code
    return code


def language() -> str:
    return _lang


def tr(text: str, **values) -> str:
    """``text`` in the language in use, with ``values`` put in by name."""
    out = _strings.get(text, text)
    if _lang == PSEUDO:
        out = f"[{out}]"
    return out.format(**values) if values else out


def plural_form(n: int, code: str | None = None) -> int:
    """Which of a language's forms a count takes."""
    code = code or _lang
    n = abs(int(n))
    if code == "zh":
        return 0
    if code == "fr":
        return 0 if n in (0, 1) else 1
    if code in ("ru", "pl"):
        if (n % 10 == 1 and n % 100 != 11) if code == "ru" else n == 1:
            return 0
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return 1
        return 2
    return 0 if n == 1 else 1


def forms(code: str) -> int:
    """How many forms counted text has in a language."""
    return {"zh": 1, "ru": 3, "pl": 3}.get(code, 2)


def ntr(n: int, one: str, many: str, **values) -> str:
    """Counted text: ``ntr(3, "{n} file", "{n} files")``. ``{n}`` is the count, written the language's way."""
    found = _plurals.get(f"{one}|{many}")
    if found:
        text = found[min(plural_form(n), len(found) - 1)]
    else:
        text = one if plural_form(n, "en") == 0 else many
    if _lang == PSEUDO:
        text = f"[{text}]"
    return text.format(n=num(n), **values)


def num(n: int) -> str:
    """A whole number with the language's thousands separator: 24,222 · 24.222 · 24 222."""
    if isinstance(n, float) and not n.is_integer():
        return str(n)
    n = int(n)
    plain = f"{abs(n):,}"
    if _lang == "de":
        plain = plain.replace(",", ".")
    elif _lang == "fr":
        plain = plain.replace(",", " ")
    elif _lang in ("ru", "pl"):
        # A thin gap between thousands, but not in a four-figure number, as both languages write them.
        plain = plain.replace(",", " ") if abs(n) >= 10000 else str(abs(n))
    return ("-" if n < 0 else "") + plain


def dec(value: float, places: int = 1) -> str:
    """A number with decimals: 1.5 in English and Chinese, 1,5 in the others."""
    text = f"{value:.{places}f}"
    return text.replace(".", ",") if _lang in ("de", "fr", "pl", "ru") else text


_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def date(when: float) -> str:
    """A day, written the language's way: 17 Sep 2026 · 17.09.2026 · 17/09/2026 · 2026年9月17日."""
    t = time.localtime(when)
    if _lang == "zh":
        return f"{t.tm_year}年{t.tm_mon}月{t.tm_mday}日"
    if _lang == "fr":
        return f"{t.tm_mday:02d}/{t.tm_mon:02d}/{t.tm_year}"
    if _lang in ("de", "pl", "ru"):
        return f"{t.tm_mday:02d}.{t.tm_mon:02d}.{t.tm_year}"
    return f"{t.tm_mday:02d} {_MONTHS_EN[t.tm_mon - 1]} {t.tm_year}"


def join(items: list[str]) -> str:
    """A list in running text: "a, b, c" (Chinese uses its own comma)."""
    return ("、" if _lang == "zh" else ", ").join(items)


def sentences(*parts: str) -> str:
    """Sentences one after another: a space between them, none in Chinese."""
    return ("" if _lang == "zh" else " ").join(p for p in parts if p)


#: The game's language names, as config.blk has them, to show in the language in use.
GAME_LANGUAGES = {
    "English": N_("English"), "German": N_("German"), "French": N_("French"), "Polish": N_("Polish"),
    "Russian": N_("Russian"), "Chinese": N_("Chinese"), "Italian": N_("Italian"), "Spanish": N_("Spanish"),
    "Czech": N_("Czech"), "Turkish": N_("Turkish"), "Japanese": N_("Japanese"), "Portuguese": N_("Portuguese"),
    "Ukrainian": N_("Ukrainian"), "Serbian": N_("Serbian"), "Hungarian": N_("Hungarian"), "Korean": N_("Korean"),
    "Belarusian": N_("Belarusian"), "Romanian": N_("Romanian"), "TChinese": N_("Traditional Chinese"),
    "HChinese": N_("Chinese (Hong Kong)"), "Vietnamese": N_("Vietnamese"),
}


def game_language(name: str) -> str:
    """A game language's name ("Russian"), in the language in use."""
    return tr(GAME_LANGUAGES[name]) if name in GAME_LANGUAGES else name
