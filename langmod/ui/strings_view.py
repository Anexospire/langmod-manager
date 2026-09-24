"""Strings: find any text the game shows, see who sets it, and say which one shows.

Type a name, a word or a string ID ("F-16") and every string whose ID, game
text or mod text has it is listed: what the game says, and what shows with
your mods. Pick one and the card under the list gives every source's text -
the game's own and each mod's, in load order - with the one that shows
marked. "Show this" makes that source's text the one that shows, whatever
the order (a pick); or type text of your own, which wins over everything.
The Conflicts tab uses the same card.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QScrollArea,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import strings
from ..core.plan import GAME
from ..i18n import game_language, ntr, num, sentences, tr
from .themes import tokens
from .widgets import GlowButton, chip, set_tone

KEY_ROLE = Qt.ItemDataRole.UserRole + 11
SEARCH_DELAY_MS = 180
TABLE_LIMIT = 500


def _one_line(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ")


class _SourceRow(QWidget):
    """One source of a string's text: who, what they say, and a way to make it the one that shows."""

    def __init__(self, name: str, text: str, shown: bool, action: str = "", on_action=None, note: str = "",
                 always: bool = False):
        super().__init__(objectName="transparent")
        t = tokens()
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 2, 0, 2)
        line.setSpacing(10)
        who = QLabel(name)
        who.setFixedWidth(150)
        who.setWordWrap(True)
        who.setContentsMargins(0, 6, 0, 0)          # level with the text of the button across the row
        f = who.font()
        f.setBold(True)
        who.setFont(f)
        who.setStyleSheet(f"color: {QColor(t['accent'] if shown else t['muted']).name()};")
        said = QLabel(text if text else tr("(empty)"))
        said.setWordWrap(True)
        said.setContentsMargins(0, 6, 0, 0)
        said.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if not text:
            said.setStyleSheet(f"color: {QColor(t['faint']).name()};")
        elif shown:
            said.setStyleSheet(f"color: {QColor(t['text']).name()};")
        else:
            said.setStyleSheet(f"color: {QColor(t['muted']).name()};")
        line.addWidget(who, 0, Qt.AlignmentFlag.AlignTop)
        line.addWidget(said, 1, Qt.AlignmentFlag.AlignTop)
        self.button = None
        if shown:
            tag = chip(note or tr("Shows in game"), "ok")
            line.addWidget(tag, 0, Qt.AlignmentFlag.AlignTop)
        if action and (always or not shown):
            self.button = GlowButton(action)
            self.button.clicked.connect(on_action)
            line.addWidget(self.button, 0, Qt.AlignmentFlag.AlignTop)
        self.name, self.text = name, text


class StringCard(QFrame):
    """Every source of one string, which one shows, and the ways to change that."""

    pick = Signal(str, str)        # key, source ("game" or a mod id); "" goes back to the load order
    own = Signal(str, str)         # key, the player's own text; "" takes it out

    def __init__(self):
        super().__init__(objectName="transparent")
        self.key = ""
        box = QVBoxLayout(self)
        box.setContentsMargins(4, 8, 4, 4)
        box.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel("", objectName="panelTitle")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        mono = QFont("Cascadia Mono")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPixelSize(13)
        mono.setBold(True)
        self.title.setFont(mono)
        self.state = chip("", "neutral")
        head.addWidget(self.title)
        head.addWidget(self.state)
        head.addStretch(1)
        self.back = GlowButton(tr("Back to the load order"), objectName="link")
        self.back.setToolTip(tr("Forget the pick: the mod lowest in the list shows again"))
        self.back.clicked.connect(lambda: self.pick.emit(self.key, ""))
        head.addWidget(self.back)
        box.addLayout(head)
        self.note = QLabel("", objectName="faint")
        self.note.setWordWrap(True)
        box.addWidget(self.note)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        box.addLayout(self.rows)
        mine = QHBoxLayout()
        mine.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setClearButtonEnabled(True)
        self.edit.returnPressed.connect(self._save_own)
        self.save = GlowButton(tr("Use my text"), objectName="primary", light=True)
        self.save.clicked.connect(self._save_own)
        mine.addWidget(self.edit, 1)
        mine.addWidget(self.save)
        box.addLayout(mine)
        box.addStretch(1)
        self.empty = QLabel(tr("Pick a string above to see who sets it."), objectName="faint")
        box.addWidget(self.empty)
        self.show_string(None, None, {}, None, "English", "")

    def _save_own(self) -> None:
        if self.key and self.edit.text().strip():
            self.own.emit(self.key, self.edit.text())

    def row_widgets(self) -> list[_SourceRow]:
        return [self.rows.itemAt(i).widget() for i in range(self.rows.count())
                if isinstance(self.rows.itemAt(i).widget(), _SourceRow)]

    def show_string(self, key: str | None, an, names: dict[str, str], own: str | None, language: str,
                    wanted: str, own_id: str = "") -> None:
        """Show one string. ``names``: source -> what to call it; ``own``: the player's text for it;
        ``wanted``: the source they picked for it, if they did; ``own_id``: the player's own layer."""
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.key = key or ""
        has = bool(key) and an is not None
        for w in (self.title, self.state, self.note, self.edit, self.save):
            w.setVisible(has)
        self.empty.setVisible(not has)
        self.back.setVisible(has and bool(wanted))
        if not has:
            return
        self.title.setText(key)
        shown, _text = an.shown(key)
        sources = an.sources(key)
        for source, text in sources:
            is_own = bool(own_id) and source == own_id
            name = tr("The game") if source == GAME else (tr("Your own text") if is_own else names.get(source, source))
            if is_own:
                row = _SourceRow(name, text, source == shown, tr("Take it out"),
                                 lambda _=False, k=key: self.own.emit(k, ""), tr("Yours: shows in game"), always=True)
            else:
                row = _SourceRow(name, text, source == shown, tr("Show this"),
                                 lambda _=False, k=key, s=source: self.pick.emit(k, s),
                                 tr("Picked: shows in game") if an.picked.get(key) == source else "")
            self.rows.addWidget(row)
        if wanted and wanted not in [s for s, _t in sources]:
            # Picked a mod that is switched off: its text still shows, so list it too.
            text = an.final.get(key, ("", ""))[0] if an.picked.get(key) == wanted else ""
            self.rows.addWidget(_SourceRow(tr("{mod} (switched off)", mod=names.get(wanted, tr("a removed mod"))),
                                           text, shown == wanted, note=tr("Picked: shows in game")))
        if own is not None:
            set_tone(self.state, "ok", tr("your own text"))
        elif an.picked.get(key):
            set_tone(self.state, "ok", tr("picked"))
        elif sum(1 for s, _t in sources if s != GAME) > 1:
            set_tone(self.state, "warn", tr("mods disagree"))
        elif shown and shown != GAME:
            set_tone(self.state, "neutral", tr("changed by a mod"))
        else:
            set_tone(self.state, "neutral", tr("the game's own"))
        notes = []
        if wanted and an.picked.get(key) != wanted:
            if own is not None:
                notes.append(tr("Your own text wins over your pick; take it out and the pick shows."))
            else:
                notes.append(tr("You picked {mod}, but it no longer sets this string, so the load order decides for "
                                "now.", mod=names.get(wanted, tr("a mod that is gone"))))
        elif not wanted and len(sources) > 1:
            notes.append(tr("The lowest mod in the list that sets it shows. Show this makes another one show, "
                            "whatever the order."))
        self.note.setText(sentences(*notes))
        self.note.setVisible(bool(notes))
        self.edit.setPlaceholderText(tr("Your own text for this string ({language})",
                                        language=game_language(language)))
        self.edit.setText(own or "")


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
    table.verticalHeader().hide()
    table.setShowGrid(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setWordWrap(False)
    return table


def card_area(card: StringCard) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(card)
    return area


class StringsView(QWidget):
    """The Strings tab: a search over every string, the matches, and the card for the one picked."""

    def __init__(self, win):
        super().__init__(objectName="transparent")
        self.win = win
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 10, 12, 8)
        box.setSpacing(6)
        self.search = QLineEdit(placeholderText=tr("Search every string: a name, a word or a string ID (Ctrl+F)"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.timer.start())
        self.search.returnPressed.connect(self.run_search)
        box.addWidget(self.search)
        self.info = QLabel("", objectName="faint")
        self.info.setWordWrap(True)
        box.addWidget(self.info)
        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        self.table = _table([tr("STRING ID"), tr("THE GAME SAYS"), tr("SHOWN IN GAME")])
        for i, w in enumerate((200, 220)):
            self.table.setColumnWidth(i, w)
        self.table.currentCellChanged.connect(lambda *_: self.show_current())
        split.addWidget(self.table)
        self.card = StringCard()
        self.card.pick.connect(win.pick_string)
        self.card.own.connect(win.set_own_string)
        split.addWidget(card_area(self.card))
        split.setSizes([260, 240])
        box.addWidget(split, 1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(SEARCH_DELAY_MS)
        self.timer.timeout.connect(self.run_search)
        self.keys: list[str] = []

    def current_key(self) -> str | None:
        item = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        return item.data(KEY_ROLE) if item is not None else None

    def run_search(self, keep: str | None = None) -> None:
        """List what the box asks for; with it empty, the strings the player picked or wrote."""
        self.timer.stop()
        win = self.win
        an = win.analysis
        keep = keep or self.current_key()
        needle = self.search.text().strip()
        if an is None:
            self.keys = []
            self.info.setText(tr("Reading the game's text and your mods…") if win.plan is not None and win.plan.placements
                              else tr("Add a mod and switch it on to search the strings."))
        elif needle:
            self.keys, total = an.search(needle, TABLE_LIMIT)
            if not total:
                self.info.setText(tr("Nothing has “{text}” in its ID or text.", text=needle))
            elif total > len(self.keys):
                self.info.setText(ntr(total, "{n} string with “{text}”; the first {shown} are listed, type more to "
                                             "narrow it.",
                                      "{n} strings with “{text}”; the first {shown} are listed, type more to "
                                      "narrow it.", text=needle, shown=num(len(self.keys))))
            else:
                self.info.setText(ntr(total, "{n} string with “{text}”.", "{n} strings with “{text}”.", text=needle))
        else:
            language = win.language()
            mine = strings.own_keys(win.lib, language)
            picked = [k for k in strings.picks(win.lib) if k not in mine]
            self.keys = picked + mine
            self.info.setText(tr("Your picks and your own text: {count}. Search to find any other string.",
                                 count=num(len(self.keys)))
                              if self.keys else
                              tr("Search to find a string: what the game says, what each mod says, and which one "
                                 "shows. Then pick which shows, or type your own."))
        self._fill(keep)

    def _fill(self, keep: str | None) -> None:
        an = self.win.analysis
        names = self.win.source_names()
        t = tokens()
        accent, faint = QColor(t["accent"]), QColor(t["faint"])
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.keys))
        chosen = -1
        for i, key in enumerate(self.keys):
            game = an.base.get(key, ("", ""))[0] if an is not None else ""
            source, text = an.shown(key) if an is not None else ("", "")
            where = (tr("the game") if source == GAME else names.get(source, source)) if source else ""
            if an is not None and an.picked.get(key):
                where = tr("{source}, picked", source=where)
            cells = (key, game or "—", f"{text}   · {where}" if where else "—")
            for col, value in enumerate(cells):
                item = QTableWidgetItem(_one_line(value))
                item.setToolTip(value)
                if col == 0:
                    item.setData(KEY_ROLE, key)
                if col == 1 and not game:
                    item.setForeground(faint)
                if col == 2 and source and source != GAME:
                    item.setForeground(accent)
                self.table.setItem(i, col, item)
            if key == keep:
                chosen = i
        self.table.blockSignals(False)
        if chosen < 0 and self.keys and (keep is None or keep not in self.keys):
            chosen = 0 if len(self.keys) == 1 or self.search.text().strip() else -1
        if chosen >= 0:
            self.table.setCurrentCell(chosen, 0)
        self.show_current()

    def show_current(self) -> None:
        self.win.show_string_card(self.card, self.current_key())
