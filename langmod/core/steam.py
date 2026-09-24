"""Starting War Thunder from Steam through the manager.

Steam starts the game by itself, so after a game update the mods wait until the manager is
opened. A game's launch options in Steam can put a program in front of it: given
``"<the manager>" launch %command%``, Steam runs the manager with the game's own command line
in place of ``%command%``, and the manager brings the mods up to date and then runs that
command (see ``cli``). This makes the line to paste, and reads Steam's settings to tell
whether it is there. It never writes them: Steam keeps its own, and would write over them.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from .game import STEAM_APP, steam_roots
from .shortcuts import PROJECT_ROOT

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def option() -> str:
    """The launch options line for this copy of the manager."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" launch %command%'
    exe = Path(sys.executable)
    windowed = exe.with_name("pythonw.exe")
    # Steam starts it in the game's folder, where "-m langmod" is not found; the launcher script
    # puts the project on the path by being in it.
    return f'"{windowed if windowed.is_file() else exe}" "{PROJECT_ROOT / "launcher.py"}" launch %command%'


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), text)


def launch_options_in(text: str) -> str:
    """War Thunder's launch options in the text of a Steam account's ``localconfig.vdf``."""
    for block in re.finditer(r'"%s"\s*\{' % STEAM_APP, text):
        depth, key = 0, None
        for m in _TOKEN.finditer(text, block.end() - 1):
            if m.group(2):
                depth += 1 if m.group(2) == "{" else -1
                key = None
                if depth == 0:
                    break
            elif key is None:
                key = m.group(1)
            else:
                if depth == 1 and key.lower() == "launchoptions":
                    return _unescape(m.group(1))
                key = None
    return ""


def launch_options() -> list[str]:
    """War Thunder's launch options for each Steam account on this computer, as far as Steam has
    saved them: it writes its settings now and then, and when it closes. ``LANGMOD_STEAM_DIR``
    stands in for Steam's folder."""
    env = os.environ.get("LANGMOD_STEAM_DIR")
    found, seen = [], set()
    for root in [Path(env)] if env else steam_roots():
        for cfg in root.glob("userdata/*/config/localconfig.vdf"):
            try:
                key = str(cfg.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                value = launch_options_in(cfg.read_text("utf-8", errors="replace"))
            except OSError:
                continue
            if value.strip():
                found.append(value)
    return found


def _plain(text: str) -> str:
    return " ".join(text.split()).lower()


def state() -> str:
    """``here`` when Steam starts War Thunder through this copy of the manager; ``elsewhere`` when
    through another copy, one moved or deleted since; ``""`` when neither, as far as can be told."""
    mine = _plain(option())
    found = [_plain(o) for o in launch_options()]
    if any(mine in o for o in found):
        return "here"
    if any("langmod" in o and "launch %command%" in o for o in found):
        return "elsewhere"
    return ""
