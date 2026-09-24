"""The one switch in ``config.blk`` the game needs to read ``lang/`` from disk.

``debug{ testLocalization:b=yes }`` is what IFN1's readme has players type in
by hand, and what the in-game "Custom Localization" option (Main page)
flips. The file is edited as text, touching only that one line, so every
other setting and the user's own layout stay as they were.
"""
from __future__ import annotations

import re

from . import blk

FLAG = "testLocalization"
_FLAG_LINE = re.compile(r"(?m)^([ \t]*)" + FLAG + r"\s*:\s*b\s*=\s*([^\s/]+)\s*(?://.*)?$")


def _top_level_block(text: str, name: str) -> tuple[int, int] | None:
    """Offsets of ``{`` and the matching ``}`` of a top-level block."""
    depth = 0
    i, n = 0, len(text)
    word_start = None
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == '"':
            j = text.find('"', i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == "{":
            if depth == 0 and word_start is not None and text[word_start:i].strip() == name:
                close = _matching(text, i)
                if close is not None:
                    return i, close
            depth += 1
            word_start = None
        elif c == "}":
            depth = max(0, depth - 1)
            word_start = None
        elif c in "\r\n;":
            word_start = None
        elif not c.isspace() and word_start is None and depth == 0:
            word_start = i
        i += 1
    return None


def _matching(text: str, open_at: int) -> int | None:
    depth = 0
    i, n = open_at, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if text[i] == '"':
            j = text.find('"', i + 1)
            i = n if j < 0 else j + 1
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def test_localization(text: str) -> bool:
    span = _top_level_block(text, "debug")
    if span is None:
        return False
    m = _FLAG_LINE.search(text, span[0], span[1])
    return bool(m) and m.group(2).lower() in ("yes", "true", "on", "1")


def set_test_localization(text: str, on: bool) -> str:
    """The same config with the switch set; the text is otherwise untouched."""
    nl = "\r\n" if "\r\n" in text else "\n"
    value = "yes" if on else "no"
    span = _top_level_block(text, "debug")
    if span is None:
        if not on:
            return text
        sep = "" if not text or text.endswith(("\n", "\r")) else nl
        return f"{text}{sep}{nl}debug{{{nl}  {FLAG}:b={value}{nl}}}{nl}"
    open_at, close_at = span
    m = _FLAG_LINE.search(text, open_at, close_at)
    if m:
        return text[:m.start(2)] + value + text[m.end(2):]
    if not on:
        return text
    body = text[open_at + 1:close_at]
    indent = "  "
    im = re.search(r"(?m)^([ \t]+)\S", body)
    if im:
        indent = im.group(1)
    # Put the new line just before the closing brace, on a line of its own.
    line_start = text.rfind("\n", open_at, close_at) + 1
    before = text[line_start:close_at]
    if before.strip():
        insert = f"{nl}{indent}{FLAG}:b={value}{nl}"
        return text[:close_at] + insert + text[close_at:]
    if line_start <= open_at:
        insert = f"{nl}{indent}{FLAG}:b={value}{nl}"
        return text[:open_at + 1] + insert + text[open_at + 1:]
    return text[:line_start] + f"{indent}{FLAG}:b={value}{nl}" + text[line_start:]


def game_language(text: str) -> str:
    root, _ = blk.parse_text(text)
    return str(root.value("language", "") or "")
