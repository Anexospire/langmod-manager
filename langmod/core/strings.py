"""The player's say over single strings.

Two ways, both kept through updates of the mods:

* a **pick**: which source a string shows, the game's own text or one
  mod's, whatever the load order says (:mod:`.plan` writes the picks);
* **their own text**, kept in the "My changes" layer, which loads after
  everything else. There is one file per language, each with that one
  language's column, so text typed for one language never blanks another.
"""
from __future__ import annotations

from .langcsv import ID_COLUMN, read_table, write_table
from .library import PERSONAL_FILE, Library, slug
from .plan import GAME

PICKS = "picks"          # the library setting: key -> "game" or a mod id


def picks(lib: Library) -> dict[str, str]:
    raw = lib.settings.get(PICKS)
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(raw, dict) else {}


def pick(lib: Library, key: str, source: str) -> None:
    """Show ``source``'s text for ``key``: "game", or a mod's id."""
    if source != GAME and lib.get(source) is None:
        raise ValueError(f"no mod {source!r}")
    chosen = picks(lib)
    chosen[key] = source
    lib.settings[PICKS] = chosen
    lib.save()


def unpick(lib: Library, key: str) -> bool:
    chosen = picks(lib)
    if chosen.pop(key, None) is None:
        return False
    lib.settings[PICKS] = chosen
    lib.save()
    return True


def own_file(language: str) -> str:
    """The file of the player's own strings for a language: English is the one there always was."""
    return PERSONAL_FILE if language.lower() == "english" else f"zz_my_changes_{slug(language)}.csv"


def own_text(lib: Library, key: str, language: str) -> str | None:
    """The player's own text for a string, if they have given it one in that language."""
    mod = lib.personal(create=False)
    if mod is None:
        return None
    found = None
    for name in mod.csv_names:
        try:
            table = read_table(lib.read_file(mod, name))
        except OSError:
            continue
        texts = table.texts(language)
        if key in texts:
            found = texts[key]
    return found


def own_keys(lib: Library, language: str) -> list[str]:
    """Every string the player has given text of their own in a language, in the order they did."""
    mod = lib.personal(create=False)
    keys: list[str] = []
    for name in mod.csv_names if mod is not None else []:
        try:
            table = read_table(lib.read_file(mod, name))
        except OSError:
            continue
        if table.column_index(language) is not None:
            keys += [k for k in table.texts(language) if k not in keys]
    return keys


def ensure_own_file(lib: Library, language: str) -> str:
    """The player's file for a language, made with just its header if there is none yet; its name."""
    mod = lib.personal()
    name = _file_with(lib, mod, language)
    if name is None:
        name = own_file(language)
        lib.update_file(mod, name, write_table([ID_COLUMN, language], []))
    return name


def set_own_text(lib: Library, key: str, language: str, text: str) -> str:
    """Give a string the player's own text; returns the file it went in. The layer is switched on."""
    mod = lib.personal()
    name = _file_with(lib, mod, language) or own_file(language)
    try:
        table = read_table(lib.read_file(mod, name))
    except OSError:
        table = read_table(b"")
    columns, rows = table.columns or [ID_COLUMN], table.rows
    if table.column_index(language) is None:           # a column for it, next to what the file already has
        columns = columns + [language]
        rows = [r + [""] * (len(columns) - len(r)) for r in rows]
    col = columns.index(next(c for c in columns if c.lower() == language.lower()))
    for row in [r for r in rows if r and r[0] == key]:
        rows.remove(row)
    new = [key] + [""] * (len(columns) - 1)
    new[col] = text
    rows.append(new)
    lib.update_file(mod, name, write_table(columns, rows))
    if not mod.enabled:
        lib.set_enabled(mod.id, True)
    return name


def clear_own_text(lib: Library, key: str, language: str) -> bool:
    """Take a string's own text out again, in that language."""
    mod = lib.personal(create=False)
    if mod is None:
        return False
    gone = False
    for name in mod.csv_names:
        try:
            table = read_table(lib.read_file(mod, name))
        except OSError:
            continue
        if table.column_index(language) is None:
            continue
        rows = [r for r in table.rows if not (r and r[0] == key)]
        if len(rows) != len(table.rows):
            lib.update_file(mod, name, write_table(table.columns, rows))
            gone = True
    return gone


def _file_with(lib: Library, mod, language: str) -> str | None:
    """The player's file that already has a column for this language: the usual one first."""
    names = sorted(mod.csv_names, key=lambda n: (n != own_file(language), n != PERSONAL_FILE, n))
    for name in names:
        try:
            if read_table(lib.read_file(mod, name)).column_index(language) is not None:
                return name
        except OSError:
            continue
    return None
