"""Newer versions of Langmod Manager itself, from its GitHub releases.

``REPO`` names where the releases are published; until it is set, nothing is
checked. Run from source, a new version is only told about; the standalone
build replaces itself, in three steps:

1. the release zip is downloaded and checked: its size, GitHub's SHA-256 of it
   when GitHub gives one, and that it is one Langmod Manager folder with the
   program in it;
2. it is unpacked beside the manager's data, and the new copy is started with
   ``_finish-update`` while this one closes;
3. the new copy waits for the old one to end, moves the program folder aside,
   takes its place, carries over any file of the player's that the new build
   does not have, and starts from there. The next start clears the old folder
   away. If anything fails, the old folder goes back and the old copy starts.

Mods and settings are kept in the manager's data folder, not beside the
program, so an update never touches them. ``LANGMOD_SELF_REPO`` and
``LANGMOD_SELF_API`` stand in for ``REPO`` and GitHub's address, for testing.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from . import sources
from .sources import SourceError
from .. import __version__
from ..i18n import tr

REPO = "Anexospire/langmod-manager"     # where its releases are published, on GitHub
FOLDER = "Langmod Manager"             # the one folder in a release zip
EXE = "Langmod Manager.exe"
INSIDES = "_internal"                  # the build's own insides: never carried over from an old copy
CHECK_EVERY = 24 * 60 * 60
_HOSTS = ("github.com", "githubusercontent.com")
_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)(?:[-.]?(a|b|rc)(\d+))?(?:[-.+]?ea)?", re.I)


def repo() -> str:
    return os.environ.get("LANGMOD_SELF_REPO", REPO).strip()


def _api() -> str:
    return os.environ.get("LANGMOD_SELF_API", "https://api.github.com").rstrip("/")


def available() -> bool:
    """Whether this copy has a place to look for new versions of itself."""
    return bool(repo())


def standalone() -> bool:
    """The built program, which can replace itself; run from source, it cannot."""
    return bool(getattr(sys, "frozen", False))


def parse(version: str) -> tuple | None:
    """What orders versions: ``1.0.0rc3ea`` -> (1, 0, 0, 2, 3). A full release sorts after its release
    candidates; "ea" (early access) does not change the order."""
    m = _VERSION.fullmatch((version or "").strip())
    if not m:
        return None
    numbers = tuple(int(x) for x in m.group(1).split("."))
    numbers += (0,) * (3 - len(numbers))
    stage = {"a": 0, "b": 1, "rc": 2}.get((m.group(2) or "").lower(), 3)
    return numbers + (stage, int(m.group(3) or 0))


def _early(version: str) -> bool:
    """An early-access or test build, which is offered test releases too; a full release is not."""
    v = parse(version)
    return v is None or v[-2] < 3 or version.strip().lower().endswith("ea")


@dataclass
class Update:
    version: str
    file_name: str
    url: str
    size: int
    sha256: str                # "" when GitHub does not give one
    page: str
    prerelease: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d) -> "Update | None":
        try:
            return cls(**{k: d[k] for k in ("version", "file_name", "url", "size", "sha256", "page")},
                       prerelease=bool(d.get("prerelease")))
        except (KeyError, TypeError):
            return None


def newer(update: Update | None, current: str = __version__) -> bool:
    a, b = parse(update.version) if update else None, parse(current)
    return a is not None and b is not None and a > b


def due(prefs) -> bool:
    """Time to look again: at most once a day, and only if the player lets it."""
    return available() and prefs.get("self_check") and time.time() - prefs.get("self_last") >= CHECK_EVERY


def record(prefs, update: Update | None) -> None:
    """What a look found (None: nothing newer), kept so the next start knows it without asking again."""
    prefs.lib.settings.setdefault("prefs", {}).update(self_last=time.time(),
                                                      self_latest=update.to_dict() if update else {})
    prefs.lib.save()


def known(prefs) -> Update | None:
    """The newer version the last look found, while it is still newer than this copy."""
    update = Update.from_dict(prefs.get("self_latest"))
    return update if newer(update) else None


def check(current: str = __version__) -> Update | None:
    """The newest release above ``current`` that has a Windows zip, or None. Network only: any thread may."""
    where = repo()
    if not where:
        return None
    data = sources.http.get_json(f"{_api()}/repos/{where}/releases?per_page=30",
                                 {"Accept": "application/vnd.github+json"})
    mine = parse(current)
    best: Update | None = None
    for rel in data if isinstance(data, list) else []:
        if not isinstance(rel, dict) or rel.get("draft") or (rel.get("prerelease") and not _early(current)):
            continue
        tag = str(rel.get("tag_name") or "")
        v = parse(tag)
        if v is None or (mine is not None and v <= mine) or (best is not None and v <= parse(best.version)):
            continue
        asset = next((a for a in rel.get("assets") or []
                      if isinstance(a, dict) and str(a.get("name", "")).lower().endswith("-windows.zip")), None)
        if asset is None:
            continue
        digest = str(asset.get("digest") or "")
        best = Update(tag.lstrip("vV"), str(asset.get("name")), str(asset.get("browser_download_url") or ""),
                      int(asset.get("size") or 0), digest[7:].lower() if digest.lower().startswith("sha256:") else "",
                      str(rel.get("html_url") or f"https://github.com/{where}/releases"), bool(rel.get("prerelease")))
    return best


def _trusted(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if os.environ.get("LANGMOD_SELF_API"):
        test = urllib.parse.urlsplit(_api())
        if (parts.scheme, parts.netloc) == (test.scheme, test.netloc):
            return True
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in _HOSTS)


def download(update: Update, folder: Path, progress=None) -> Path:
    """The release zip, fetched and checked against what GitHub says of it. Network and that one file only."""
    if not _trusted(update.url):
        raise SourceError(tr("the new version is not on GitHub, so it was not downloaded"))
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w.+-]", "_", update.version) or "new"
    path = sources.http.download(update.url, folder / f"langmod-manager-{name}.zip", update.size, progress)
    if update.sha256:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() != update.sha256:
            path.unlink(missing_ok=True)
            raise SourceError(tr("the download is not the file GitHub lists, so it was not installed"))
    return path


def _stray(name: str) -> bool:
    p = PurePosixPath(name)
    return p.is_absolute() or ".." in p.parts or ":" in name or "\\" in name or p.parts[:1] != (FOLDER,)


def unpack(zip_path: Path, into: Path) -> Path:
    """The release unpacked into ``into`` (emptied first); the path of its program folder. Anything but one
    Langmod Manager folder with the program in it is refused. The zip goes either way."""
    shutil.rmtree(into, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            if f"{FOLDER}/{EXE}" not in names or any(_stray(n) for n in names):
                raise SourceError(tr("the download is not a Langmod Manager release, so it was not installed"))
            z.extractall(into)
    except (zipfile.BadZipFile, OSError, EOFError, ValueError, NotImplementedError, zlib.error) as exc:
        shutil.rmtree(into, ignore_errors=True)
        raise SourceError(tr("the download could not be unpacked ({error}); try again", error=exc)) from None
    finally:
        zip_path.unlink(missing_ok=True)
    return into / FOLDER


def _detached() -> int:
    if sys.platform != "win32":
        return 0
    return subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP


def begin(staged: Path, target: Path) -> None:
    """Start the new copy, which takes this one's place once it has closed. The caller closes next."""
    subprocess.Popen([str(staged / EXE), "_finish-update", str(os.getpid()), str(target)], cwd=str(staged),
                     close_fds=True, creationflags=_detached())


def _wait(pid: int, seconds: float) -> None:
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)    # SYNCHRONIZE
        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(handle, int(seconds * 1000))
            ctypes.windll.kernel32.CloseHandle(handle)
        return
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def _start(folder: Path) -> None:
    exe = folder / EXE
    if exe.is_file():
        subprocess.Popen([str(exe)], cwd=str(folder), close_fds=True, creationflags=_detached())


def _tell(text: str) -> None:
    print(text, file=sys.stderr)
    if sys.platform == "win32" and standalone():
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Langmod Manager", 0x30)


def finish(pid: int, target: Path, staged: Path | None = None, wait: float = 60.0, tries: int = 40) -> int:
    """In the new copy: wait for the old one to close, take its place, and start from there. 0 when done."""
    staged = staged or Path(sys.executable).parent
    _wait(pid, wait)
    old = target.with_name(target.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    for attempt in range(tries):
        try:
            os.replace(target, old)
            break
        except OSError:
            time.sleep(0.5)                     # another copy (Steam's, a shortcut's) may still be closing
    else:
        _tell(tr("Langmod Manager could not replace {folder}: another copy of it may still be running, or the "
                 "folder may need administrator rights. The version you had starts again.", folder=target))
        _start(target)
        return 1
    try:
        shutil.copytree(staged, target)
        # What the player keeps beside the program stays; the old build's own insides do not.
        for p in old.rglob("*"):
            rel = p.relative_to(old)
            if p.is_file() and rel.parts[0] != INSIDES and not (target / rel).exists():
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, target / rel)
    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        try:
            os.replace(old, target)
        except OSError:
            pass
        _tell(tr("Langmod Manager could not be updated ({error}). The version you had starts again.", error=exc))
        _start(target)
        return 1
    _start(target)
    return 0


def tidy(program: Path, home: Path) -> None:
    """After an update: the old program folder and the unpacked download go. What cannot be removed yet
    (the copy that did the swap may still be closing) goes next time."""
    shutil.rmtree(program.with_name(program.name + ".old"), ignore_errors=True)
    shutil.rmtree(home / "update", ignore_errors=True)
