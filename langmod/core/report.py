"""A bug report to paste into a message: what someone helping needs, and nothing personal.

Versions, the game and how it is set up, the mods in their order, the
choices that change what is written, and the end of ``errors.log``. The
home folder in any path is written as ``%USERPROFILE%``, so a user name
does not travel with it.
"""
from __future__ import annotations

import platform
import sys
import time
from pathlib import Path

from .. import __version__
from . import sources, updates
from .library import Library

ERROR_LOG = "errors.log"
MAX_LOG = 512 * 1024       # past this, errors.log starts afresh
LOG_TAIL = 6000            # characters of errors.log, from its end


def _private(text: str) -> str:
    home = str(Path.home())
    for form in {home, home.replace("\\", "/")}:
        text = text.replace(form, "%USERPROFILE%")
    return text


def open_log(root: Path):
    """``errors.log`` in ``root``, open to add to, after a line saying when and which version."""
    path = Path(root) / ERROR_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > MAX_LOG:
        path.unlink()
    fh = open(path, "a", encoding="utf-8", errors="replace")
    fh.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')}, Langmod Manager {__version__}\n")
    return fh


def log_error(root: Path, text: str) -> None:
    """Keep an error the window caught in ``errors.log``, for the bug report; a failure to is let go."""
    try:
        with open_log(root) as fh:
            fh.write(text.rstrip() + "\n")
    except OSError:
        pass


def error_tail(lib: Library) -> str:
    try:
        text = (lib.root / ERROR_LOG).read_text("utf-8", errors="replace")
    except OSError:
        return ""
    return text[-LOG_TAIL:].lstrip()


def bug_report(lib: Library, install=None, game=None, state: dict | None = None, extra: list[str] | None = None) -> str:
    frozen = getattr(sys, "frozen", False)
    lines = ["Langmod Manager bug report", "",
             f"Version: {__version__} ({'standalone build' if frozen else 'from source'})",
             f"Windows: {platform.platform()}" if sys.platform == "win32" else f"System: {platform.platform()}",
             f"Python: {platform.python_version()}"]
    lines += extra or []
    lines.append("")
    if install is None:
        lines.append("Game: not found")
    else:
        lines.append(f"Game: {install.label}, {_private(str(install.root))}")
        if game is not None:
            lines.append(f"Game version: {game.version}")
        st = state or {}
        m = st.get("manifest")
        lines.append(f"Language: {st.get('language') or 'unknown'}; custom localization "
                     f"{'on' if st.get('switch_on') else 'off'}")
        if m is not None:
            lines.append(f"Applied: {m.applied} for game {m.game_version}, {len(m.files)} files"
                         + ("; the game has updated since" if st.get("stale") else ""))
        else:
            lines.append("Applied: not yet")
        if st.get("stray"):
            lines.append(f"Other files in lang: {len(st['stray'])}")
    lines += ["", f"Mods ({len(lib.mods)}), in load order:"]
    for i, m in enumerate(lib.mods, 1):
        src = updates.source_of(m)
        bits = [f"{len(m.csv_names)} files"]
        if m.has_list:
            bits.append("own list")
        if m.modules:
            bits.append(f"{len(m.modules)} modules")
        edited = sum(1 for f in m.files.values() if f.edited) if not m.personal else 0
        if edited:
            bits.append(f"{edited} edited")
        if src is not None:
            bits.append(f"follows {src.place} {src.ref}" + (f" ({updates.state(m)})" if updates.state(m) else ""))
        lines.append(f"  {i}. [{'x' if m.enabled else ' '}] {m.label}: {', '.join(bits)}")
    picks = lib.settings.get("picks")
    if isinstance(picks, dict) and picks:
        lines.append(f"Picked strings: {len(picks)}")
    prefs = lib.settings.get("prefs") if isinstance(lib.settings.get("prefs"), dict) else {}
    shown = {k: prefs[k] for k in ("theme", "motion", "fps", "switch_on", "on_update", "update_check",
                                   "update_install") if k in prefs}
    lines += ["", "Settings: " + (", ".join(f"{k}={v}" for k, v in shown.items()) or "none"),
              f"Nexus Mods: {'on' if sources.NEXUS else 'off in this build'}"]
    tail = error_tail(lib)
    lines += ["", "errors.log:" if tail else "errors.log: empty"]
    if tail:
        lines.append(_private(tail))
    return "\n".join(lines).rstrip() + "\n"
