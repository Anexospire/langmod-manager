"""Keeping mods up to date from where they are published.

A mod *follows* a source (see :mod:`.sources`). A check asks the source for
its newest release and remembers it on the mod; installing downloads that
release and adds it to the mod exactly as adding a newer version by hand
does, so files the player edited and optional modules stay.

Whether the mod already has that release is known for certain once the
manager has installed it (the release's key is kept). Before that, the
versions are compared when both have one, and otherwise the manager says
it cannot tell rather than guess.

Each step comes in two halves, so the window can do the slow one in the
background without two threads ever changing the library at once: asking
and downloading (:func:`look`, :func:`fetch`) touch nothing shared, and
keeping the answer or adding the download (:func:`record`, :func:`take`) run
on the library's own thread. :func:`check` and :func:`install` do both.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import sources
from .library import ImportResult, Library, Mod
from .prefs import Prefs
from .sources import Release, Source, SourceError
from ..i18n import tr

INTERVAL = {"open": 60 * 60, "daily": 24 * 60 * 60, "never": None}


def source_of(mod: Mod) -> Source | None:
    return Source.from_dict(mod.follow)


def latest_of(mod: Mod) -> Release | None:
    return Release.from_dict(mod.latest)


def suggestion(mod: Mod) -> Source | None:
    return sources.suggest([mod.name, mod.prefix, Path(mod.source).name if mod.source else ""]
                           + mod.csv_names[:3])


def follow(lib: Library, mod: Mod, source: Source) -> None:
    if not source.match and source.kind == "wtlive" and source.ref.startswith("user:"):
        source.match = (mod.prefix.rstrip("_") or mod.name.split()[0]) if not mod.personal else ""
    mod.follow = source.to_dict()
    mod.installed_key = ""
    mod.latest = None
    mod.checked = 0.0
    mod.check_error = ""
    lib.save()


def unfollow(lib: Library, mod: Mod) -> None:
    mod.follow = None
    mod.latest = None
    mod.installed_key = ""
    mod.check_error = ""
    lib.save()


def auto_follow(lib: Library, mod: Mod) -> Source | None:
    """Follow the mod's known home, if it has one and follows nothing yet."""
    if mod.follow or mod.personal:
        return None
    found = suggestion(mod)
    if found is not None and found.kind != "nexus":
        follow(lib, mod, found)
        return found
    return None


#: Files dated this close before a release are taken to be that release: authors zip
#: a version up and post it a day or two later.
DATE_SLACK = 3 * 24 * 60 * 60


def judge(mod: Mod) -> tuple[str, str]:
    """(state, how it was judged).

    state: "update", "current", "unknown" (cannot tell), "error", or "" (follows
    nothing, or not checked yet). How: "file" (the manager installed it itself, so it
    knows), "version", "date", or "".
    """
    if not mod.follow:
        return "", ""
    if mod.check_error:
        return "error", ""
    rel = latest_of(mod)
    if rel is None:
        return "", ""
    if mod.installed_key:
        return ("current" if mod.installed_key == rel.key else "update"), "file"
    newer = sources.is_newer(rel.version, mod.version)
    if newer is not None:
        return ("update" if newer else "current"), "version"
    stamp = _stamp(mod.stamp)
    if stamp and rel.published:
        return ("current" if stamp >= rel.published - DATE_SLACK else "update"), "date"
    return "unknown", ""


def state(mod: Mod) -> str:
    return judge(mod)[0]


def _stamp(text: str) -> float:
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d")) + 12 * 3600 if text else 0.0
    except ValueError:
        return 0.0


def _plain(name: str) -> str:
    """``IFN1 Langmod V76 (1).zip`` -> ``ifn1 langmod v76.zip``: the name a browser gives a second copy."""
    return re.sub(r"\s*\(\d+\)(?=\.\w+$)", "", name).strip().lower()


def _same_file(mod: Mod, rel: Release) -> bool:
    """The zip the mod was added from is still there, with this release's name and exact size."""
    try:
        p = Path(mod.source)
        return (bool(rel.size) and p.is_file() and _plain(p.name) == _plain(rel.file_name)
                and p.stat().st_size == rel.size)
    except OSError:
        return False


@dataclass
class Look:
    """What a mod's source said when asked."""
    mod_id: str
    source: Source                 # as asked; its label may have been filled in
    release: Release | None = None
    error: str = ""                # the source could not be read
    when: float = 0.0


def look(mod_id: str, source: Source, nexus_key: str = "") -> Look:
    """Ask a source what is newest. Network only, nothing shared is touched: any thread may."""
    src = Source.from_dict(source.to_dict())
    try:
        rel = sources.latest(src, nexus_key)
    except SourceError as exc:
        return Look(mod_id, src, None, str(exc), time.time())
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        # It answered, but not in the shape it used to: the site has changed.
        return Look(mod_id, src, None, tr("its page answered in a way the manager does not understand"), time.time())
    return Look(mod_id, src, rel, "", time.time())


def record(lib: Library, found: Look) -> Release | None:
    """Keep what a look found on its mod. Skipped if the mod went, or now follows elsewhere."""
    mod = lib.get(found.mod_id)
    now = source_of(mod) if mod is not None else None
    if now is None or (now.kind, now.ref, now.match) != (found.source.kind, found.source.ref, found.source.match):
        return None
    mod.checked = found.when or time.time()
    if found.error:
        mod.check_error = found.error
        lib.save()
        return None
    rel = found.release
    mod.follow = found.source.to_dict()            # the label may have been filled in
    mod.check_error = "" if rel is not None else tr("nothing to download was found there")
    mod.latest = rel.to_dict() if rel else None
    if rel is not None and not mod.installed_key and _same_file(mod, rel):
        mod.installed_key = rel.key
    lib.save()
    return rel


def check(lib: Library, mod: Mod) -> Release | None:
    """Ask the mod's source what is newest; remembered on the mod either way."""
    src = source_of(mod)
    if src is None:
        return None
    return record(lib, look(mod.id, src, Prefs(lib).get("nexus_key")))


def check_all(lib: Library) -> list[Mod]:
    """Check every mod that follows somewhere; returns the ones with an update."""
    for mod in lib.mods:
        if mod.follow:
            check(lib, mod)
    Prefs(lib).set("update_last", time.time())
    return [m for m in lib.mods if state(m) == "update"]


def due(lib: Library) -> bool:
    prefs = Prefs(lib)
    every = INTERVAL.get(prefs.get("update_check"), INTERVAL["open"])
    if every is None or not any(m.follow for m in lib.mods):
        return False
    return time.time() - prefs.get("update_last") >= every


def mark_current(lib: Library, mod: Mod) -> None:
    """The player says the files they have are the latest release."""
    rel = latest_of(mod)
    if rel is not None:
        mod.installed_key = rel.key
        mod.version = rel.version or mod.version
        lib.save()


@dataclass
class Installed:
    mod: Mod
    release: Release
    result: ImportResult


def downloads(lib: Library) -> Path:
    return lib.root / "downloads"


def fetch(release: Release, folder: Path, progress=None) -> Path:
    """Download a release into ``folder``. Network and that one file only: any thread may."""
    return sources.download(release, folder, progress)


def take(lib: Library, mod_id: str, rel: Release, path: Path) -> Installed:
    """Add a downloaded release to its mod as the new version; the download is then deleted."""
    try:
        mod = lib.get(mod_id)
        if mod is None:
            raise SourceError(tr("it was removed while the update downloaded"))
        res = lib.add(path, into=mod.id, how="update")
    finally:
        path.unlink(missing_ok=True)
    mod = lib.get(mod_id) or mod
    mod.installed_key = rel.key
    mod.version = rel.version or mod.version
    mod.latest = rel.to_dict()
    mod.source = rel.page
    lib.save()
    return Installed(mod, rel, res)


def take_new(lib: Library, source: Source, rel: Release, path: Path) -> Installed:
    """Add a download as a mod of its own (or as the new version of one already here, if it is one),
    following where it came from. The download is then deleted."""
    try:
        res = lib.add(path)
    finally:
        path.unlink(missing_ok=True)
    mod = res.mod
    follow(lib, mod, Source.from_dict(source.to_dict()))
    mod.installed_key = rel.key
    mod.version = rel.version or mod.version
    mod.latest = rel.to_dict()
    mod.checked = time.time()
    mod.source = rel.page
    lib.save()
    return Installed(mod, rel, res)


def install(lib: Library, mod: Mod, release: Release | None = None, progress=None) -> Installed:
    """Download a release and add it to the mod as its new version."""
    rel = release or latest_of(mod)
    if rel is None:
        raise SourceError(tr("check for updates first"))
    return take(lib, mod.id, rel, fetch(rel, downloads(lib), progress))


def install_all(lib: Library, progress=None) -> tuple[list[Installed], list[str]]:
    done, failed = [], []
    for mod in list(lib.mods):
        if state(mod) != "update":
            continue
        rel = latest_of(mod)
        if rel is None or not rel.downloadable:
            continue
        try:
            done.append(install(lib, mod, rel, progress))
        except Exception as exc:          # one bad download must not stop the rest
            failed.append(f"{mod.name}: {exc}")
    return done, failed
