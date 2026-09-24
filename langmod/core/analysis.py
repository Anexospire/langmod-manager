"""What each mod changes, where two mods fight over the same string, and every string's text.

Reading every table takes about a second, so this runs apart from
building the plan, and the interface does it in the background. What it
keeps - the game's text and each mod's for every string - is what the
window's string search looks through.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .game import GameLang
from .langcsv import is_noise, read_table
from .library import Library
from .plan import GAME, PICKS_ID, Plan, mod_table, placement_bytes
from ..i18n import ntr

SEARCH_LIMIT = 500
RANK_LIMIT = 20000         # more matches than this are listed as found, unranked: the needle is too short to mean much


def _rank(hay: str, n: str) -> tuple:
    key, *texts = hay.split("\n")
    matching = [t for t in texts if n in t]
    return (key != n, not key.startswith(n), n not in texts, not any(t.startswith(n) for t in matching),
            min((len(t) for t in matching), default=len(key)))


@dataclass
class ModStats:
    mod_id: str
    rows: int = 0              # every row in the files it loads
    changes: int = 0           # keys whose text differs from the game's
    same: int = 0              # keys it sets to exactly what the game already says
    new: int = 0               # keys the game does not have (often old vehicles or typos)
    noise: int = 0             # comment and spacer rows
    languages: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # "file: problem"
    missing_language: bool = False


@dataclass
class Conflict:
    key: str
    game: str                                # the game's own text ("" if none)
    texts: list[tuple[str, str]]             # (mod id, text), in load order
    shown: tuple[str, str] = ("", "")        # (source, text) the game shows: a mod id, or "game"
    picked: str = ""                         # the source the player picked, if they did

    @property
    def winner(self) -> str:
        return self.shown[0] or self.texts[-1][0]


@dataclass
class Analysis:
    language: str
    stats: dict[str, ModStats]
    conflicts: list[Conflict]
    final: dict[str, tuple[str, str]]        # key -> (text, source) for every key a mod or a pick sets
    base: dict = field(default_factory=dict)               # key -> (text, table): the game's own
    per_mod: dict[str, dict[str, str]] = field(default_factory=dict)   # mod id -> key -> text, load order
    picked: dict[str, str] = field(default_factory=dict)   # key -> source, for picks that show
    left_out: set[str] = field(default_factory=set)        # the game's tables a mod leaves out: their text never shows
    _keys: list[str] = field(default_factory=list, repr=False)
    _hay: list[str] = field(default_factory=list, repr=False)

    def sources(self, key: str) -> list[tuple[str, str]]:
        """Everything that sets a string: ("game", its text) if the game has it, then each mod that
        changes it, in load order."""
        out = [(GAME, self.base[key][0])] if key in self.base else []
        return out + [(mod_id, texts[key]) for mod_id, texts in self.per_mod.items() if key in texts]

    def shown(self, key: str) -> tuple[str, str]:
        """(source, text) the game shows for a string: a mod id, or "game"."""
        if key in self.final:
            text, source = self.final[key]
            return source, text
        if key in self.base and self.base[key][1] not in self.left_out:
            return GAME, self.base[key][0]
        return "", ""

    def search(self, needle: str, limit: int = SEARCH_LIMIT) -> tuple[list[str], int]:
        """Strings whose ID or any text has ``needle`` in it, any case. Names before sentences: the ID
        itself, then texts that are the needle, then ones that start with it, then the shortest texts
        that have it (a vehicle's name before a loading tip that mentions it). Returns up to ``limit``
        and how many there are."""
        n = needle.strip().lower()
        if not n:
            return [], 0
        if not self._hay:
            self._index()
        hay = self._hay
        hits = [i for i, h in enumerate(hay) if n in h]
        if len(hits) <= RANK_LIMIT:
            hits.sort(key=lambda i: _rank(hay[i], n))
        return [self._keys[i] for i in hits[:limit]], len(hits)

    def _index(self) -> None:
        keys = list(self.base)
        seen = set(keys)
        for texts in self.per_mod.values():
            for k in texts:
                if k not in seen and not is_noise(k):
                    seen.add(k)
                    keys.append(k)
        mods = list(self.per_mod.values())
        hay = []
        for k in keys:
            parts = [k]
            b = self.base.get(k)
            if b is not None:
                parts.append(b[0])
            parts += [t[k] for t in mods if k in t]
            hay.append("\n".join(parts).lower())
        self._keys, self._hay = keys, hay


def _table(lib: Library, p):
    if p.data is None and p.source:
        mod = lib.get(p.mod_id)
        if mod is not None:
            try:
                return mod_table(lib, mod, p.source)
            except OSError:
                pass
    return read_table(placement_bytes(lib, p))


def analyze(lib: Library, game: GameLang, plan: Plan, language: str = "English") -> Analysis:
    base = game.texts(language)
    per_mod: dict[str, dict[str, str]] = {}
    stats: dict[str, ModStats] = {}
    order: list[str] = []            # mods, in load order; the picks' place among them is marked
    pick_texts: dict[str, str] = {}
    after_picks: set[str] = set()
    picks_seen = False
    for p in plan.placements:
        if p.mod_id == PICKS_ID:
            if not picks_seen:
                picks_seen = True
                for dest in plan.reports[PICKS_ID].loads:
                    q = plan.placement(dest)
                    if q is not None:
                        pick_texts.update(_table(lib, q).texts(language))
            continue
        if p.mod_id not in per_mod:
            per_mod[p.mod_id] = {}
            stats[p.mod_id] = ModStats(p.mod_id)
            order.append(p.mod_id)
            if picks_seen:
                after_picks.add(p.mod_id)
    # Walk each mod's load order, entries repeated included, as the game would.
    for mod_id in order:
        s = stats[mod_id]
        texts = per_mod[mod_id]
        langs: set[str] = set()
        seen_files: set[str] = set()
        doubled: list[tuple[str, str]] = []
        for dest in plan.reports[mod_id].loads:
            table = _table(lib, plan.placement(dest))
            if dest not in seen_files:
                seen_files.add(dest)
                s.rows += len(table.rows)
                s.problems += [f"{dest}: {x}" for x in table.problems]
                doubled += [(dest, k) for k in table.duplicate_keys() if not is_noise(k)]
            langs.update(table.languages)
            texts.update(table.texts(language))
        if doubled:
            files = sorted({f for f, _ in doubled})
            s.problems.append(ntr(len(doubled), "{n} string ID is set twice inside the same file ({files}); the "
                                                "later row wins. First one: {key} in {file}",
                                  "{n} string IDs are set twice inside the same file ({files}); the later row "
                                  "wins. First one: {key} in {file}",
                                  files=ntr(len(files), "{n} file", "{n} files"), key=doubled[0][1],
                                  file=doubled[0][0]))
        s.languages = sorted(langs)
        s.missing_language = bool(langs) and language not in langs
        for key, text in texts.items():
            if key in base:
                if base[key][0] == text:
                    s.same += 1
                else:
                    s.changes += 1
            elif is_noise(key):
                s.noise += 1
            else:
                s.new += 1

    final: dict[str, tuple[str, str]] = {}
    setters: dict[str, list[tuple[str, str]]] = {}
    for mod_id in order:
        for key, text in per_mod[mod_id].items():
            if is_noise(key) and key not in base:
                continue
            setters.setdefault(key, []).append((mod_id, text))
            final[key] = (text, mod_id)
    # A pick shows unless a mod loading after the picks (the player's own strings) sets the string too.
    picked: dict[str, str] = {}
    for key, source in plan.picks.items():
        if key not in pick_texts:
            continue
        late = [m for m, _t in setters.get(key, []) if m in after_picks]
        if late:
            continue
        final[key] = (pick_texts[key], source)
        picked[key] = source
    conflicts = []
    for key, texts in setters.items():
        if len(texts) > 1 and len({t for _, t in texts}) > 1:
            text, source = final[key]
            conflicts.append(Conflict(key, base[key][0] if key in base else "", texts, (source, text),
                                      plan.picks.get(key, "")))
    conflicts.sort(key=lambda c: c.key)
    an = Analysis(language, stats, conflicts, final, base, {m: per_mod[m] for m in order}, picked,
                  set(plan.left_out))
    an._index()
    return an
