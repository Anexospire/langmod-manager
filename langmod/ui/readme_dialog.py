"""A mod's read me, as its author wrote it: Markdown shown as such, plain text with its web links live."""
from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout

from ..i18n import tr
from .themes import tokens
from .widgets import GlowButton

_LINK = re.compile(r"https?://[^\s<>\"')\]]+")


def as_html(text: str) -> str:
    """Plain text as it was laid out, its web addresses made into links."""
    t = tokens()
    out, at = [], 0
    for m in _LINK.finditer(text):
        url = m.group(0).rstrip(".,;:!?")
        out.append(html.escape(text[at:m.start()]))
        out.append(f"<a href='{html.escape(url)}' style='color:{t['accent']}'>{html.escape(url)}</a>")
        at = m.start() + len(url)
    out.append(html.escape(text[at:]))
    return f"<div style='white-space:pre-wrap; color:{t['text']}'>{''.join(out)}</div>"


class ReadmeDialog(QDialog):
    def __init__(self, name: str, path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("{mod}: read me", mod=name))
        self.resize(760, 640)
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 18, 20, 16)
        box.setSpacing(10)
        title = QLabel(tr("{mod}: read me", mod=name), objectName="appTitle")
        title.setWordWrap(True)
        box.addWidget(title)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        self.text.document().setDocumentMargin(12)
        body = path.read_bytes().decode("utf-8-sig", "replace")
        if path.suffix.lower() in (".md", ".markdown"):
            self.text.setMarkdown(body)
        else:
            self.text.setHtml(as_html(body))
        box.addWidget(self.text, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = GlowButton(tr("Close"), objectName="primary", light=True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        box.addLayout(row)
