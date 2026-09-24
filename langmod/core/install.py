"""Write a plan into the game's ``lang`` folder, and take it out again.

Nothing in the game folder is deleted without a copy:

* a file the manager wrote and the player then edited there (players are
  used to typing into ``IFN1_99_useroverwrite.csv`` in place) is taken back
  into the mod it came from, so the next Apply does not lose it;
* anything else found in ``lang`` - a mod installed by hand, an old copy of
  one of the game's own tables - is moved into the manager's backups,
  except exact copies of the game's current tables, which the game still has;
* ``config.blk`` is copied before its switch is flipped.

A small manifest in ``lang`` records what was written, so the next Apply,
or Restore, knows which files are the manager's.

A mod someone installed by hand, found in ``lang`` before the manager ever
applied, is not simply moved out of the way: :func:`keep_hand_install` makes
it one of the library's mods first, at the top of the order so everything
else goes on top of it, and it keeps working (unless the player said no).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .game import GameInstall, GameLang, lang_stamp
from .library import LIST_NAME, Library, LibraryError, Mod, Package, read_package, sha1
from .plan import Plan, plan_files
from ..i18n import tr

MANIFEST = "langmod-manager.json"
_LEFTOVERS = (".langmod-tmp", MANIFEST + ".tmp")       # half-written files from an Apply cut short
DECLINED = "hand_installs_declined"                    # settings: install folder -> the hand install said no to


@dataclass
class Manifest:
    files: dict[str, dict] = field(default_factory=dict)   # dest -> {"sha1", "mod", "source"}
    game_version: str = ""
    game_stamp: str = ""
    applied: str = ""
    mods: list[str] = field(default_factory=list)


def read_manifest(lang_dir: Path) -> Manifest | None:
    try:
        raw = json.loads((lang_dir / MANIFEST).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return Manifest(**{k: raw.get(k, v) for k, v in Manifest().__dict__.items()})


def _write_manifest(lang_dir: Path, m: Manifest) -> None:
    tmp = lang_dir / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(m.__dict__, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, lang_dir / MANIFEST)


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".langmod-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


@dataclass
class ApplyResult:
    written: int = 0               # files the plan puts in the game
    changed: int = 0               # of those, the ones that were not already there as they are
    removed: int = 0
    backed_up: list[str] = field(default_factory=list)
    taken_back: list[str] = field(default_factory=list)
    switched_on: bool = False
    backup: Path | None = None
    notes: list[str] = field(default_factory=list)


class _Backup:
    def __init__(self, lib: Library, install: GameInstall):
        self.base = lib.backup_dir(install.root)
        self.dir: Path | None = None

    def folder(self) -> Path:
        if self.dir is None:
            self.dir = self.base / time.strftime("%Y-%m-%d_%H-%M-%S")
            (self.dir / "lang").mkdir(parents=True, exist_ok=True)
        return self.dir

    def move(self, path: Path) -> None:
        shutil.move(str(path), str(self.folder() / "lang" / path.name))

    def copy(self, path: Path, name: str | None = None) -> None:
        shutil.copy2(path, self.folder() / (name or path.name))


def _language_folder(path: Path) -> bool:
    """A folder in ``lang`` with language files in it: a mod's module put there by hand (WTHLM's
    Package_Farsi), which the manager's own list would otherwise leave behind, unloaded."""
    try:
        return path.is_dir() and any(p.suffix.lower() in (".csv", ".blk") for p in path.rglob("*"))
    except OSError:
        return False


def status(install: GameInstall) -> dict:
    """What the game folder holds now, for the interface: cheap, no archive reads. Folders among the
    files the manager did not write end in "/"."""
    m = read_manifest(install.lang_dir)
    try:
        cfg = install.config_path.read_text("utf-8", errors="replace")
    except OSError:
        cfg = ""
    stray = []
    edited = []
    if install.lang_dir.is_dir():
        mine = set(m.files) if m else set()
        stray = sorted([p.name for p in install.lang_dir.iterdir()
                        if p.is_file() and p.name not in mine and p.name != MANIFEST
                        and not p.name.endswith(_LEFTOVERS)]
                       + [p.name + "/" for p in install.lang_dir.iterdir() if _language_folder(p)])
        for dest, info in (m.files.items() if m else ()):
            p = install.lang_dir / dest
            try:
                if sha1(p.read_bytes()) != info.get("sha1"):
                    edited.append(dest)
            except OSError:
                edited.append(dest)
    return {
        "managed": m is not None,
        "manifest": m,
        "stale": bool(m) and m.game_stamp != lang_stamp(install.root),
        "switch_on": config.test_localization(cfg),
        "language": config.game_language(cfg) if cfg else "",
        "stray": stray,
        "edited": edited,
    }


def pending(lib: Library, plan: Plan, manifest: Manifest | None) -> bool:
    """True when Apply would write something different from what is there."""
    if manifest is None:
        return bool(plan.placements)
    want = {dest: sha1(data) for dest, data in plan_files(lib, plan).items()}
    have = {dest: info.get("sha1") for dest, info in manifest.files.items()}
    return want != have


@dataclass
class HandInstall:
    """A language mod someone put in ``lang`` themselves, before the manager ever applied there."""
    package: Package
    name: str
    signature: str               # its load list's sha1: the same install is known again by it
    mod: Mod | None = None       # the library's mod that it is, if there is one
    declined: bool = False       # the player said not to take it in
    taken: bool = False          # made one of the library's mods just now

    @property
    def same(self) -> bool:
        """Whether the library's mod holds these very files (as they arrived there, or as edited since)."""
        if self.mod is None:
            return False
        for name, data in self.package.files.items():
            f = self.mod.files.get(name)
            if f is None or sha1(data) not in (f.sha1, f.shipped):
                return False
        return True

    def holds(self, stray: str) -> bool:
        """Whether a file or folder in ``lang``, as :func:`status` names it, is part of this mod."""
        if stray.endswith("/"):
            return stray[:-1] in self.package.modules
        return any(n.lower() == stray.lower() for n in self.package.files)


def hand_install(lib: Library, install: GameInstall) -> HandInstall | None:
    """The mod in ``lang`` that was installed by hand, if the manager has never applied there."""
    lang = install.lang_dir
    try:
        if read_manifest(lang) is not None:
            return None
        listing = next((p for p in lang.iterdir() if p.is_file() and p.name.lower() == LIST_NAME), None)
        if listing is None:
            return None
        pkg = read_package(lang, at="")
        signature = sha1(listing.read_bytes())
    except (LibraryError, OSError):
        return None
    mod, how = lib.match(pkg)
    mod = mod if how == "update" else None
    declined = (lib.settings.get(DECLINED) or {}).get(str(install.root)) == signature
    return HandInstall(pkg, mod.name if mod else lib.name_for(pkg), signature, mod, declined)


def keep_hand_install(lib: Library, install: GameInstall) -> HandInstall | None:
    """Before the first Apply somewhere: a mod installed there by hand becomes one of the library's, at the
    top of the order so everything else goes on top of it, with the modules it had in folders of their own
    switched on, so applying keeps it working. Not if the library has it already, or the player said no."""
    hand = hand_install(lib, install)
    if hand is not None and hand.mod is None and not hand.declined:
        hand.mod = lib.add_package(hand.package, first=True, modules_on=True).mod
        hand.taken = True
    return hand


def decline(lib: Library, install: GameInstall, hand: HandInstall | None) -> None:
    """Leave the hand install out of the library (None: take it in again after all). Apply then moves it
    to the backup, as it does anything else in ``lang`` that it did not write."""
    said = lib.settings.setdefault(DECLINED, {})
    if hand is None:
        said.pop(str(install.root), None)
    else:
        said[str(install.root)] = hand.signature
    lib.save()


def prune_backups(lib: Library, install: GameInstall, keep: int) -> int:
    """Keep the newest ``keep`` backups (0 keeps all). The first one always stays:
    it is what the lang folder held before the manager ever touched it."""
    if keep <= 0:
        return 0
    base = lib.backup_dir(install.root)
    first = lib.settings.get("first_backup", {}).get(str(install.root))
    try:
        dirs = sorted(d for d in base.iterdir() if d.is_dir())
    except OSError:
        return 0
    others = [d for d in dirs if str(d) != first]
    gone = 0
    for d in others[:-keep] if len(others) > keep else []:
        shutil.rmtree(d, ignore_errors=True)
        gone += 1
    return gone


def apply(lib: Library, install: GameInstall, game: GameLang, plan: Plan,
          switch_on: bool = True, keep_backups: int = 0) -> ApplyResult:
    lang = install.lang_dir
    lang.mkdir(parents=True, exist_ok=True)
    res = ApplyResult()
    backup = _Backup(lib, install)
    old = read_manifest(lang) or Manifest()

    # 1. Edits made in place to files this manager wrote.
    on_disk: dict[str, str] = {}
    for dest, info in old.files.items():
        p = lang / dest
        if not p.is_file():
            continue
        data = p.read_bytes()
        on_disk[dest] = sha1(data)
        if on_disk[dest] == info.get("sha1"):
            continue
        mod = lib.get(info.get("mod", ""))
        source = info.get("source", "")
        if mod is not None and source and source in mod.files:
            lib.update_file(mod, source, data)
            res.taken_back.append(f"{dest} -> {mod.name}")
        else:
            backup.copy(p)
            res.backed_up.append(dest)
            res.notes.append(tr("{file} was edited in the game folder; that copy is in the backup, because the "
                                "manager writes this file itself.", file=dest))

    files = plan_files(lib, plan)          # read after the edits above were taken in

    # 2. Files the manager did not write, and folders of language files (modules put in by hand).
    base = {n.lower() for n in game.base_files()}
    for p in sorted(lang.iterdir()):
        if _language_folder(p):
            backup.move(p)
            res.backed_up.append(p.name + "/")
            continue
        if not p.is_file() or p.name == MANIFEST or p.name in old.files:
            continue
        if p.name.endswith(_LEFTOVERS):
            p.unlink(missing_ok=True)
            continue
        if p.name.lower() in base:
            current = game.resolve("%lang/" + p.name)
            if current is not None and current == p.read_bytes():
                p.unlink()
                res.removed += 1
                continue
            res.notes.append(tr("{file} was an old copy of the game's own table; on disk it hides every string "
                                "the game added since, so it went to the backup.", file=p.name))
        backup.move(p)
        res.backed_up.append(p.name)

    # 3. Last time's files that this plan no longer has.
    for dest in old.files:
        if dest not in files:
            (lang / dest).unlink(missing_ok=True)
            res.removed += 1

    # 4. The plan itself.
    manifest = Manifest(game_version=plan.game_version, game_stamp=plan.game_stamp,
                        applied=time.strftime("%Y-%m-%d %H:%M"),
                        mods=[m.id for m in lib.enabled()])
    sources = {p.dest: p for p in plan.placements}
    for dest, data in files.items():
        h = sha1(data)
        if on_disk.get(dest) != h:           # already there, byte for byte: leave it be
            _write(lang / dest, data)
            res.changed += 1
        p = sources.get(dest)
        manifest.files[dest] = {"sha1": h, "mod": p.mod_id if p else "", "source": p.source if p else ""}
        res.written += 1
    _write_manifest(lang, manifest)

    # 5. The switch in config.blk.
    if switch_on:
        res.switched_on = _set_switch(install, True, backup)
    res.backup = backup.dir
    firsts = lib.settings.setdefault("first_backup", {})
    first = str(install.root) not in firsts
    if first:
        # What lang held before the manager's first Apply, for Restore: "" when it held nothing, so a
        # backup made later (of a mod pasted in by hand since) is never taken for it.
        firsts[str(install.root)] = str(backup.dir) if backup.dir is not None else ""
    if first or backup.dir is not None:
        lib.save()
    if backup.dir is not None:
        prune_backups(lib, install, keep_backups)
    return res


def _set_switch(install: GameInstall, on: bool, backup: _Backup) -> bool:
    try:
        raw = install.config_path.read_bytes()
    except OSError:
        raw = b""
    text = raw.decode("utf-8", errors="surrogateescape")
    new = config.set_test_localization(text, on)
    if new == text:
        return False
    if raw:
        backup.copy(install.config_path, "config.blk")
    _write(install.config_path, new.encode("utf-8", errors="surrogateescape"))
    return True


@dataclass
class RestoreResult:
    removed: int = 0
    restored: list[str] = field(default_factory=list)
    switched_off: bool = False
    notes: list[str] = field(default_factory=list)


def restore(lib: Library, install: GameInstall, bring_back: bool = True,
            switch_off: bool = True) -> RestoreResult:
    """Take the manager's files out; optionally put back what was there first."""
    lang = install.lang_dir
    res = RestoreResult()
    backup = _Backup(lib, install)
    m = read_manifest(lang)
    if m is not None:
        for dest, info in m.files.items():
            p = lang / dest
            if not p.is_file():
                continue
            if sha1(p.read_bytes()) != info.get("sha1"):
                backup.copy(p)
                res.notes.append(tr("{file} had been edited in place; a copy is in the backup.", file=dest))
            p.unlink()
            res.removed += 1
        (lang / MANIFEST).unlink(missing_ok=True)
    if bring_back:
        first = lib.settings.get("first_backup", {}).get(str(install.root))
        src = Path(first) / "lang" if first else None
        if src is not None and src.is_dir():
            lang.mkdir(exist_ok=True)
            for p in sorted(src.iterdir()):
                if (lang / p.name).exists():
                    continue
                if p.is_file():
                    shutil.copy2(p, lang / p.name)
                    res.restored.append(p.name)
                elif p.is_dir():
                    shutil.copytree(p, lang / p.name)
                    res.restored.append(p.name + "/")
    if switch_off and not res.restored:
        res.switched_off = _set_switch(install, False, backup)
    try:
        if lang.is_dir() and not any(lang.iterdir()):
            lang.rmdir()
    except OSError:
        pass
    return res
