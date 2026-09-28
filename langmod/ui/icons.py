"""Small inline SVG icons, tinted to the theme."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_S = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
_SVGS = {
    "bubbles": '<path d="M9 4h9a3 3 0 0 1 3 3v4a3 3 0 0 1-3 3h-1" ' + _S + ' opacity="0.55"/>'
               '<path d="M5 8h9a3 3 0 0 1 3 3v4a3 3 0 0 1-3 3H9l-4 3v-3a3 3 0 0 1-3-3v-4a3 3 0 0 1 3-3z"/>',
    "plus": f'<path d="M12 5v14M5 12h14" {_S}/>',
    "apply": f'<path d="M12 3v11m0 0l-4-4m4 4l4-4" {_S}/><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" {_S}/>',
    "play": '<path d="M8 5.5v13a1 1 0 0 0 1.5.9l10.2-6.5a1 1 0 0 0 0-1.7L9.5 4.6A1 1 0 0 0 8 5.5z"/>',
    "restore": f'<path d="M4 12a8 8 0 1 0 2.3-5.7" {_S}/><path d="M4 4v5h5" {_S}/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18c1.2 0 1.8-.9 1.8-1.8 0-1.2-1-1.6-1-2.6 0-1 .8-1.6 1.8-1.6H17a4 4 0 0 0 4-4c0-4.4-4-8-9-8z" '
               + _S + '/><circle cx="7.5" cy="11" r="1.4"/><circle cx="10" cy="7" r="1.4"/><circle cx="15" cy="7.5" r="1.4"/>',
    "settings": '<circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" fill="none" stroke="currentColor" stroke-width="1.8"/>',
    "left": f'<path d="M15 5l-7 7 7 7" {_S}/>',
    "right": f'<path d="M9 5l7 7-7 7" {_S}/>',
    "up": f'<path d="M12 19V5m0 0l-6 6m6-6l6 6" {_S}/>',
    "down": f'<path d="M12 5v14m0 0l-6-6m6 6l6-6" {_S}/>',
    "trash": f'<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" {_S}/>',
    "update": f'<path d="M20 12a8 8 0 1 1-2.3-5.7" {_S}/><path d="M20 4v5h-5" {_S}/>',
    "pencil": f'<path d="M4 20h4L19 9l-4-4L4 16v4z" {_S}/><path d="M13.5 6.5l4 4" {_S}/>',
    "folder": '<path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z"/>',
    "check": f'<path d="M5 12l5 5L20 7" {_S}/>',
    "warn": f'<path d="M12 4l9 16H3z" {_S}/><path d="M12 10v4" {_S}/><circle cx="12" cy="17" r="1.1"/>',
    "drop": f'<path d="M12 4v10m0 0l-4-4m4 4l4-4" {_S}/><rect x="3" y="15" width="18" height="5" rx="2" {_S}/>',
    "star": '<path d="M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7z"/>',
    "help": f'<circle cx="12" cy="12" r="9" {_S}/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.7" {_S}/>'
            '<circle cx="12" cy="17.2" r="1.2"/>',
    "cloud": f'<path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 8.5a4 4 0 0 1 .5 7.97" {_S}/>'
             f'<path d="M12 11v8m0 0l-3-3m3 3l3-3" {_S}/>',
    "link": f'<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" {_S}/>'
            f'<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" {_S}/>',
    "shortcut": f'<rect x="3" y="3" width="18" height="18" rx="4" {_S}/><path d="M9 15l6-6m-4 0h4v4" {_S}/>',
    "layers": f'<path d="M12 3l9 5-9 5-9-5z" {_S}/><path d="M3 13l9 5 9-5" {_S}/>',
    "search": f'<circle cx="11" cy="11" r="6.5" {_S}/><path d="M16 16l4.5 4.5" {_S}/>',
    "picture": f'<rect x="3" y="5" width="18" height="14" rx="2" {_S}/><path d="M4 17l5-5 4 4 2.5-2.5L20 17" {_S}/>'
               '<circle cx="15.5" cy="9.5" r="1.6"/>',
    "report": f'<path d="M6 3h9l4 4v14H6z" {_S}/><path d="M9 11h7M9 15h7M14 3v4h4" {_S}/>',
}

_cache: dict[tuple, QIcon] = {}


def icon(name: str, color: str, size: int = 18) -> QIcon:
    colour = QColor(color).name()
    key = (name, colour, size)
    if key in _cache:
        return _cache[key]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{size}" height="{size}" '
           f'fill="{colour}" stroke="none">{_SVGS[name]}</svg>').replace("currentColor", colour)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(QSize(size * 2, size * 2))
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    _cache[key] = QIcon(pm)
    return _cache[key]
