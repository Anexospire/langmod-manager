"""Work out what goes into the game's ``lang`` folder for a set of mods.

The game's own ``localization.blk`` is the starting point every time, so
whatever files a game update adds are loaded, instead of the frozen list
a mod was shipped with. Each enabled mod's files are then appended to
``locTable`` in the library's order; a file later in the list overrides an
earlier one, so the last mod wins where two change the same string.

A mod that ships a full copy of one of the game's own tables (``units.csv``
and so on) is the other way new strings go missing: on disk, that copy
stands in for the game's newer one. Such a copy is cut down to the rows
the mod actually changed and loaded as an extra file instead.

The game's event tables (``regional``: decals, skins, titles, trophies and
event items, in an archive of their own) load after all of ``locTable``,
over every mod. One in which a mod gives a string other text is moved into
``locTable`` ahead of the mods, as IFN1 does in its own list; the others
stay where the game has them.

Where the player has *picked* which text a string shows (the game's own,
or one mod's, whatever the order says), that source's row for the string
is copied into a small file of picks that loads after the mods, and before
the player's own strings. The row is copied afresh on every Apply, so a
pick follows its mod's updates.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from . import blk
from .game import GameLang
from .langcsv import LangTable, read_table, write_table
from .library import LIST_NAME, Library, Mod
from ..i18n import tr

PICKS_ID = "picks"                 # the files that carry the player's picks, in reports and the manifest
PICKS_FILE = "langmod_picks.csv"
GAME = "game"                      # a pick of the game's own text
_COMMENTED = re.compile(r'(?m)^[ \t]*//[ \t]*file[ \t]*:[ \t]*t[ \t]*=[ \t]*"%lang/([^"]+)"')

HEADER = ("// Written by Langmod Manager for game version {version}.\n"
          "// It is rebuilt from the game's own list on every Apply, so edit your mods\n"
          "// in the manager rather than here.\n\n")


@dataclass
class Placement:
    dest: str                      # file name in the game's lang folder
    mod_id: str
    source: str = ""               # file name in the mod; "" for generated files
    data: bytes | None = None      # contents, for generated files


@dataclass
class ModReport:
    mod_id: str
    loads: list[str] = field(default_factory=list)          # files appended, in order
    not_shipped: list[str] = field(default_factory=list)    # in its list, not in the package
    unlisted: list[str] = field(default_factory=list)       # in the package, not in its list
    stale_list: list[str] = field(default_factory=list)     # game files its own list lacks
    left_out: list[str] = field(default_factory=list)       # game files its list comments out, on purpose
    gone: list[str] = field(default_factory=list)           # its list names game files that no longer exist
    replaced: list[tuple[str, int, int]] = field(default_factory=list)  # (file, rows kept, rows in copy)
    regional: list[str] = field(default_factory=list)       # event tables moved ahead of the mods for it
    renamed: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Plan:
    game_version: str
    game_stamp: str
    localization: str
    placements: list[Placement]
    reports: dict[str, ModReport]
    loc_table: list[str]
    picks: dict[str, str] = field(default_factory=dict)      # key -> "game" or mod id: the picks written
    regional_moved: list[str] = field(default_factory=list)  # event tables loaded ahead of the mods, in order
    regional_last: list[str] = field(default_factory=list)   # the ones left to load after everything
    left_out: list[str] = field(default_factory=list)        # the game's own tables a mod leaves out

    def placement(self, dest: str) -> Placement | None:
        return next((p for p in self.placements if p.dest.lower() == dest.lower()), None)


def _name(ref: str) -> str:
    return ref.split("/", 1)[1] if "/" in ref else ref


def overlay_rows(mod_data: bytes, game_data: bytes) -> tuple[list[str], list[list[str]], int]:
    """The rows of a full copy that differ from the game's table, in the copy's layout."""
    mine = read_table(mod_data)
    theirs = read_table(game_data)
    cols = mine.columns
    index = {}
    for c in cols[1:]:
        index[c] = theirs.column_index(c)
    game_rows = {row[0]: row for row in theirs.rows if row}
    kept = []
    for row in mine.rows:
        g = game_rows.get(row[0])
        if g is None:
            kept.append(row)
            continue
        for i, c in enumerate(cols[1:], start=1):
            gi = index[c]
            mine_cell = row[i] if i < len(row) else ""
            game_cell = g[gi] if gi is not None and gi < len(g) else ""
            if gi is not None and mine_cell != game_cell:
                kept.append(row)
                break
    return cols, kept, len(mine.rows)


def _differs(mine: LangTable, theirs: LangTable, key: str) -> bool:
    """Whether a file gives a string other text than a game table has for it, in a language both have."""
    a, b = mine.row_of(key), theirs.row_of(key)
    langs = set(mine.languages)
    for i, c in enumerate(mine.columns[1:], start=1):
        j = theirs.column_index(c) if c in langs else None
        if j is not None and (a[i] if i < len(a) else "") != (b[j] if j < len(b) else ""):
            return True
    return False


_tables: dict[tuple, LangTable] = {}


def mod_table(lib: Library, mod: Mod, name: str) -> LangTable:
    """One of a mod's files, read; kept while the file stays as it is, so reading it again is free."""
    path = lib.file_path(mod, name)
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    table = _tables.get(key)
    if table is None:
        if len(_tables) > 200:
            _tables.clear()
        table = _tables[key] = read_table(path.read_bytes())
    return table


def mod_order(lib: Library, mod: Mod, game: GameLang) -> list[str]:
    """A mod's own files in the order they load: its copies of game tables, then its own list."""
    names = [n for n in mod.csv_on if game.own_table(n)]
    names += [what for kind, what in _sequence(lib, mod, game, ModReport(mod.id)) if kind == "file"]
    return names


def source_row(lib: Library, game: GameLang, source: str, key: str,
               orders: dict | None = None) -> tuple[list[str], list[str]] | None:
    """The row a source gives a key, as its file has it: (columns, row). The game's own, or a mod's,
    switched on or not; None if it does not set the key."""
    if source == GAME:
        return game.row(key)
    mod = lib.get(source)
    if mod is None:
        return None
    orders = {} if orders is None else orders
    if mod.id not in orders:
        orders[mod.id] = mod_order(lib, mod, game)
    for name in reversed(orders[mod.id]):
        try:
            table = mod_table(lib, mod, name)
        except OSError:
            continue
        row = table.row_of(key)
        if row is not None:
            return table.columns, row
    return None


def _sequence(lib: Library, mod: Mod, game: GameLang, report: ModReport) -> list[tuple[str, str]]:
    """What a mod loads, in its own order: ("file", name) or ("regional", ref)."""
    shipped = {n.lower(): n for n in mod.csv_on}
    switched_off = {n.lower() for n in mod.csv_names} - set(shipped)
    base_refs = game.loc_table()
    base_lower = {_name(r).lower() for r in base_refs}
    seq: list[tuple[str, str]] = []
    if mod.has_list:
        text = lib.read_file(mod, next(n for n in mod.files if n.lower() == LIST_NAME)).decode("utf-8-sig", "replace")
        root, problems = blk.parse_text(text)
        report.notes += [f"{LIST_NAME}: {p}" for p in problems]
        table = root.block("locTable")
        refs = [v for v in (table.values("file") if table else []) if isinstance(v, str)]
        listed: set[str] = set()
        regional = {r.lower() for r in game.regional_files()}

        def own(n: str) -> str | None:
            """The shipped file a ``%lang/`` entry names. One in a subfolder (WTHLM's optional
            packages) comes as a module of its own, so it is found by its file name alone."""
            low = n.lower()
            if low in shipped:
                return shipped[low]
            alt = shipped.get(low.rsplit("/", 1)[-1])
            return alt if alt and mod.files[alt].origin != "main" and alt.lower() not in listed else None

        for ref in refs:
            if ref.startswith("%lang/"):
                n = _name(ref)
                low = n.lower()
                have = own(n)
                listed.add(low)
                if have:
                    listed.add(have.lower())
                if have and not game.own_table(have):
                    seq.append(("file", have))
                elif have or low in base_lower or game.is_base_file(n):
                    continue            # the game's own file, or the mod's copy of one (loaded cut down, first)
                elif low in switched_off or low.rsplit("/", 1)[-1] in switched_off:
                    continue            # a module the player switched off
                elif mod.prefix and low.rsplit("/", 1)[-1].startswith(mod.prefix.lower()):
                    report.not_shipped.append(n)
                else:
                    report.gone.append(n)
            elif ref.startswith("%langRegional/"):
                if _name(ref).lower() in regional or not regional:
                    seq.append(("regional", ref))
                    if _name(ref) not in report.regional:
                        report.regional.append(_name(ref))
                else:
                    report.gone.append(ref)
            else:
                report.notes.append(tr("Ignored an entry its list has: {entry}", entry=ref))
        # Its own files among the event tables load after all of locTable, over those tables: here, after
        # its other files. The game's own event tables are the game's to list.
        for ref in root.values("regional"):
            if isinstance(ref, str) and ref.startswith("%lang/"):
                have = own(_name(ref))
                if have and have.lower() not in listed and not game.own_table(have):
                    listed.add(have.lower())
                    seq.append(("file", have))
        # A game file whose line the author commented out is left out on purpose (IFN1 replaces the game's
        # loading tips with its own); one the list simply lacks is newer than the list, and loads.
        for m in _COMMENTED.finditer(text):
            low = m.group(1).lower()
            if low in base_lower and low not in listed and m.group(1) not in report.left_out:
                report.left_out.append(m.group(1))
        off = {n.lower() for n in report.left_out}
        report.stale_list = [_name(r) for r in base_refs if _name(r).lower() not in listed | off]
        for low, n in shipped.items():
            if low in listed or game.own_table(n):
                continue
            f = mod.files[n]
            if f.origin != "main":
                seq.append(("file", n))   # a module newer than the mod's list: load it last
                report.notes.append(tr("{file} is not in the mod's list; it loads after the mod's own files.",
                                       file=n))
            else:
                report.unlisted.append(n)
    else:
        seq = [("file", shipped[low]) for low in sorted(shipped) if not game.own_table(shipped[low])]
    return seq


def build_plan(lib: Library, game: GameLang) -> Plan:
    base_refs = game.loc_table()
    taken = {n.lower() for n in game.base_files()} | {LIST_NAME}
    placements: list[Placement] = []
    reports: dict[str, ModReport] = {}
    appended: list[str] = []
    # Regional tables a mod loads inside locTable (IFN1, WTHLM), so its own files can override them.
    # Each loads once, ahead of every mod: loaded again before a later mod, it would put the game's
    # text back over what the mods before that one changed.
    regional_first: list[str] = []
    pulled_regional: set[str] = set()
    placed: dict[tuple[str, str], str] = {}

    def place(mod_id: str, source: str, data: bytes | None = None, want: str | None = None) -> str:
        key = (mod_id, source or want or "")
        if key in placed:
            return placed[key]
        dest = want or source
        if dest.lower() in taken:
            dest = f"{mod_id}__{dest}"
            reports[mod_id].renamed.append((source or want or "", dest))
        n = 2
        while dest.lower() in taken:
            dest = f"{mod_id}{n}__{want or source}"
            n += 1
        taken.add(dest.lower())
        placements.append(Placement(dest, mod_id, source, data))
        placed[key] = dest
        return dest

    wanted = {k: v for k, v in (lib.settings.get("picks") or {}).items() if isinstance(k, str) and isinstance(v, str)}
    picked: dict[str, str] = {}

    def place_picks() -> None:
        if not wanted:
            return
        report = reports[PICKS_ID] = ModReport(PICKS_ID)
        groups: dict[tuple, list[list[str]]] = {}
        orders: dict[str, list[str]] = {}
        for key, source in sorted(wanted.items()):
            found = source_row(lib, game, source, key, orders)
            if found is None:
                if source == GAME:
                    note = tr("{key}: the game no longer has it, so that pick waits.", key=key)
                elif lib.get(source) is not None:
                    note = tr("{key}: {mod} no longer sets it, so that pick waits.", key=key, mod=lib.get(source).name)
                else:
                    note = tr("{key}: the mod picked for it was removed, so that pick waits.", key=key)
                report.notes.append(note)
                continue
            cols, row = found
            groups.setdefault(tuple(cols), []).append(list(row[:len(cols)]))
            picked[key] = source
        # One file per layout of columns, so no row gets a language it did not have.
        for n, (cols, rows) in enumerate(groups.items(), 1):
            dest = place(PICKS_ID, "", write_table(list(cols), rows),
                         want=PICKS_FILE if n == 1 else f"langmod_picks_{n}.csv")
            appended.append(f"%lang/{dest}")
            report.loads.append(dest)

    enabled = lib.enabled()
    # Picks win over every mod, but the player's own strings, loading last, still win over picks.
    own_last = bool(enabled) and enabled[-1].personal
    for i, mod in enumerate(enabled):
        if own_last and i == len(enabled) - 1:
            place_picks()
        report = reports[mod.id] = ModReport(mod.id)
        # Full copies of the game's own tables go first, cut down to what the mod changed,
        # so that the mod's own files still override them as their author meant.
        for n in mod.csv_on:
            ref = game.own_table(n)
            if ref is None:
                continue
            cols, rows, total = overlay_rows(lib.read_file(mod, n), game.resolve(ref) or b"")
            report.replaced.append((n, len(rows), total))
            if rows:
                dest = place(mod.id, "", write_table(cols, rows), want=f"{mod.id}__{n}")
                appended.append(f"%lang/{dest}")
                report.loads.append(dest)
        for kind, what in _sequence(lib, mod, game, report):
            if kind == "regional":
                if what.lower() not in pulled_regional:
                    pulled_regional.add(what.lower())
                    regional_first.append(what)
                continue
            dest = place(mod.id, what)
            appended.append(f"%lang/{dest}")
            report.loads.append(dest)
    if not own_last:
        place_picks()
    # The game loads its event tables after locTable, over everything in it. Where a mod, a pick or the
    # player's own text gives one of their strings other text, that table loads ahead of the mods instead,
    # as IFN1 loads its own, or the change would never show. A row that only repeats the table moves
    # nothing: every table ends in an empty CLIPPED_LANG_KEEP_IT_ALWAYS_FIRST row, and mods copy it.
    game_regional = [r for r in game.localization.values("regional") if isinstance(r, str)]
    held = [r for r in game_regional if r.lower() not in pulled_regional]
    if held and placements:
        keyed: dict[str, list[tuple[str, LangTable]]] = {}
        for ref in held:
            regional = game.table(ref)
            for key in (regional.keys() if regional else ()):
                keyed.setdefault(key, []).append((ref, regional))
        changed_by: dict[str, set[str]] = {}
        for p in placements:
            try:
                table = read_table(p.data) if p.data is not None else mod_table(lib, lib.get(p.mod_id), p.source)
            except OSError:
                continue                  # gone from the mod's folder: Apply will say so
            for key in table.keys():
                for ref, regional in keyed.get(key, ()):
                    if _differs(table, regional, key):
                        changed_by.setdefault(ref, set()).add(p.mod_id)
        for ref in held:
            if ref in changed_by:
                pulled_regional.add(ref.lower())
                regional_first.append(ref)
                for mod_id in changed_by[ref]:
                    reports[mod_id].regional.append(_name(ref))
    # Among themselves, in the game's order: where two have a string, the game's choice of text holds.
    order = {r.lower(): i for i, r in enumerate(game_regional)}
    regional_first.sort(key=lambda r: order.get(r.lower(), len(order)))
    appended = regional_first + appended

    root = copy.deepcopy(game.localization)
    table = root.block("locTable")
    if table is None:
        table = blk.Block("locTable")
        root.blocks.insert(0, table)
    left_out = {n.lower() for mod in enabled if mod.id in reports for n in reports[mod.id].left_out}
    kept = [r for r in base_refs if not (r.startswith("%lang/") and _name(r).lower() in left_out)]
    table.params = [p for p in table.params
                    if not (p.name == "file" and isinstance(p.value, str) and p.value not in kept
                            and p.value in base_refs)]
    table.params += [blk.Param("file", "t", ref) for ref in appended]
    # A regional table a mod loads inside locTable (so its own files can override it)
    # is taken off the regional list, as the mod's own list does.
    root.params = [p for p in root.params
                   if not (p.name == "regional" and str(p.value).lower() in pulled_regional)]
    text = HEADER.format(version=game.version) + blk.to_text(root)
    last = [str(p.value) for p in root.params if p.name == "regional"]
    return Plan(game.version, game.stamp, text, placements, reports, kept + appended, picked,
                regional_first, last, [r for r in base_refs if r not in kept])


def placement_bytes(lib: Library, p: Placement) -> bytes:
    if p.data is not None:
        return p.data
    mod = lib.get(p.mod_id)
    return lib.read_file(mod, p.source)


def plan_files(lib: Library, plan: Plan) -> dict[str, bytes]:
    """Every file the plan puts in the lang folder, the load list included."""
    out = {p.dest: placement_bytes(lib, p) for p in plan.placements}
    out[LIST_NAME] = plan.localization.replace("\n", "\r\n").encode("utf-8")
    return out
