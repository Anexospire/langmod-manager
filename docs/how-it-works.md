# How Langmod Manager works

The [read me](../README.md) says what it does. This page is for anyone who wants
to know exactly how: what goes into the game folder, how mods are merged and
updated, the command line, and building it from source.

## The game folder, exactly

With `testLocalization:b=yes` in `config.blk`, War Thunder reads
`lang/localization.blk` from disk: the list of language files to load, each
one looked for in the `lang` folder first and in the game's own archive
(`lang.vromfs.bin`) after. A later file wins over an earlier one.

**Apply** writes into `<game>/lang/`:

- each enabled mod's files, under their own names (`modid__name.csv` when two
  mods ship a file of the same name), switched-on modules included;
- `localization.blk`, built from **the game's own current list**, then each
  mod's files in the order that mod's own list gives them, mods in the order of
  your list;
- `langmod-manager.json`, which records what it wrote and for which game
  version.

It never changes the game's own files, and never writes a file under a game
table's name, so a game update cannot undo anything: the list is simply rebuilt
from the game's new one. Apply also switches custom localization on in
`config.blk`, changing only that one line, after backing the file up.

Before writing, anything in `lang` that the manager did not write goes to
`%APPDATA%\Langmod Manager\backups\`, folders of language files included; a mod
installed by hand has become one of your mods first, so it goes on working.
Exact copies of the game's own current tables are deleted instead, since the
game still has them. A file the manager wrote that you edited in the game
folder is taken back into its mod, so the edit survives. **Restore game**
takes the manager's files out and puts back what `lang` held before the first
Apply.

## Merging mods

- **The game's list is the base.** A mod's own list is frozen at the game
  version its author had, so game files added since are loaded anyway, and
  files the game has dropped are skipped. A game file whose line a mod's list
  comments out is left out on purpose (IFN1 does this to show only its own
  loading tips), while that mod is on.
- **Full copies of game tables** (an old `units.csv` in a mod) would hide
  every string the game added since. They are cut down to the rows the mod
  actually changed, and loaded under another name.
- **Event tables.** The game loads its event tables (decals, skins, titles,
  trophies, event items) after everything else, so they win over every mod.
  IFN1 and WTHLM move them ahead of their own files in their lists. The manager
  does the same for any table in which a mod, a pick or your own text changes
  a string, once, ahead of all the mods and in the game's order, so every mod's
  decal names show. The rest stay where the game has them.
- **Order.** Where two mods change the same string, the one lower in your list
  wins. The Conflicts tab lists every such string.

## Mods, updates and modules

**Adding** takes a zip or 7z, a folder or a single `.csv`, as it downloaded.
A newer version of a mod you have is recognised as an update (by its files'
shared prefix, or its name), and files you edited are kept. A mod's read me, if
its download has one, is kept too, and opens from its details.

**Optional modules** are the extras an author publishes: IFN1's on Nexus Mods,
WTHLM's `Packages`. One added on its own is recognised and merged into its mod.
Those in the mod's own download arrive switched off, since several are
alternatives to each other. Each loads where the mod's own list puts it, and
keeps its switch through updates and profiles.

**A mod installed by hand**, found in `lang` before the manager ever applied
there, becomes one of your mods the first time the manager sees it: first in
the order, so everything else goes on top of it, with the modules it had in
folders of its own switched on. Undo, or **Leave it out**, keeps it out for
good.

**Updates.** A mod can follow the page it is published on:

| Where | Checking | Downloading |
|---|---|---|
| WT Live | no account | no account |
| GitHub | no account | no account |
| Nexus Mods | your own API key | Premium accounts only |

IFN1, the Localization Overhaul Project and WTHLM are recognised by
themselves; any other mod follows the link you paste. Settings → Updates
chooses when to look and whether to install by itself; with that on, **Play**
fetches updates before the game starts. Nexus allows a personal API key only
in a player's own tools, so the downloadable build leaves Nexus out; run from
source, it is there.

## Strings, picks and your own text

The Strings tab (Ctrl+F) searches every string the game loads and every string
your mods set: its ID, the game's text and each mod's. Pick a string to see
every source of its text:

- **Show this** makes that source's text the one that shows, whatever the
  order. It is copied into `langmod_picks.csv`, which loads after every mod, and
  afresh on every Apply, so a pick follows its mod's updates.
- **Use my text** puts text of your own in *My changes*, which loads last and
  wins over everything.

## Profiles

A profile is a named set: the order of your mods, which ones and which modules
are on, and your picks. Switch from the Profiles button or with Ctrl+1 to
Ctrl+9. **Export** writes one to a `.langmod-profile` file for a friend;
**Import** matches the mods they have, fetches the ones it can, and switches to
it.

## Starting from Steam

Settings → Shortcuts has a line to paste into War Thunder's launch options in
Steam (right-click the game, Properties, General):

```
"C:\...\Langmod Manager.exe" launch %command%
```

Steam then runs the manager with the game's own command in place of
`%command%`: the manager brings your mods up to date, runs the game and waits,
so Steam sees the game running as long as before. Whatever goes wrong with the
mods, the game still starts. To stop, empty the launch options again, before
deleting the manager, or Steam cannot start the game.

## Updating Langmod Manager

It looks for new versions of itself on this repository's releases once a day
(Settings → Updates). **Update and restart** downloads the new one, checks it
against the size and SHA-256 GitHub gives, closes, and lets the new copy take
its place in the same folder, keeping any file you put beside the program. If
that cannot be done (another copy still running, a folder that needs
administrator rights), the old one goes back and starts again. Releases are
tagged with their version (`v1.0.0`); GitHub pre-releases (early-access
builds) are offered only to early-access builds.

## Languages

The window, the tour and Settings are in English, German, French, Polish,
Russian and Simplified Chinese, following Windows' language unless Settings →
Look → Language says otherwise. Where the game has a word for something, the
translation uses the game's own. The mods themselves are not translated, and a
mod with no column for the game's language is flagged. The command line and the
bug report stay in English.

## The command line

`Langmod Manager.exe <command>` from a command prompt, or `run.bat <command>`
from the source:

```
launch               apply if needed, then start the game
launch <command...>  apply if needed, then run the game's own command (for Steam)
list                 mods in load order
add <zip|7z|folder>  add a mod, an update, or a module
module <id> [<module> on|off]  a mod's optional modules, or switch one
readme <id>          a mod's read me
plan --show-list     what Apply would write, changing nothing
check                what each mod changes, and conflicts
apply | restore
remove <id>          take a mod out (kept a month, in case); unremove brings it back
find <text>          search every string
pick <key> game      show the game's own text; a mod id, or --clear
get [ifn1|lop|wthlm] fetch a well-known mod
updates | update     look for and install new versions of mods
profile ...          list, save, switch, delete, export, import
upgrade [--check]    a new version of Langmod Manager itself
report               a bug report, to paste into a message
shortcut create [--kind manager|play] [--where desktop|programs]
```

`--game <folder>` picks the install (found on its own otherwise).

## Building and developing

`run.bat` runs it from source: the first run creates `.venv` and installs
PySide6. Python 3.14 is needed, for `compression.zstd`. The interface is Qt
Widgets through PySide6 (QtCore, QtGui, QtWidgets, QtSvg), with no QML.

```
.venv\Scripts\python -m unittest discover -s tests
python tools\release.py
```

The tests work on a fake game built on the fly, run the window offscreen in a
temporary home, and never touch a real game folder, the internet, the real
desktop or Steam's settings. `tools/i18n.py` checks the translation catalogs in
`langmod/resources/locale`.

`tools/release.py` builds `dist\Langmod-Manager-<version>-windows.zip` with
PyInstaller (`langmod.spec`), then checks the build before zipping it: no
player data or path from the building machine in it, every DLL it needs
present, the command line and the window starting without errors, and the
licence files beside the program.

Layout: `langmod/core` has no Qt in it (`vromf` and `blk` read the game's
archive, `langcsv` the tables, `library` the mods, `plan` the merge,
`analysis` the conflicts and the search, `install` the game folder,
`selfupdate` new versions of the manager); `langmod/ui` is the window;
`langmod/cli.py` is the command line.

## Not done yet

- Mods in languages other than English are flagged, not translated.
- `.rar` packages, and `.7z` ones packed with PPMd or a password: extract them
  first.
- The translations were written with care, using the game's own words where it
  has them, but no native speaker has read them over yet.
