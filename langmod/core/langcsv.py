"""The game's language tables: semicolon-separated, double-quoted CSV.

The game's own files quote every field and double inner quotes. Mods are
typed by hand and are far looser: IFN1 alone has rows of one to four fields,
unquoted text with ``""`` in it, quoted text running over several lines,
and hundreds of comment and separator rows (``;``, ``-- Republic of China;``,
``Filename goes in this column.;...``) that the game simply loads as keys
nobody asks for.

Files are only ever *read* here to report on them. The manager copies a
mod's files byte for byte, so nothing this reader gets wrong can change what
the game sees.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from ..i18n import tr

ID_COLUMN = "ID|readonly|noverify"
_CELL = re.compile(r'^\s*"?<?(.*?)>?"?\s*$', re.S)


def column_name(cell: str) -> str:
    """``"<English>"`` -> ``English``."""
    return _CELL.match(cell).group(1).strip()


@dataclass
class LangTable:
    columns: list[str]                       # column names, the ID column first
    rows: list[list[str]]                    # every row after the header, as read
    problems: list[str] = field(default_factory=list)
    _index: dict | None = field(default=None, repr=False, compare=False)

    @property
    def languages(self) -> list[str]:
        return [c for c in self.columns[1:] if c not in ("Comments", "max_chars")]

    def column_index(self, language: str) -> int | None:
        low = language.lower()
        for i, c in enumerate(self.columns):
            if c.lower() == low:
                return i
        return None

    def texts(self, language: str = "English") -> dict[str, str]:
        """Key -> text in one language. A key repeated in the file: the last row wins."""
        col = self.column_index(language)
        if col is None:
            return {}
        out: dict[str, str] = {}
        for row in self.rows:
            if row and col < len(row):
                out[row[0]] = row[col]
            elif row:
                out[row[0]] = ""
        return out

    def keys(self) -> list[str]:
        return [row[0] for row in self.rows if row]

    def row_of(self, key: str) -> list[str] | None:
        """The row for a key, the last one if it is there twice (as the game reads it)."""
        if self._index is None:
            self._index = {row[0]: row for row in self.rows if row}
        return self._index.get(key)

    def duplicate_keys(self) -> list[str]:
        seen: set[str] = set()
        dups: list[str] = []
        for k in self.keys():
            if k in seen and k not in dups:
                dups.append(k)
            seen.add(k)
        return dups


def decode(data: bytes) -> tuple[str, list[str]]:
    problems = []
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8"), problems
    except UnicodeDecodeError as exc:
        problems.append(tr("not valid UTF-8 at byte {byte}; odd characters will show in the game", byte=exc.start))
        return data.decode("utf-8", "replace"), problems


def read_table(data: bytes) -> LangTable:
    text, problems = decode(data)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=";", quotechar='"',
                        doublequote=True, strict=False)
    try:
        rows = list(reader)
    except csv.Error as exc:  # the reader is not strict
        problems.append(tr("could not be read past line {line}: {error}", line=reader.line_num, error=exc))
        rows = []
    if not rows:
        return LangTable([ID_COLUMN], [], problems)
    header = [column_name(c) for c in rows[0]]
    body = [r for r in rows[1:] if r]
    if body and _unterminated(text, body[-1]):
        problems.append(tr("a quote is never closed: everything after it reads as one string"))
    return LangTable(header, body, problems)


def _unterminated(text: str, last_row: list[str]) -> bool:
    last = last_row[-1] if last_row else ""
    return ("\n" in last or "\r" in last) and not text.rstrip().endswith('"')


def is_noise(key: str) -> bool:
    """Comment and spacer rows: empty keys, ``-- headings``, sentences."""
    k = key.strip()
    return not k or k.startswith(("--", "//", "#")) or " " in k


def _quote(cell: str) -> str:
    return '"' + cell.replace('"', '""') + '"'


def write_table(columns: list[str], rows: list[list[str]]) -> bytes:
    """Write in the game's own style: every cell quoted, CRLF, UTF-8."""
    lines = [";".join(_quote(f"<{c}>") for c in columns)]
    for row in rows:
        lines.append(";".join(_quote(c) for c in row))
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")
