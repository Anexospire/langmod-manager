"""The well-known language mods, to get in one click.

Each follows the page its author publishes on, the same as a mod added by
hand and followed, so getting one is looking up its newest release there
and adding the download (:func:`.updates.look`, :func:`.updates.fetch`,
:func:`.updates.take_new`). The lines about each were written from the
authors' own pages (September 2026).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..i18n import N_
from .library import Library, Mod
from .sources import Release, Source
from .updates import source_of, suggestion


@dataclass(frozen=True)
class Entry:
    key: str
    name: str
    author: str
    blurb: str
    source: Source
    languages: str = "English"

    @property
    def page(self) -> str:
        return self.source.page


CATALOG: tuple[Entry, ...] = (
    Entry("ifn1", "IFN1 Vehicle Re-Name Mod", "InFerNos1",
          N_("Detailed, accurate and consistent names for vehicles, guns and ammunition, for every nation. "
             "Updated for each major game version, in the same post."),
          Source("wtlive", "694386", "IFN1", "WT Live · InFerNos1")),
    Entry("lop", "Localization Overhaul Project", "Wiggly_Armed_Man",
          N_("Every vehicle under its full, official name. Each version is a new post, and the manager "
             "follows the author to find the newest."),
          Source("wtlive", "user:36978671", "LOP", "WT Live · Wiggly_Armed_Man")),
    Entry("wthlm", "War Tinder's Historical Localization Mod", "WarTinder",
          N_("Corrects the names of vehicles, weapons, engines and nations, and reworks the text of loading "
             "screens, tips and menus. Published as GitHub releases."),
          Source("github", "Addysaurus/lang_modding", "WTHLM", "GitHub · Addysaurus")),
)


def _same(a: Source | None, b: Source) -> bool:
    return a is not None and (a.kind, a.ref) == (b.kind, b.ref)


def installed(lib: Library, entry: Entry) -> Mod | None:
    """The mod here that is this one: it follows the same page, or is known by its name."""
    mods = [m for m in lib.mods if not m.personal]
    return (next((m for m in mods if _same(source_of(m), entry.source)), None)
            or next((m for m in mods if not m.follow and _same(suggestion(m), entry.source)), None))


_MADE_FOR = re.compile(r"game\s+version\s*:?\s*(?:updated\s+to\s*)?(\d+)\s*\.\s*(\d+)", re.I)


def made_for(rel: Release | None) -> str:
    """The game version a release says it was made for, as "2.59"; "" if it does not say."""
    m = _MADE_FOR.search(rel.title if rel else "")
    return f"{m.group(1)}.{m.group(2)}" if m else ""
