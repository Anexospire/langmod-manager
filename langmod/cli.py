"""Command-line use: ``python -m langmod <command>``. Without a command the window opens."""
from __future__ import annotations

import argparse
import subprocess
import sys
import traceback
from pathlib import Path

from . import __version__
from .core import install as inst
from .core import catalog, profiles, report, selfupdate, shortcuts, sources, strings, updates
from .core.analysis import analyze
from .core.sources import SourceError
from .core.prefs import Prefs
from .core.shortcuts import KINDS, WHERE, ShortcutError
from .core.game import GameInstall, GameLang, find_installs, game_running, install_at, start_game
from .core.library import Library, LibraryError
from .core.plan import GAME, PICKS_ID, build_plan
from .core.profiles import ProfileError


def pick_install(lib: Library, path: str | None) -> GameInstall:
    if path:
        found = install_at(path)
        if found is None:
            raise SystemExit(f"{path} is not a War Thunder folder (no lang.vromfs.bin)")
        return found
    saved = lib.settings.get("game")
    if saved and install_at(saved):
        return install_at(saved)
    installs = find_installs()
    if not installs:
        raise SystemExit("no War Thunder install found; pass --game <folder>")
    live = [i for i in installs if i.channel == "live"]
    return (live or installs)[0]


def _mods(lib: Library) -> None:
    if not lib.mods:
        print("No mods yet. Add one with: langmod add <zip, 7z or folder>")
        return
    for i, m in enumerate(lib.mods, 1):
        mark = "x" if m.enabled else " "
        extra = f"  +{len(m.modules)} module(s), {len(m.modules) - len(m.off)} on" if m.modules else ""
        if updates.state(m) == "update":
            extra += "  UPDATE AVAILABLE"
        edited = sum(1 for f in m.files.values() if f.edited and not m.personal)
        if edited:
            extra += f"  {edited} edited by you"
        print(f"{i:>2}. [{mark}] {m.id:<24} {m.label}  ({len(m.csv_names)} files){extra}")


def _who(lib: Library, source: str) -> str:
    """A source of text as a person reads it: the game, a mod's name, or the player's picks."""
    mod = lib.get(source)
    return "the game" if source == GAME else "your picks" if source == PICKS_ID else mod.name if mod else source


def _plan_report(plan, lib: Library) -> None:
    if plan.regional_moved:
        print("\nthe game's event tables moved ahead of the mods, so their changes show: "
              + ", ".join(ref.split("/", 1)[1] for ref in plan.regional_moved))
    for mod_id, r in plan.reports.items():
        mod = lib.get(mod_id)
        print(f"\n{mod.label if mod else _who(lib, mod_id)}: loads {len(r.loads)} file(s)")
        if r.regional:
            print(f"  the game's event tables moved ahead of the mods for it: {', '.join(r.regional)}")
        if r.left_out:
            print(f"  leaves out the game's {', '.join(r.left_out)} on purpose (commented out in its list), "
                  "so the manager does too")
        if r.stale_list:
            print(f"  its own list misses {len(r.stale_list)} of the game's files "
                  f"(their strings would show as IDs): {', '.join(r.stale_list)}")
        if r.gone:
            print(f"  its list names files the game no longer has: {', '.join(r.gone)}")
        if r.not_shipped:
            print(f"  {len(r.not_shipped)} optional file(s) in its list are not installed")
        if r.unlisted:
            print(f"  not loaded (not in its list): {', '.join(r.unlisted)}")
        for name, kept, total in r.replaced:
            print(f"  full copy of {name}: {kept} of {total} rows differ from the game, only those load")
        for src, dest in r.renamed:
            print(f"  {src} is placed as {dest} (name taken)")
        for n in r.notes:
            print(f"  {n}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")      # a console that cannot show a character shows ? instead
        except (AttributeError, ValueError):
            pass
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_finish-update"] and len(argv) == 3:
        # The new copy of the program, started by the old one to take its place once it has closed.
        return selfupdate.finish(int(argv[1]), Path(argv[2]))
    ap = argparse.ArgumentParser(prog="langmod", description="Load several War Thunder language mods at once.")
    ap.add_argument("--game", help="the War Thunder folder (found on its own if left out)")
    ap.add_argument("--home", help="where the manager keeps its mods (for testing)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="the game, its version and what is applied")
    sub.add_parser("list", help="the mods, in load order")
    a = sub.add_parser("add", help="add a mod, a newer version of one, or an optional module")
    a.add_argument("path")
    a.add_argument("--into", help="mod id to add to")
    a.add_argument("--as", dest="how", choices=["update", "module"])
    for name in ("remove", "enable", "disable"):
        sub.add_parser(name).add_argument("id")
    sub.add_parser("readme", help="print a mod's read me, if it came with one").add_argument("id")
    mo = sub.add_parser("module", help="a mod's optional modules: list them, or switch one on or off")
    mo.add_argument("id")
    mo.add_argument("module", nargs="?", help="as the list shows it")
    mo.add_argument("state", nargs="?", choices=["on", "off"])
    mv = sub.add_parser("move", help="move a mod; later mods win")
    mv.add_argument("id")
    mv.add_argument("position", type=int)
    p = sub.add_parser("plan", help="what Apply would write, changing nothing")
    p.add_argument("--show-list", action="store_true", help="print the localization.blk it would write")
    c = sub.add_parser("check", help="what each mod changes, and conflicts between mods")
    c.add_argument("--language", default="English")
    c.add_argument("--limit", type=int, default=20)
    ap_apply = sub.add_parser("apply", help="write the mods into the game")
    ap_apply.add_argument("--no-switch", action="store_true", help="leave config.blk alone")
    la = sub.add_parser("launch", help="apply again if the game or the mods changed, then start the game "
                                       "(point a desktop shortcut at this)")
    la.add_argument("command", nargs=argparse.REMAINDER,
                    help="the game's own command line, run instead and waited for: in Steam's launch options, "
                         "'<this program> launch %%command%%'")
    r = sub.add_parser("restore", help="take the manager's files out of the game")
    r.add_argument("--keep-switch", action="store_true")
    r.add_argument("--no-bring-back", action="store_true", help="do not put back what was there before")
    sc = sub.add_parser("shortcut", help="make or remove a Windows shortcut")
    sc.add_argument("action", choices=["create", "remove"])
    sc.add_argument("--kind", choices=list(KINDS), default="manager",
                    help="manager opens this window; play applies if needed and starts the game")
    sc.add_argument("--where", choices=list(WHERE), default="desktop")
    sub.add_parser("updates", help="check the mods that follow somewhere for new versions")
    up = sub.add_parser("update", help="install new versions found by 'updates'")
    up.add_argument("id", nargs="?", help="just this mod (all of them if left out)")
    fo = sub.add_parser("follow", help="say where a mod's new versions come from")
    fo.add_argument("id")
    fo.add_argument("link", help="a WT Live post or profile, a GitHub repository"
                    + (", or a Nexus Mods page" if sources.NEXUS else ""))
    fo.add_argument("--match", default="", help="a word in the file name, to pick this mod out of an author's posts")
    sub.add_parser("unfollow").add_argument("id")
    un = sub.add_parser("unremove", help="bring back a removed mod (the last one, unless named)")
    un.add_argument("name", nargs="?", help="as 'unremove --list' shows it")
    un.add_argument("--list", action="store_true", help="the removed mods still kept")
    fd = sub.add_parser("find", help="every string whose ID or text has this in it, and who sets it")
    fd.add_argument("text")
    fd.add_argument("--language", default="")
    fd.add_argument("--limit", type=int, default=20)
    pk = sub.add_parser("pick", help="which text a string shows: 'game', a mod id, or --clear for the load order")
    pk.add_argument("key")
    pk.add_argument("source", nargs="?", default="")
    pk.add_argument("--clear", action="store_true")
    ge = sub.add_parser("get", help="fetch a well-known mod: " + ", ".join(e.key for e in catalog.CATALOG))
    ge.add_argument("name", nargs="?", help="leave out to list them")
    pr = sub.add_parser("profile", help="named sets of mods: list, save, switch, delete, export, import")
    pr.add_argument("action", choices=["list", "save", "switch", "delete", "export", "import"])
    pr.add_argument("name", nargs="?", help="the profile (for import: the file)")
    pr.add_argument("file", nargs="?", help="for export: the file to write")
    sub.add_parser("report", help="print a bug report to paste into a message")
    upg = sub.add_parser("upgrade", help="look for a new version of Langmod Manager itself, and install it")
    upg.add_argument("--check", action="store_true", help="only say whether there is one")
    args = ap.parse_args(argv)
    if args.cmd != "launch":
        args.command = []

    lib = Library(args.home)
    if lib.load_problem:
        print("warning: " + lib.load_problem, file=sys.stderr)
    try:
        return _run(args, lib)
    except (LibraryError, ShortcutError, SourceError, ProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if args.cmd == "launch" and not args.command and sys.stdout is None:
            # Started from the desktop shortcut, with no console to print to.
            _message_box("War Thunder with mods",
                         f"The mods could not be applied, so the game was not started.\n\n"
                         f"{type(exc).__name__}: {exc}\n\nOpen Langmod Manager to see more.")
            return 1
        raise


def _message_box(title: str, text: str) -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x30)


def _update_line(mod) -> str:
    src = updates.source_of(mod)
    rel = updates.latest_of(mod)
    where = src.label or f"{src.place} {src.ref}" if src else ""
    st = updates.state(mod)
    if st == "error":
        return f"{mod.name}: {where}: {mod.check_error}"
    if rel is None:
        return f"{mod.name}: {where}: not checked yet"
    what = {"update": "update available", "current": "up to date",
            "unknown": "cannot tell whether yours is the same"}.get(st, "")
    return f"{mod.name}: {where}: newest is {rel.file_name} ({rel.version or 'no version'}) - {what}"


def _up_to_date(lib: Library, prefs: Prefs, install: GameInstall) -> None:
    """What `launch` does before the game starts: fetch new versions of the mods if the player
    lets it, then apply again if the game or the mods changed."""
    _updates_then(lib, prefs)
    _keep_hand(lib, install)
    game = GameLang(install.root, lib.root / "cache")
    plan = build_plan(lib, game)
    st = inst.status(install)
    if st["stale"] or st["edited"] or inst.pending(lib, plan, st["manifest"]):
        res = inst.apply(lib, install, game, plan, switch_on=prefs.get("switch_on"),
                         keep_backups=prefs.get("keep_backups"))
        print(f"applied {len(lib.enabled())} mod(s) for game {plan.game_version} ({res.written} files)")
    else:
        print("mods are up to date")


def _upgrade(lib: Library, only_check: bool) -> int:
    if not selfupdate.available():
        print("this copy of Langmod Manager has no place to look for new versions of itself")
        return 1
    new = selfupdate.check()
    selfupdate.record(Prefs(lib), new)
    if new is None:
        print(f"Langmod Manager {__version__} is the newest")
        return 0
    print(f"Langmod Manager {new.version} is out (this is {__version__}): {new.page}")
    if only_check:
        return 0
    if not selfupdate.standalone():
        print("run from source: get the new version from the page above")
        return 0
    staged = selfupdate.unpack(selfupdate.download(new, updates.downloads(lib)), lib.root / "update" / new.version)
    selfupdate.begin(staged, Path(sys.executable).parent)
    print("the new version takes this one's place as soon as it closes, and starts")
    return 0


def _keep_hand(lib: Library, install: GameInstall) -> None:
    """A mod put in lang/ by hand, before the first Apply there, joins the library instead of being moved away."""
    hand = inst.keep_hand_install(lib, install)
    if hand is not None and hand.taken:
        found = updates.auto_follow(lib, hand.mod)
        print(f"{hand.name} was in lang/, put there by hand: it is one of your mods now, the first, so the others "
              "go on top of it" + (f"; it follows {found.label} for updates" if found else ""))


def _install_of(program: str) -> GameInstall | None:
    """The install a command line starts: the folder its program is in, or the one above."""
    here = Path(program).parent
    return install_at(here) or install_at(here.parent)


def _through(args, lib: Library, prefs: Prefs) -> int:
    """`launch %command%`, from Steam's launch options: bring the mods up to date for the install
    Steam is starting, then start it Steam's way and wait, so Steam sees the game running for as
    long as it would have. Whatever goes wrong with the mods, the game still starts."""
    command = args.command
    try:
        if not game_running():
            _up_to_date(lib, prefs, _install_of(command[0]) or pick_install(lib, args.game))
    except (Exception, SystemExit) as exc:
        report.log_error(lib.root, "Starting from Steam, the mods were not brought up to date:\n"
                         + traceback.format_exc())
        what = f"{type(exc).__name__}: {exc}"
        if sys.stdout is None:
            _message_box("War Thunder with mods", "The mods could not be brought up to date, so the game starts "
                                                  f"as it is.\n\n{what}\n\nOpen Langmod Manager to see more.")
        else:
            print(f"the mods could not be brought up to date ({what}); starting the game as it is",
                  file=sys.stderr)
    folder = Path(command[0]).parent
    try:
        return subprocess.call(command, cwd=folder if folder.is_dir() else None)
    except OSError as exc:
        what = f"{command[0]} could not be started: {exc}"
        if sys.stdout is None:
            _message_box("War Thunder with mods", f"{what}\n\nCheck War Thunder's launch options in Steam: they "
                                                  f"should end in launch %command%.")
        else:
            print(f"error: {what}", file=sys.stderr)
        return 1


def _updates_then(lib: Library, prefs: Prefs) -> None:
    """Check and, if the player allows it, install; what `launch` does before it applies."""
    if not updates.due(lib):
        return
    try:
        found = updates.check_all(lib)
    except Exception as exc:          # offline: play anyway
        print(f"could not check for mod updates: {exc}")
        return
    if not found:
        return
    if prefs.get("update_install") != "auto":
        print("updates available: " + ", ".join(m.name for m in found) + " (open the manager to install them)")
        return
    done, failed = updates.install_all(lib)
    for d in done:
        print(f"updated {d.mod.name} to {d.release.version or d.release.file_name}")
    for f in failed:
        print(f"could not update {f}")


def _get(lib: Library, name: str, source) -> str:
    """Look up, download and add a mod that follows ``source``; what was added, for printing."""
    found = updates.look(name, source, Prefs(lib).get("nexus_key"))
    if found.error:
        raise SourceError(found.error)
    if found.release is None or not found.release.downloadable:
        raise SourceError((found.release.note if found.release else "") or "its page has nothing to download")
    got = updates.take_new(lib, found.source, found.release, updates.fetch(found.release, updates.downloads(lib)))
    return f"{got.mod.label} ({got.release.file_name}), following {found.source.label or found.source.page}"


def _profile(args, lib: Library) -> int:
    if args.action == "list":
        current = profiles.active(lib)
        for i, name in enumerate(profiles.names(lib), 1):
            print(f"{i:>2}. {'*' if name == current else ' '} {name}")
        if not profiles.names(lib):
            print("No profiles yet. Save the mods as they are with: langmod profile save <name>")
        return 0
    if not args.name:
        raise ProfileError(f"profile {args.action} needs a name")
    if args.action == "save":
        print(f"saved as {profiles.save_as(lib, args.name)}; changes from now on go into it")
    elif args.action == "switch":
        profiles.switch(lib, args.name)
        print(f"switched to {args.name}; apply to put it in the game")
        _mods(lib)
    elif args.action == "delete":
        profiles.delete(lib, args.name)
        print(f"forgot {args.name}")
    elif args.action == "export":
        out = profiles.export(lib, args.name, args.file or profiles.file_name(args.name))
        print(f"wrote {out}")
    else:
        doc = profiles.read_file(args.name)
        for e in profiles.entries(lib, doc):
            if e.have is None and not e.personal and e.source is not None and e.source.kind != "nexus":
                try:
                    print("fetched " + _get(lib, e.name, e.source))
                except (SourceError, OSError, LibraryError) as exc:
                    print(f"could not fetch {e.name}: {exc}")
        name, missing = profiles.take(lib, doc)
        print(f"imported {name}, and switched to it" + (f"; not in it: {', '.join(missing)}" if missing else ""))
        _mods(lib)
    return 0


def _run(args, lib: Library) -> int:
    prefs = Prefs(lib)
    if args.cmd == "profile":
        return _profile(args, lib)
    if args.cmd == "get":
        if not args.name:
            for e in catalog.CATALOG:
                have = catalog.installed(lib, e)
                print(f"{e.key:<6} {e.name}, by {e.author} ({e.source.place})"
                      + (f"  - you have {have.label}" if have else ""))
            return 0
        entry = next((e for e in catalog.CATALOG if e.key == args.name.lower()), None)
        if entry is None:
            print(f"error: no mod called {args.name}; one of: {', '.join(e.key for e in catalog.CATALOG)}",
                  file=sys.stderr)
            return 1
        print("added " + _get(lib, entry.name, entry.source))
        return 0
    if args.cmd == "pick":
        if args.clear or not args.source:
            print(f"{args.key}: back to the load order" if strings.unpick(lib, args.key)
                  else f"{args.key} was not picked")
            return 0
        try:
            strings.pick(lib, args.key, args.source)
        except ValueError as exc:
            print(f"error: {exc}; use 'game' or one of the ids 'langmod list' shows", file=sys.stderr)
            return 1
        print(f"{args.key} shows {'the game' if args.source == GAME else args.source}'s text; apply to put it in the game")
        return 0
    if args.cmd == "follow":
        mod = lib.get(args.id)
        src = sources.parse_link(args.link)
        if mod is None or src is None:
            print("error: " + ("no such mod" if mod is None else f"that is not a {sources.places()} link"),
                  file=sys.stderr)
            return 1
        src.match = args.match or src.match
        updates.follow(lib, mod, src)
        print(f"{mod.name} now follows {src.page}")
        return 0
    if args.cmd == "unremove":
        if args.list:
            for name, label in lib.removed():
                print(f"{name}  {label}")
            return 0
        mod = lib.unremove(args.name)
        print(f"{mod.label} is back")
        _mods(lib)
        return 0
    if args.cmd == "unfollow":
        mod = lib.get(args.id)
        if mod is None:
            print(f"error: no mod {args.id!r}", file=sys.stderr)
            return 1
        updates.unfollow(lib, mod)
        return 0
    if args.cmd == "updates":
        followed = [m for m in lib.mods if m.follow]
        if not followed:
            print("No mod follows anywhere yet. Use: langmod follow <id> <link>")
            return 0
        updates.check_all(lib)
        for m in followed:
            print(_update_line(m))
        return 0
    if args.cmd == "update":
        todo = [lib.get(args.id)] if args.id else [m for m in lib.mods if updates.state(m) == "update"]
        if args.id and todo[0] is None:
            print(f"error: no mod {args.id!r}", file=sys.stderr)
            return 1
        if not todo:
            print("Nothing to update. Run 'langmod updates' to check.")
            return 0
        for mod in todo:
            if mod.follow and updates.latest_of(mod) is None:
                updates.check(lib, mod)
            done = updates.install(lib, mod)
            print(f"updated {mod.name} to {done.release.version or done.release.file_name}")
            for n in done.result.notes:
                print(f"  {n}")
        return 0
    if args.cmd == "shortcut":
        if args.action == "create":
            print(f"made {shortcuts.make(lib, args.kind, args.where)}")
        else:
            gone = shortcuts.drop(lib, args.kind, args.where)
            print(f"removed {gone}" if gone else "there was no such shortcut")
        return 0
    if args.cmd == "upgrade":
        return _upgrade(lib, args.check)
    if args.cmd == "list":
        _mods(lib)
        return 0
    if args.cmd == "add":
        res = lib.add(args.path, args.into, args.how)
        verb = {"added": "Added", "updated": "Updated", "module": "Added a module to"}[res.action]
        print(f"{verb} {res.mod.label} ({len(res.mod.csv_names)} files)")
        found = updates.auto_follow(lib, res.mod) if res.action == "added" else None
        if found:
            print(f"  it follows {found.label} for updates")
        for n in res.notes:
            print(f"  {n}")
        return 0
    if args.cmd == "readme":
        mod = lib.get(args.id)
        path = lib.readme_path(mod) if mod is not None else None
        if path is None:
            print(f"error: no mod {args.id!r}" if mod is None else f"{mod.label} came without a read me",
                  file=sys.stderr)
            return 1
        print(path.read_bytes().decode("utf-8-sig", "replace"))
        return 0
    if args.cmd == "module":
        mod = lib.get(args.id)
        if mod is None:
            print(f"error: no mod {args.id!r}", file=sys.stderr)
            return 1
        if args.module:
            if args.state is None:
                print("error: say on or off", file=sys.stderr)
                return 1
            lib.set_module(mod.id, args.module, args.state == "on")
        if not mod.modules:
            print(f"{mod.label} has no optional modules")
        for label in mod.modules:
            print(f"  [{' ' if label in mod.off else 'x'}] {label}  ({len(mod.module_files(label))} file(s))")
        return 0
    if args.cmd in ("remove", "enable", "disable", "move"):
        if lib.get(args.id) is None:
            print(f"error: no mod {args.id!r}", file=sys.stderr)
            return 1
        if args.cmd == "remove":
            lib.remove(args.id)
            print(f"removed {args.id}; bring it back with: langmod unremove")
        elif args.cmd == "move":
            lib.move(args.id, args.position - 1)
        else:
            lib.set_enabled(args.id, args.cmd == "enable")
        _mods(lib)
        return 0

    if args.cmd == "launch" and args.command:
        return _through(args, lib, prefs)
    install = pick_install(lib, args.game)
    if args.cmd == "launch":
        if game_running():
            print("War Thunder is already running.")
            return 0
        _up_to_date(lib, prefs, install)
        print("starting " + start_game(install))
        return 0
    if args.cmd == "report":
        st = inst.status(install)
        try:
            game = GameLang(install.root, lib.root / "cache")
        except Exception:
            game = None
        print(report.bug_report(lib, install, game, st), end="")
        return 0
    if args.cmd == "status":
        st = inst.status(install)
        game = GameLang(install.root)
        print(f"{install.label}: {install.root}")
        print(f"game version {game.version}, language {st['language'] or 'unknown'}")
        print(f"custom localization switch: {'on' if st['switch_on'] else 'off'}")
        m = st["manifest"]
        if m:
            print(f"applied {m.applied} for {m.game_version}: {len(m.files)} file(s)"
                  + ("  - the game has updated since, apply again" if st["stale"] else ""))
        if st["stray"]:
            print(f"{len(st['stray'])} file(s) in lang/ the manager did not write: {', '.join(st['stray'][:6])}"
                  + (" ..." if len(st["stray"]) > 6 else ""))
        return 0
    if args.cmd == "restore":
        res = inst.restore(lib, install, bring_back=not args.no_bring_back, switch_off=not args.keep_switch)
        print(f"removed {res.removed} file(s)"
              + (f", put back {len(res.restored)}" if res.restored else "")
              + (", custom localization switched off" if res.switched_off else ""))
        for n in res.notes:
            print(f"  {n}")
        return 0

    if args.cmd == "apply":
        _keep_hand(lib, install)
    elif args.cmd == "plan":
        hand = inst.hand_install(lib, install)
        if hand is not None and hand.mod is None and not hand.declined:
            print(f"{hand.name}, put in lang/ by hand, becomes one of your mods (the first) when you apply")
    game = GameLang(install.root, lib.root / "cache")
    plan = build_plan(lib, game)
    if args.cmd == "find":
        language = args.language or inst.status(install)["language"] or "English"
        an = analyze(lib, game, plan, language)
        keys, total = an.search(args.text, args.limit)
        print(f"{total} string(s) with {args.text!r} ({language})" + (f", the first {len(keys)}:" if total > len(keys) else ":"))
        names = {m.id: m.name for m in lib.mods}
        for key in keys:
            shown, _text = an.shown(key)
            print(f"\n{key}")
            for source, text in an.sources(key):
                mark = "*" if source == shown else " "
                who = "the game" if source == GAME else names.get(source, source)
                print(f"  {mark} {who}: {text}" + ("  (picked)" if an.picked.get(key) == source else ""))
        return 0
    if args.cmd == "plan":
        own = len(game.loc_table()) - len(plan.left_out)
        print(f"{install.label}: game {game.version}; {len(plan.placements)} file(s) to write, "
              f"{len(plan.loc_table) - own} entries after the game's {own}")
        _plan_report(plan, lib)
        if args.show_list:
            print("\n" + plan.localization)
        return 0
    if args.cmd == "check":
        an = analyze(lib, game, plan, args.language)
        for mod_id, s in an.stats.items():
            mod = lib.get(mod_id)
            print(f"{mod.label}: {s.changes} changed, {s.same} same as the game, {s.new} not in the game, "
                  f"{s.noise} comment rows")
            if s.missing_language:
                print(f"  has no {args.language} column (has {', '.join(s.languages)}): it changes nothing "
                      f"while the game is in {args.language}")
            for prob in s.problems[:args.limit]:
                print(f"  {prob}")
        print(f"\n{len(an.conflicts)} string(s) changed by more than one mod")
        for c in an.conflicts[:args.limit]:
            print(f"  {c.key}: " + " | ".join(f"{_who(lib, m)}: {t!r}" for m, t in c.texts)
                  + f"  -> {_who(lib, c.winner)} wins" + ("  (picked)" if c.picked == c.winner else ""))
        return 0
    if args.cmd == "apply":
        if game_running():
            print("War Thunder is running: it reads language files at start, so close it first.",
                  file=sys.stderr)
            return 1
        res = inst.apply(lib, install, game, plan, switch_on=prefs.get("switch_on") and not args.no_switch,
                         keep_backups=prefs.get("keep_backups"))
        lib.settings["game"] = str(install.root)
        lib.save()
        print(f"wrote {res.written} file(s) into {install.lang_dir}"
              + (", custom localization switched on" if res.switched_on else ""))
        if res.taken_back:
            print("took back your edits: " + ", ".join(res.taken_back))
        if res.backed_up:
            print(f"moved {len(res.backed_up)} file(s) to {res.backup}")
        for n in res.notes:
            print(f"  {n}")
        return 0
    return 1
