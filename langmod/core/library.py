"""The mods a player has added, kept in the manager's own folder.

Each mod is a copy of its language files under ``mods/<id>/lang/``; the
order, the on/off switches and what came from where live in
``library.json``. The game folder is only ever written by :mod:`.install`.

Adding a package works out on its own whether it is

* a new mod,
* a newer version of a mod already here (same file prefix or same name),
  which replaces the old files but keeps any the player edited and any
  optional modules added since, or
* an optional module for a mod already here (no ``localization.blk`` of its
  own, files named like the mod's or listed in its load list), which is
  merged into that mod so the mod's own list decides where it loads.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

from . import blk, sevenzip
from ..i18n import join, ntr, tr

LIST_NAME = "localization.blk"
MAX_FILE = 200 * 1024 * 1024
MAX_WALK = 50_000          # files looked at in a folder before it is taken for not being a mod
PERSONAL_ID = "my-changes"
PERSONAL_FILE = "zz_my_changes.csv"
REMOVED = "removed"        # where removed mods wait, in case the removal is undone
KEEP_REMOVED_DAYS = 30
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]|[. ]$')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.I)


def usable_name(name: str) -> bool:
    """A name Windows can store as a file of its own. ``AB:units.csv`` is not: it would land as a
    hidden stream of a file called ``AB``."""
    return bool(name) and not _BAD_NAME.search(name) and not _RESERVED.match(name)


def data_dir() -> Path:
    env = os.environ.get("LANGMOD_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Langmod Manager"
    return Path.home() / ".local" / "share" / "langmod-manager"


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "mod"


class LibraryError(Exception):
    pass


# -- packages ---------------------------------------------------------------

@dataclass
class Package:
    """Language files found in a zip, a folder or a single CSV."""
    source: Path
    label: str                                   # the zip or folder name, cleaned up
    files: dict[str, bytes]                      # file name -> contents
    stamp: str = ""                              # newest file date, "2026-09-17"
    readme: str = ""
    notes: list[str] = field(default_factory=list)
    # Optional modules that came with it, in folders of their own: folder name -> its files. Only
    # files the package's load list names count (WTHLM's Packages/Package_Farsi and the like).
    modules: dict[str, dict[str, bytes]] = field(default_factory=dict)
    readme_name: str = ""                        # the read me's file name ("readme.txt"), if it has one

    @property
    def has_list(self) -> bool:
        return any(n.lower() == LIST_NAME for n in self.files)

    @property
    def csv_names(self) -> list[str]:
        return sorted((n for n in self.files if n.lower().endswith(".csv")), key=str.lower)

    def list_text(self) -> str | None:
        for n, data in self.files.items():
            if n.lower() == LIST_NAME:
                return data.decode("utf-8-sig", "replace")
        return None


_NEXUS = re.compile(r"^(?P<name>.+?)-\d+-(?P<ver>\d+(?:-\d+)*)-\d{9,}$")
_GENERIC = re.compile(r"^(lang|langmod|mod|files?|new folder)(\s*\(\d+\))?$", re.I)


#: A version at either end of a name, set off by a space, _ or -: ``Cool Mod v2.3``,
#: ``WTHLM_1.19.00``, ``1.3.09 LOP``.
_VERSIONED = (re.compile(r"^(?P<name>.*?\S)[\s_-]+[vV](?:ersion)?\.?\s*(?P<ver>\d+(?:\.\d+)*)$"),
              re.compile(r"^(?P<name>.*?\S)[\s_-]+(?P<ver>\d+(?:\.\d+)+)$"),
              re.compile(r"^[vV]?(?P<ver>\d+(?:\.\d+)+)[\s_-]+(?P<name>\S.*)$"))


def clean_label(name: str) -> tuple[str, str]:
    """``IFN1 Re-Name Mod-2162-74-1-1773898746`` -> (``IFN1 Re-Name Mod``, ``74.1``); a version at
    either end comes off the name, ``WTHLM_1.19.00`` -> (``WTHLM``, ``1.19.00``), so the name stays the
    same from one version to the next."""
    stem = re.sub(r"\.(zip|7z|csv)$", "", name, flags=re.I)
    stem = re.sub(r"\s*\(\d+\)$", "", stem).strip()
    m = _NEXUS.match(stem)
    if m:
        return m.group("name").strip(), m.group("ver").replace("-", ".")
    for pattern in _VERSIONED:
        m = pattern.match(stem)
        if m:
            return m.group("name"), m.group("ver")
    v = re.search(r"\b[vV](\d+(?:\.\d+)*)\b", stem)
    return stem, v.group(1) if v else ""


def _unversioned(mod: Mod) -> Mod:
    """A name from before versions came off names (``WTHLM_1.19.00``) loses its version; the mod
    keeps it as its version if it has none."""
    name, version = clean_label(mod.name)
    if not mod.personal and version and name != mod.name and not _GENERIC.match(name):
        mod.name = name
        mod.version = mod.version or version
    return mod


def _pick_dir(paths: list[str]) -> tuple[str, list[str]]:
    """The folder of a package that holds its language files."""
    groups: dict[str, list[str]] = {}
    for p in paths:
        parent, _, name = p.replace("\\", "/").rpartition("/")
        low = name.lower()
        if low.endswith(".csv") or low == LIST_NAME:
            groups.setdefault(parent, []).append(p)
    if not groups:
        raise LibraryError(tr("There are no language files (.csv) in it."))

    def rank(d: str) -> tuple:
        names = [x.rpartition("/")[2].lower() for x in groups[d]]
        return (LIST_NAME in names, d.rpartition("/")[2].lower() == "lang", len(names), -d.count("/"))

    best = max(groups, key=rank)
    others = [d or "(top level)" for d in groups if d != best]
    return best, others


def _walk(src: Path) -> list[str]:
    """Files under a folder, relative; refuses a folder far too big to be one mod (a whole drive)."""
    out = []
    for p in src.rglob("*"):
        if p.is_file():
            out.append(p.relative_to(src).as_posix())
            if len(out) > MAX_WALK:
                raise LibraryError(tr("{folder} holds too many files to be a language mod: pick the mod's own "
                                      "folder.", folder=src))
    return out


def _keep(files: dict[str, bytes], name: str, notes: list[str]) -> bool:
    """Whether a file can join the package: a usable name, and not one already there in other case."""
    if not usable_name(name):
        notes.append(tr("Skipped {name}: Windows cannot store a file of that name.", name=name))
        return False
    if any(n.lower() == name.lower() for n in files):
        notes.append(tr("Skipped {name}: the package already has a file of that name, in other letter case.",
                        name=name))
        return False
    return True


@dataclass
class _Member:
    name: str
    size: int
    mtime: float
    read: Callable[[], bytes]


@contextmanager
def _archive(src: Path):
    """The files in a zip or a 7z archive, whichever it is by its first bytes, whatever its name says."""
    try:
        with open(src, "rb") as fh:
            head = fh.read(8)
    except OSError as exc:
        raise LibraryError(tr("{file} could not be read: {error}", file=src.name, error=exc)) from None
    if head.startswith(b"Rar!"):
        raise LibraryError(tr("{file} is a RAR archive, which the manager cannot open: extract it first, then add "
                              "the folder.", file=src.name))
    if head.startswith(sevenzip.SIGNATURE):
        try:
            z = sevenzip.SevenZip(src)
        except sevenzip.SevenZipError as exc:
            raise LibraryError(tr("{file} could not be opened: {error}", file=src.name, error=exc)) from None

        def reader(entry):
            def read() -> bytes:
                try:
                    return z.read(entry)
                except sevenzip.SevenZipError as exc:
                    raise LibraryError(tr("{file} could not be opened: {error}", file=src.name, error=exc)) from None
            return read

        yield [_Member(e.name, e.size, e.mtime, reader(e)) for e in z.entries if not e.is_dir]
        return
    try:
        zf = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        raise LibraryError(tr("{file} is not a zip or 7z archive: extract it first.", file=src.name)) from None
    except (OSError, EOFError, ValueError, NotImplementedError) as exc:
        raise LibraryError(tr("{file} is damaged ({error}): download it again.", file=src.name, error=exc)) from None

    def zread(info):
        def read() -> bytes:
            try:
                return zf.read(info)
            except RuntimeError:            # zipfile's way of saying it needs a password
                raise LibraryError(tr("{file} is password-protected: extract it first, then add the folder.",
                                      file=src.name)) from None
            # A broken deflate stream (zlib.error), or a header saying it needs a zip version or
            # method that does not exist (NotImplementedError): the download is damaged.
            except (zipfile.BadZipFile, OSError, EOFError, ValueError, NotImplementedError, zlib.error) as exc:
                raise LibraryError(tr("{file} is damaged ({error}): download it again.", file=src.name,
                                      error=exc)) from None
        return read

    with zf:
        yield [_Member(i.filename, i.file_size, time.mktime(i.date_time + (0, 0, -1)), zread(i))
               for i in zf.infolist() if not i.is_dir()]


_README = re.compile(r"read[ _.-]?me(\.(txt|md|markdown|text))?", re.I)
MAX_README = 1_000_000


def _pick_readme(split: list, chosen: str):
    """The package's read me: a text file called readme, the one nearest the mod's own folder (beside it, or
    in a folder above it) first, so a module's own read me does not stand in for the mod's."""
    def depth(folder: str) -> int:
        return len(folder.split("/")) if folder else 0

    def rank(item) -> tuple:
        _m, parent, _name = item
        above = parent == chosen or not parent or chosen.startswith(parent + "/")
        return (not above, depth(chosen) - depth(parent) if above else depth(parent))

    found = [x for x in split if _README.fullmatch(x[2]) and x[0].size < MAX_README and usable_name(x[2])]
    return min(found, key=rank) if found else None


def _module_label(parent: str) -> str:
    label = parent.rpartition("/")[2] or "extra"
    return f"{label} (module)" if label in ("main", "yours") else label


def read_package(path: str | Path, at: str | None = None) -> Package:
    """The language files of a zip, 7z, folder or single CSV. ``at`` names the folder inside that holds
    the mod ("" for the top) instead of working it out."""
    src = Path(path)
    if not src.exists():
        raise LibraryError(tr("{path} does not exist.", path=src))
    files: dict[str, bytes] = {}
    modules: dict[str, dict[str, bytes]] = {}
    readme = readme_name = ""
    newest = 0.0
    notes: list[str] = []
    if src.is_file() and src.suffix.lower() == ".csv":
        files[src.name] = src.read_bytes()
        newest = src.stat().st_mtime
        label, _version = clean_label(src.name)
        return Package(src, label, files, time.strftime("%Y-%m-%d", time.localtime(newest)), "", notes)

    def use(members: list[_Member]) -> list[str]:
        nonlocal readme, readme_name, newest
        chosen, others = _pick_dir([m.name for m in members])
        if at is not None:
            chosen = at
        split = [(m, *m.name.replace("\\", "/").rpartition("/")[::2]) for m in members]
        listing = next((m for m, parent, name in split if parent == chosen and name.lower() == LIST_NAME), None)
        named = set()
        if listing is not None and listing.size <= MAX_FILE:
            named = {n.rsplit("/", 1)[-1].lower()
                     for n in listed_files(listing.read().decode("utf-8-sig", "replace"))}
        doc = _pick_readme(split, chosen)
        if doc is not None:
            readme, readme_name = doc[0].read().decode("utf-8-sig", "replace"), doc[2]
        for m, parent, name in split:
            low = name.lower()
            if parent != chosen or not (low.endswith(".csv") or low == LIST_NAME):
                continue
            if m.size > MAX_FILE:
                notes.append(tr("Skipped {name}: it is too large.", name=name))
                continue
            if not _keep(files, name, notes):
                continue
            files[name] = m.read()
            newest = max(newest, m.mtime)
        # Modules in folders of their own, for the files the mod's list names; the rest is only mentioned.
        seen = {n.lower() for n in files}
        loose = set()
        for m, parent, name in split:
            low = name.lower()
            if parent == chosen or not low.endswith(".csv"):
                continue
            if low not in named or m.size > MAX_FILE or not usable_name(name):
                loose.add(parent or "(top level)")
            elif low in seen:
                notes.append(tr("Skipped {name} in {folder}: another file of that name came first.",
                                name=name, folder=parent))
            else:
                seen.add(low)
                modules.setdefault(_module_label(parent), {})[name] = m.read()
        return [d for d in others if d in loose]

    if src.is_file():
        with _archive(src) as members:
            others = use(members)
    else:
        others = use([_Member(rel, (src / rel).stat().st_size, (src / rel).stat().st_mtime,
                              (lambda p=src / rel: p.read_bytes())) for rel in _walk(src)])
    if not files:
        raise LibraryError(tr("None of its language files could be used: {why}", why=" ".join(notes)))
    if others:
        notes.append(tr("It also has language files in {folders}: add that folder on its own if it is a separate "
                        "module.", folders=join(others[:4])))
    label, _version = clean_label(src.name)
    stamp = time.strftime("%Y-%m-%d", time.localtime(newest)) if newest else ""
    return Package(src, label, files, stamp, readme, notes, modules, readme_name)


def file_prefix(names: list[str]) -> str:
    """Shared start of the file names, up to an underscore: ``IFN1_``."""
    csvs = [n for n in names if n.lower().endswith(".csv")]
    if len(csvs) < 2:
        m = re.match(r"^([A-Za-z0-9]+_)", csvs[0]) if csvs else None
        return m.group(1) if m else ""
    common = os.path.commonprefix([n.lower() for n in csvs])
    cut = common.rfind("_")
    return csvs[0][:cut + 1] if cut >= 2 else ""


def listed_files(list_text: str) -> list[str]:
    """``%lang/<name>`` entries of a load list's ``locTable``, in order."""
    root, _ = blk.parse_text(list_text)
    table = root.block("locTable")
    if table is None:
        return []
    return [v.split("/", 1)[1] for v in table.values("file") if isinstance(v, str) and v.startswith("%lang/")]


# -- the library -------------------------------------------------------------

@dataclass
class ModFile:
    name: str
    origin: str          # "main", or the label of the module package it came with
    sha1: str            # the copy held now
    shipped: str         # as it arrived; differs once the player edits it

    @property
    def edited(self) -> bool:
        return self.sha1 != self.shipped


@dataclass
class Mod:
    id: str
    name: str
    version: str = ""
    stamp: str = ""
    source: str = ""
    added: str = ""
    updated: str = ""
    enabled: bool = True
    prefix: str = ""
    files: dict[str, ModFile] = field(default_factory=dict)
    modules: list[str] = field(default_factory=list)
    off: list[str] = field(default_factory=list)          # modules switched off: their files do not load
    bundled: list[str] = field(default_factory=list)      # modules that come in the mod's own download
    readme: str = ""                                      # its read me's file name, kept beside its files
    notes: list[str] = field(default_factory=list)
    personal: bool = False
    follow: dict | None = None     # where new versions come from (a sources.Source as a dict)
    installed_key: str = ""        # which release of that source these files are, once known
    latest: dict | None = None     # the newest release seen at the last check
    checked: float = 0.0           # when that was
    check_error: str = ""          # why the last check failed, if it did

    @property
    def has_list(self) -> bool:
        return any(n.lower() == LIST_NAME for n in self.files)

    @property
    def csv_names(self) -> list[str]:
        return sorted((n for n in self.files if n.lower().endswith(".csv")), key=str.lower)

    @property
    def csv_on(self) -> list[str]:
        """The language files that load: all but those of modules switched off."""
        return [n for n in self.csv_names if self.files[n].origin not in self.off]

    def module_files(self, label: str) -> list[str]:
        return [n for n in self.csv_names if self.files[n].origin == label]

    def module_states(self) -> dict[str, bool]:
        """Each module, and whether it is on."""
        return {label: label not in self.off for label in self.modules}

    def set_module_states(self, states) -> None:
        """Switch modules as ``states`` says; one it does not name stays as it is."""
        if isinstance(states, dict):
            self.off = [m for m in self.modules
                        if (not states[m] if isinstance(states.get(m), bool) else m in self.off)]

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}".strip() if self.version else self.name


@dataclass
class ImportResult:
    mod: Mod
    action: str               # "added", "updated" or "module"
    notes: list[str]


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _mod_from(raw: dict) -> Mod | None:
    """A mod as ``library.json`` keeps it. Fields this version does not know (from a newer one)
    are passed over; an entry it cannot make sense of at all gives None."""
    known = {f.name for f in fields(Mod)}
    known_file = {f.name for f in fields(ModFile)}
    try:
        files = {n: ModFile(**{k: v for k, v in f.items() if k in known_file})
                 for n, f in (raw.get("files") or {}).items()}
        return Mod(files=files, **{k: v for k, v in raw.items() if k in known and k != "files"})
    except (TypeError, AttributeError):
        return None


class Library:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else data_dir()
        self.mods: list[Mod] = []
        self.settings: dict = {}
        self.load()

    # -- storage -----------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.root / "library.json"

    def mod_dir(self, mod: Mod | str) -> Path:
        return self.root / "mods" / (mod if isinstance(mod, str) else mod.id) / "lang"

    def file_path(self, mod: Mod, name: str) -> Path:
        return self.mod_dir(mod) / name

    def readme_path(self, mod: Mod) -> Path | None:
        """The mod's read me, as its download had it; None if it came without one."""
        path = self.mod_dir(mod).parent / mod.readme if mod.readme else None
        return path if path is not None and path.is_file() else None

    def _keep_readme(self, mod: Mod, pkg: Package) -> None:
        """Beside the mod's language files, not among them: it never goes into the game."""
        if not pkg.readme_name:
            return                                   # a version without one keeps the one it had
        old = self.readme_path(mod)
        if old is not None:
            old.unlink(missing_ok=True)
        path = self.mod_dir(mod).parent / pkg.readme_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pkg.readme.encode("utf-8"))
        mod.readme = pkg.readme_name

    def read_file(self, mod: Mod, name: str) -> bytes:
        return self.file_path(mod, name).read_bytes()

    def load(self) -> None:
        """Read ``library.json``. A damaged one is set aside, not overwritten by the next save,
        and fields this version does not know (from a newer one) are passed over."""
        self.load_problem = ""
        try:
            state = json.loads(self.state_path.read_text("utf-8"))
            if not isinstance(state, dict):
                raise ValueError("not a library")
        except OSError:
            state = {}
        except ValueError as exc:
            kept = self.state_path.with_name(f"library.broken-{time.strftime('%Y%m%d-%H%M%S')}.json")
            try:
                os.replace(self.state_path, kept)
            except OSError:
                kept = self.state_path
            self.load_problem = tr("library.json could not be read ({error}), so the list of mods starts empty. "
                                   "The damaged file is kept as {file}.", error=exc, file=kept.name)
            state = {}
        self.settings = state.get("settings", {}) if isinstance(state.get("settings"), dict) else {}
        self.mods = []
        for m in state.get("mods", []):
            mod = _mod_from(m)
            if mod is not None:          # one entry this version cannot make sense of; the rest still load
                self.mods.append(_unversioned(mod))
        self._seen = self._on_disk()

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._keep_profile()
        state = {"version": 1, "settings": self.settings, "mods": [asdict(m) for m in self.mods]}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.state_path)
        self._seen = self._on_disk()

    def _on_disk(self) -> tuple:
        try:
            st = self.state_path.stat()
        except OSError:
            return ()
        return st.st_mtime_ns, st.st_size

    def changed_elsewhere(self) -> bool:
        """Whether another copy of the manager wrote ``library.json`` since this one last read or wrote
        it: Steam's launch options or the War Thunder shortcut, while the window is open."""
        return self._on_disk() != self._seen

    # -- profiles --------------------------------------------------------

    def snapshot(self) -> dict:
        """What a profile holds: the order, which mods and modules are on, and the picks."""
        picks = self.settings.get("picks")
        return {"order": [m.id for m in self.mods], "enabled": [m.id for m in self.mods if m.enabled],
                "picks": dict(picks) if isinstance(picks, dict) else {},
                "modules": {m.id: m.module_states() for m in self.mods if m.modules}}

    def _keep_profile(self) -> None:
        """The profile in use follows every change, so switching away and back finds it as it was left."""
        store = self.settings.get("profiles")
        active = self.settings.get("profile")
        if isinstance(store, dict) and isinstance(active, str) and isinstance(store.get(active), dict):
            store[active] = {**store[active], **self.snapshot()}

    # -- lookup ------------------------------------------------------------

    def get(self, mod_id: str) -> Mod | None:
        return next((m for m in self.mods if m.id == mod_id), None)

    def enabled(self) -> list[Mod]:
        return [m for m in self.mods if m.enabled]

    def _new_id(self, name: str) -> str:
        base = slug(name)
        taken = {m.id for m in self.mods}
        mid, n = base, 2
        while mid in taken:
            mid, n = f"{base}-{n}", n + 1
        return mid

    def match(self, pkg: Package) -> tuple[Mod | None, str]:
        """The mod a package belongs to, and how: ``update``, ``module`` or ``new``."""
        prefix = file_prefix(list(pkg.files))
        names = {n.lower() for n in pkg.csv_names}
        for mod in self.mods:
            if mod.personal:
                continue
            same_prefix = bool(prefix) and len(prefix) >= 3 and prefix.lower() == mod.prefix.lower()
            same_name = slug(pkg.label) == slug(mod.name) and not _GENERIC.match(pkg.label)
            if pkg.has_list and (same_prefix or same_name):
                return mod, "update"
            if not pkg.has_list:
                wanted = set()
                if mod.has_list:
                    wanted = {n.lower() for n in listed_files(self.read_file(mod, self._list_name(mod)).decode("utf-8-sig", "replace"))}
                    wanted |= {n.rsplit("/", 1)[-1] for n in wanted}      # packages the list names in subfolders
                if names and (names <= wanted or same_prefix):
                    return mod, "module"
                if same_name:
                    return mod, "update"
        return None, "new"

    @staticmethod
    def _list_name(mod: Mod) -> str:
        return next(n for n in mod.files if n.lower() == LIST_NAME)

    # -- changes -----------------------------------------------------------

    def add(self, path: str | Path, into: str | None = None, how: str | None = None) -> ImportResult:
        """Add a zip, folder or CSV. ``into``/``how`` force the target mod and mode."""
        return self.add_package(read_package(path), into, how)

    def add_package(self, pkg: Package, into: str | None = None, how: str | None = None, first: bool = False,
                    modules_on: bool = False) -> ImportResult:
        """``first``: a new mod goes to the top of the order, where every other one wins over it.
        ``modules_on``: modules that came with it load straight away (they were installed already);
        otherwise they wait to be switched on, as a mod's optional extras do."""
        if into:
            mod = self.get(into)
            if mod is None:
                raise LibraryError(tr("There is no mod {mod}.", mod=repr(into)))
            action = how or ("update" if pkg.has_list else "module")
        else:
            mod, action = self.match(pkg)
        if mod is None:
            return self._add_new(pkg, first, modules_on)
        if action == "module":
            return self._add_module(mod, pkg)
        return self._update(mod, pkg, modules_on)

    def name_for(self, pkg: Package) -> str:
        return self._label(pkg)[0]

    def _label(self, pkg: Package) -> tuple[str, str]:
        name, version = clean_label(pkg.source.name)
        if _GENERIC.match(name) or not name:
            prefix = file_prefix(list(pkg.files)).rstrip("_")
            name = prefix or "Language mod"
        return name, version

    def _write_files(self, mod: Mod, files: dict[str, bytes], origin: str) -> None:
        d = self.mod_dir(mod)
        d.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (d / name).write_bytes(data)
            h = sha1(data)
            mod.files[name] = ModFile(name, origin, h, h)

    def _add_new(self, pkg: Package, first: bool = False, modules_on: bool = False) -> ImportResult:
        name, version = self._label(pkg)
        mod = Mod(id=self._new_id(name), name=name, version=version, stamp=pkg.stamp,
                  source=str(pkg.source), added=_now(), updated=_now(),
                  prefix=file_prefix(list(pkg.files)), notes=list(pkg.notes))
        self._write_files(mod, pkg.files, "main")
        self._write_modules(mod, pkg, modules_on)
        self._keep_readme(mod, pkg)
        # Keep the personal layer last, where it wins over everything.
        at = 0 if first else next((i for i, m in enumerate(self.mods) if m.personal), len(self.mods))
        self.mods.insert(at, mod)
        self.save()
        return ImportResult(mod, "added", list(pkg.notes))

    def _write_modules(self, mod: Mod, pkg: Package, on: bool) -> None:
        """The modules a mod's own download brings; new ones wait switched off unless ``on``."""
        for label, files in pkg.modules.items():
            self._write_files(mod, files, label)
            if label not in mod.modules:
                mod.modules.append(label)
                if not on:
                    mod.off.append(label)
            if label not in mod.bundled:
                mod.bundled.append(label)

    def set_module(self, mod_id: str, label: str, on: bool) -> None:
        mod = self.get(mod_id)
        if mod is None:
            raise LibraryError(tr("There is no mod {mod}.", mod=repr(mod_id)))
        if label not in mod.modules:
            raise LibraryError(tr("{mod} has no module {module}.", mod=mod.name, module=repr(label)))
        mod.off = [m for m in mod.off if m != label] + ([] if on else [label])
        self.save()

    def add_files(self, name: str, files: dict[str, bytes], source: str = "") -> Mod:
        """A new mod made from files in hand rather than a download: the strings a profile brought."""
        mod = Mod(id=self._new_id(name), name=name, stamp=time.strftime("%Y-%m-%d"), source=source,
                  added=_now(), updated=_now(), prefix=file_prefix(list(files)))
        self._write_files(mod, {n: d for n, d in files.items() if usable_name(n)}, "main")
        at = next((i for i, m in enumerate(self.mods) if m.personal), len(self.mods))
        self.mods.insert(at, mod)
        self.save()
        return mod

    def _add_module(self, mod: Mod, pkg: Package) -> ImportResult:
        label = pkg.label if not _GENERIC.match(pkg.label) else f"module {len(mod.modules) + 1}"
        notes = list(pkg.notes)
        replaced = [n for n in pkg.files if n in mod.files]
        if replaced:
            notes.append(ntr(len(replaced), "Replaced {n} file that was already there.",
                             "Replaced {n} files that were already there."))
        self._write_files(mod, pkg.files, label)
        if label not in mod.modules:
            mod.modules.append(label)
        mod.off = [m for m in mod.off if m != label]          # added on purpose: it loads
        mod.updated = _now()
        self.save()
        return ImportResult(mod, "module", notes)

    def refresh(self, mod: Mod) -> None:
        """Re-hash the stored files, so edits made straight in the folder count too."""
        d = self.mod_dir(mod)
        for name, f in list(mod.files.items()):
            try:
                f.sha1 = sha1((d / name).read_bytes())
            except OSError:
                del mod.files[name]

    def _update(self, mod: Mod, pkg: Package, modules_on: bool = False) -> ImportResult:
        notes = list(pkg.notes)
        self.refresh(mod)
        d = self.mod_dir(mod)
        incoming = dict(pkg.files)
        from_modules = {name: label for label, files in pkg.modules.items() for name in files}
        arriving = set(incoming) | set(from_modules)
        # Modules its old download brought and this one does not: they go, as its other files do.
        gone_modules = [label for label in mod.bundled if label not in pkg.modules]
        # A file its new list still names but its download does not ship is an optional module the player put
        # in beside it (IFN1's, pasted into lang by hand, then taken in with the mod): it stays, as a module.
        named = {n.rsplit("/", 1)[-1].lower() for n in listed_files(pkg.list_text() or "")}
        loose: list[str] = []

        def take(name: str) -> bytes | None:
            """The update's copy of a file, taken out of what is still to be written."""
            if name in incoming:
                return incoming.pop(name)
            return pkg.modules[from_modules[name]].pop(name) if name in from_modules else None

        keep: dict[str, ModFile] = {}
        for name, f in mod.files.items():
            if f.edited:
                new = take(name)
                if new is not None and sha1(new) != f.shipped:
                    # Both the player and the author changed it: the player's copy stays
                    # in place, the author's is set beside it to compare.
                    (d / (name + ".new")).write_bytes(new)
                    notes.append(tr("Kept your edited {name}; the update's version is saved as {name}.new.",
                                    name=name))
                else:
                    notes.append(tr("Kept your edited {name}.", name=name))
                keep[name] = f
            elif f.origin not in ("main", *gone_modules, *pkg.modules) and name not in arriving:
                keep[name] = f          # an optional module added on its own, which the update does not ship
            elif f.origin == "main" and name not in arriving and name.lower() in named:
                f.origin = _module_label(Path(name).stem)
                keep[name] = f
                loose.append(f.origin)
            elif name not in arriving:
                (d / name).unlink(missing_ok=True)
        dropped = [n for n, f in mod.files.items() if n not in keep and n not in arriving]
        mod.files = keep
        self._write_files(mod, incoming, "main")
        self._write_modules(mod, pkg, modules_on)
        self._keep_readme(mod, pkg)
        mod.modules = [m for m in mod.modules if m not in gone_modules] + [m for m in loose if m not in mod.modules]
        mod.off = [m for m in mod.off if m not in gone_modules]
        mod.bundled = [m for m in mod.bundled if m not in gone_modules]
        if loose:
            notes.append(ntr(len(loose), "Kept {n} file its new list still names but does not bring, as an optional "
                                         "module you can switch off.",
                             "Kept {n} files its new list still names but does not bring, as optional modules you "
                             "can switch off."))
        _name, version = self._label(pkg)
        mod.version = version        # a bare "lang.zip" has none; the files' date tells them apart
        mod.stamp = pkg.stamp or mod.stamp
        mod.source = str(pkg.source)
        mod.updated = _now()
        mod.prefix = file_prefix(list(mod.files)) or mod.prefix
        mod.notes = list(pkg.notes)
        if dropped:
            notes.append(ntr(len(dropped), "Removed {n} file the new version no longer has.",
                             "Removed {n} files the new version no longer has."))
        self.save()
        return ImportResult(mod, "updated", notes)

    def update_file(self, mod: Mod, name: str, data: bytes) -> None:
        """Take in a file the player changed (it now counts as edited)."""
        self.file_path(mod, name).write_bytes(data)
        f = mod.files.get(name)
        if f is None:
            h = sha1(data)
            mod.files[name] = ModFile(name, "yours", h, "")
        else:
            f.sha1 = sha1(data)
        self.save()

    # -- removing, and taking it back ----------------------------------------

    def remove(self, mod_id: str) -> str | None:
        """Take a mod out of the list. Its files are set aside, not deleted, with where it stood,
        so :meth:`unremove` can put it back as it was; after a month they are cleared away.
        Returns the name it is kept under."""
        mod = self.get(mod_id)
        if mod is None:
            return None
        index = self.mods.index(mod)
        token = f"{time.strftime('%Y%m%d-%H%M%S')}-{mod.id}"
        kept = self.root / REMOVED / token
        kept.mkdir(parents=True, exist_ok=True)
        folder = self.mod_dir(mod).parent
        if folder.exists():
            shutil.move(str(folder), str(kept / "files"))
        (kept / "mod.json").write_text(json.dumps({"index": index, "mod": asdict(mod)}, indent=2,
                                                  ensure_ascii=False), "utf-8")
        self.mods.remove(mod)
        self.save()
        self.prune_removed()
        return token

    def removed(self) -> list[tuple[str, str]]:
        """Mods set aside by :meth:`remove`, newest first: (the name it is kept under, its label)."""
        out = []
        base = self.root / REMOVED
        for d in sorted(base.iterdir(), reverse=True) if base.is_dir() else []:
            try:
                raw = json.loads((d / "mod.json").read_text("utf-8"))
                mod = _mod_from(raw["mod"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if mod is not None:
                out.append((d.name, mod.label))
        return out

    def unremove(self, token: str | None = None) -> Mod:
        """Put a removed mod back where it stood: the newest one if no name is given."""
        if token is None:
            waiting = self.removed()
            if not waiting:
                raise LibraryError(tr("There is no removed mod to bring back."))
            token = waiting[0][0]
        kept = self.root / REMOVED / token
        if not (kept / "mod.json").is_file():
            raise LibraryError(tr("There is no mod {mod}.", mod=repr(token)))
        try:
            raw = json.loads((kept / "mod.json").read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise LibraryError(tr("{name} cannot be brought back: {error}", name=token, error=exc)) from None
        mod = _mod_from(raw.get("mod") or {})
        if mod is None:
            raise LibraryError(tr("{name} cannot be brought back.", name=token))
        if self.get(mod.id) is not None:           # a mod of that name was added meanwhile
            mod.id = self._new_id(mod.name)
        if (kept / "files").exists():
            target = self.mod_dir(mod).parent
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(kept / "files"), str(target))
        self.mods.insert(max(0, min(int(raw.get("index", len(self.mods))), len(self.mods))), mod)
        shutil.rmtree(kept, ignore_errors=True)
        self.save()
        return mod

    def prune_removed(self, days: int = KEEP_REMOVED_DAYS) -> int:
        """Clear away removed mods older than ``days``."""
        base = self.root / REMOVED
        cut = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() - days * 86400))
        gone = 0
        for d in base.iterdir() if base.is_dir() else []:
            if d.is_dir() and d.name[:15] < cut:
                shutil.rmtree(d, ignore_errors=True)
                gone += 1
        return gone

    def move(self, mod_id: str, index: int) -> None:
        mod = self.get(mod_id)
        if mod is None:
            return
        self.mods.remove(mod)
        self.mods.insert(max(0, min(index, len(self.mods))), mod)
        self.save()

    def set_enabled(self, mod_id: str, on: bool) -> None:
        mod = self.get(mod_id)
        if mod is not None:
            mod.enabled = on
            self.save()

    def personal(self, create: bool = True) -> Mod | None:
        """The player's own layer: one CSV that loads after every mod."""
        mod = self.get(PERSONAL_ID)
        if mod is None and create:
            mod = Mod(id=PERSONAL_ID, name="My changes", added=_now(), updated=_now(), personal=True)
            header = b'"<ID|readonly|noverify>";"<English>"\r\n'
            self._write_files(mod, {PERSONAL_FILE: header}, "yours")
            mod.files[PERSONAL_FILE].shipped = ""
            self.mods.append(mod)
            self.save()
        return mod

    def backup_dir(self, install_root: Path) -> Path:
        """``backups/war-thunder-1a2b3c4d``: readable, and one per install."""
        tag = hashlib.sha1(str(install_root).lower().encode("utf-8")).hexdigest()[:8]
        return self.root / "backups" / f"{slug(Path(install_root).name)}-{tag}"
