"""Profiles: named sets of mods, switched in one go.

A profile holds the order of the mods, which ones are on, and the player's
picks (see :mod:`.strings`). The profile in use follows every change made
while it is in use (:meth:`.Library.save` sees to that), so switching away
and back finds it as it was left. A mod added while another profile was in
use is off in this one, until it is switched on here.

A profile can be written to a file and sent to a friend. The file names
each mod and where its updates come from, so the friend's manager can fetch
the ones they do not have; the sender's own strings ride along, and arrive
as a mod of their own.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from .langcsv import read_table
from .library import Library, Mod, clean_label, slug, usable_name
from .plan import GAME
from .sources import Source
from ..i18n import tr

FORMAT = 1
EXTENSION = ".langmod-profile"
MAX_FILE = 8 * 1024 * 1024
MAX_NAME = 40
STORE = "profiles"       # library setting: name -> what the profile holds
ACTIVE = "profile"       # library setting: the name of the one in use


class ProfileError(Exception):
    pass


def _store(lib: Library) -> dict:
    store = lib.settings.get(STORE)
    if not isinstance(store, dict):
        store = lib.settings[STORE] = {}
    return store


def names(lib: Library) -> list[str]:
    return [n for n, v in _store(lib).items() if isinstance(v, dict)]


def active(lib: Library) -> str | None:
    name = lib.settings.get(ACTIVE)
    return name if isinstance(name, str) and name in names(lib) else None


def clean_name(name: str, lib: Library | None = None, keep: str | None = None) -> str:
    """A usable profile name, or ProfileError saying why not."""
    name = " ".join((name or "").split())
    if not name:
        raise ProfileError(tr("A profile needs a name."))
    if len(name) > MAX_NAME:
        raise ProfileError(tr("A profile name can be at most {count} letters long.", count=MAX_NAME))
    if lib is not None and name != keep and name.lower() in (n.lower() for n in names(lib)):
        raise ProfileError(tr("There is already a profile called {profile}.", profile=name))
    return name


def save_as(lib: Library, name: str) -> str:
    """A new profile holding what is set up now; it becomes the one in use."""
    name = clean_name(name, lib)
    _store(lib)[name] = {**lib.snapshot(), "made": time.strftime("%Y-%m-%d %H:%M")}
    lib.settings[ACTIVE] = name
    lib.save()
    return name


def switch(lib: Library, name: str) -> None:
    """Put a profile's mods, order and picks in place."""
    held = _store(lib).get(name)
    if not isinstance(held, dict):
        raise ProfileError(tr("There is no profile called {profile}.", profile=name))
    lib.settings[ACTIVE] = None                  # so saving on the way does not write over it
    _put(lib, held)
    lib.settings[ACTIVE] = name
    lib.save()


def _put(lib: Library, held: dict) -> None:
    order = [i for i in held.get("order") or [] if isinstance(i, str) and lib.get(i) is not None]
    rest = [m.id for m in lib.mods if m.id not in order]        # added since the profile last saw them
    last = lib.get(order[-1]) if order else None
    if last is not None and last.personal:
        order = order[:-1] + rest + order[-1:]                  # the player's own strings stay last
    else:
        order = order + rest
    on = set(held.get("enabled") or [])
    lib.mods.sort(key=lambda m: order.index(m.id))
    modules = held.get("modules") if isinstance(held.get("modules"), dict) else {}
    for m in lib.mods:
        m.enabled = m.id in on
        m.set_module_states(modules.get(m.id))
    picks = held.get("picks")
    lib.settings["picks"] = dict(picks) if isinstance(picks, dict) else {}


def rename(lib: Library, old: str, new: str) -> str:
    store = _store(lib)
    if old not in store:
        raise ProfileError(tr("There is no profile called {profile}.", profile=old))
    new = clean_name(new, lib, keep=old)
    lib.settings[STORE] = {(new if k == old else k): v for k, v in store.items()}
    if lib.settings.get(ACTIVE) == old:
        lib.settings[ACTIVE] = new
    lib.save()
    return new


def delete(lib: Library, name: str) -> None:
    """Forget a profile. The mods stay as they are."""
    if _store(lib).pop(name, None) is None:
        raise ProfileError(tr("There is no profile called {profile}.", profile=name))
    if lib.settings.get(ACTIVE) == name:
        lib.settings[ACTIVE] = None
    lib.save()


# -- to and from a file ------------------------------------------------------------------------

def export(lib: Library, name: str, path: str | Path, with_strings: bool = True) -> Path:
    """Write a profile to a file to send to someone."""
    held = lib.snapshot() if name == active(lib) else _store(lib).get(name)
    if not isinstance(held, dict):
        raise ProfileError(tr("There is no profile called {profile}.", profile=name))
    on = set(held.get("enabled") or [])
    mods = []
    for mod_id in held.get("order") or []:
        m = lib.get(mod_id)
        if m is None:
            continue
        entry = {"id": m.id, "name": m.name, "version": m.version, "enabled": m.id in on,
                 "follow": m.follow, "personal": m.personal}
        states = (held.get("modules") or {}).get(m.id) if isinstance(held.get("modules"), dict) else None
        if isinstance(states, dict) and states:
            entry["modules"] = states
        if m.personal:
            if not with_strings:
                continue
            files = {}
            for n in m.csv_names:
                data = lib.read_file(m, n)
                if read_table(data).rows:
                    files[n] = data.decode("utf-8-sig", "replace")
            if not files:
                continue
            entry["files"] = files
        mods.append(entry)
    doc = {"langmod_profile": FORMAT, "name": name, "made_with": f"Langmod Manager {__version__}",
           "saved": time.strftime("%Y-%m-%d"), "mods": mods, "picks": held.get("picks") or {}}
    out = Path(path)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), "utf-8")
    return out


def read_file(path: str | Path) -> dict:
    """A profile file, checked; ProfileError if it is not one."""
    p = Path(path)
    try:
        if p.stat().st_size > MAX_FILE:
            raise ProfileError(tr("{file} is far too large to be a profile.", file=p.name))
        doc = json.loads(p.read_text("utf-8-sig"))
    except OSError as exc:
        raise ProfileError(tr("{file} could not be read: {error}", file=p.name, error=exc)) from None
    except ValueError:
        raise ProfileError(tr("{file} is not a Langmod Manager profile.", file=p.name)) from None
    if not isinstance(doc, dict) or "langmod_profile" not in doc or not isinstance(doc.get("mods"), list):
        raise ProfileError(tr("{file} is not a Langmod Manager profile.", file=p.name))
    if not isinstance(doc["langmod_profile"], int) or doc["langmod_profile"] > FORMAT:
        raise ProfileError(tr("{file} was made by a newer Langmod Manager: update this one first.", file=p.name))
    mods = []
    for e in doc["mods"]:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not isinstance(e.get("name"), str):
            continue
        files = e.get("files") if isinstance(e.get("files"), dict) else {}
        e["files"] = {n: t for n, t in files.items()
                      if isinstance(n, str) and isinstance(t, str) and usable_name(n) and n.lower().endswith(".csv")}
        mods.append(e)
    doc["mods"] = mods
    doc["picks"] = {k: v for k, v in (doc.get("picks") or {}).items()
                    if isinstance(k, str) and isinstance(v, str)} if isinstance(doc.get("picks"), dict) else {}
    doc["name"] = " ".join(str(doc.get("name") or p.stem.replace(EXTENSION, "")).split())[:MAX_NAME] or "Imported"
    return doc


@dataclass
class Entry:
    """One mod a profile file names, and what this library has of it."""
    id: str
    name: str
    version: str
    enabled: bool
    personal: bool
    source: Source | None           # where it can be fetched from
    have: Mod | None                # the mod here that it is
    files: dict                     # the sender's own strings, for their entry
    modules: dict | None = None     # its modules, on or off, as the sender has them


def entries(lib: Library, doc: dict) -> list[Entry]:
    out = []
    for e in doc["mods"]:
        src = Source.from_dict(e.get("follow")) if isinstance(e.get("follow"), dict) else None
        personal = bool(e.get("personal"))
        states = e.get("modules") if isinstance(e.get("modules"), dict) else {}
        out.append(Entry(e["id"], e["name"], str(e.get("version") or ""), bool(e.get("enabled", True)), personal,
                         src, None if personal else _match(lib, e, src), e.get("files") or {},
                         {k: v for k, v in states.items() if isinstance(k, str) and isinstance(v, bool)}))
    return out


def _match(lib: Library, e: dict, src: Source | None) -> Mod | None:
    mods = [m for m in lib.mods if not m.personal]
    if src is not None:
        for m in mods:
            mine = Source.from_dict(m.follow)
            if mine is not None and (mine.kind, mine.ref, mine.match.lower()) == (src.kind, src.ref, src.match.lower()):
                return m
    # By name without its version: a profile from before versions came off names says "WTHLM_1.19.00".
    name = slug(clean_label(e["name"])[0])
    for m in mods:
        if m.id == e["id"] and slug(m.name) == name:
            return m
    return next((m for m in mods if slug(m.name) == name), None)


def take(lib: Library, doc: dict) -> tuple[str, list[str]]:
    """Make a profile of a file's, with the mods this library has (fetch the missing ones first),
    and switch to it. Returns its name and the mods it names that are not here."""
    order: list[str] = []
    on: list[str] = []
    ids: dict[str, str] = {}
    modules: dict[str, dict] = {}
    missing: list[str] = []
    name = _free_name(lib, doc["name"])
    for e in entries(lib, doc):
        mod = e.have
        if e.personal:
            if not e.files:
                continue
            files = {f"{slug(name)}__{n}": t.encode("utf-8") for n, t in e.files.items()}
            mod = lib.add_files(f"{name} strings", files, source=f"the profile {doc['name']}")
        if mod is None:
            missing.append(e.name)
            continue
        ids[e.id] = mod.id
        if e.modules:
            modules[mod.id] = e.modules
        if mod.id not in order:
            order.append(mod.id)
            if e.enabled:
                on.append(mod.id)
    own = lib.personal(create=False)            # the player's own strings stay theirs, last
    if own is not None:
        order.append(own.id)
        if own.enabled:
            on.append(own.id)
    picks = {k: (GAME if v == GAME else ids.get(v)) for k, v in doc["picks"].items()}
    _store(lib)[name] = {"order": order, "enabled": on, "picks": {k: v for k, v in picks.items() if v},
                         "modules": modules, "made": time.strftime("%Y-%m-%d %H:%M"),
                         "from": doc.get("made_with", "")}
    lib.save()
    switch(lib, name)
    return name, missing


def _free_name(lib: Library, wanted: str) -> str:
    taken = {n.lower() for n in names(lib)}
    name, n = wanted, 2
    while name.lower() in taken:
        suffix = f" ({n})"
        name = wanted[:MAX_NAME - len(suffix)] + suffix
        n += 1
    return name


def file_name(name: str) -> str:
    """``Historical names`` -> ``Historical names.langmod-profile``."""
    return (re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "profile") + EXTENSION
