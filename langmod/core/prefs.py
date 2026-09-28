"""The player's choices, kept with the library in ``library.json``.

No Qt here: the command line honours the same choices as the window
(whether to flip the switch, how many backups to keep).
"""
from __future__ import annotations

from typing import Any

from .library import Library

DEFAULTS: dict[str, Any] = {
    "theme": "standard",
    "standard_mode": "system",
    "motion": True,
    "motion_follows_windows": True,
    "amount": "some",
    "fps": 30,
    "ui_language": "system",
    "switch_on": True,
    "on_update": "tell",
    "keep_backups": 10,
    "confirm_move": True,
    "icons_follow_theme": True,
    "shortcuts": {},
    "channel": "live",
    "folders": {},
    "tour_seen": False,
    "update_check": "open",
    "update_install": "ask",
    "update_apply": True,
    "update_last": 0.0,
    "nexus_key": "",
    "self_check": True,
    "self_last": 0.0,
    "self_latest": {},
    "window": "",
    "picture": "",
    "picture_accent": "",
    "picture_dim": "medium",
}


class Prefs:
    def __init__(self, lib: Library):
        self.lib = lib

    @property
    def _store(self) -> dict:
        return self.lib.settings.setdefault("prefs", {})

    def get(self, key: str) -> Any:
        value = self._store.get(key, DEFAULTS[key])
        default = DEFAULTS[key]
        if isinstance(default, dict):
            return dict(value) if isinstance(value, dict) else {}
        if default is not None and not isinstance(value, type(default)):
            return default
        return value

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        self._store[key] = value
        self.lib.save()

    def reset(self, keep: tuple[str, ...] = ("shortcuts", "tour_seen", "nexus_key", "update_last", "window",
                                             "picture", "self_last", "self_latest")) -> None:
        """Every choice back to how a fresh copy starts, bar the ones listed."""
        kept = {k: self._store[k] for k in keep if k in self._store}
        self.lib.settings["prefs"] = kept
        self.lib.save()
