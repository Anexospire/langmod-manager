"""Get mods: the well-known language mods, each one click from being added.

Each card says what the mod is, who makes it and where, and - once the
authors' pages have been asked - what their newest release is and which
game version it was made for. "Get it" downloads that release, adds it and
has it follow its page, so it keeps itself up to date from then on.
"""
from __future__ import annotations

import html

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..core import catalog, updates
from ..core.catalog import CATALOG, Entry
from ..i18n import date as day
from ..i18n import dec, game_language, num, tr
from .icons import icon
from .themes import tokens
from .widgets import GlowButton, card, chip, set_tone


def _size(n: int) -> str:
    if n >= 1024 * 1024:
        return tr("{size} MB", size=dec(n / 1024 / 1024))
    return tr("{size} KB", size=num(max(1, n // 1024)))


class Offer(QFrame):
    """One mod on offer, and the one thing to do about it."""

    def __init__(self, entry: Entry, dialog: "GetModsDialog"):
        super().__init__()
        self.entry = entry
        self.dialog = dialog
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 14, 12)
        box.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.title = QLabel(entry.name, objectName="panelTitle")
        self.tag = chip("", "ok")
        top.addWidget(self.title)
        top.addWidget(self.tag)
        top.addStretch(1)
        self.page = GlowButton(tr("Its page"), objectName="link")
        self.page.setToolTip(entry.page)
        self.page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(entry.page)))
        self.button = GlowButton(tr("Get it"), objectName="primary", light=True)
        self.button.setMinimumWidth(116)
        self.button.clicked.connect(self._act)
        top.addWidget(self.page)
        top.addWidget(self.button)
        box.addLayout(top)
        who = QLabel(tr("by {author}", author=entry.author) + f" · {entry.source.place} · "
                     + game_language(entry.languages), objectName="muted")
        box.addWidget(who)
        blurb = QLabel(tr(entry.blurb), objectName="hint")
        blurb.setWordWrap(True)
        box.addWidget(blurb)
        self.status = QLabel("", objectName="faint")
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.action = ""

    def _act(self) -> None:
        self.dialog.act(self.entry, self.action)

    def show_state(self, found, have, busy: bool, game_version: str) -> None:
        """``found``: what its page said (an updates.Look, or None while asking); ``have``: the mod here."""
        t = tokens()
        rel = found.release if found is not None else None
        bits = []
        if found is None:
            bits.append(tr("Asking its page what is newest…"))
        elif found.error:
            bits.append(tr("Its page could not be read: {error}", error=found.error))
        elif rel is None:
            bits.append(tr("Its page has nothing to download right now."))
        else:
            line = tr("Newest: {file}", file=rel.file_name)
            if rel.version:
                line += " · " + tr("version {version}", version=rel.version)
            if rel.size:
                line += f" · {_size(rel.size)}"
            if rel.published:
                line += f" · {day(rel.published)}"
            bits.append(line)
            made = catalog.made_for(rel)
            if made:
                now = ".".join(game_version.split(".")[:2]) if game_version else ""
                bits.append(tr("Made for game {made}; the game is on {now} now. Strings added since show in the "
                               "game's own words until the mod catches up.", made=made, now=now)
                            if now and made != now else tr("Made for game {made}.", made=made))
        self.status.setText("<br>".join(html.escape(b) for b in bits))
        state = updates.state(have) if have is not None else ""
        self.tag.setVisible(have is not None)
        if have is not None:
            set_tone(self.tag, "ok", tr("you have {mod}", mod=have.label))
        self.button.setIcon(icon("check", t["muted"], 15) if have is not None and state != "update" else
                            icon("cloud", t["accent_text"], 15))
        if busy:
            self.action, text, on = "", tr("Getting it…") if self.dialog.getting == self.entry.key else tr("Get it"), False
        elif have is not None:
            if state == "update":
                self.action, text, on = "update", tr("Update"), True
            else:
                self.action, text, on = "", tr("You have it"), False
        elif found is None:
            self.action, text, on = "", tr("Get it"), False
        elif found.error:
            self.action, text, on = "retry", tr("Try again"), True
        elif rel is not None and rel.downloadable:
            self.action, text, on = "get", tr("Get it"), True
        else:
            self.action, text, on = "", tr("Get it"), False
        self.button.setText(text)
        self.button.setEnabled(on)


class GetModsDialog(QDialog):
    """The catalogue, a card a mod."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.found: dict[str, updates.Look] = {}
        self.getting = ""
        self.setWindowTitle(tr("Get mods"))
        self.setMinimumSize(640, 520)
        self.resize(760, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(QLabel(tr("Get mods"), objectName="appTitle"))
        head.addWidget(QLabel(tr("well-known language mods, fetched from where their authors publish them"),
                              objectName="faint"))
        head.addStretch(1)
        root.addLayout(head)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget(objectName="transparent")
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 8, 0)
        box.setSpacing(10)
        self.offers = [Offer(e, self) for e in CATALOG]
        for o in self.offers:
            box.addWidget(o)
        note = card()
        nl = QVBoxLayout(note)
        nl.setContentsMargins(16, 10, 16, 10)
        more = QLabel(tr("Another mod? Download it as its author says, then use <b>Add</b>, or drop the zip or 7z on "
                         "the window. Give it its page with Follow, in its details, and it updates like these do."),
                      objectName="hint")
        more.setWordWrap(True)
        nl.addWidget(more)
        box.addWidget(note)
        box.addStretch(1)
        area.setWidget(inner)
        root.addWidget(area, 1)
        buttons = QHBoxLayout()
        self.info = QLabel("", objectName="faint")
        buttons.addWidget(self.info, 1)
        close = GlowButton(tr("Close"), objectName="primary", light=True)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)
        self.refresh()
        win.refreshed.connect(self.refresh)
        self.look()

    def look(self) -> None:
        """Ask each mod's page what is newest, in the background."""
        self.found = {}
        self.refresh()
        key = self.win.prefs.get("nexus_key")
        asks = [(e.key, e.source) for e in CATALOG]
        self.win._run(lambda: [updates.look(k, s, key) for k, s in asks], self._looked, self._look_failed)

    def _looked(self, looks) -> None:
        self.found = {f.mod_id: f for f in looks}
        self.refresh()

    def _look_failed(self, text: str) -> None:
        last = text.strip().splitlines()[-1]
        self.found = {e.key: updates.Look(e.key, e.source, None, last) for e in CATALOG}
        self.refresh()

    def refresh(self) -> None:
        busy = bool(self.getting) or self.win._busy_updating
        version = self.win.game.version if self.win.game is not None else ""
        for o in self.offers:
            o.show_state(self.found.get(o.entry.key), catalog.installed(self.win.lib, o.entry), busy, version)
        self.info.setText(tr("Getting a mod adds it to your list, switched on. Apply, or Play, puts it in the game.")
                          if not busy else tr("Downloading…"))

    def act(self, entry: Entry, action: str) -> None:
        if action == "retry":
            self.look()
        elif action == "update":
            have = catalog.installed(self.win.lib, entry)
            if have is not None:
                self.win.install_updates([have])
        elif action == "get":
            self.getting = entry.key
            self.refresh()
            self.win.fetch_mods([(entry.name, entry.source)], self._got)

    def _got(self, added, failed) -> None:
        self.getting = ""
        self.refresh()
        if added:
            self.info.setText(tr("Added {mod}: it is in your list, switched on. Apply, or Play, puts it in the game.",
                                 mod=added[0].label))
        elif failed:
            self.info.setText(tr("Could not get it: {error}", error=failed[0]))
