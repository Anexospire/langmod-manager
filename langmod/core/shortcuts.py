"""Windows shortcuts: one that opens the manager, one that plays with mods.

``.lnk`` files are written through the Windows Script Host, with every value
passed in the environment so no path ever has to survive being quoted. ``LANGMOD_DESKTOP_DIR`` and
``LANGMOD_PROGRAMS_DIR`` move both places, which is how the tests stay off
the real desktop.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..i18n import N_, tr

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCES = Path(__file__).resolve().parents[1] / "resources"

KINDS = {
    # kind: (name, arguments after the program, description, icon variant); name and description are shown
    # in the language in use when the shortcut is made
    "manager": ("Langmod Manager", "", N_("Load several War Thunder language mods at once"), ""),
    "play": (N_("War Thunder with mods"), "launch",
             N_("Apply your language mods if anything changed, then start War Thunder"), "-play"),
}
WHERE = ("desktop", "programs")


class ShortcutError(Exception):
    pass


def _known_folder(csidl: int) -> Path | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) != 0:
        return None
    return Path(buf.value)


def folder(where: str) -> Path | None:
    """The desktop (as Explorer sees it, OneDrive or not), or the Start menu's Programs."""
    env = os.environ.get("LANGMOD_DESKTOP_DIR" if where == "desktop" else "LANGMOD_PROGRAMS_DIR")
    if env:
        return Path(env)
    return _known_folder(0x10 if where == "desktop" else 0x02)


def icon_path(theme: str, kind: str = "manager") -> Path:
    variant = KINDS[kind][3]
    for name in (f"icon-{theme}{variant}.ico", f"icon-standard{variant}.ico", "icon-standard.ico"):
        p = RESOURCES / name
        if p.is_file():
            return p
    return RESOURCES / "icon-standard.ico"


@dataclass(frozen=True)
class Command:
    target: str
    arguments: str
    working_dir: str


def command(kind: str) -> Command:
    """What a shortcut runs: the frozen app itself, or this Python with ``-m langmod``.

    ``pythonw`` rather than ``python``, so no console window opens beside it.
    """
    extra = KINDS[kind][1]
    if getattr(sys, "frozen", False):
        return Command(sys.executable, extra, str(Path(sys.executable).parent))
    exe = Path(sys.executable)
    windowed = exe.with_name("pythonw.exe")
    target = windowed if windowed.is_file() else exe
    return Command(str(target), f"-m langmod {extra}".strip(), str(PROJECT_ROOT))


def path_for(kind: str, where: str = "desktop") -> Path | None:
    base = folder(where)
    return base / (tr(KINDS[kind][0]) + ".lnk") if base else None


def _powershell(script: str, env: dict[str, str]) -> None:
    if sys.platform != "win32":
        raise ShortcutError(tr("shortcuts can only be made on Windows"))
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            env={**os.environ, **env}, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShortcutError(tr("PowerShell could not be run: {error}", error=exc)) from exc
    if done.returncode != 0:
        err = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ShortcutError(err[-1] if err else f"PowerShell stopped with code {done.returncode}")


_WRITE = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LM_LNK); "
          "$s.TargetPath = $env:LM_TARGET; $s.Arguments = $env:LM_ARGS; "
          "$s.WorkingDirectory = $env:LM_DIR; $s.IconLocation = $env:LM_ICON; "
          "$s.Description = $env:LM_DESC; $s.Save()")
_READ = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LM_LNK); "
         "[Console]::Out.Write($s.TargetPath + '|' + $s.Arguments + '|' + $s.IconLocation)")


def create(kind: str, where: str = "desktop", theme: str = "standard", lnk: Path | None = None) -> Path:
    """Make a shortcut, or make one again where it is (``lnk``), whatever language its name is in."""
    lnk = lnk or path_for(kind, where)
    if lnk is None:
        raise ShortcutError(tr("The desktop could not be found.") if where == "desktop"
                            else tr("The Start menu could not be found."))
    lnk.parent.mkdir(parents=True, exist_ok=True)
    cmd = command(kind)
    _powershell(_WRITE, {
        "LM_LNK": str(lnk), "LM_TARGET": cmd.target, "LM_ARGS": cmd.arguments,
        "LM_DIR": cmd.working_dir, "LM_ICON": f"{icon_path(theme, kind)},0", "LM_DESC": tr(KINDS[kind][2]),
    })
    if not lnk.is_file():
        raise ShortcutError(tr("Windows did not write {file}", file=lnk))
    _refresh_icon(lnk)
    return lnk


def read(lnk: Path) -> tuple[str, str, str]:
    """(target, arguments, icon) of a shortcut; for checking one."""
    if sys.platform != "win32":
        raise ShortcutError(tr("shortcuts can only be read on Windows"))
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _READ],
                          env={**os.environ, "LM_LNK": str(lnk)}, capture_output=True, timeout=30,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    parts = done.stdout.decode("utf-8", "replace").split("|")
    return (parts + ["", "", ""])[:3] if len(parts) >= 3 else ("", "", "")


def remove(lnk: Path) -> None:
    lnk.unlink(missing_ok=True)


def _theme_for(prefs) -> str:
    return prefs.get("theme") if prefs.get("icons_follow_theme") else "standard"


def make(lib, kind: str, where: str = "desktop") -> Path:
    """Create a shortcut and remember it, so its icon can follow the theme."""
    from .prefs import Prefs
    prefs = Prefs(lib)
    lnk = create(kind, where, _theme_for(prefs))
    made = prefs.get("shortcuts")
    made[f"{kind}:{where}"] = str(lnk)
    prefs.set("shortcuts", made)
    return lnk


def drop(lib, kind: str, where: str = "desktop") -> Path | None:
    from .prefs import Prefs
    prefs = Prefs(lib)
    made = prefs.get("shortcuts")
    known = made.pop(f"{kind}:{where}", None)
    lnk = Path(known) if known else path_for(kind, where)
    if lnk is not None:
        remove(lnk)
    prefs.set("shortcuts", made)
    return lnk


def exists(lib, kind: str, where: str = "desktop") -> bool:
    from .prefs import Prefs
    known = Prefs(lib).get("shortcuts").get(f"{kind}:{where}")
    lnk = Path(known) if known else path_for(kind, where)
    return lnk is not None and lnk.is_file()


def follow_theme(lib) -> list[Path]:
    """Re-dress every shortcut this app made in the current theme's icon."""
    from .prefs import Prefs
    prefs = Prefs(lib)
    done = []
    for key, path in prefs.get("shortcuts").items():
        kind, _, where = key.partition(":")
        if kind in KINDS and Path(path).is_file():
            done.append(create(kind, where, _theme_for(prefs), Path(path)))
    return done


def _refresh_icon(lnk: Path) -> None:
    """Tell Explorer the shortcut changed, so a new icon shows without a restart."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x00002000, 0x0005, str(lnk), None)   # UPDATEITEM, PATHW
    except Exception:
        pass
