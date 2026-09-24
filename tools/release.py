"""Make the copy of Langmod Manager to hand round: the standalone build, checked and zipped.

    python tools\\release.py              build, check, zip
    python tools\\release.py --no-build   check and zip the last build again

Needs PyInstaller in the Python that runs it (``python -m pip install pyinstaller``). The
result is ``dist\\Langmod-Manager-<version>-windows.zip``: the program and a read-me, and
nothing of whoever built it. The manager keeps a player's mods, settings and backups in
%APPDATA%\\Langmod Manager, never beside the program; the checks below make sure the build
carries none of them, and no path from this machine either, before the zip is written.

Every DLL the build's own files import has to be in it (the spec leaves out parts of Qt the
manager never loads, and this is what proves they are not needed). The build is then started
once, in a throwaway home: the command line lists the (no) mods, and the window opens on
screen, runs for a few seconds with the tour going, and closes. Neither may leave an error
behind, nor anything in the build's own folder.

Beside the program go its licence, and the notices and licences of what the build carries:
Qt and PySide6 (LGPL 3, which asks for its text, the GPL's, and where their source is), and
Python with the libraries it brings, its licence taken from the Python that made the build.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP = DIST / "Langmod Manager"
EXE = APP / "Langmod Manager.exe"
LICENSES = ROOT / "licenses"
#: What goes beside the program besides the read-me; the zip must have every one.
SMALL_PRINT = ("License.txt", "Third-party notices.txt", "licenses/LGPL-3.0.txt", "licenses/GPL-3.0.txt",
               "licenses/Python.txt")

#: Names that only a player's own data would have.
PERSONAL_NAMES = re.compile(r"^(library\.json|langmod-manager\.json|errors\.log|.*\.lnk|backups|downloads|mods)$",
                            re.I)

README = """\
Langmod Manager {version}
{rule}

Load several War Thunder language mods at once, and keep them working through
game updates. No more pasting a lang folder in again for every mod release,
and no names turning into string IDs after a game update.


Starting it
-----------
1. Unzip this folder anywhere you like, for example into Documents. Not into
   the War Thunder folder.
2. Open "Langmod Manager.exe". There is nothing to install.

   Windows may say it "protected your PC": the program is not signed with a
   paid certificate. Choose "More info", then "Run anyway".

3. A short tour shows you round. Skip it if you like; the ? button at the top
   shows it again.

The manager speaks English, Deutsch, Français, Polski, Русский and 简体中文.
It starts in the language Windows is in, if it is one of those; Settings,
Look, Language changes it.


Using it
--------
* Add, then Get mods, fetches IFN1, the Localization Overhaul Project or
  WTHLM in one click. Or add any mod with Add: the zip or 7z as you
  downloaded it, a folder, or a .csv. Adding a newer version of a mod you have updates that
  mod, and keeps any files of it you edited.
* Put your mods in order. Where two change the same name, the one lower in
  the list wins.
* The Strings tab (Ctrl+F) finds any name: type F-16 and see what the game
  says, what each mod says, and which one shows. Pick another one to show,
  or type your own.
* Profiles (the button above your mods) keep named sets of mods, switched in
  one click. Export one to send to a friend; Import brings one in and fetches
  the mods you do not have.
* Apply writes them into the game. Play applies (only if something changed)
  and starts War Thunder. The game has to be closed while it applies.
* Restore game (the round arrow at the top) takes everything out again, and
  puts back what your lang folder had before.

IFN1, the Localization Overhaul Project and WTHLM are recognised by
themselves and can update on their own (Settings, Updates). Other mods can
follow their WT Live post or GitHub page.

Desktop icons: Settings, Shortcuts makes "Langmod Manager", and "War Thunder
with mods", which applies your mods if anything changed and then starts the
game. If you move this folder, make them again from its new place.

Playing through Steam: Settings, Shortcuts has a line to paste into War
Thunder's launch options in Steam (right-click the game, Properties, General).
Steam then starts the game through the manager, which brings your mods up to
date first. To stop, empty that box again. If you move this folder, paste the
line again.


Where your things are kept
--------------------------
Your mods, settings and backups are in %APPDATA%\\Langmod Manager, not in this
folder, so a newer version of the manager can simply replace this one.{updating}

If something goes wrong: Settings, Folders, "Copy a bug report" puts what
someone helping you needs on the clipboard (no user name or other personal
details). Paste it into your message.


Removing it
-----------
1. In the manager, press Restore game (the round arrow at the top).
2. If you gave Steam the launch options line, empty War Thunder's launch
   options in Steam, or Steam cannot start the game.
3. Delete this folder, and %APPDATA%\\Langmod Manager too if you want your
   mods and settings gone.


The small print
---------------
Langmod Manager is free software under the MIT licence (License.txt). It is
built with Qt and PySide6, used under the GNU LGPL version 3, and Python; see
"Third-party notices.txt" and the licenses folder.

It is not made by, endorsed by or connected with Gaijin Entertainment, the
makers of War Thunder.
"""

NOTICES = """\
Third-party notices
===================

Langmod Manager {version} is free software under the MIT licence (License.txt).
This copy of it carries the following, each under its own licence.


Qt {qt} and PySide6 {pyside}
{qt_rule}
The window is made with Qt (The Qt Company Ltd and other contributors) and
PySide6, Qt for Python (The Qt Company Ltd), used under the GNU Lesser General
Public License version 3: licenses\\LGPL-3.0.txt, which adds to the GNU General
Public License version 3 in licenses\\GPL-3.0.txt.

They are used unchanged, as the separate library files in _internal\\PySide6 and
_internal\\shiboken6, which you may replace with your own builds of the same
versions. Their source code:
  Qt       https://download.qt.io/official_releases/qt/{qt_minor}/{qt}/
  PySide6  https://code.qt.io/cgit/pyside/pyside-setup.git, tag v{pyside}

Qt holds code from other projects, under their own licences:
  FreeType           the FreeType License. Portions of this software are
                     copyright (c) The FreeType Project (www.freetype.org).
                     All rights reserved.
  HarfBuzz           the MIT License
  libpng             the PNG Reference Library License version 2
  zlib               the zlib License
  PCRE2              the BSD 3-Clause License, University of Cambridge
  double-conversion  the BSD 3-Clause License, the V8 project authors
  libjpeg-turbo      the IJG License, the BSD 3-Clause License and the zlib
                     License. This software is based in part on the work of
                     the Independent JPEG Group.
  libwebp            the BSD 3-Clause License, Google Inc.
  Unicode data       the Unicode License v3, Unicode, Inc.
Qt lists each with its full licence at
  https://doc.qt.io/qt-6/licenses-used-in-qt.html


Python {python}
{python_rule}
The program runs on Python, under the Python Software Foundation License
version 2, with the libraries its Windows build brings (OpenSSL, libffi,
bzip2, xz, zstd and others), each under its licence: licenses\\Python.txt.


PyInstaller
-----------
The program's starter comes from PyInstaller, under the GNU GPL version 2
with an exception that leaves programs built with it under their own terms.


Microsoft Visual C++ runtime
----------------------------
VCRUNTIME140.dll and the MSVCP140 files are Microsoft's, passed on as their
licence allows ("Additional Conditions for this Windows binary build" in
licenses\\Python.txt).
"""


def version() -> str:
    text = (ROOT / "langmod" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def updating() -> str:
    """A line for the Read me on updating in one click, if this build knows where its releases are."""
    text = (ROOT / "langmod" / "core" / "selfupdate.py").read_text(encoding="utf-8")
    if not re.search(r'^REPO = "[^"]+"', text, re.M):
        return ""
    return ("\nWhen a new version is out, the manager says so at the top of its window;\n"
            "\"Update and restart\" puts it in this folder for you.")


def say(text: str) -> None:
    print(text, flush=True)


def build() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is missing: python -m pip install pyinstaller")
    shutil.rmtree(APP, ignore_errors=True)
    say("Building (a minute or two)...")
    done = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                           "--distpath", str(DIST), "--workpath", str(ROOT / "build"), str(ROOT / "langmod.spec")],
                          cwd=ROOT)
    if done.returncode != 0 or not EXE.is_file():
        sys.exit("the build failed")


def _needles() -> list[tuple[str, bytes]]:
    """This machine's paths, as they could appear in a file: either slash, UTF-8 or UTF-16."""
    found = []
    for label, path in (("your user folder", Path.home()), ("the project folder", ROOT)):
        for text in {str(path), str(path).replace("\\", "/")}:
            for enc in ("utf-8", "utf-16-le"):
                found.append((label, text.lower().encode(enc)))
    user = os.environ.get("USERNAME", "")
    if len(user) >= 4:
        found.append(("your user name", f"\\users\\{user}\\".lower().encode()))
        found.append(("your user name", f"/users/{user}/".lower().encode()))
    return found


def check_clean() -> None:
    """Nothing personal in the build: no player data, and no path from this machine."""
    problems = []
    needles = _needles()
    for p in APP.rglob("*"):
        rel = p.relative_to(APP)
        if PERSONAL_NAMES.match(p.name):
            problems.append(f"{rel}: looks like a player's own data")
        if p.name == "__pycache__":
            problems.append(f"{rel}: a Python cache")
        if not p.is_file():
            continue
        data = p.read_bytes().lower()
        for label, needle in needles:
            if needle in data:
                problems.append(f"{rel}: holds {label}")
                break
    if problems:
        sys.exit("The build is not clean:\n  " + "\n  ".join(problems))
    say("Clean: no player data, and no path from this machine.")


#: DLLs that come with the build rather than with Windows, by the start of their names.
BUNDLED = ("qt6", "python3", "shiboken", "pyside", "libcrypto", "libssl", "libffi", "msvcp", "vcruntime")


def check_dependencies() -> None:
    """Every DLL a file in the build imports, of the kinds the build carries, is in it."""
    import pefile                      # comes with PyInstaller
    have = {p.name.lower() for p in APP.rglob("*") if p.is_file()}
    missing: dict[str, set[str]] = {}
    for p in APP.rglob("*"):
        if p.suffix.lower() not in (".dll", ".pyd", ".exe"):
            continue
        pe = pefile.PE(str(p), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                               pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []):
            name = entry.dll.decode("ascii", "replace").lower()
            if name.startswith(BUNDLED) and name not in have:
                missing.setdefault(name, set()).add(p.name)
        pe.close()
    if missing:
        sys.exit("The build is missing DLLs it needs:\n  " + "\n  ".join(
            f"{dll}, for {', '.join(sorted(users))}" for dll, users in sorted(missing.items())))
    # Plugins nothing imports, which Qt loads by name: the window itself, and the picture formats.
    plugins = [f"plugins/{p}" for p in ("platforms/qwindows.dll", "imageformats/qjpeg.dll",
                                        "imageformats/qwebp.dll", "imageformats/qgif.dll", "imageformats/qico.dll")]
    absent = [p for p in plugins if not any(f.as_posix().lower().endswith(p) for f in APP.rglob("*.dll"))]
    # And the words: the manager's catalogs, and Qt's own for its buttons and dialogs.
    words = [f"langmod/resources/locale/{c}.json" for c in ("de", "fr", "pl", "ru", "zh")]
    words += [f"translations/qtbase_{c}.qm" for c in ("de", "fr", "pl", "ru", "zh_cn")]
    have = [f.as_posix().lower() for f in APP.rglob("*") if f.is_file()]
    absent += [w for w in words if not any(h.endswith(w) for h in have)]
    if absent:
        sys.exit("The build is missing: " + ", ".join(absent))
    say("Complete: every DLL the build imports is in it, the plugins Qt loads by name, and every language.")


def smoke_test() -> None:
    """Start the build once, in a throwaway home, both ways it is used."""
    home = Path(tempfile.mkdtemp(prefix="langmod-release-"))
    before = sorted(p.relative_to(APP) for p in APP.rglob("*"))
    try:
        env = {**os.environ, "LANGMOD_HOME": str(home), "LANGMOD_DESKTOP_DIR": str(home / "desktop"),
               "LANGMOD_PROGRAMS_DIR": str(home / "programs"),
               "LANGMOD_SELF_REPO": ""}        # not a look at its releases: the check stays off the network
        env.pop("LANGMOD_THEME", None)
        done = subprocess.run([str(EXE), "list"], env=env, capture_output=True, timeout=60)
        if done.returncode != 0:
            sys.exit(f"'Langmod Manager.exe list' stopped with code {done.returncode}")
        # The window, on screen: Windows's own Qt platform is the one players get.
        env.pop("QT_QPA_PLATFORM", None)
        env["LANGMOD_QUIT_AFTER"] = "6000"
        started = time.monotonic()
        done = subprocess.run([str(EXE)], env=env, timeout=90)
        took = time.monotonic() - started
        log = home / "errors.log"
        if log.is_file():
            sys.exit("The window wrote errors:\n" + log.read_text(encoding="utf-8", errors="replace"))
        if done.returncode != 0 or took < 5:
            sys.exit(f"The window stopped with code {done.returncode} after {took:.1f} s")
        # Once more in Russian: the window says so in errors.log if a catalog of its own or Qt's is missing.
        env.update(LANGMOD_LANGUAGE="ru", LANGMOD_QUIT_AFTER="2500")
        done = subprocess.run([str(EXE)], env=env, timeout=90)
        if log.is_file() or done.returncode != 0:
            sys.exit("In Russian, the window " + (log.read_text(encoding="utf-8", errors="replace")
                                                   if log.is_file() else f"stopped with code {done.returncode}"))
        after = sorted(p.relative_to(APP) for p in APP.rglob("*"))
        if after != before:
            sys.exit("Running it left files in the build: " + ", ".join(map(str, set(after) - set(before))))
        say(f"Started: the command line, the window for {took:.0f} s, and again in Russian, with no errors.")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _text(path: Path, text: str) -> None:
    # Windows line ends, and marked as UTF-8, so every editor shows its Cyrillic and Chinese.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8-sig"))


def small_print(ver: str) -> None:
    """The licence, the third-party notices and the licences they name, beside the program."""
    import PySide6
    from PySide6.QtCore import qVersion
    python = Path(sys.base_prefix) / "LICENSE.txt"
    if not python.is_file():
        sys.exit(f"Python's licence is not at {python}")
    qt, py = qVersion(), sys.version.split()[0]
    heading = f"Qt {qt} and PySide6 {PySide6.__version__}"
    _text(APP / "Third-party notices.txt", NOTICES.format(
        version=ver, qt=qt, qt_minor=".".join(qt.split(".")[:2]), pyside=PySide6.__version__,
        qt_rule="-" * len(heading), python=py, python_rule="-" * len(f"Python {py}")))
    _text(APP / "License.txt", (ROOT / "LICENSE").read_text(encoding="utf-8"))
    for name in ("LGPL-3.0.txt", "GPL-3.0.txt"):
        _text(APP / "licenses" / name, (LICENSES / name).read_text(encoding="utf-8"))
    _text(APP / "licenses" / "Python.txt", python.read_text(encoding="utf-8", errors="replace"))


def package(ver: str) -> Path:
    _text(APP / "Read me.txt", README.format(version=ver, rule="=" * len(f"Langmod Manager {ver}"),
                                             updating=updating()))
    small_print(ver)
    out = DIST / f"Langmod-Manager-{ver}-windows.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(APP.rglob("*")):
            if p.is_file():
                z.write(p, Path(APP.name) / p.relative_to(APP))
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
    absent = [f for f in ("Read me.txt",) + SMALL_PRINT if f"{APP.name}/{f}" not in names]
    if absent:
        sys.exit("The zip is missing: " + ", ".join(absent))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build, check and zip the copy of Langmod Manager to hand round.")
    ap.add_argument("--no-build", action="store_true", help="check and zip the last build again")
    args = ap.parse_args()
    ver = version()
    if not args.no_build:
        build()
    elif not EXE.is_file():
        sys.exit(f"there is no build in {APP}")
    (APP / "Read me.txt").unlink(missing_ok=True)
    for name in SMALL_PRINT:
        (APP / name).unlink(missing_ok=True)
    check_clean()
    check_dependencies()
    smoke_test()
    out = package(ver)
    files = sum(1 for p in APP.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in APP.rglob("*") if p.is_file())
    say(f"\n{out}\n  {out.stat().st_size / 2**20:.1f} MB zipped, {size / 2**20:.0f} MB unzipped, {files} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
