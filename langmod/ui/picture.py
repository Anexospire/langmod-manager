"""Your picture: the player's own background.

The picture they choose is copied into the manager's folder, cut down to at
most 4K, so moving or deleting the original does not lose it and a huge
photo does not slow every start. Its accent colour, unless the player picks
one, is taken from the picture itself: the hue that most of its vivid
pixels share.
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QImageReader

from ..i18n import tr

FOLDER = "picture"
MAX_SIDE = 3840
DIMS = {"light": 0.34, "medium": 0.52, "strong": 0.70}     # how dark the veil over it is
FILTER = " (*.jpg *.jpeg *.png *.webp *.bmp *.gif)"      # after the word "Pictures", in the language in use


class PictureError(Exception):
    pass


def take_picture(root: Path, source: str | Path) -> Path:
    """Copy a picture into ``root/picture``, at most 4K; the one before it is cleared away."""
    reader = QImageReader(str(source))
    reader.setAutoTransform(True)               # a phone photo taken sideways stands up
    # A big phone photo (108 MP) is more than Qt's 256 MB for one image: it is decoded at the size it
    # is kept at, which JPEG does as it reads, and the rest may take more room to decode in full.
    reader.setAllocationLimit(1024)
    size = reader.size()
    if size.isValid() and max(size.width(), size.height()) > MAX_SIDE:
        reader.setScaledSize(size.scaled(MAX_SIDE, MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise PictureError(tr("{file} could not be read as a picture: {error}", file=Path(source).name,
                              error=reader.errorString()))
    if max(image.width(), image.height()) > MAX_SIDE:
        image = image.scaled(MAX_SIDE, MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    folder = Path(root) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    # A new name each time, so every look drawn from the old picture is drawn anew.
    target = folder / f"picture-{int(time.time() * 1000)}.jpg"
    if not image.convertToFormat(QImage.Format.Format_RGB32).save(str(target), "JPG", 92):
        raise PictureError(tr("The picture could not be saved in the manager's folder."))
    for old in folder.glob("picture-*.jpg"):
        if old != target:
            old.unlink(missing_ok=True)
    return target


_accents: dict[tuple, str] = {}


def accent_from(path: str | Path) -> str:
    """The picture's own accent: the most common vivid hue in it, "" if it has none to speak of."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return ""
    key = (str(p), st.st_mtime_ns)
    if key in _accents:
        return _accents[key]
    image = QImage(str(p))
    colour = ""
    if not image.isNull():
        small = image.scaled(48, 48, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        buckets: dict[int, list[float]] = {}
        for y in range(small.height()):
            for x in range(small.width()):
                c = QColor(small.pixel(x, y))
                h, s, v, _a = c.getHsvF()
                weight = s * s * v                     # vivid and bright counts most
                if h < 0 or weight < 0.05:
                    continue
                b = buckets.setdefault(int(h * 24) % 24, [0.0, 0.0, 0.0, 0.0])
                b[0] += weight
                b[1] += c.redF() * weight
                b[2] += c.greenF() * weight
                b[3] += c.blueF() * weight
        if buckets:
            w, r, g, b = max(buckets.values(), key=lambda v: v[0])
            if w > 2.0:                                # enough of it to call the picture's colour
                colour = QColor.fromRgbF(r / w, g / w, b / w).name()
    _accents[key] = colour
    return colour
