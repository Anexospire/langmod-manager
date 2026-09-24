"""The player's choices, kept with the library in ``library.json``.

No Qt here: the command line honours the same choices as the window
(whether to flip the switch, how many backups to keep).
"""
from __future__ import annotations

from typing import Any

from .library import Library

DEFAULTS: dict[str, Any] = {
    "theme": "standard",          # a key of ui.themes.THEMES
    "standard_mode": "system",    # system, dark, light: only Standard front has two faces
    "motion": True,               # the animated background moves
    "motion_follows_windows": True,  # and stands still while Windows' Animation effects is off
    "amount": "some",             # few, some, many: how much of it there is
    "fps": 30,                    # 10 to 60: frames a second the background moves at, at most
    "ui_language": "system",      # system, or a code of i18n.LANGUAGES: the manager's own language
    "switch_on": True,            # turn custom localization on when applying
    "on_update": "tell",          # tell, apply: what to do when the game has updated
    "keep_backups": 10,           # 0 keeps them all; the first one is always kept
    "confirm_move": True,         # ask before moving someone else's files out of lang/
    "icons_follow_theme": True,   # shortcuts wear the current theme's icon
    "shortcuts": {},              # "kind:where" -> path of each shortcut this app made
    "channel": "live",            # which install the window shows: live or dev
    "folders": {},                # channel -> a game folder chosen by hand
    "tour_seen": False,           # the first-run tour has been finished or skipped
    "update_check": "open",       # open, daily, never: when to look for mod updates
    "update_install": "ask",      # ask, auto: install what is found, or say so
    "update_apply": True,         # apply again after an update was installed by itself
    "update_last": 0.0,           # when the last round of checks ran
    "nexus_key": "",              # the player's own Nexus Mods API key, pasted by them
    "self_check": True,           # look for new versions of Langmod Manager itself, once a day
    "self_last": 0.0,             # when it last looked
    "self_latest": {},            # the newest version it found (selfupdate.Update as a dict)
    "window": "",                 # where the window was, its size and whether it was maximised
    "picture": "",                # Your picture: the copy of the player's picture the theme shows
    "picture_accent": "",         # its accent colour, "#rrggbb"; "" takes one from the picture
    "picture_dim": "medium",      # light, medium, strong: how far the picture is darkened
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
