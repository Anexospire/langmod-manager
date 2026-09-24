"""The one window: pick the game, line up the mods, apply, play.

A top bar with the game switch and the main actions, then panels - here, cards floating over the theme's scene - with
headers of one height, and a status bar along the bottom.
"""
from __future__ import annotations

import html
import sys
import textwrap
import time
import traceback
from pathlib import Path
from urllib.parse import quote, unquote

import PySide6
from PySide6.QtCore import (QByteArray, QObject, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl,
                            Signal, qVersion)
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QFont, QFontMetrics, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QSplitter, QStackedWidget,
    QStatusBar, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidgetItem, QTextBrowser, QVBoxLayout,
    QWidget,
)

from ..core import config
from ..core import install as inst
from ..core import profiles, report, selfupdate, shortcuts, sources, strings, updates
from ..core.analysis import Analysis, analyze
from ..core.game import GameInstall, GameLang, find_installs, game_running, install_at, lang_stamp, start_game
from ..core.library import Library, LibraryError, Mod
from ..core.plan import GAME, PICKS_ID, Plan, build_plan
from ..core.prefs import Prefs
from ..core.profiles import ProfileError
from .. import __version__, i18n
from ..i18n import N_, game_language, join, ntr, num, sentences, tr
from ..i18n import date as day
from . import system, themes
from .icons import icon
from .picture import FILTER as PICTURE_FILTER
from .picture import PictureError, take_picture
from .scenes import Backdrop
from .strings_view import KEY_ROLE, StringCard, StringsView, _table, card_area
from .themes import ORDER, THEMES, solid, tokens
from .widgets import (Banner, EmptyState, GlowButton, PanelHeader, PortalButton, SegmentSwitch, card, chip,
                      section_label, set_tone)

ID_ROLE = Qt.ItemDataRole.UserRole + 1
DETAIL_ROLE = Qt.ItemDataRole.UserRole + 2
ORDER_ROLE = Qt.ItemDataRole.UserRole + 3
UPDATE_ROLE = Qt.ItemDataRole.UserRole + 4
CHANNELS = (("live", N_("Live")), ("dev", N_("Dev")))
VIEWS = ("details", "strings", "conflicts", "order")


def _ago(when: float) -> str:
    s = max(0, time.time() - when)
    if s < 90:
        return tr("just now")
    if s < 3600:
        return ntr(int(s // 60), "{n} minute ago", "{n} minutes ago")
    if s < 86400:
        return ntr(int(s // 3600), "{n} hour ago", "{n} hours ago")
    return ntr(int(s // 86400), "{n} day ago", "{n} days ago")


def mod_name(mod: Mod) -> str:
    """A mod's name as shown: the player's own layer is called by its name in the language in use."""
    return tr("My changes") if mod.personal else mod.name


def mod_label(mod: Mod) -> str:
    return tr("My changes") if mod.personal else mod.label


def module_name(label: str) -> str:
    """A module as shown: WTHLM's ``Package_Full_Ammo_Names`` reads "Full Ammo Names"."""
    name = label[8:] if label.lower().startswith(("package_", "package ")) else label
    return name.replace("_", " ").strip() or label


# -- background work --------------------------------------------------------------------

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception:
            self.signals.failed.emit(traceback.format_exc())
        else:
            self.signals.done.emit(result)


# -- the mod list ------------------------------------------------------------------------

class ModDelegate(QStyledItemDelegate):
    """A mod as a row: its tick, its name, what it is, and its place in the order."""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title, detail = opt.text, index.data(DETAIL_ROLE) or ""
        place = index.data(ORDER_ROLE) or ""
        opt.text = ""
        style = opt.widget.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        t = tokens()
        state = index.data(Qt.ItemDataRole.CheckStateRole)
        on = state == Qt.CheckState.Checked or state == Qt.CheckState.Checked.value
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Its place in the load order, on the right: the higher, the more it wins.
        badge_w = 0
        if place:
            f = QFont(opt.font)
            f.setPixelSize(11)
            f.setBold(True)
            painter.setFont(f)
            fm = QFontMetrics(f)
            badge_w = max(22, fm.horizontalAdvance(place) + 12)
            badge = QRectF(rect.right() - badge_w - 6, rect.center().y() - 10, badge_w, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t["accent_soft"] if on else t["raised"]))
            painter.drawRoundedRect(badge, 10, 10)
            painter.setPen(QColor(t["accent"] if on else t["faint"]))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, place)
        # A new version is waiting: a filled tag beside the place.
        if index.data(UPDATE_ROLE):
            f = QFont(opt.font)
            f.setPixelSize(10)
            f.setBold(True)
            painter.setFont(f)
            fm = QFontMetrics(f)
            word = tr("UPDATE")
            tag_w = fm.horizontalAdvance(word) + 14
            tag = QRectF(rect.right() - badge_w - 12 - tag_w, rect.center().y() - 9, tag_w, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t["accent"]))
            painter.drawRoundedRect(tag, 9, 9)
            painter.setPen(QColor(t["accent_text"]))
            painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, word)
            badge_w += tag_w + 6
        width = rect.width() - badge_w - 18
        bold = QFont(opt.font)
        bold.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(bold)
        fm2 = QFontMetrics(opt.font)
        top = rect.top() + (rect.height() - fm.height() - fm2.height() - 2) // 2
        painter.setFont(bold)
        painter.setPen(QColor(t["text"] if on else t["faint"]))
        painter.drawText(QRect(rect.left() + 6, top, width, fm.height()), Qt.AlignmentFlag.AlignLeft,
                         fm.elidedText(title, Qt.TextElideMode.ElideRight, width))
        painter.setFont(opt.font)
        painter.setPen(QColor(t["muted"] if on else t["faint"]))
        painter.drawText(QRect(rect.left() + 6, top + fm.height() + 2, width, fm2.height()),
                         Qt.AlignmentFlag.AlignLeft, fm2.elidedText(detail, Qt.TextElideMode.ElideRight, width))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(200, QFontMetrics(option.font).height() * 2 + 26)


# -- the window ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    refreshed = Signal()          # the list, the plan and the game's state were read again

    def __init__(self, library: Library | None = None, installs: list[GameInstall] | None = None,
                 threaded: bool = True, first_run_tour: bool = True):
        super().__init__()
        self.first_run_tour = first_run_tour
        self.tour = None
        self._shown_once = False
        self._looked_for_updates = False
        self._busy_updating = False
        self.lib = library or Library()
        self.prefs = Prefs(self.lib)
        system.qt_translations(i18n.set_language(i18n.choose(self.prefs.get("ui_language"))))
        self.threaded = threaded
        self.pool = QThreadPool.globalInstance()
        self._tasks: set[Task] = set()
        self.install: GameInstall | None = None
        self.game: GameLang | None = None
        self.plan: Plan | None = None
        self.analysis: Analysis | None = None
        self.state: dict = {}
        self.hand: inst.HandInstall | None = None      # a mod installed by hand in the lang folder, if any
        self._generation = 0
        self._game_error: str | None = None
        self._found = installs
        self.settings_dialog = None
        self.getmods_dialog = None
        self.readme_dialog = None
        self._animations = system.animations_on()
        self.setWindowTitle("Langmod Manager")
        self.setAcceptDrops(True)
        self.resize(1220, 780)
        self._build()
        self._restore_place()
        self.apply_look()
        try:
            QApplication.styleHints().colorSchemeChanged.connect(lambda *_: self._system_scheme_changed())
        except AttributeError:
            pass
        self.set_installs(installs if installs is not None else find_installs())

    # -- layout ----------------------------------------------------------------------------
    def _build(self) -> None:
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        self.backdrop = Backdrop(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = QWidget(objectName="topbar")
        top.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        top.setFixedHeight(54)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(14, 0, 12, 0)
        tl.setSpacing(10)
        self.logo = QLabel()
        self.logo.setFixedSize(26, 26)
        tl.addWidget(self.logo)
        tl.addWidget(QLabel("Langmod Manager", objectName="appTitle"))
        tl.addSpacing(6)
        self.game_switch = SegmentSwitch([(k, tr(label)) for k, label in CHANNELS])
        self.game_switch.set_tip("live", tr("The game everyone plays"))
        self.game_switch.set_tip("dev", tr("The dev server client, if you have it"))
        self.game_switch.selected.connect(self._channel_chosen)
        tl.addWidget(self.game_switch)
        self.game_meta = QLabel("", objectName="topMeta")
        tl.addWidget(self.game_meta)
        self.state_chip = chip()
        tl.addWidget(self.state_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        tl.addStretch(1)
        self.apply_btn = GlowButton(tr("Apply to game"), light=True, objectName="primary")
        self.apply_btn.clicked.connect(lambda: self.apply())
        self.play_btn = PortalButton(tr("Play"))
        self.play_btn.setToolTip(tr("Apply if anything changed, then start War Thunder"))
        self.play_btn.clicked.connect(self.play)
        self.restore_btn = GlowButton(radius=8, objectName="flat")
        self.restore_btn.setToolTip(tr("Restore game: take the manager's files out again"))
        self.restore_btn.clicked.connect(self.restore)
        self.theme_btn = GlowButton(radius=8, objectName="flat")
        self.theme_btn.setToolTip(tr("Theme"))
        self.theme_btn.clicked.connect(self._theme_menu)
        self.settings_btn = GlowButton(radius=8, objectName="flat")
        self.settings_btn.setToolTip(tr("Settings (Ctrl+,)"))
        self.settings_btn.clicked.connect(lambda: self.open_settings())
        self.help_btn = GlowButton(radius=8, objectName="flat")
        self.help_btn.setToolTip(tr("Take the tour: every button, one at a time (F1)"))
        self.help_btn.clicked.connect(self.start_tour)
        for b in (self.restore_btn, self.theme_btn, self.settings_btn, self.help_btn):
            b.setFixedSize(34, 34)
        for w in (self.apply_btn, self.play_btn, self.restore_btn, self.theme_btn, self.settings_btn,
                  self.help_btn):
            tl.addWidget(w)
        outer.addWidget(top)

        body = QWidget(objectName="transparent")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(10)
        self.banner = Banner()
        self.banner2 = Banner()
        self.update_banner = Banner()
        self.app_banner = Banner()                      # a new version of the manager itself
        bl.addWidget(self.banner)
        bl.addWidget(self.banner2)
        bl.addWidget(self.update_banner)
        bl.addWidget(self.app_banner)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(12)

        left = card()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        head = PanelHeader()
        head.add(section_label(tr("Mods")))
        self.count_chip = chip("0", "neutral")
        head.add(self.count_chip)
        self.profile_btn = GlowButton(tr("Profiles"), objectName="flat")
        self.profile_btn.setToolTip(tr("Profiles: named sets of mods and their order, switched in one go "
                                       "(Ctrl+1 to Ctrl+9)"))
        self.profile_menu = QMenu(self)
        self.profile_menu.aboutToShow.connect(self._fill_profile_menu)
        self.profile_btn.setMenu(self.profile_menu)
        head.add(self.profile_btn)
        head.add_stretch()
        self.updates_btn = GlowButton(tr("Updates"))
        self.updates_btn.setToolTip(tr("Look for new versions of the mods that follow a page on {places}",
                                       places=sources.places()))
        self.updates_btn.clicked.connect(lambda: self.check_updates())
        head.add(self.updates_btn)
        self.add_btn = GlowButton(tr("Add"))
        menu = QMenu(self)
        menu.addAction(tr("Get mods: IFN1, LOP, WTHLM…"), self.open_get_mods)
        menu.addSeparator()
        menu.addAction(tr("A zip, 7z or .csv…"), self.add_from_file)
        menu.addAction(tr("A folder…"), self.add_from_folder)
        self.add_btn.setMenu(menu)
        self.add_btn.setToolTip(tr("Add a mod, a newer version of one, or an optional module"))
        head.add(self.add_btn)
        ll.addWidget(head)
        hint = QLabel(tr("Lower in the list wins where two mods change the same string. Drag to reorder."),
                      objectName="faint")
        hint.setWordWrap(True)
        hint.setContentsMargins(14, 8, 14, 2)
        ll.addWidget(hint)
        self.mod_list = QListWidget()
        self.mod_list.setItemDelegate(ModDelegate(self.mod_list))
        self.mod_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.mod_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.mod_list.setContentsMargins(0, 0, 0, 0)
        self.mod_list.setViewportMargins(8, 4, 8, 4)
        self.mod_list.model().rowsMoved.connect(self._rows_moved)
        self.mod_list.itemChanged.connect(self._item_changed)
        self.mod_list.currentItemChanged.connect(lambda *_: self._show_details())
        self.mod_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mod_list.customContextMenuRequested.connect(self._context_menu)
        ll.addWidget(self.mod_list, 1)
        tools = QHBoxLayout()
        tools.setContentsMargins(10, 6, 10, 10)
        tools.setSpacing(4)
        self.update_btn = GlowButton(radius=8, objectName="flat")
        self.update_btn.setToolTip(tr("Update: add a newer version of this mod, or one of its optional modules"))
        self.update_btn.clicked.connect(self.update_selected)
        self.remove_btn = GlowButton(radius=8, objectName="flat")
        self.remove_btn.setToolTip(tr("Remove this mod from the manager"))
        self.remove_btn.clicked.connect(self.remove_selected)
        self.up_btn = GlowButton(radius=8, objectName="flat")
        self.up_btn.setToolTip(tr("Load earlier: loses to the mods below it"))
        self.up_btn.clicked.connect(lambda: self.move_selected(-1))
        self.down_btn = GlowButton(radius=8, objectName="flat")
        self.down_btn.setToolTip(tr("Load later: wins over the mods above it"))
        self.down_btn.clicked.connect(lambda: self.move_selected(1))
        for b in (self.update_btn, self.remove_btn, self.up_btn, self.down_btn):
            b.setFixedSize(32, 30)
            tools.addWidget(b)
        tools.addStretch(1)
        self.mine_btn = GlowButton(tr("My own strings"), objectName="link")
        self.mine_btn.setToolTip(tr("A file of your own that loads after every mod: one line per string, "
                                    "string ID;your text"))
        self.mine_btn.clicked.connect(self.edit_personal)
        tools.addWidget(self.mine_btn)
        ll.addLayout(tools)
        split.addWidget(left)

        right = card()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rhead = PanelHeader()
        self.view_switch = SegmentSwitch([("details", tr("Details")), ("strings", tr("Strings")),
                                          ("conflicts", tr("Conflicts")), ("order", tr("Load order"))])
        self.view_switch.set_tip("details", tr("What the selected mod is, and what it changes"))
        self.view_switch.set_tip("strings", tr("Search every string: what the game says, what each mod says, and "
                                               "which one shows; pick another, or type your own (Ctrl+F)"))
        self.view_switch.set_tip("conflicts", tr("Strings more than one mod changes, which one shows, and a pick "
                                                 "of your own"))
        self.view_switch.set_tip("order", tr("Every file the game will load, in order"))
        self.view_switch.selected.connect(self._view_chosen)
        rhead.add(self.view_switch)
        rhead.add_stretch()
        self.view_info = QLabel("", objectName="faint")
        rhead.add(self.view_info)
        rl.addWidget(rhead)
        self.stack = QStackedWidget()
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(self._link)
        self.details.document().setDocumentMargin(16)
        self.stack.addWidget(self.details)
        self.strings_view = StringsView(self)
        self.stack.addWidget(self.strings_view)
        conf = QWidget(objectName="transparent")
        cl = QVBoxLayout(conf)
        cl.setContentsMargins(12, 10, 12, 8)
        self.conflict_filter = QLineEdit(placeholderText=tr("Filter by string ID or text"))
        self.conflict_filter.setClearButtonEnabled(True)
        self.conflict_filter.textChanged.connect(lambda _t: self._fill_conflicts())
        cl.addWidget(self.conflict_filter)
        csplit = QSplitter(Qt.Orientation.Vertical)
        csplit.setChildrenCollapsible(False)
        csplit.setHandleWidth(10)
        self.conflicts = _table([tr("STRING ID"), tr("THE GAME SAYS"), tr("SHOWN IN GAME"), tr("ALSO CHANGED BY")])
        for i, w in enumerate((200, 170, 230)):
            self.conflicts.setColumnWidth(i, w)
        self.conflicts.currentCellChanged.connect(lambda *_: self._show_conflict())
        csplit.addWidget(self.conflicts)
        self.conflict_card = StringCard()
        self.conflict_card.pick.connect(self.pick_string)
        self.conflict_card.own.connect(self.set_own_string)
        csplit.addWidget(card_area(self.conflict_card))
        csplit.setSizes([280, 220])
        cl.addWidget(csplit, 1)
        self.stack.addWidget(conf)
        self.load_list = QPlainTextEdit(readOnly=True, objectName="mono")
        self.load_list.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.stack.addWidget(self.load_list)
        self.empty = EmptyState(tr("Add your first language mod"),
                                tr("A zip or 7z, a folder, or a single .csv, just as it downloads: IFN1's monthly "
                                   "lang.zip works as it comes. You can also drop it anywhere on this window."))
        self.empty_zip = GlowButton(tr("Add a zip, 7z or .csv…"), objectName="primary", light=True)
        self.empty_zip.clicked.connect(self.add_from_file)
        self.empty_folder = GlowButton(tr("Add a folder…"))
        self.empty_folder.clicked.connect(self.add_from_folder)
        self.empty_get = GlowButton(tr("Get mods…"))
        self.empty_get.setToolTip(tr("IFN1, the Localization Overhaul Project or WTHLM, fetched in one click"))
        self.empty_get.clicked.connect(self.open_get_mods)
        self.empty.add_action(self.empty_zip)
        self.empty.add_action(self.empty_folder)
        self.empty.add_action(self.empty_get)
        self.empty.finish()
        self.stack.addWidget(self.empty)
        rl.addWidget(self.stack, 1)
        split.addWidget(right)
        split.setSizes([400, 780])
        bl.addWidget(split, 1)
        outer.addWidget(body, 1)

        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        self.status = QLabel("")
        self.status.linkActivated.connect(self._status_link)
        self.status_right = QLabel("")
        bar.addWidget(self.status, 1)
        bar.addPermanentWidget(self.status_right)
        self.setStatusBar(bar)

        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.open_settings)
        QShortcut(QKeySequence("F5"), self, activated=self.reload_game)
        QShortcut(QKeySequence("F1"), self, activated=self.start_tour)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.find_strings)
        for n in range(1, 10):
            QShortcut(QKeySequence(f"Ctrl+{n}"), self, activated=lambda n=n: self.switch_profile_number(n))

    # -- where the window was ----------------------------------------------------------------
    def _restore_place(self) -> None:
        saved = self.prefs.get("window")
        if saved:
            try:
                self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii")))
            except (ValueError, UnicodeError):
                pass

    def _keep_place(self) -> None:
        try:
            self.prefs.set("window", bytes(self.saveGeometry().toBase64()).decode("ascii"))
        except OSError:
            pass

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.backdrop.setGeometry(self.centralWidget().rect())
        self.backdrop.lower()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.backdrop.setGeometry(self.centralWidget().rect())
        self.backdrop.lower()
        if not self._shown_once:
            self._shown_once = True
            if getattr(self.lib, "load_problem", ""):
                QTimer.singleShot(0, lambda: QMessageBox.warning(self, tr("Your list of mods could not be read"),
                                                                 self.lib.load_problem))
            if self.first_run_tour and not self.prefs.get("tour_seen"):
                # A moment after the window appears, so the tour starts on a settled layout.
                QTimer.singleShot(600, self.start_tour)
            self.look_for_new_self()

    # -- the tour ------------------------------------------------------------------------------
    def start_tour(self) -> None:
        from .tour import Tour, steps_for
        if self.tour is not None and self.tour.running:
            return
        if self.settings_dialog is not None:
            self.settings_dialog.close()
        self.tour = Tour(self, steps_for(self))
        self.tour.finished.connect(self._tour_done)
        self.tour.start()

    def _tour_done(self, completed: bool) -> None:
        self.prefs.set("tour_seen", True)
        self.tour = None
        self.say(tr("Tour done. The ? button at the top shows it again.") if completed
                 else tr("Tour skipped. The ? button at the top shows it whenever you like."))

    def changeEvent(self, e) -> None:
        if e.type() == e.Type.WindowStateChange:
            self.backdrop.set_paused(self.isMinimized())
        elif e.type() == e.Type.ActivationChange and self.isActiveWindow():
            self.take_in_changes()
            # Windows' Animation effects may have been switched while the window was in the back.
            now = system.animations_on()
            if now != self._animations:
                self._animations = now
                self.backdrop.set_motion(self.motion())
                if self.settings_dialog is not None:
                    self.settings_dialog.backdrop.set_motion(self.motion())
        super().changeEvent(e)

    def take_in_changes(self) -> None:
        """Another copy of the manager changed the library while this window was in the back (Steam
        started the game through it, say): read it again, rather than write over it at the next change."""
        if self.lib.changed_elsewhere():
            self.lib.load()
            if self.install is not None:
                self.hand = inst.hand_install(self.lib, self.install)
            self._refresh_all()

    # -- the look ----------------------------------------------------------------------------
    def motion(self) -> bool:
        """Whether the background moves: the player's choice, and Windows' Animation effects unless
        they said not to follow it."""
        return self.prefs.get("motion") and (not self.prefs.get("motion_follows_windows") or self._animations)

    def picture_config(self) -> dict:
        return {"path": self.prefs.get("picture"), "accent": self.prefs.get("picture_accent"),
                "dim": self.prefs.get("picture_dim")}

    def apply_look(self) -> None:
        theme, t = themes.apply_look(QApplication.instance(), self.prefs.get("theme"),
                                     self.prefs.get("standard_mode"), self.picture_config())
        self.theme = theme
        fps = self.prefs.get("fps")
        self.backdrop.configure(theme, t, self.motion(), self.prefs.get("amount"), fps)
        for dlg in (self.settings_dialog, self.getmods_dialog):
            if dlg is not None and hasattr(dlg, "backdrop"):
                dlg.backdrop.configure(theme, t, self.motion(), self.prefs.get("amount"), fps)
        self.setWindowIcon(themes.icon_for(theme.key))
        self.logo.setPixmap(themes.icon_for(theme.key).pixmap(26, 26))
        muted = t["muted"]
        self.apply_btn.setIcon(icon("apply", t["accent_text"], 16))
        self.play_btn.setIcon(icon("play", t["accent"], 15))
        self.restore_btn.setIcon(icon("restore", muted, 18))
        self.theme_btn.setIcon(icon("palette", muted, 18))
        self.settings_btn.setIcon(icon("settings", muted, 18))
        self.updates_btn.setIcon(icon("cloud", t["accent"], 16))
        self.help_btn.setIcon(icon("help", muted, 18))
        self.add_btn.setIcon(icon("plus", t["accent"], 15))
        self.profile_btn.setIcon(icon("layers", t["accent"] if profiles.active(self.lib) else muted, 15))
        self.empty_get.setIcon(icon("cloud", t["accent"], 15))
        self.update_btn.setIcon(icon("update", muted, 16))
        self.remove_btn.setIcon(icon("trash", muted, 16))
        self.up_btn.setIcon(icon("up", muted, 16))
        self.down_btn.setIcon(icon("down", muted, 16))
        self.mine_btn.setIcon(icon("pencil", muted, 14))
        self.empty.glyph.setPixmap(icon("drop", t["accent"], 44).pixmap(44, 44))
        self.empty_zip.setIcon(icon("plus", t["accent_text"], 15))
        self.mod_list.viewport().update()
        self._show_details()
        self.update()

    def _system_scheme_changed(self) -> None:
        if not self.isVisible():
            return                  # a window made again in another language: the new one follows it
        if self.prefs.get("theme") == "standard" and self.prefs.get("standard_mode") == "system":
            self.apply_look()

    def set_fps(self, fps: int) -> None:
        """A new frame rate for the background, here and behind Settings, without drawing it anew."""
        self.backdrop.set_fps(fps)
        if self.settings_dialog is not None:
            self.settings_dialog.backdrop.set_fps(fps)

    def set_theme(self, key: str) -> None:
        if key == "picture" and not Path(self.prefs.get("picture") or "").is_file() and not self.choose_picture():
            return                    # Your picture needs a picture: none was chosen
        self.prefs.set("theme", key)
        self.apply_look()
        self.follow_shortcut_icons()

    def choose_picture(self, parent=None) -> bool:
        """Ask for a picture for Your picture and keep a copy of it; True if one was chosen."""
        start = str(Path.home() / "Pictures")
        path, _ = QFileDialog.getOpenFileName(parent or self, tr("A picture for the background"), start,
                                              tr("Pictures") + PICTURE_FILTER)
        if not path:
            return False
        return self.use_picture(path, parent)

    def use_picture(self, path: str, parent=None) -> bool:
        try:
            kept = take_picture(self.lib.root, path)
        except PictureError as exc:
            QMessageBox.warning(parent or self, tr("Not a picture"), str(exc))
            return False
        self.prefs.set("picture", str(kept))
        self.prefs.set("picture_accent", "")          # a new picture brings its own accent
        if self.prefs.get("theme") == "picture":
            self.apply_look()
        return True

    def follow_shortcut_icons(self) -> None:
        """Shortcuts this app made take on the theme's icon, off the thread that draws."""
        if not self.prefs.get("shortcuts") or not self.threaded:
            return
        lib = self.lib
        self._run(lambda: shortcuts.follow_theme(lib), lambda _r: None, lambda _e: None)

    def _theme_menu(self) -> None:
        menu = QMenu(self)
        group = QActionGroup(menu)
        current = self.prefs.get("theme")
        for key in ORDER:
            act = QAction(tr(THEMES[key].name), menu, checkable=True)
            act.setChecked(key == current)
            act.setIcon(themes.icon_for(key))
            act.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            group.addAction(act)
            menu.addAction(act)
        menu.addSeparator()
        motion = QAction(tr("Animated background"), menu, checkable=True)
        motion.setChecked(self.prefs.get("motion"))
        if self.prefs.get("motion") and not self.motion():
            motion.setText(tr("Animated background (still: Windows' animation effects are off)"))
        motion.setEnabled(THEMES.get(current, THEMES["standard"]).scene != "none")
        motion.triggered.connect(lambda on: (self.prefs.set("motion", on), self.apply_look()))
        menu.addAction(motion)
        menu.addAction(tr("More in Settings…"), lambda: self.open_settings("look"))
        menu.exec(self.theme_btn.mapToGlobal(self.theme_btn.rect().bottomLeft()))

    def open_settings(self, page: str = "look") -> None:
        from .settings_dialog import SettingsDialog
        if self.settings_dialog is not None:
            self.settings_dialog.raise_()
            return
        dlg = SettingsDialog(self.lib, self, page)
        self._dress(dlg)
        dlg.look_changed.connect(self.apply_look)
        dlg.fps_changed.connect(self.set_fps)
        dlg.game_changed.connect(lambda: self.set_installs(find_installs()))
        dlg.language_changed.connect(lambda: QTimer.singleShot(0, self.change_language))
        self.settings_dialog = dlg
        dlg.finished.connect(lambda _r: setattr(self, "settings_dialog", None))
        if self.threaded:
            dlg.open()
        else:
            dlg.show()

    def change_language(self) -> "MainWindow":
        """The player chose another language: the window is made again in it, where and as big as it was,
        with Settings open again on the page it was on."""
        page = self.settings_dialog.current_page() if self.settings_dialog is not None else None
        for dlg in (self.settings_dialog, self.getmods_dialog):
            if dlg is not None:
                dlg.close()
        app = QApplication.instance()
        app.setQuitOnLastWindowClosed(False)       # for the moment between this window and the next
        self._keep_place()
        self.close()
        new = MainWindow(self.lib, self._found, self.threaded, first_run_tour=False)
        app._langmod_window = new                  # keeps it alive: the one made at start is this one
        new.show()
        app.setQuitOnLastWindowClosed(True)
        if page:
            new.open_settings(page)
        return new

    def _dress(self, dlg) -> None:
        """The theme's scene behind a dialog too."""
        dlg.backdrop = Backdrop(dlg)
        dlg.backdrop.configure(self.theme, tokens(), self.motion(), self.prefs.get("amount"), self.prefs.get("fps"))
        dlg.backdrop.lower()
        _fit = dlg.resizeEvent

        def fit(e, _fit=_fit, dlg=dlg):
            dlg.backdrop.setGeometry(dlg.rect())
            dlg.backdrop.lower()
            _fit(e)

        dlg.resizeEvent = fit

    def open_get_mods(self) -> None:
        from .getmods_dialog import GetModsDialog
        if self.getmods_dialog is not None:
            self.getmods_dialog.raise_()
            return
        dlg = GetModsDialog(self)
        self._dress(dlg)
        self.getmods_dialog = dlg
        dlg.finished.connect(lambda _r: setattr(self, "getmods_dialog", None))
        if self.threaded:
            dlg.open()
        else:
            dlg.show()

    # -- running things -------------------------------------------------------------------------
    def _run(self, fn, done, failed=None) -> None:
        if not self.threaded:
            try:
                result = fn()
            except Exception:
                (failed or self._failed)(traceback.format_exc())
            else:
                done(result)
            return
        task = Task(fn)
        task.setAutoDelete(False)
        self._tasks.add(task)

        def finish(result, t=task):
            self._tasks.discard(t)
            done(result)

        def fail(text, t=task):
            self._tasks.discard(t)
            (failed or self._failed)(text)

        task.signals.done.connect(finish)
        task.signals.failed.connect(fail)
        self.pool.start(task)

    def _failed(self, text: str) -> None:
        report.log_error(self.lib.root, text)
        self.say_with_link(tr("Something went wrong: {error}", error=text.strip().splitlines()[-1]), "report:",
                           tr("Copy a bug report"))

    def say(self, text: str) -> None:
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setText(text)

    def say_with_link(self, text: str, href: str, label: str) -> None:
        """A line in the status bar with one thing to do after it. The next thing said replaces it."""
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setText(f"{html.escape(text)} &nbsp;<a href='{html.escape(href)}' "
                            f"style='color:{tokens()['accent']}; font-weight:600'>{html.escape(label)}</a>")

    def say_with_undo(self, text: str, undo: str) -> None:
        self.say_with_link(text, undo, tr("Undo"))

    def bug_report(self) -> str:
        extra = [f"Qt: {qVersion()} (PySide6 {PySide6.__version__})",
                 f"Theme: {self.prefs.get('theme')}, background {'moving' if self.backdrop.moving else 'still'}"
                 f"{'' if self._animations else ' (Windows animation effects off)'}, "
                 f"{self.backdrop.rate} fps of {self.backdrop.cap}",
                 f"Profiles: {len(profiles.names(self.lib))}"
                 + (f", using {profiles.active(self.lib)}" if profiles.active(self.lib) else "")]
        return report.bug_report(self.lib, self.install, self.game, self.state, extra)

    def copy_bug_report(self) -> None:
        QApplication.clipboard().setText(self.bug_report())
        self.say(tr("A bug report is on the clipboard: paste it into your message. It has no user name or other "
                    "personal details in it."))

    def _status_link(self, href: str) -> None:
        if href == "report:":
            self.copy_bug_report()
            return
        if href.startswith("unremove:"):
            try:
                mod = self.lib.unremove(href.split(":", 1)[1])
            except LibraryError as exc:
                self.say(str(exc))
                return
            if self.install is not None:
                self.hand = inst.hand_install(self.lib, self.install)
            self._refresh_all(mod.id)
            self.say(tr("{mod} is back, where it was.", mod=mod_label(mod)))
        elif href.startswith("untake:"):
            self._leave_out_hand_install(href.split(":", 1)[1])

    # -- the game --------------------------------------------------------------------------------
    def installs_by_channel(self) -> dict[str, GameInstall]:
        out: dict[str, GameInstall] = {}
        for inst_ in self._found or []:
            out.setdefault(inst_.channel, inst_)
        for channel, path in self.prefs.get("folders").items():
            chosen = install_at(path)
            if chosen is not None:
                out[channel] = GameInstall(chosen.root, "custom", channel)
        return out

    def set_installs(self, installs: list[GameInstall]) -> None:
        self._found = installs
        by = self.installs_by_channel()
        for key, _label in CHANNELS:
            self.game_switch.set_enabled_choice(key, key in by)
        want = self.prefs.get("channel")
        channel = want if want in by else next(iter(by), None)
        if channel is None:
            self.install = None
            self.game = None
            self._refresh_all()
            return
        self.game_switch.set_current(channel, animate=False)
        if self.install is None or self.install.root != by[channel].root:
            self.select_install(by[channel])
        else:
            self._refresh_all()

    def _channel_chosen(self, channel: str) -> None:
        by = self.installs_by_channel()
        if channel in by:
            self.prefs.set("channel", channel)
            self.select_install(by[channel])

    def choose_game_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("The War Thunder folder"))
        if not path:
            return
        found = install_at(path)
        if found is None:
            QMessageBox.warning(self, tr("Not the game folder"),
                                tr("That folder has no lang.vromfs.bin. Choose the folder War Thunder is installed "
                                   "in; it also holds config.blk and aces.vromfs.bin."))
            return
        folders = self.prefs.get("folders")
        folders[found.channel] = str(found.root)
        self.prefs.set("folders", folders)
        self.prefs.set("channel", found.channel)
        self.set_installs(self._found or [])

    def select_install(self, install: GameInstall) -> None:
        self.install = install
        self.game = None
        self._game_error = None
        self.lib.settings["game"] = str(install.root)
        self.lib.save()
        self.reload_game()

    def reload_game(self) -> None:
        if self.install is None:
            return
        self.say(tr("Reading the game's language files…"))
        root, cache = self.install.root, self.lib.root / "cache"
        try:
            language = config.game_language(self.install.config_path.read_text("utf-8", errors="replace"))
        except OSError:
            language = ""

        def load():
            game = GameLang(root, cache)
            # Every string's text: a second the first time a game version is seen, then from the cache.
            game.texts(language or "English")
            return game

        self._run(load, self._game_loaded, self._game_failed)

    def _game_loaded(self, game: GameLang) -> None:
        if self.install is None or game.root != self.install.root:
            return
        self.game = game
        self._game_error = None
        self.say(ntr(len(game.loc_table()), "Game {version}: {n} language table.", "Game {version}: {n} language tables.",
                     version=game.version))
        taken = self._keep_hand_install()
        self._refresh_all(taken.id if taken else None)
        if (self.prefs.get("on_update") == "apply" and self.state.get("stale")
                and not game_running()):
            self.apply(auto=True)
        if not self._looked_for_updates:
            self._looked_for_updates = True
            if updates.due(self.lib):
                self.check_updates(auto=True)

    def _game_failed(self, text: str) -> None:
        self.game = None
        self._game_error = text.strip().splitlines()[-1]
        self._refresh_all()
        self.banner.show_message(tr("The game's language archive could not be read: {error}",
                                    error=html.escape(self._game_error)), tone="err",
                                 glyph=icon("warn", tokens()["err"], 18))

    # -- refreshing ---------------------------------------------------------------------------------
    def _refresh_all(self, keep: str | None = None) -> None:
        self._fill_mods(keep)
        self.plan = None
        self.analysis = None
        self.state = {}
        if self.game is not None and self.install is not None:
            try:
                self.plan = build_plan(self.lib, self.game)
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
            self.state = inst.status(self.install)
        self._update_state()
        self._show_details()
        self.load_list.setPlainText(self._load_list_text())
        self._fill_conflicts()
        self._show_view()
        self._show_profile()
        self.refreshed.emit()
        self._start_analysis()

    def _start_analysis(self) -> None:
        if self.plan is None or not self.plan.placements:
            return
        self._generation += 1
        gen = self._generation
        lib, game, plan = self.lib, self.game, self.plan
        language = self.state.get("language") or "English"
        self.view_switch.set_label("conflicts", tr("Conflicts") + " …")

        def done(result, gen=gen):
            if gen == self._generation:
                self.analysis = result
                self._fill_conflicts()
                self._show_details()
                if self.view_switch.current() == "strings":
                    self.strings_view.run_search()

        def failed(text, gen=gen):
            if gen == self._generation:           # a newer one is running: this one no longer matters
                self.view_switch.set_label("conflicts", tr("Conflicts"))
                self._failed(text)

        self._run(lambda: analyze(lib, game, plan, language), done, failed)

    def _fill_mods(self, keep: str | None = None) -> None:
        current = keep or (self.mod_list.currentItem().data(ID_ROLE) if self.mod_list.currentItem() else None)
        self.mod_list.blockSignals(True)
        self.mod_list.clear()
        place = 0
        for mod in self.lib.mods:
            item = QListWidgetItem(mod_label(mod))
            item.setData(ID_ROLE, mod.id)
            item.setData(DETAIL_ROLE, self._mod_line(mod))
            if mod.enabled:
                place += 1
                item.setData(ORDER_ROLE, str(place))
            item.setData(UPDATE_ROLE, updates.state(mod) == "update")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            item.setCheckState(Qt.CheckState.Checked if mod.enabled else Qt.CheckState.Unchecked)
            item.setToolTip(tr("{mod}: loads in place {place}", mod=mod_label(mod), place=place) if mod.enabled
                            else tr("{mod}: switched off", mod=mod_label(mod)))
            self.mod_list.addItem(item)
            if mod.id == current:
                self.mod_list.setCurrentItem(item)
        if self.mod_list.currentItem() is None and self.mod_list.count():
            self.mod_list.setCurrentRow(0)
        self.mod_list.blockSignals(False)
        set_tone(self.count_chip, "neutral", str(len(self.lib.mods)))
        waiting = sum(1 for m in self.lib.mods if updates.state(m) == "update")
        self.updates_btn.setText(f"{tr('Updates')} · {waiting}" if waiting else tr("Updates"))
        self.updates_btn.setVisible(bool(self.lib.mods))
        self._enable_tools()

    def _enable_tools(self) -> None:
        mod = self._selected()
        for b in (self.update_btn, self.remove_btn, self.up_btn, self.down_btn):
            b.setEnabled(mod is not None)
        if mod is not None:
            i = self.lib.mods.index(mod)
            self.up_btn.setEnabled(i > 0)
            self.down_btn.setEnabled(i < len(self.lib.mods) - 1)
            self.update_btn.setEnabled(not mod.personal)

    @staticmethod
    def _mod_line(mod: Mod) -> str:
        if mod.personal:
            return tr("Your own strings, loaded after every mod")
        bits = [ntr(len(mod.csv_on), "{n} file", "{n} files")]
        if mod.modules:
            bits.append(ntr(len(mod.modules), "{on} of {n} module on", "{on} of {n} modules on",
                            on=num(len(mod.modules) - len(mod.off))))
        edited = sum(1 for f in mod.files.values() if f.edited)
        if edited:
            bits.append(ntr(edited, "{n} edited by you", "{n} edited by you"))
        if mod.stamp:
            bits.append(tr("from {date}", date=mod.stamp))
        return " · ".join(bits)

    def _update_state(self) -> None:
        g, st = self.game, self.state
        if self._game_error is None:
            self.banner.hide()
        self.banner2.hide()
        t = tokens()
        waiting = [m for m in self.lib.mods if updates.state(m) == "update"]
        self.update_banner.hide()
        if waiting and not self._busy_updating:
            names = join([f"{m.name} {updates.latest_of(m).version or ''}".strip() for m in waiting[:3]])
            if len(waiting) > 3:
                names = ntr(len(waiting) - 3, "{names} and {n} more", "{names} and {n} more", names=names)
            fetchable = [m for m in waiting if updates.latest_of(m).downloadable]
            self.update_banner.show_message(
                ntr(len(waiting), "New version ready: {names}.", "New versions ready: {names}.",
                    names=html.escape(names)),
                (tr("Update all") if len(fetchable) > 1 else tr("Update")) if fetchable else "",
                lambda: self.install_updates(), glyph=icon("cloud", t["accent"], 18))
        if g is None:
            self.game_meta.setText(tr("no game found") if self.install is None else tr("reading…"))
            self.state_chip.hide()
            self.status_right.setText("")
            self.apply_btn.setEnabled(False)
            self.play_btn.setEnabled(False)
            self.restore_btn.setEnabled(False)
            if self.install is None:
                self.banner.show_message(tr("War Thunder was not found on this computer. Choose its folder: the "
                                            "one with lang.vromfs.bin and config.blk in it."),
                                         tr("Choose folder…"), self.choose_game_folder,
                                         glyph=icon("warn", t["warn"], 18))
            return
        where = {"steam": "Steam", "launcher": tr("Gaijin launcher")}.get(self.install.source, tr("Folder"))
        self.game_meta.setText(f"{where} · {g.version}")
        self.game_meta.setToolTip(str(self.install.root))
        m = st.get("manifest")
        is_pending = self.plan is not None and inst.pending(self.lib, self.plan, m)
        self.state_chip.show()
        if m is None:
            set_tone(self.state_chip, "neutral", tr("Not applied yet"))
        elif st.get("stale"):
            set_tone(self.state_chip, "warn", tr("Game updated: apply again"))
        elif is_pending:
            set_tone(self.state_chip, "warn", tr("Changes not applied yet"))
        else:
            set_tone(self.state_chip, "ok", tr("Applied {when}", when=m.applied))
        language = game_language(st["language"]) if st.get("language") else tr("language unknown")
        switch = tr("custom localization on") if st.get("switch_on") else tr("custom localization off")
        self.status_right.setText(f"{language} · {switch}")
        self.apply_btn.setEnabled(self.plan is not None)
        self.play_btn.setEnabled(self.plan is not None)
        self.restore_btn.setEnabled(bool(m) or bool(st.get("stray")))

        if st.get("stale"):
            self.banner.show_message(
                tr("War Thunder has updated since you last applied (then {old}, now {new}). Apply again so the "
                   "strings the update added load, instead of showing as their IDs.",
                   old=m.game_version, new=g.version),
                tr("Apply now"), lambda: self.apply(), glyph=icon("update", t["accent"], 18))
        stray = st.get("stray") or []

        def some(names: list[str]) -> str:
            return html.escape(join(names[:3]) + ("…" if len(names) > 3 else ""))

        hand = self.hand if m is None else None
        mine = hand.mod if hand is not None and hand.mod is not None and self.lib.get(hand.mod.id) else None
        if mine is not None and (hand.taken or hand.same):
            self.banner2.show_message(
                tr("{mod} was put in the game's lang folder by hand, and it is one of your mods now: first in the "
                   "order, so everything else goes on top of it. Apply writes it back as the manager's, and the "
                   "files as they were go to a backup.", mod=html.escape(mod_label(mine))),
                tr("Leave it out") if hand.taken else "", lambda: self._leave_out_hand_install(mine.id),
                glyph=icon("bubbles", t["accent"], 18),
                tip=tr("Take it back out of your mods and do not take it in again: Apply then moves it to a backup, "
                       "which Restore game puts back"))
        elif mine is not None:
            self.banner2.show_message(
                tr("{mod} is in the game's lang folder, put there by hand, and in your mods too, but the two copies "
                   "differ. Apply uses the one in your mods and moves the other to a backup.",
                   mod=html.escape(mod_label(mine))),
                tr("Use the lang folder's copy"), self.use_hand_copy, glyph=icon("bubbles", t["accent"], 18),
                tip=tr("Make the copy in the lang folder the one in your mods, as if it were a new version of "
                       "{mod}", mod=mine.name))
        elif hand is not None:
            said = (tr("{mod} is in the game's lang folder, put there by hand, and you left it out of your mods, so "
                       "Apply moves it to a backup.", mod=html.escape(hand.name)) if hand.declined else
                    tr("{mod} is in the game's lang folder, put there by hand. Apply makes it one of your mods, "
                       "first in the order, so it keeps working and everything else goes on top of it.",
                       mod=html.escape(hand.name)))
            self.banner2.show_message(said, tr("Add it to your mods"), self.import_hand_install,
                                      glyph=icon("bubbles", t["accent"], 18),
                                      tip=tr("Add it to your mods now: first in the order, with the modules it "
                                             "has switched on, so it keeps working when you apply"))
        elif stray:
            self.banner2.show_message(
                ntr(len(stray), "{n} file in the game's lang folder was not written by this manager ({names}). "
                                "Apply moves it to a backup.",
                    "{n} files in the game's lang folder were not written by this manager ({names}). Apply moves "
                    "them to a backup.", names=some(stray)),
                glyph=icon("warn", t["warn"], 18))
        elif st.get("edited"):
            ed = st["edited"]
            self.banner2.show_message(
                ntr(len(ed), "{n} file in the game's lang folder changed since the last Apply ({names}). Apply "
                             "takes the edit into its mod. If you pasted in a new version of a mod, add it with "
                             "Update instead.",
                    "{n} files in the game's lang folder changed since the last Apply ({names}). Apply takes "
                    "those edits into their mod. If you pasted in a new version of a mod, add it with Update "
                    "instead.", names=some(ed)),
                glyph=icon("pencil", t["accent"], 18))

    # -- the right-hand panel -----------------------------------------------------------------------
    def _view_chosen(self, key: str) -> None:
        self._show_view()
        if key == "strings":
            self.strings_view.run_search()
            self.strings_view.search.setFocus()

    def find_strings(self) -> None:
        """Ctrl+F: the Strings tab, ready to type in."""
        if not self.lib.mods:
            return
        self.view_switch.set_current("strings")
        self._view_chosen("strings")
        self.strings_view.search.selectAll()

    def _show_view(self) -> None:
        self.view_switch.setVisible(bool(self.lib.mods))
        if not self.lib.mods:
            self.stack.setCurrentWidget(self.empty)
            self.view_info.setText("")
            return
        key = self.view_switch.current()
        self.stack.setCurrentIndex(VIEWS.index(key))
        if key == "order" and self.plan is not None and self.game is not None:
            own = len(self.game.loc_table()) - len(self.plan.left_out)
            extra = len(self.plan.loc_table) - own - len(self.plan.regional_moved)
            self.view_info.setText(tr("game tables: {game} · from your mods: {mods}", game=num(own), mods=num(extra)))
        elif key == "conflicts" and self.analysis is not None:
            self.view_info.setText(tr("the lowest mod in the list shows, unless you pick"))
        elif key == "strings":
            self.view_info.setText(tr("game language: {language}", language=game_language(self.language()))
                                   if self.analysis is not None else "")
        elif key == "details":
            mod = self._selected()
            r = self.plan.reports.get(mod.id) if (mod and self.plan) else None
            self.view_info.setText(ntr(len(r.loads), "loads {n} entry", "loads {n} entries") if r else "")
        else:
            self.view_info.setText("")

    def _selected(self) -> Mod | None:
        item = self.mod_list.currentItem()
        return self.lib.get(item.data(ID_ROLE)) if item else None

    def _show_details(self) -> None:
        self._enable_tools()
        mod = self._selected()
        self.details.setHtml(self._mod_html(mod) if mod is not None else "")
        self._show_view()

    def _mod_html(self, mod: Mod) -> str:
        t = {k: solid(v) for k, v in tokens().items() if not k.startswith("_")}
        e = html.escape
        parts = [f"<div style='color:{t['text']}'>"]
        parts.append(f"<p style='font-size:18px; font-weight:600; margin:0'>{e(mod_label(mod))}</p>")
        meta = []
        if mod.personal:
            meta.append(tr("Your own strings. One line each: <code>string ID;your text</code>. This loads after "
                           "every mod, so it wins over all of them."))
            meta.append(f"<a href='edit-personal' style='color:{t['accent']}'>{tr('Open the file')}</a>")
        else:
            meta.append(ntr(len(mod.csv_on), "{n} language file and its own load list",
                            "{n} language files and its own load list") if mod.has_list
                        else ntr(len(mod.csv_on), "{n} language file", "{n} language files"))
            if mod.added:
                meta.append(tr("added {added}, updated {updated}", added=e(mod.added), updated=e(mod.updated))
                            if mod.updated != mod.added else tr("added {added}", added=e(mod.added)))
            if mod.source:
                meta.append(tr("from {source}", source=f"<span style='color:{t['muted']}'>{e(mod.source)}</span>"))
            edited = [n for n, f in mod.files.items() if f.edited]
            if edited:
                meta.append(tr("edited by you, kept through updates: {names}", names=e(join(edited))))
            links = [f"<a href='open-folder' style='color:{t['accent']}'>{tr('Open its folder')}</a>"]
            if self.lib.readme_path(mod) is not None:
                links.insert(0, f"<a href='readme' style='color:{t['accent']}'>{tr('Read me')}</a>")
            meta.append(" · ".join(links))
        parts.append(f"<p style='margin-top:4px; color:{t['muted']}'>" + "<br>".join(meta) + "</p>")
        if not mod.enabled:
            parts.append(f"<p style='color:{t['warn']}'>{tr('Switched off: it is not written to the game.')}</p>")
        if not mod.personal:
            parts.append(self._updates_html(mod, t))
            parts.append(self._modules_html(mod, t))
        checks = self._checks_html(mod, t)
        if checks:
            parts.append(checks)
        s = self.analysis.stats.get(mod.id) if self.analysis else None
        head = f"<p style='color:{t['muted']}; font-size:11px; font-weight:600; letter-spacing:1px; margin-top:14px'>"
        if s is not None:
            lang = game_language(self.analysis.language)
            parts.append(head + e(tr("WHAT IT CHANGES ({language})", language=lang.upper())) + "</p><p>")
            count = f"<b style='color:{t['accent']}; font-size:15px'>{num(s.changes)}</b>"
            parts.append(ntr(s.changes, "{count} string differs from the game",
                             "{count} strings differ from the game", count=count))
            if s.same:
                parts.append("<br>" + ntr(s.same, "{n} set to exactly what the game already says",
                                          "{n} set to exactly what the game already says"))
            if s.new:
                parts.append("<br>" + ntr(s.new, "{n} for a string ID the game does not have (removed vehicles, "
                                                 "typos, or strings from a newer game)",
                                          "{n} for string IDs the game does not have (removed vehicles, typos, "
                                          "or strings from a newer game)"))
            if s.noise:
                parts.append("<br>" + ntr(s.noise, "{n} comment or spacer row, harmless",
                                          "{n} comment or spacer rows, harmless"))
            parts.append("</p>")
            if s.missing_language:
                parts.append(f"<p style='color:{t['warn']}'>"
                             + e(tr("This mod has no “{language}” column ({columns}), so it changes nothing while "
                                    "the game is in “{language}”.", language=lang,
                                    columns=join([game_language(c) for c in s.languages])))
                             + "</p>")
            if s.problems:
                more = len(s.problems) - 12
                parts.append(head + e(tr("WORTH A LOOK")) + "</p><ul>"
                             + "".join(f"<li>{e(p)}</li>" for p in s.problems[:12])
                             + (f"<li>{e(ntr(more, 'and {n} more', 'and {n} more'))}</li>" if more > 0 else "")
                             + "</ul>")
        elif self.plan is not None and mod.enabled and not mod.personal:
            parts.append(f"<p style='color:{t['faint']}'>{tr('Checking what it changes…')}</p>")
        parts.append("</div>")
        return "".join(parts)

    def _modules_html(self, mod: Mod, t: dict) -> str:
        """The mod's optional modules, each with its switch; nothing if it has none."""
        if not mod.modules:
            return ""
        e = html.escape
        on = len(mod.modules) - len(mod.off)
        parts = [f"<p style='color:{t['muted']}; font-size:11px; font-weight:600; letter-spacing:1px; "
                 f"margin-top:14px'>{e(tr('OPTIONAL MODULES · {on} OF {n} ON', on=num(on), n=num(len(mod.modules))))}"
                 f"</p><p style='color:{t['muted']}'>"
                 + e(tr("Extras its author publishes with it: names in other languages, longer ammunition names and "
                        "the like. Switch on the ones you want; each loads where the mod's own list puts it."))
                 + "</p><p>"]
        rows = []
        for label in sorted(mod.modules, key=lambda m: module_name(m).lower()):
            is_on = label not in mod.off
            files = mod.module_files(label)
            names = e(tr("Its files: {names}", names=join(files[:8]) + ("…" if len(files) > 8 else "")))
            do = (tr("Leave this module out: its files stop loading at the next Apply") if is_on
                  else tr("Load this module: its files go where the mod's list puts them at the next Apply"))
            rows.append(f"<span style='color:{t['accent'] if is_on else t['faint']}'>{'●' if is_on else '○'}</span>"
                        f"&nbsp; <span title='{names}' style='color:{t['text'] if is_on else t['muted']}'>"
                        f"{e(module_name(label))}</span> <span style='color:{t['faint']}'>"
                        f"{e(ntr(len(files), '{n} file', '{n} files'))}</span> · "
                        f"<a href='module:{'off' if is_on else 'on'}:{e(quote(label))}' title='{e(do)}' "
                        f"style='color:{t['accent']}'>{e(tr('switch off') if is_on else tr('switch on'))}</a>")
        more = tr("A module from a download of its own, such as the ones IFN1 publishes on Nexus Mods")
        rows.append(f"<a href='add-module' title='{e(more)}' style='color:{t['accent']}'>{e(tr('Add a module…'))}</a>")
        return "".join(parts) + "<br>".join(rows) + "</p>"

    def _updates_html(self, mod: Mod, t: dict) -> str:
        """Where the mod's new versions come from, what the newest is, and what to do about it."""
        e = html.escape

        def a(href: str, text: str, tip: str = "") -> str:
            title = f" title='{e(tip)}'" if tip else ""
            return f"<a href='{href}'{title} style='color:{t['accent']}'>{text}</a>"

        head = (f"<p style='color:{t['muted']}; font-size:11px; font-weight:600; letter-spacing:1px; "
                f"margin-top:14px'>{e(tr('UPDATES'))}</p>")
        src = updates.source_of(mod)
        if src is None:
            lines = [e(tr("Follows nowhere yet, so it cannot update by itself."))]
            hint = updates.suggestion(mod)
            offers = []
            if hint is not None:
                offers.append(a("upd:suggest", e(tr("Follow {place}", place=hint.label or hint.place))))
            offers.append(a("upd:follow", e(tr("Follow a page on {places}…", places=sources.places()))))
            lines.append(" · ".join(offers))
            return head + "<p>" + "<br>".join(lines) + "</p>"
        lines = [tr("Follows {page}", page=a("upd:page", e(src.label or src.page))) + " · "
                 + a("upd:follow", e(tr("change"))) + " · " + a("upd:stop", e(tr("stop")))]
        state, how = updates.judge(mod)
        rel = updates.latest_of(mod)
        dated = how == "date"
        if self._busy_updating:
            lines.append(f"<span style='color:{t['muted']}'>{e(tr('Working on it…'))}</span>")
        elif state == "error":
            lines.append(f"<span style='color:{t['warn']}'>{e(tr('Could not check: {error}', error=mod.check_error))}"
                         "</span> · " + a("upd:check", e(tr("try again"))))
        elif rel is None:
            lines.append(e(tr("Not checked yet")) + " · " + a("upd:check", e(tr("Check now"))))
        else:
            bits = [e(rel.version) if rel.version else e(tr("no version"))]
            if rel.size:
                bits.append(e(tr("{size} KB", size=num(round(rel.size / 1024)))))
            if rel.published:
                bits.append(e(day(rel.published)))
            lines.append(tr("Newest there: {file} ({details})", file=f"<b>{e(rel.file_name)}</b>",
                            details=", ".join(bits)))
            if state == "update":
                if rel.downloadable:
                    said = tr("A new version is ready (judging by the date).") if dated else tr("A new version is ready.")
                    lines.append(f"<b style='color:{t['accent']}'>{e(said)}</b> " + a("upd:install", e(tr("Update now"))))
                else:
                    said = tr("A new version is out (judging by the date).") if dated else tr("A new version is out.")
                    lines.append(f"<b style='color:{t['accent']}'>{e(said)}</b> {e(rel.note)} "
                                 + a("upd:page", e(tr("Open the download page"))))
            elif state == "current":
                said = tr("You have it (judging by the date).") if dated else tr("You have it.")
                lines.append(f"<span style='color:{t['ok']}'>{e(said)}</span>"
                             + (" · " + a("upd:again", e(tr("Reinstall this version")),
                                          tr("Download it again and add it as it comes, to bring in what the manager "
                                             "now takes from it, such as optional modules. Your edits and switches "
                                             "stay.")) if rel.downloadable else ""))
            else:
                lines.append(e(tr("Cannot tell whether yours is the same.")) + " "
                             + (a("upd:install", e(tr("Update to be sure"))) + " · " if rel.downloadable else "")
                             + a("upd:mark", e(tr("It is the one I have"))))
            lines.append(f"<span style='color:{t['faint']}'>{e(tr('checked {when}', when=_ago(mod.checked)))}</span> · "
                         + a("upd:check", e(tr("Check now"))))
        return head + "<p>" + "<br>".join(lines) + "</p>"

    def _checks_html(self, mod: Mod, t: dict) -> str:
        e = html.escape
        r = self.plan.reports.get(mod.id) if self.plan else None
        if r is None or self.game is None:
            return ""
        items = []
        fixed = f"<b style='color:{t['ok']}'>{e(tr('Fixed'))}</b>"
        if r.stale_list:
            names = e(join(r.stale_list[:6]) + ("…" if len(r.stale_list) > 6 else ""))
            items.append("<li>" + ntr(len(r.stale_list),
                                      "{fixed} · its own load list leaves out {n} of the game's current files "
                                      "({names}). Used as shipped, its strings would be missing; the manager loads it.",
                                      "{fixed} · its own load list leaves out {n} of the game's current files "
                                      "({names}). Used as shipped, their strings would be missing; the manager "
                                      "loads them.", fixed=fixed, names=names) + "</li>")
        if r.gone:
            items.append("<li>" + e(tr("Its list names files the game no longer has, skipped: {names}",
                                       names=join(r.gone))) + "</li>")
        for name, kept, total in r.replaced:
            items.append("<li>" + tr("{fixed} · ships a full copy of the game's {file}. Only the rows that differ load "
                                     "({kept} of {total}), so strings the game added since still come through.",
                                     fixed=fixed, file=f"<code>{e(name)}</code>", kept=num(kept), total=num(total))
                         + "</li>")
        if r.regional:
            items.append("<li>" + e(tr("Loads the event tables ({names}) ahead of its own files, so it can rename "
                                       "event items too.", names=join(r.regional))) + "</li>")
        if r.not_shipped:
            items.append("<li>" + ntr(len(r.not_shipped),
                                      "{n} optional file its list can load is not installed. Add a module with "
                                      "<b>Update</b> to switch one on.",
                                      "{n} optional files its list can load are not installed. Add a module with "
                                      "<b>Update</b> to switch one on.") + "</li>")
        if r.unlisted:
            items.append("<li>" + e(tr("Not loaded, because its own list does not name them: {names}",
                                       names=join(r.unlisted))) + "</li>")
        for src, dest in r.renamed:
            items.append("<li>" + tr("{source} is written as {dest}, because another mod or the game has that name.",
                                     source=f"<code>{e(src)}</code>", dest=f"<code>{e(dest)}</code>") + "</li>")
        items += [f"<li>{e(n)}</li>" for n in r.notes]
        if not items:
            return ""
        return (f"<p style='color:{t['muted']}; font-size:11px; font-weight:600; letter-spacing:1px; "
                f"margin-top:14px'>{e(tr('CHECKED AGAINST GAME {version}', version=self.game.version))}</p>"
                f"<ul>{''.join(items)}</ul>")

    def _load_list_text(self) -> str:
        if self.plan is None or self.game is None:
            return ""
        labels = {m.id: mod_label(m) for m in self.lib.mods}
        labels[PICKS_ID] = tr("Your picks")
        base = [ref.split("/", 1)[-1] for ref in self.game.loc_table() if ref not in self.plan.left_out]
        out = [tr("What the game loads, in order. Each file can override the ones above it."), "",
               tr("The game's own tables ({count})", count=num(len(base)))]
        out += textwrap.wrap(", ".join(base), 96, initial_indent="    ", subsequent_indent="    ")
        if self.plan.left_out:
            out.append("    " + tr("Left out, as a mod's own list asks: {names}",
                                   names=join([ref.split("/", 1)[-1] for ref in self.plan.left_out])))
        moved = [ref.split("/", 1)[-1] for ref in self.plan.regional_moved]
        if moved:
            out += ["", tr("The game's event tables, moved ahead of the mods so that their changes show ({count})",
                           count=num(len(moved)))]
            out += textwrap.wrap(", ".join(moved), 96, initial_indent="    ", subsequent_indent="    ")
        for mod_id, r in self.plan.reports.items():
            out += ["", f"{labels.get(mod_id, mod_id)} ({ntr(len(r.loads), '{n} entry', '{n} entries')})"]
            out += [f"    {dest}" for dest in r.loads]
        if not self.plan.reports:
            out += ["", tr("No mods switched on: Apply would write the game's own list and nothing else.")]
        last = [ref.split("/", 1)[-1] for ref in self.plan.regional_last]
        if last:
            out += ["", tr("The game's event tables, loaded after everything else ({count})", count=num(len(last)))]
            out += textwrap.wrap(", ".join(last), 96, initial_indent="    ", subsequent_indent="    ")
        out += ["", "-" * 72, tr("The localization.blk that Apply writes:"), "", self.plan.localization]
        return "\n".join(out)

    def _fill_conflicts(self) -> None:
        an = self.analysis
        rows = an.conflicts if an else []
        needle = self.conflict_filter.text().strip().lower()
        if needle:
            rows = [c for c in rows if needle in c.key.lower() or any(needle in t.lower() for _, t in c.texts)]
        keep = self._conflict_key()
        self.conflicts.blockSignals(True)
        self.conflicts.setRowCount(len(rows[:5000]))
        names = self.source_names()
        accent = QColor(tokens()["accent"])
        chosen = -1
        for i, c in enumerate(rows[:5000]):
            win_mod, win_text = c.shown if c.shown[0] else c.texts[-1]
            who = tr("the game") if win_mod == GAME else names.get(win_mod, win_mod)
            if c.picked and c.picked == win_mod:
                who = tr("{source}, picked", source=who)
            others = "; ".join(f"{names.get(m, m)}: {t}" for m, t in c.texts if m != win_mod)
            for col, text in enumerate((c.key, c.game, f"{win_text}   · {who}", others)):
                item = QTableWidgetItem(text.replace("\r", " ").replace("\n", " "))
                item.setToolTip(text)
                if col == 0:
                    item.setData(KEY_ROLE, c.key)
                if col == 2:
                    item.setForeground(accent)
                self.conflicts.setItem(i, col, item)
            if c.key == keep:
                chosen = i
        self.conflicts.blockSignals(False)
        if chosen >= 0:
            self.conflicts.setCurrentCell(chosen, 0)
        self._show_conflict()
        if an is None:
            self.view_switch.set_label("conflicts", tr("Conflicts"))
        else:
            self.view_switch.set_label("conflicts", f"{tr('Conflicts')} · {num(len(an.conflicts))}")
        self._show_view()

    def _conflict_key(self) -> str | None:
        row = self.conflicts.currentRow()
        item = self.conflicts.item(row, 0) if row >= 0 else None
        return item.data(KEY_ROLE) if item is not None else None

    def _show_conflict(self) -> None:
        self.show_string_card(self.conflict_card, self._conflict_key())

    # -- single strings: picks and the player's own text ----------------------------------------------
    def language(self) -> str:
        return self.state.get("language") or "English"

    def source_names(self) -> dict[str, str]:
        names = {m.id: mod_name(m) for m in self.lib.mods}
        names[GAME] = tr("The game")
        return names

    def show_string_card(self, card: StringCard, key: str | None) -> None:
        language = self.language()
        own_mod = self.lib.personal(create=False)
        own = strings.own_text(self.lib, key, language) if key else None
        card.show_string(key, self.analysis, self.source_names(), own, language,
                         strings.picks(self.lib).get(key, "") if key else "", own_mod.id if own_mod else "")

    def pick_string(self, key: str, source: str) -> None:
        """Make ``source``'s text the one a string shows (the game's, or a mod's); "" goes back to the order."""
        if source:
            strings.pick(self.lib, key, source)
            text = (tr("{key}: the game's own text shows now, whatever the order.", key=key) if source == GAME
                    else tr("{key}: the text from {mod} shows now, whatever the order.", key=key,
                            mod=self.source_names().get(source, source)))
        else:
            strings.unpick(self.lib, key)
            text = tr("{key}: back to the load order.", key=key)
        self._refresh_all()
        self.say(sentences(text, tr("Apply, or Play, to put it in the game.")))

    def set_own_string(self, key: str, text: str) -> None:
        """The player's own text for a string, in the game's language; "" takes it out."""
        language = self.language()
        if text.strip():
            strings.set_own_text(self.lib, key, language, text.strip())
            said = tr("{key} shows your own text now.", key=key)
        else:
            strings.clear_own_text(self.lib, key, language)
            said = tr("{key}: your own text is out.", key=key)
        self._refresh_all()
        self.say(sentences(said, tr("Apply, or Play, to put it in the game.")))

    # -- profiles --------------------------------------------------------------------------------------
    def _show_profile(self) -> None:
        name = profiles.active(self.lib)
        self.profile_btn.setText(name or tr("Profiles"))
        self.profile_btn.setIcon(icon("layers", tokens()["accent"] if name else tokens()["muted"], 15))
        self.profile_btn.setVisible(bool(self.lib.mods) or bool(profiles.names(self.lib)))

    def _fill_profile_menu(self) -> None:
        menu = self.profile_menu
        menu.clear()
        current = profiles.active(self.lib)
        names = profiles.names(self.lib)
        group = QActionGroup(menu)
        for i, name in enumerate(names, 1):
            act = QAction(name + ("\t" + tr("Ctrl+{key}", key=i) if i < 10 else ""), menu, checkable=True)
            act.setChecked(name == current)
            act.triggered.connect(lambda _=False, n=name: self.switch_profile(n))
            group.addAction(act)
            menu.addAction(act)
        if not names:
            hint = menu.addAction(tr("Save the mods as they are now, and switch between sets in one go"))
            hint.setEnabled(False)
        menu.addSeparator()
        menu.addAction(tr("Save as a new profile…"), self.save_profile_as)
        if current:
            menu.addAction(tr("Rename {profile}…", profile=current), self.rename_profile)
            menu.addAction(tr("Delete {profile}", profile=current), self.delete_profile)
        menu.addSeparator()
        if current:
            menu.addAction(tr("Export {profile} to send to a friend…", profile=current), self.export_profile)
        menu.addAction(tr("Import a profile…"), self.import_profile)

    def _ask_name(self, title: str, text: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, tr("Name:"), QLineEdit.EchoMode.Normal, text)
        return name if ok else None

    def save_profile_as(self, name: str | None = None) -> None:
        name = name if name is not None else self._ask_name(tr("Save as a profile"),
                                                            "" if profiles.names(self.lib) else tr("My mods"))
        if name is None:
            return
        try:
            name = profiles.save_as(self.lib, name)
        except ProfileError as exc:
            QMessageBox.warning(self, tr("Save as a profile"), str(exc))
            return
        self._refresh_all()
        self.say(tr("Saved as {profile}. Changes you make now go into it; switch with the Profiles button or "
                    "{keys}.", profile=name, keys=tr("Ctrl+{key}", key=profiles.names(self.lib).index(name) + 1)))

    def switch_profile(self, name: str) -> None:
        try:
            profiles.switch(self.lib, name)
        except ProfileError as exc:
            self.say(str(exc))
            return
        self._refresh_all()
        on = len(self.lib.enabled())
        self.say(sentences(ntr(on, "Switched to {profile}: {n} mod on.", "Switched to {profile}: {n} mods on.",
                               profile=name), tr("Apply, or Play, to put it in the game.")))

    def switch_profile_number(self, n: int) -> None:
        names = profiles.names(self.lib)
        if 0 < n <= len(names):
            self.switch_profile(names[n - 1])

    def rename_profile(self) -> None:
        current = profiles.active(self.lib)
        new = self._ask_name(tr("Rename {profile}", profile=current), current) if current else None
        if not new:
            return
        try:
            profiles.rename(self.lib, current, new)
        except ProfileError as exc:
            QMessageBox.warning(self, tr("Rename"), str(exc))
            return
        self._show_profile()

    def delete_profile(self) -> None:
        current = profiles.active(self.lib)
        if not current or QMessageBox.question(
                self, tr("Delete profile"),
                tr("Forget the profile {profile}? Your mods stay just as they are now.", profile=current)) \
                != QMessageBox.StandardButton.Yes:
            return
        profiles.delete(self.lib, current)
        self._show_profile()
        self.say(tr("Forgot {profile}. Your mods are as they were.", profile=current))

    def export_profile(self, path: str | None = None) -> None:
        current = profiles.active(self.lib)
        if not current:
            return
        if path is None:
            start = str(Path.home() / "Desktop" / profiles.file_name(current))
            path, _ = QFileDialog.getSaveFileName(self, tr("Export {profile}", profile=current), start,
                                                  f"{tr('Langmod Manager profiles')} (*{profiles.EXTENSION})")
            if not path:
                return
        try:
            out = profiles.export(self.lib, current, path)
        except (OSError, ProfileError) as exc:
            QMessageBox.warning(self, tr("Export"), str(exc))
            return
        self.say(tr("Exported {profile} to {file}. Whoever gets it uses Import a profile, under Profiles; the mods "
                    "they do not have are fetched for them where they can be.", profile=current, file=out.name))

    def import_profile(self, path: str | None = None, ask: bool = True) -> None:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, tr("Import a profile"), str(Path.home() / "Downloads"),
                f"{tr('Langmod Manager profiles')} (*{profiles.EXTENSION});;{tr('All files')} (*)")
            if not path:
                return
        try:
            doc = profiles.read_file(path)
        except ProfileError as exc:
            QMessageBox.warning(self, tr("Import a profile"), str(exc))
            return
        found = profiles.entries(self.lib, doc)
        have = [e for e in found if e.have is not None]
        fetch = [e for e in found if e.have is None and not e.personal and e.source is not None
                 and e.source.kind != "nexus"]
        lost = [e for e in found if e.have is None and not e.personal and e not in fetch]
        own = [e for e in found if e.personal and e.files]
        count = len([e for e in found if not e.personal])
        lines = [ntr(count, "{profile}: {n} mod, made with {program}.", "{profile}: {n} mods, made with {program}.",
                     profile=doc["name"], program=doc.get("made_with"))
                 if doc.get("made_with") else ntr(count, "{profile}: {n} mod.", "{profile}: {n} mods.",
                                                  profile=doc["name"])]
        if have:
            lines.append(tr("You have: {names}.", names=join([e.name for e in have])))
        if fetch:
            lines.append(tr("Fetched for you: {names}.", names=join(
                [tr("{mod} (from {place})", mod=e.name, place=e.source.place) for e in fetch])))
        if lost:
            lines.append(tr("Not here, and it cannot be fetched: {names}. Add it by hand afterwards; the profile "
                            "leaves it out.", names=join([e.name for e in lost])))
        if own:
            lines.append(tr("Its own strings come along as a mod of their own. Yours stay yours, and still win."))
        if doc["picks"]:
            lines.append(ntr(len(doc["picks"]), "{n} picked string.", "{n} picked strings."))
        lines.append("\n" + tr("It becomes a profile of yours, switched to straight away."))
        if ask and QMessageBox.question(self, tr("Import {profile}", profile=doc["name"]), "\n".join(lines)) \
                != QMessageBox.StandardButton.Yes:
            return

        def finish(_added=(), failed=()):
            name, missing = profiles.take(self.lib, doc)
            self._refresh_all()
            parts = [tr("Imported {profile}, and switched to it.", profile=name)]
            if missing:
                parts.append(tr("Not in it, as you do not have them: {names}.", names=join(missing)))
            if failed:
                parts.append(tr("Could not fetch: {failures}.", failures="; ".join(failed)))
            self.say(sentences(*parts, tr("Apply, or Play, to put it in the game.")))

        if fetch:
            self.fetch_mods([(e.name, e.source) for e in fetch], finish)
        else:
            finish()

    def fetch_mods(self, items: list, then=None) -> None:
        """Look up and download mods this library does not have yet - (name, source) each - and add them,
        each following its page. ``then(added mods, failures)`` runs once they are in."""
        if self._busy_updating:
            if then:
                then([], [tr("another download is still going")])
            return
        self._busy_updating = True
        self.updates_btn.setEnabled(False)
        folder = updates.downloads(self.lib)
        key = self.prefs.get("nexus_key")
        self.say(tr("Downloading {names}…", names=join([name for name, _src in items])))

        def work():
            got, failed = [], []
            for name, src in items:
                found = updates.look(name, src, key)
                rel = found.release
                if found.error or rel is None or not rel.downloadable:
                    failed.append(f"{name}: " + (found.error or (rel.note if rel is not None else "")
                                                 or tr("its page has nothing to download")))
                    continue
                try:
                    got.append((name, found.source, rel, updates.fetch(rel, folder)))
                except Exception as exc:
                    failed.append(f"{name}: {exc}")
            return got, failed

        def done(result):
            got, failed = result
            added = []
            for name, src, rel, path in got:            # added here, on the thread that owns the library
                try:
                    added.append(updates.take_new(self.lib, src, rel, path).mod)
                except Exception as exc:
                    failed.append(f"{name}: {exc}")
            self._busy_updating = False
            self.updates_btn.setEnabled(True)
            if added:
                self.view_switch.set_current("details")
            self._refresh_all(added[0].id if len(added) == 1 else None)
            bits = [tr("Added {mod}; it keeps itself up to date from its page.", mod=m.label) for m in added]
            bits += [tr("Could not get {failure}.", failure=f) for f in failed]
            if added:
                bits.append(tr("Apply, or Play, to put it in the game."))
            self.say(sentences(*bits))
            if then:
                then(added, failed)

        def fail(text):
            self._busy_updating = False
            self.updates_btn.setEnabled(True)
            self._failed(text)
            if then:
                then([], [text.strip().splitlines()[-1]])

        self._run(work, done, fail)

    def _link(self, url: QUrl) -> None:
        mod = self._selected()
        what = url.toString()
        if what == "open-folder" and mod:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.lib.mod_dir(mod))))
        elif what == "edit-personal":
            self.edit_personal()
        elif what == "readme" and mod is not None:
            self.show_readme(mod)
        elif what.startswith("upd:") and mod is not None:
            self._update_link(mod, what[4:])
        elif what == "add-module":
            self.update_selected()
        elif what.startswith("module:") and mod is not None:
            _scheme, state, label = bytes(url.toEncoded()).decode("ascii").split(":", 2)
            label = unquote(label)
            try:
                self.lib.set_module(mod.id, label, state == "on")
            except LibraryError as exc:
                self.say(str(exc))
                return
            self._refresh_all(mod.id)
            self.say(tr("{module} is on: Apply to put it in the game.", module=module_name(label)) if state == "on"
                     else tr("{module} is off: Apply to take it out of the game.", module=module_name(label)))

    # -- updates from where mods are published --------------------------------------------------------
    def _update_link(self, mod: Mod, what: str) -> None:
        if what == "check":
            self.check_updates(mods=[mod])
        elif what == "install":
            self.install_updates([mod])
        elif what == "again":
            self.install_updates([mod], again=True)
        elif what == "stop":
            updates.unfollow(self.lib, mod)
            self._refresh_all(mod.id)
        elif what == "mark":
            updates.mark_current(self.lib, mod)
            self._refresh_all(mod.id)
        elif what == "page":
            src, rel = updates.source_of(mod), updates.latest_of(mod)
            page = (rel.page if rel and not rel.downloadable else None) or (src.page if src else "")
            if page:
                QDesktopServices.openUrl(QUrl(page))
        elif what == "suggest":
            hint = updates.suggestion(mod)
            if hint is not None:
                updates.follow(self.lib, mod, hint)
                self.check_updates(mods=[mod])
        elif what == "follow":
            self.follow_dialog(mod)

    def follow_dialog(self, mod: Mod) -> None:
        from .follow_dialog import FollowDialog
        dlg = FollowDialog(mod, updates.source_of(mod) or updates.suggestion(mod), self)
        if dlg.exec() and dlg.source is not None:
            updates.follow(self.lib, mod, dlg.source)
            self.check_updates(mods=[mod])

    def check_updates(self, mods: list[Mod] | None = None, auto: bool = False, then=None) -> None:
        """Ask each followed mod's source for its newest release.

        Only the asking happens off the thread that draws; what comes back is kept on the
        mods here, so the library is never changed from two threads at once.
        """
        if self._busy_updating:
            if then:
                then()
            return
        lib = self.lib
        todo = [m for m in (mods or lib.mods) if m.follow]
        if not todo:
            if not auto:
                self.say(tr("No mod follows anywhere yet. Select one and use Follow in its details."))
                self.view_switch.set_current("details")
                self._show_view()
            if then:
                then()
            return
        self._busy_updating = True
        self.updates_btn.setEnabled(False)
        self.say(ntr(len(todo), "Looking for a new version of {n} mod…", "Looking for new versions of {n} mods…"))
        asks = [(m.id, updates.source_of(m)) for m in todo]
        key = self.prefs.get("nexus_key")

        def work():
            return [updates.look(mod_id, src, key) for mod_id, src in asks]

        def done(looks):
            for found in looks:
                updates.record(lib, found)
            if mods is None:
                self.prefs.set("update_last", time.time())
            self._checked([m for m in lib.mods if updates.state(m) == "update"], todo, auto, then)

        self._run(work, done, lambda text: self._update_failed(text, then))

    def _checked(self, found: list[Mod], asked: list[Mod], auto: bool, then=None) -> None:
        self._busy_updating = False
        self.updates_btn.setEnabled(True)
        failed = [m for m in asked if m.check_error]
        if found and auto and self.prefs.get("update_install") == "auto":
            self.install_updates(found, auto=True, then=then)
            return
        self._refresh_all()
        if then:
            then()
            return
        if found:
            self.say(ntr(len(found), "New version ready for {names}.", "New versions ready for {names}.",
                         names=join([m.name for m in found])))
        elif failed:
            self.say(tr("Could not check {names}: {error}", names=join([m.name for m in failed]),
                        error=failed[0].check_error))
        elif not auto:
            self.say(tr("Everything that follows a page is up to date.") if len(asked) > 1
                     else tr("{mod}: checked.", mod=asked[0].name))

    def _update_failed(self, text: str, then=None) -> None:
        self._busy_updating = False
        self.updates_btn.setEnabled(True)
        self._refresh_all()
        self._failed(text)
        if then:
            then()                       # offline, or the source broke: play anyway

    def install_updates(self, mods: list[Mod] | None = None, auto: bool = False, then=None,
                        again: bool = False) -> None:
        """Download what is waiting and add it to each mod as its new version. ``again``: the newest release
        even where the mod has it already."""
        if self._busy_updating:
            if then:
                then()
            return
        lib = self.lib
        todo = [m for m in (mods or lib.mods)
                if updates.latest_of(m) is not None and updates.latest_of(m).downloadable
                and (again or updates.state(m) in ("update", "unknown"))]
        if not todo:
            if then:
                then()
            return
        self._busy_updating = True
        self.updates_btn.setEnabled(False)
        self.say(tr("Downloading {names}…", names=join([m.name for m in todo])))
        jobs = [(m.id, m.name, updates.latest_of(m)) for m in todo]
        names = {mod_id: name for mod_id, name, _rel in jobs}
        folder = updates.downloads(lib)
        applied_before = self.install is not None and inst.read_manifest(self.install.lang_dir) is not None

        def work():
            # Only the downloads happen here; they are added to their mods back on this thread.
            got, failed = [], []
            for mod_id, name, rel in jobs:
                try:
                    got.append((mod_id, rel, updates.fetch(rel, folder)))
                except Exception as exc:
                    failed.append(f"{name}: {exc}")
            return got, failed

        def finished(result):
            got, failed = result
            done = []
            for mod_id, rel, path in got:
                try:
                    done.append(updates.take(lib, mod_id, rel, path))
                except Exception as exc:
                    failed.append(f"{names[mod_id]}: {exc}")
            self._busy_updating = False
            self.updates_btn.setEnabled(True)
            keep = done[0].mod.id if len(done) == 1 else None
            self._refresh_all(keep)
            said = N_("Installed {mod} {version} again.") if again else N_("Updated {mod} to {version}.")
            bits = [tr(said, mod=d.mod.name, version=d.release.version or d.release.file_name) for d in done]
            bits += [tr("Could not update {failure}.", failure=f) for f in failed]
            if done and self.prefs.get("update_apply") and applied_before and not then:
                if self.apply(auto=True):
                    bits.append(tr("Applied."))
            elif done and not then:
                bits.append(tr("Apply to put it in the game."))
            text = sentences(*bits)
            self.say(tr("By itself: {what}", what=text) if auto else text)
            if then:
                then()

        self._run(work, finished, lambda text: self._update_failed(text, then))

    # -- new versions of the manager itself ---------------------------------------------------------------
    def look_for_new_self(self, now: bool = False, then=None) -> None:
        """Ask its GitHub releases whether there is a newer Langmod Manager: once a day by itself, or ``now``.
        ``then(error)`` is called when done, with "" or what went wrong."""
        if not selfupdate.available() or not (now or selfupdate.due(self.prefs)):
            self._show_new_self()
            if then:
                then("")
            return

        def done(found):
            selfupdate.record(self.prefs, found)
            self._show_new_self()
            if then:
                then("")

        def failed(text):
            if not now:
                selfupdate.record(self.prefs, selfupdate.known(self.prefs))     # not again for a day
            if then:
                then(text.strip().splitlines()[-1].split(": ", 1)[-1])

        self._run(selfupdate.check, done, failed)

    def _show_new_self(self) -> None:
        new = selfupdate.known(self.prefs)
        if new is None:
            self.app_banner.hide()
            return
        said = tr("Langmod Manager {version} is out; this is {current}.", version=new.version, current=__version__)
        if selfupdate.standalone():
            self.app_banner.show_message(said, tr("Update and restart"), self.update_self,
                                         glyph=icon("update", tokens()["accent"], 18),
                                         tip=tr("Your mods and settings stay as they are"))
        else:
            self.app_banner.show_message(said, tr("Open the download page"),
                                         lambda: QDesktopServices.openUrl(QUrl(new.page)),
                                         glyph=icon("update", tokens()["accent"], 18))

    def update_self(self) -> None:
        """Fetch the new version, then close: it takes this one's place and starts."""
        new = selfupdate.known(self.prefs)
        if new is None or not selfupdate.standalone():
            return
        self.app_banner.button.setEnabled(False)
        self.say(tr("Downloading Langmod Manager {version}…", version=new.version))
        folder, staging = updates.downloads(self.lib), self.lib.root / "update" / new.version

        def work():
            return selfupdate.unpack(selfupdate.download(new, folder), staging)

        def done(staged):
            try:
                selfupdate.begin(staged, Path(sys.executable).parent)
            except OSError as exc:
                failed(str(exc))
                return
            self.say(tr("Langmod Manager {version} starts in a moment.", version=new.version))
            self.close()

        def failed(text):
            self.app_banner.button.setEnabled(True)
            self.say(tr("Could not update Langmod Manager: {error}",
                        error=text.strip().splitlines()[-1].split(": ", 1)[-1]))

        self._run(work, done, failed)

    # -- editing the list ---------------------------------------------------------------------------
    def _item_changed(self, item: QListWidgetItem) -> None:
        on = item.checkState() == Qt.CheckState.Checked
        self.lib.set_enabled(item.data(ID_ROLE), on)
        self._refresh_all(item.data(ID_ROLE))

    def _rows_moved(self, *args) -> None:
        order = [self.mod_list.item(i).data(ID_ROLE) for i in range(self.mod_list.count())]
        self.lib.mods.sort(key=lambda m: order.index(m.id) if m.id in order else len(order))
        self.lib.save()
        QTimer.singleShot(0, lambda: self._refresh_all())

    def move_selected(self, step: int) -> None:
        mod = self._selected()
        if mod is None:
            return
        self.lib.move(mod.id, self.lib.mods.index(mod) + step)
        self._refresh_all(mod.id)

    def _context_menu(self, pos) -> None:
        mod = self._selected()
        if mod is None:
            return
        menu = QMenu(self)
        if not mod.personal:
            menu.addAction(tr("Update, or add a module…"), self.update_selected)
        if self.lib.readme_path(mod) is not None:
            menu.addAction(tr("Read me"), lambda: self.show_readme(mod))
        menu.addAction(tr("Open its folder"), lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.lib.mod_dir(mod)))))
        menu.addSeparator()
        menu.addAction(tr("Remove"), self.remove_selected)
        menu.exec(self.mod_list.mapToGlobal(pos))

    def add_from_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Add language mods"), str(Path.home() / "Downloads"),
                                                self._mod_filter())
        for p in paths:
            self.add_path(p)

    @staticmethod
    def _mod_filter() -> str:
        return f"{tr('Language mods')} (*.zip *.7z *.csv);;{tr('All files')} (*)"

    def add_from_folder(self) -> None:
        p = QFileDialog.getExistingDirectory(self, tr("A language mod's folder"), str(Path.home() / "Downloads"))
        if p:
            self.add_path(p)

    def add_path(self, path: str, into: str | None = None) -> None:
        try:
            res = self.lib.add(path, into=into)
        except LibraryError as exc:
            QMessageBox.warning(self, tr("Could not add that"), str(exc))
            return
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not add that"), f"{type(exc).__name__}: {exc}")
            return
        said = {"added": tr("Added {mod}.", mod=res.mod.label), "updated": tr("Updated {mod}.", mod=res.mod.label),
                "module": tr("Added a module to {mod}.", mod=res.mod.label)}[res.action]
        followed = updates.auto_follow(self.lib, res.mod) if res.action == "added" else None
        self.say(sentences(said, *[n + ("" if n.endswith((".", "。")) else ".") for n in res.notes],
                           tr("It follows {page} for new versions.", page=followed.label) if followed else ""))
        self.view_switch.set_current("details")
        self._refresh_all(res.mod.id)
        if followed is not None:
            self.check_updates(mods=[res.mod], auto=True)

    def update_selected(self) -> None:
        mod = self._selected()
        if mod is None or mod.personal:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("A newer version of {mod}, or an optional module", mod=mod.name), str(Path.home() / "Downloads"),
            self._mod_filter())
        if path:
            self.add_path(path, into=mod.id)

    def remove_selected(self) -> None:
        mod = self._selected()
        if mod is None:
            return
        # No asking first: it can be undone, straight away from the status bar, or for a month
        # with "langmod unremove".
        token = self.lib.remove(mod.id)
        hand = self.hand
        if hand is not None and hand.mod is not None and hand.mod.id == mod.id and self.install is not None:
            inst.decline(self.lib, self.install, hand)          # or the next Apply would take it in again
            hand.mod, hand.taken, hand.declined = None, False, True
        self._refresh_all()
        if token:
            self.say_with_undo(tr("Removed {mod}. Its files leave the game at the next Apply.", mod=mod_label(mod)),
                               f"unremove:{token}")

    def edit_personal(self) -> None:
        # The file for the game's language: a column for another one would change nothing in the game.
        name = strings.ensure_own_file(self.lib, self.language())
        mod = self.lib.personal()
        self._refresh_all(mod.id)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.lib.file_path(mod, name))))
        self.say(tr("Your file is open in its editor. Save it, then Apply."))

    # -- a mod installed by hand -----------------------------------------------------------------------
    def _keep_hand_install(self) -> Mod | None:
        """A mod installed by hand in the game's lang folder joins the list the first time it is seen, at the
        top, so it keeps working and everything else goes on top of it. Returns it if it joined just now."""
        if self.install is None:
            return None
        self.hand = inst.keep_hand_install(self.lib, self.install)
        if self.hand is None or not self.hand.taken:
            return None
        mod = self.hand.mod
        followed = updates.auto_follow(self.lib, mod)
        self.say_with_undo(sentences(
            tr("{mod} was in the game's lang folder, put there by hand. It is one of your mods now, first in the "
               "order, so anything you add goes on top of it.", mod=mod.name),
            tr("It follows {page} for new versions.", page=followed.label) if followed else ""), f"untake:{mod.id}")
        return mod

    def _leave_out_hand_install(self, mod_id: str) -> None:
        """Undo taking in a hand install: out of the list, and not taken in again."""
        hand = self.hand
        if hand is None or self.install is None or hand.mod is None or hand.mod.id != mod_id:
            return
        self.lib.remove(mod_id)
        inst.decline(self.lib, self.install, hand)
        hand.mod, hand.taken, hand.declined = None, False, True
        self._refresh_all()
        self.say(tr("{mod} stays out of your mods. Apply moves its files in the lang folder to a backup, which "
                    "Restore game puts back.", mod=hand.name))

    def show_readme(self, mod: Mod) -> None:
        """The mod's read me, in a window of its own beside this one."""
        from .readme_dialog import ReadmeDialog
        path = self.lib.readme_path(mod)
        if path is None:
            return
        try:
            self.readme_dialog = ReadmeDialog(mod_label(mod), path, self)
        except OSError as exc:
            self.say(str(exc))
            return
        self.readme_dialog.show()

    def import_hand_install(self) -> None:
        if self.install is None:
            return
        inst.decline(self.lib, self.install, None)
        mod = self._keep_hand_install()
        self.view_switch.set_current("details")
        self._refresh_all(mod.id if mod else None)

    def use_hand_copy(self) -> None:
        """The copy of a mod in the lang folder, rather than the one in the library, as its newer version."""
        hand = self.hand
        if hand is None or hand.mod is None or self.install is None:
            return
        try:
            res = self.lib.add_package(hand.package, into=hand.mod.id, how="update", modules_on=True)
        except LibraryError as exc:
            QMessageBox.warning(self, tr("Could not add that"), str(exc))
            return
        self.hand = inst.hand_install(self.lib, self.install)
        self._refresh_all(res.mod.id)
        self.say(tr("{mod} is now the copy from the lang folder.", mod=res.mod.name))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.add_path(url.toLocalFile())

    # -- apply, play, restore ---------------------------------------------------------------------------
    def apply(self, auto: bool = False) -> bool:
        if self.install is None or self.game is None:
            return False
        if game_running():
            if not auto:
                QMessageBox.information(self, tr("Close War Thunder first"),
                                        tr("The game reads its language files when it starts. Close it, apply, "
                                           "then start it again."))
            return False
        st = inst.status(self.install)
        if self._keep_hand_install() is not None:
            self._refresh_all(self.hand.mod.id)
        # A mod installed by hand that is one of the library's goes on working: only the rest is worth a question.
        kept = self.hand if self.hand is not None and self.hand.mod is not None else None
        lost = [n for n in st["stray"] if kept is None or not kept.holds(n)]
        if lost and self.prefs.get("confirm_move") and not auto:
            text = ntr(len(lost), "{n} file in the game's lang folder was not written by this manager. It will "
                                  "be moved to a backup (Restore game puts it back).",
                       "{n} files in the game's lang folder were not written by this manager. They will be moved to "
                       "a backup (Restore game puts them back).")
            if QMessageBox.question(self, tr("Apply"), text + "\n\n" + tr("Go ahead?")) \
                    != QMessageBox.StandardButton.Yes:
                return False

        def fail(title: str, text: str) -> bool:
            if auto:
                self.say(f"{title}: {text.splitlines()[0]}")
            else:
                QMessageBox.warning(self, title, text)
            return False

        try:
            # The game may have updated while the window was open; if not, what is read stays good.
            if self.game.stamp != lang_stamp(self.install.root):
                self.game = GameLang(self.install.root, self.lib.root / "cache")
            plan = build_plan(self.lib, self.game)
        except Exception as exc:
            return fail(tr("Could not read the game or your mods"), f"{type(exc).__name__}: {exc}")
        try:
            res = inst.apply(self.lib, self.install, self.game, plan, switch_on=self.prefs.get("switch_on"),
                             keep_backups=self.prefs.get("keep_backups"))
        except OSError as exc:
            return fail(tr("Could not write to the game folder"),
                        f"{exc}\n\n" + tr("If the game is installed under Program Files, the folder may need you to "
                                           "run the manager as administrator once."))
        except Exception as exc:
            return fail(tr("Could not apply"), f"{type(exc).__name__}: {exc}")
        if res.changed == res.written:
            what = ntr(res.written, "{n} file for game {version}", "{n} files for game {version}",
                       version=plan.game_version)
        else:
            what = ntr(res.written, "{n} file for game {version} ({changed} changed)",
                       "{n} files for game {version} ({changed} changed)", version=plan.game_version,
                       changed=num(res.changed))
        bits = [tr("Applied by itself: {what}.", what=what) if auto else tr("Applied {what}.", what=what)]
        if res.switched_on:
            bits.append(tr("Custom localization is now on."))
        if res.taken_back:
            bits.append(tr("Took back your edits: {names}.", names=join(res.taken_back)))
        if res.backed_up:
            bits.append(ntr(len(res.backed_up), "Moved {n} file to a backup.", "Moved {n} files to a backup."))
        bits += res.notes
        self.hand = None                          # the lang folder is the manager's now
        self._refresh_all()
        self.say(sentences(*bits))
        return True

    def play(self) -> None:
        if self.install is None or self.game is None:
            return
        if game_running():
            self.say(tr("War Thunder is already running."))
            return
        self._updates_first(self._start_playing)

    def _updates_first(self, then) -> None:
        """With updates set to install by themselves: fetch what is new, then go on. Offline, just go on."""
        lib = self.lib
        if self.prefs.get("update_install") != "auto" or self._busy_updating:
            then()
            return
        need_check = updates.due(lib)
        waiting = [m for m in lib.mods if updates.state(m) == "update"]
        if not need_check and not waiting:
            then()
            return
        # Check (if it is time), install what is new, then go on: each step hands on to the
        # next, and a failure anywhere still ends in starting the game.
        if need_check:
            self.check_updates(auto=True, then=then)
        else:
            self.install_updates(waiting, auto=True, then=then)

    def _start_playing(self) -> None:
        if self.install is None or self.game is None:
            return
        st = inst.status(self.install)
        if st["stale"] or st["edited"] or (self.plan and inst.pending(self.lib, self.plan, st["manifest"])):
            if not self.apply():
                return
        try:
            start_game(self.install)
        except OSError as exc:
            QMessageBox.warning(self, tr("Could not start the game"), str(exc))
            return
        self.say(tr("Starting War Thunder with your mods…"))

    def restore(self) -> None:
        if self.install is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle(tr("Restore game"))
        box.setText(tr("Take the manager's files out of the game's lang folder?"))
        first = self.lib.settings.get("first_backup", {}).get(str(self.install.root))
        box.setInformativeText(
            tr("Put back what was there first puts back the files that were in the lang folder before the first "
               "Apply.") if first else tr("Custom localization is switched off again, so the game uses its own text."))
        back = (box.addButton(tr("Put back what was there first"), QMessageBox.ButtonRole.AcceptRole)
                if first else None)
        clean = box.addButton(tr("Remove everything"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or clicked not in (back, clean):
            return
        try:
            res = inst.restore(self.lib, self.install, bring_back=clicked is back)
        except OSError as exc:
            QMessageBox.warning(self, tr("Could not write to the game folder"),
                                f"{exc}\n\n" + tr("If the game is installed under Program Files, the folder may need "
                                                   "you to run the manager as administrator once."))
            self._refresh_all()
            return
        bits = [ntr(res.removed, "Removed {n} file.", "Removed {n} files.")]
        if res.restored:
            bits.append(ntr(len(res.restored), "Put back {n}.", "Put back {n}."))
        if res.switched_off:
            bits.append(tr("Custom localization is off."))
        self._refresh_all()
        self.say(sentences(*bits, *res.notes))

    def closeEvent(self, event) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.close()            # it saves what it was still waiting to
        self._keep_place()
        self._generation += 1
        self.backdrop.set_paused(True)
        self.pool.waitForDone(3000)
        super().closeEvent(event)
