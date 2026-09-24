"""Find War Thunder installs and read the language data the game ships.

The base language tables live in ``lang.vromfs.bin`` next to the game, and
the tables the servers hand out for events (``%langRegional``) in
``cache/binary.<version>/regional-lang.vromfs.bin``. With the "custom
localization" switch on, the game reads ``lang/localization.blk`` from disk
instead of its own copy, and resolves every ``%lang/<file>`` it lists
against the ``lang`` folder first and the archive second.

Reading every table the game loads takes about a second; the result, one
language's text for every string, is kept in the manager's cache folder
for each game version, so only the first start after a game update pays it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import blk
from .langcsv import LangTable, read_table
from .vromf import Vromf

LANG_ARCHIVE = "lang.vromfs.bin"
REGIONAL_ARCHIVE = "regional-lang.vromfs.bin"
TEXTS_FORMAT = 1           # raise when what the cache holds changes shape
STEAM_APP = "236390"       # War Thunder on Steam
KEEP_TEXTS = 6             # cached languages and game versions kept; older ones are cleared


@dataclass(frozen=True)
class GameInstall:
    root: Path
    source: str            # "steam", "launcher" or "custom"
    channel: str = "live"  # "live" or "dev"

    @property
    def label(self) -> str:
        where = {"steam": "Steam", "launcher": "Gaijin launcher"}.get(self.source, "Folder")
        return f"{'Dev server' if self.channel == 'dev' else 'Live'} · {where}"

    @property
    def lang_dir(self) -> Path:
        return self.root / "lang"

    @property
    def config_path(self) -> Path:
        return self.root / "config.blk"


def is_game_folder(path: Path) -> bool:
    return (path / LANG_ARCHIVE).is_file()


def _channel(root: Path) -> str:
    try:
        text = (root / "config.blk").read_text("utf-8", errors="ignore")
    except OSError:
        text = ""
    m = re.search(r'curCircuit\s*:\s*t\s*=\s*"([^"]*)"', text)
    if m and "dev" in m.group(1).lower():
        return "dev"
    return "dev" if root.name.lower().replace(" ", "").endswith("dev") else "live"


def steam_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for value in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(Path(winreg.QueryValueEx(k, value)[0]))
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        roots.append(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam")
    else:
        roots += [Path.home() / ".steam" / "steam", Path.home() / ".local" / "share" / "Steam"]
    return roots


def _steam_libraries(steam: Path) -> list[Path]:
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text("utf-8", errors="replace")
    except OSError:
        return libs
    libs += [Path(m.group(1).replace("\\\\", "\\")) for m in re.finditer(r'"path"\s+"([^"]+)"', text)]
    return libs


def find_installs() -> list[GameInstall]:
    """Every War Thunder install this machine seems to have, live and dev."""
    found: list[GameInstall] = []
    seen: set[str] = set()

    def add(path: Path, source: str) -> None:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            return
        if key in seen or not is_game_folder(path):
            return
        seen.add(key)
        found.append(GameInstall(path.resolve(), source, _channel(path)))

    for steam in steam_roots():
        for lib in _steam_libraries(steam):
            add(lib / "steamapps" / "common" / "War Thunder", "steam")
    if sys.platform == "win32":
        drives = [f"{d}:/" for d in "CDEFGH" if Path(f"{d}:/").exists()]
        local = os.environ.get("LOCALAPPDATA")
        bases = ([Path(local)] if local else []) + [Path(d) for d in drives] + [Path(d) / "Games" for d in drives]
        for base in bases:
            for name in ("WarThunder", "War Thunder", "WarThunderDev", "War Thunder Dev"):
                add(base / name, "launcher")
    return found


def install_at(path: str | Path) -> GameInstall | None:
    p = Path(path)
    return GameInstall(p.resolve(), "custom", _channel(p)) if is_game_folder(p) else None


def game_running() -> bool:
    """True while the game itself (``aces.exe``) is running."""
    if sys.platform != "win32":
        return False
    try:
        # Bytes, not text: the console speaks the system's code page, not UTF-8.
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq aces.exe", "/NH"], capture_output=True,
                             timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return b"aces.exe" in (out.stdout or b"").lower()


def started_by_steam() -> bool:
    """Whether Steam started this process for War Thunder, as it starts a game's launch options: it
    marks what it starts with the game's id."""
    return STEAM_APP in (os.environ.get("SteamGameId", ""), os.environ.get("SteamAppId", ""))


def start_game(install: GameInstall) -> str:
    """Start War Thunder the way it is installed; returns what was started. Started by Steam itself
    (launch options that lack %command%), going back through Steam would only start this again."""
    if install.source == "steam" and not started_by_steam():
        target = f"steam://rungameid/{STEAM_APP}"
    else:
        launcher = install.root / "launcher.exe"
        target = str(launcher if launcher.is_file() else install.root / "win64" / "aces.exe")
    if sys.platform == "win32":
        os.startfile(target)  # the game's own launcher, or Steam's URL for it
    else:
        subprocess.Popen(["xdg-open", target])
    return target


def _read_texts(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text("utf-8"))
        texts = raw["texts"] if raw.get("format") == TEXTS_FORMAT else None
    except (OSError, ValueError, KeyError, AttributeError):
        return None
    if not isinstance(texts, dict):
        return None
    try:
        os.utime(path)                      # used: it stays among the ones kept
    except OSError:
        pass
    return texts


def _write_texts(path: Path, texts: dict) -> None:
    """Keep one language's text for this game version; a failure only costs the next start time."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"format": TEXTS_FORMAT, "texts": texts}, ensure_ascii=False,
                                  separators=(",", ":")), "utf-8")
        os.replace(tmp, path)
        old = sorted(path.parent.glob("texts-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in old[KEEP_TEXTS:]:
            p.unlink(missing_ok=True)
    except OSError:
        pass


def lang_stamp(root: Path) -> str:
    """Changes whenever the game's language archive does: version, size and time."""
    p = root / LANG_ARCHIVE
    try:
        st = p.stat()
    except OSError:
        return ""
    try:
        with open(p, "rb") as fh:
            head = fh.read(24)
        v = head[20:24] if head[:4] == b"VRFx" else b""
        version = ".".join(str(b) for b in reversed(v)) if v else "?"
    except OSError:
        version = "?"
    return f"{version}:{st.st_size}:{int(st.st_mtime)}"


def _regional_archive(root: Path) -> Path | None:
    cache = root / "cache"
    try:
        dirs = sorted((d for d in cache.iterdir() if d.is_dir() and d.name.startswith("binary.")),
                      key=lambda d: [int(x) if x.isdigit() else 0 for x in d.name.split(".")[1:]])
    except OSError:
        return None
    for d in reversed(dirs):
        if (d / REGIONAL_ARCHIVE).is_file():
            return d / REGIONAL_ARCHIVE
    return None


class GameLang:
    """The language data of one install, as the game ships it."""

    def __init__(self, root: str | Path, cache_dir: str | Path | None = None):
        self.root = Path(root)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        # Taken before reading, so it names the archive this data came from even if the game
        # updates later while this object is still in use.
        self._stamp = lang_stamp(self.root)
        self.archive = Vromf(self.root / LANG_ARCHIVE)
        regional = _regional_archive(self.root)
        self.regional = None
        self._regional_stamp = ""
        if regional is not None:
            try:
                st = regional.stat()
                self._regional_stamp = f"{regional.parent.name}:{st.st_size}:{int(st.st_mtime)}"
                self.regional = Vromf(regional)
            except Exception:
                self.regional = None
        data = self.archive.read("lang/localization.blk")
        self.localization, _ = blk.parse_any(data, self.archive.name_map, self.archive.zstd_dict)
        self._tables: dict[str, LangTable] = {}
        self._texts: dict[str, dict] = {}

    @property
    def version(self) -> str:
        return self.archive.version_str

    @property
    def stamp(self) -> str:
        """The archive's stamp when it was read (see :func:`lang_stamp`)."""
        return self._stamp

    def base_files(self) -> list[str]:
        """File names under ``lang/`` in the archive, CSV only."""
        return [n.split("/", 1)[1] for n in self.archive.names()
                if n.startswith("lang/") and n.lower().endswith(".csv")]

    def is_base_file(self, name: str) -> bool:
        return self.archive.get("lang/" + name) is not None

    def regional_files(self) -> list[str]:
        if self.regional is None:
            return []
        return [n.split("/", 1)[1] for n in self.regional.names()
                if n.startswith("lang/") and n.lower().endswith(".csv")]

    def own_table(self, name: str) -> str | None:
        """How the game refers to its own table of this file name (``%lang/units.csv``,
        ``%langRegional/regional_decals.csv``); None if it has none."""
        if self.is_base_file(name):
            return "%lang/" + name
        if self.regional is not None and self.regional.get("lang/" + name) is not None:
            return "%langRegional/" + name
        return None

    def loc_table(self) -> list[str]:
        """The ``file`` entries of ``locTable``, in load order, as written."""
        block = self.localization.block("locTable")
        return list(block.values("file")) if block else []

    def resolve(self, ref: str) -> bytes | None:
        """The bytes a ``%lang/...`` or ``%langRegional/...`` reference points at."""
        if ref.startswith("%langRegional/"):
            if self.regional is None:
                return None
            e = self.regional.get("lang/" + ref.split("/", 1)[1])
            return self.regional.read(e.name) if e else None
        if ref.startswith("%lang/"):
            e = self.archive.get("lang/" + ref.split("/", 1)[1])
            return self.archive.read(e.name) if e else None
        return None

    def table(self, ref: str) -> LangTable | None:
        if ref not in self._tables:
            data = self.resolve(ref)
            if data is None:
                return None
            self._tables[ref] = read_table(data)
        return self._tables[ref]

    def refs(self) -> list[str]:
        """Every table the game loads, in its order: ``locTable``, then the regional tables."""
        return list(self.loc_table()) + [v for v in self.localization.values("regional") if isinstance(v, str)]

    def texts(self, language: str = "English") -> dict[str, tuple[str, str]]:
        """Every key the game loads, with its text and the table it comes from (``%lang/units.csv``).

        Mirrors the game's order: ``locTable`` first, then the regional tables,
        a later file overriding an earlier one. Read once per language, from the
        cache when this game version was read before.
        """
        if language in self._texts:
            return self._texts[language]
        path = self._cache_path(language)
        out = _read_texts(path) if path is not None else None
        if out is None:
            out = {}
            for ref in self.refs():
                t = self.table(ref)
                if t is None:
                    continue
                for key, text in t.texts(language).items():
                    out[key] = (text, ref)
            if path is not None:
                _write_texts(path, out)
        self._texts[language] = out
        return out

    def _cache_path(self, language: str) -> Path | None:
        if self.cache_dir is None or not self._stamp:
            return None
        what = f"{TEXTS_FORMAT}|{str(self.root).lower()}|{self._stamp}|{self._regional_stamp}|{language}"
        return self.cache_dir / f"texts-{hashlib.sha1(what.encode('utf-8')).hexdigest()[:20]}.json"

    def row(self, key: str) -> tuple[list[str], list[str]] | None:
        """The game's own row for a key, every language in it, as its table has it: (columns, row)."""
        known = next((t[key][1] for t in self._texts.values() if key in t), None)
        for ref in [known] if known else reversed(self.refs()):
            t = self.table(ref)
            found = t.row_of(key) if t is not None else None
            if found is not None:
                return t.columns, found
        return None
