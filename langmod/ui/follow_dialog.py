"""Say where a mod's new versions are published: paste its page."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from ..core import sources
from ..core.library import Mod
from ..core.sources import Source
from ..i18n import tr
from .themes import tokens
from .widgets import GlowButton


class FollowDialog(QDialog):
    def __init__(self, mod: Mod, current: Source | None, parent=None):
        super().__init__(parent)
        self.mod = mod
        self.source: Source | None = None
        self.setWindowTitle(tr("Where {mod} updates from", mod=mod.name))
        self.setMinimumWidth(560)
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 18, 20, 16)
        box.setSpacing(10)
        title = QLabel(tr("Where does {mod} publish new versions?", mod=mod.name), objectName="appTitle")
        title.setWordWrap(True)
        box.addWidget(title)
        intro = QLabel(tr("Paste its page, as it appears in your browser's address bar:") + "<br>• "
                       + tr("a <b>WT Live</b> post, or the author's WT Live profile") + "<br>• "
                       + tr("a <b>GitHub</b> repository with releases")
                       + ("<br>• " + tr("a <b>Nexus Mods</b> page (needs your API key, set in Settings → Updates)")
                          if sources.NEXUS else ""),
                       objectName="hint")
        intro.setWordWrap(True)
        box.addWidget(intro)
        self.link = QLineEdit(placeholderText="https://live.warthunder.com/post/…")
        if current is not None:
            self.link.setText(current.page.replace("?tab=files", "").replace("/releases", ""))
        self.link.textChanged.connect(self._check)
        box.addWidget(self.link)
        only = QLabel(tr("Only files with this word in their name (for an author with several mods):"),
                      objectName="faint")
        only.setWordWrap(True)
        box.addWidget(only)
        self.match = QLineEdit(placeholderText=tr("for example IFN1 or LOP; leave empty to take any"))
        self.match.setText((current.match if current else "") or mod.prefix.rstrip("_"))
        box.addWidget(self.match)
        self.verdict = QLabel("", objectName="faint")
        self.verdict.setWordWrap(True)
        box.addWidget(self.verdict)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = GlowButton(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        self.ok = GlowButton(tr("Follow"), objectName="primary", light=True)
        self.ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        box.addLayout(row)
        self._check()

    def _check(self) -> None:
        found = sources.parse_link(self.link.text())
        t = tokens()
        if not self.link.text().strip():
            self.verdict.setText("")
        elif found is None:
            nexus = "nexusmods.com" in self.link.text().lower() and not sources.NEXUS
            self.verdict.setText(f"<span style='color:{t['warn']}'>"
                                 + (tr("This copy of the manager does not check Nexus Mods. If the mod is on WT Live "
                                       "or GitHub too, paste that page.") if nexus
                                    else tr("That is not an address on {places}.", places=sources.places()))
                                 + "</span>")
        else:
            what = {"wtlive": tr("It will look at the author's newest post with a file.")
                    if found.ref.startswith("user:")
                    else tr("It will look at this post's file (authors often swap it for the new version)."),
                    "github": tr("It will look at the newest release."),
                    "nexus": tr("It will look at the newest main file.")}[found.kind]
            self.verdict.setText(f"<span style='color:{t['ok']}'>{found.place}</span>: {what}")
        self.ok.setEnabled(found is not None)

    def _accept(self) -> None:
        found = sources.parse_link(self.link.text())
        if found is None:
            return
        found.match = self.match.text().strip()
        self.source = found
        self.accept()
