# Langmod Manager

Load several War Thunder language mods at once, and keep them working through
game updates.

**Download:** the newest `Langmod-Manager-<version>-windows.zip` from
[Releases](https://github.com/Anexospire/langmod-manager/releases). Unzip it
anywhere (not into the game folder) and open `Langmod Manager.exe`; there is
nothing to install, and it keeps itself up to date from there.

## The problem

A War Thunder language mod is a `lang` folder pasted into the game folder, plus
`testLocalization:b=yes` typed into `config.blk`. That goes wrong in three ways:

1. **Only one mod at a time.** The game reads a single `lang/localization.blk`,
   the list of language files to load. Every mod ships its own copy of that
   list, so the second mod you paste in overwrites the first one's.
2. **Strings show up as their IDs after a game update.** The list a mod ships
   is frozen at the game version the author had. When an update adds a
   language file, the mod's list does not name it, and everything in that file
   goes missing. A mod that ships a full copy of one of the game's own tables
   (`units.csv`, ...) does the same thing: on disk, its old copy stands in for
   the game's newer one.
3. **Updating is manual, and it loses your changes.** Each mod release means
   deleting the `lang` folder and pasting it in again, and anything you typed
   into it (IFN1's `IFN1_99_useroverwrite.csv`) has to be backed up by hand
   first.

## What the manager does

* **Add** a mod once, as it comes: a zip or 7z, a folder, or a single `.csv`.
  A newer version of a mod you have is recognised as an update: IFN1's monthly
  `lang (2).zip` updates the IFN1 you added from Nexus. Files you edited are
  kept through updates.
* **Optional modules**: the extras a mod's author publishes (IFN1's modules on
  Nexus, WTHLM's `Packages`: names in other languages, full ammunition names
  and so on). One added on its own is recognised and merged into its mod;
  those that come in the mod's own download arrive switched off. Each has a
  switch in Details, loads where the mod's own list puts it, keeps its switch
  through updates, and goes with profiles (`run.bat module <id> ...`).
* **A mod you installed by hand** is not thrown away: the first time the
  manager sees one in `lang`, it becomes one of your mods, first in the order,
  so everything else goes on top of it, with the modules it had in folders of
  their own switched on. Undo in the status bar leaves it out for good; Apply
  then moves it to a backup, which Restore game puts back.
* **Get mods**: IFN1, the Localization Overhaul Project and WTHLM, each one
  click away (Add → Get mods), fetched from where their authors publish them
  and kept up to date from there.
* **Order** the mods. Where two change the same string, the lower one in the
  list wins; the Conflicts tab lists every such string and which mod shows,
  and lets you pick another one for any of them.
* **Search every string** (the Strings tab, Ctrl+F): type `F-16` and see what
  the game says, what each mod says, and which one shows. Pick which one
  shows, whatever the order, or type text of your own.
* **Profiles**: named sets of mods, their order and your picks ("historical
  names", "IFN1 only"), switched in one go or with Ctrl+1 to Ctrl+9, and
  exported as a file for a friend.
* **Remove** one without fear: its files, your edits to them included, are
  set aside rather than deleted. Undo in the status bar puts it straight
  back where it was, and for a month `run.bat unremove` still can.
* **Apply** builds `localization.blk` from **the game's own current list**,
  then adds each mod's files in the order that mod's own list gives them. New
  game files load, and stale ones the mod still names are left out. A full
  copy of a game table, event tables included, is cut down to the rows the
  mod actually changed.
  The game's regional tables (decals, skins, titles, event items) load after
  everything in its list and win over it, which is why IFN1 and WTHLM move
  them into the list ahead of their own files. The manager does that for any
  table in which a mod, a pick or your own text gives a string other text,
  once, ahead of all the mods and in the game's order, so every mod's decal
  names show, not only those of the mods that move the tables themselves.
  The rest stay where the game has them. A mod that lists a file of its own
  among the regional tables, to load it last, has it loaded after its other
  files.
  Apply also switches custom localization on in `config.blk`, changing only
  that one line.
* **After a game update**, the window says so; apply again, or set it to
  apply by itself. Better still, start the game with **Play**, or with the
  *War Thunder with mods* shortcut: both re-apply only when the game or your
  mods changed, then start War Thunder, so you never have to think about it.
  Playing through Steam, one line in the game's launch options does the same
  (see Starting from Steam).
* **Restore game** takes everything out again and puts back what was in the
  `lang` folder before the first Apply.

## Updates from where mods are published

A mod can *follow* the page it is published on, and then its new versions
are fetched for you:

| Where | Checking | Downloading | How it is read |
|---|---|---|---|
| WT Live | no account | no account | `POST /api/posts/get/` (one post, by the number in its address) or `/api/feed/get_user/` (an author's posts); files come from `/dl/<hash>/` via WT Live's CDN |
| GitHub | no account | no account | the releases API; the newest release that is not a pre-release |
| Nexus Mods | your own API key | Premium only | the v1 API; for other accounts it opens the download page |

IFN1 (WT Live, InFerNos1's post), the Localization Overhaul Project (WT
Live, a new post per version) and WTHLM (GitHub) are recognised by
themselves; any other mod follows the link you paste. WT Live's addresses are
the ones its own site uses, not a published API, so if WT Live changes them
a check says it failed and nothing else breaks.

Whether you already have the newest is known for certain once the manager
installed it. Before that it compares version numbers, then the dates (your
files against the date the author wrote on the post), and otherwise says it
cannot tell. Settings → Updates chooses when to look (when the manager
opens, at most hourly; daily; never) and whether to install by itself. With
that on, **Play** and the *War Thunder with mods* shortcut fetch updates
before the game starts. An update goes in exactly as adding a new version
by hand does, so edited files and optional modules stay.

```
run.bat updates              check every mod that follows somewhere
run.bat update [id]          install what was found
run.bat follow <id> <link>   a WT Live post or profile, a GitHub repo, a Nexus page
```

Vortex is Nexus's own manager, so a mod you know from Vortex is a Nexus
Mods one: follow its Nexus page, or better, its WT Live or GitHub page if it
has one.

Nexus allows a personal API key only in a player's own tools; a program
handed to other people has to be registered with Nexus and sign players in
through it. So the standalone build, the copy that is handed round, leaves
Nexus out: it asks for no key and takes no Nexus links. Run from source, the
manager is your own tool and Nexus is there. `LANGMOD_NEXUS=0` or `1`
overrides either way.

## In your language

The window, its messages, the tour and Settings are in English, German,
French, Polish, Russian and Simplified Chinese. The manager starts in the
language Windows is shown in, if it is one of those, and Settings → Look →
Language changes it: the window is made again in the new language at once,
where it was, with Settings open on the same page. Qt's own words (Yes, No,
Cancel, the file and colour dialogs) follow, from Qt's catalogs for these
five, which the standalone build keeps. Chinese is set in Microsoft YaHei UI.

Wherever the game has a word for something, the translation uses the game's
own, read from its archive in each language: the switch is called what the
game's options call it (Benutzerdefinierte Lokalisation, Localisation
personnalisée, Niestandardowe tłumaczenie, Пользовательская локализация,
自定义文本), on the page the game calls Hauptmenü, Principal, Główne,
Основные or 主选项. Windows' own words are used for Windows' things (Animation
effects, the Start menu). Counted text has each language's forms (Russian and
Polish have three: 1 файл, 2 файла, 5 файлов), and numbers and dates are
written the language's way (24.222, 24 222, 17.09.2026, 2026年9月17日).
German says du, as the game does; French and Russian say vous and вы.

The mods themselves, their text and the game are not translated: they are
what they are. The command line and the bug report stay in English, the
report because it is read by whoever helps.

The code's text is English and is its own key (`tr("Apply to game")`);
`langmod/resources/locale/<code>.json` holds each language. `python
tools/i18n.py` lists what a catalog lacks, has spare, or has wrong (a
`{name}` or a piece of markup that does not match the English), and
`tests/test_i18n.py` fails on any of those, and opens every screen in every
language. `LANGMOD_LANGUAGE=de` (or `qps`, which brackets every translated
text so anything left untranslated stands out) forces one.

## Mods packed as .7z

A `.7z` adds just like a zip, whatever its name says (a 7z renamed `.zip`
opens too). The manager reads 7z itself, with nothing to install:
`langmod/core/sevenzip.py` follows 7-Zip's own description of the format and
unpacks through Python's `lzma`, `bz2`, `zlib` and `compression.zstd`. What
opens: LZMA2 (7-Zip's default, solid or not, header packed or not), LZMA,
BZip2, Deflate, Zstandard, stored files, and the BCJ and Delta filters, every
file checked against the archive's CRC. PPMd, Deflate64, BCJ2 and
password-protected archives are refused with a message saying to unpack them
with 7-Zip first; so is RAR. The tests read archives 7-Zip made
(`tests/data`) and, with 7-Zip installed, make bigger ones in every way it
packs and read them back byte for byte.

## Strings, picks and your own text

The Strings tab searches every string the game loads and every string your
mods set: its ID, the game's text and each mod's (79,000-odd strings on game
2.59, in a few milliseconds). Names come before sentences, so `F-16` lists the
F-16s before the loading tips that mention one. Pick a string and a card
shows every source of its text in load order, the one that shows marked:

* **Show this** makes that source's text the one that shows, the game's own
  or any mod's, even a mod that is switched off. This is a *pick*: that
  source's row is copied into `langmod_picks.csv`, which loads after every
  mod and before your own strings. It is copied afresh on every Apply, so a
  pick follows its mod's updates. **Back to the load order** forgets it.
* **Use my text** puts text of your own in *My changes*, which loads last and
  wins over everything, picks included. Each language has a file of its own
  (`zz_my_changes.csv` for English, `zz_my_changes_french.csv` and so on), so
  text typed for one language never blanks another.

The Conflicts tab has the same card under its list. With the search box
empty, the Strings tab lists what you picked or wrote.

```
run.bat find F-16            the same search, with every source's text
run.bat pick <key> game      show the game's own text; a mod id, or --clear
```

## Profiles

A profile is a named set: the order of the mods, which ones are on, and
your picks. The Profiles button in the Mods panel saves the mods as they are
as a new profile and switches between them; Ctrl+1 to Ctrl+9 switch too.
The profile in use keeps every change you make, so switching away and back
finds it as you left it. A mod added while another profile was in use is
off in this one until you switch it on here.

**Export** writes a profile to a `.langmod-profile` file to send to a
friend. It names each mod and the page it updates from, and carries your own
strings. **Import** matches the mods the friend has (by that page, then by
name), fetches the ones that follow WT Live or GitHub, makes your own
strings a mod of their own, and switches to the new profile. The friend's
own strings stay theirs, loading last.

```
run.bat profile list | save <name> | switch <name> | delete <name>
run.bat profile export <name> [file] | import <file>
```

## Getting mods

Add → **Get mods** lists the well-known language mods: IFN1 (InFerNos1, WT
Live), the Localization Overhaul Project (Wiggly_Armed_Man, WT Live) and
War Tinder's Historical Localization Mod (WarTinder, GitHub). It asks each
page what is newest and shows the file, its size and date, and the game
version it was made for. **Get it** downloads it, adds it switched on, and
has it follow its page, so it updates like any followed mod. A mod you
already have shows as such, with Update when a newer one is out.

```
run.bat get                  the list, and which ones you have
run.bat get ifn1             fetch one: ifn1, lop or wthlm
```

## Looks

The window has a top bar with the Live/Dev
switch and the main actions, cards with headers of one height, and buttons
whose highlight trails the cursor. It opens where it was last closed, at
that size, maximised if it was. Ten themes, from the palette button in the
top bar or Settings → Look:

| Theme | Colours | Behind the window |
|---|---|---|
| Standard front | amber on grey, dark or light (or like Windows) | nothing: the plain look |
| Astral | blue-black | the Milky Way across the sky, its glow broken by lanes of dust and pink knots of gas; stars in their colours, the bright ones with spikes, twinkling; the odd shooting star |
| Sakura | pink fading to white | a blossoming branch reaching in, over misty hills, a snow-capped peak and groves in flower, in soft light from the top left; petals tumbling on a gusting wind, turning over to show their paler backs, now and then one blurred close to the eye |
| Frost | pale winter blue | a low sun behind a range of snowy peaks, frosted spruce on the hills, deep drifts, a tall spruce heavy with snow at either side; snow in three depths, crystals turning among it |
| Ember | charcoal and red | a fire at the foot of the window: charred logs on glowing coals, flames licking up between them, their light pulsing on the smoky air; sparks swirling up and cooling, smoke drifting |
| Aurora | near black, green light | curtains of northern light rippling over mountains and a spruce shore, the faint Milky Way behind them; the peaks lit green on one side, and mirrored with the lights in a still lake that glints |
| Factory | gunmetal and hazard yellow | gear trains turning (they really mesh), lit from the skylights however they turn; two conveyors carrying crates, dust in skylight beams, grimy riveted walls, a trembling gauge, a valve that lets off steam, a stack light |
| Space travel | deep violet | a starship (the LM-01) cruising past nebulae, spiral galaxies and a banded, ringed planet; rocks and wreckage tumbling by, in front of it and behind, lit from one side however they turn; engines flickering, lights blinking, a glint running along the hull |
| Warfare | slate and olive | a city at dusk under a pall of smoke, blocks of flats with windows lit (some going on and off), houses and bare trees; on the dry grass of the hill in front a Leopard 2A6 in three-colour camouflage, idling, antennas swaying, and now and then firing along the hill (the barrel runs back, the hull rocks, smoke rolls out and dust lifts off the grass); smoke billowing up the column, ash drifting down, crows wheeling, now and then jets or a helicopter under the cloud. No flags, markings or places that could be named |
| Your picture | dark glass, with an accent taken from the picture (or one you choose) | a picture of your own, darkened a little, some or a lot, with soft edges, wandering slowly |

Your picture is copied into the manager's folder (at most 4K, as a JPEG),
so the original can move or go. It is scaled once per window size, the veil
baked in, and then only moved: a frame is drawn only when it has moved a
whole pixel, a few times a second.

The background stands still while Windows' *Animation effects* is off
(Settings, Accessibility, Visual effects), and moves again when it is
switched back on; a setting lets it move anyway. The tour then glides
nowhere either, and the theme pages turn without sliding.

The animation can be switched off, thinned out to a few, or set anywhere
from 10 to 60 frames a second, in Settings. It starts at 30. A scene takes from a third of a
millisecond (Astral) to under three (Ember, whose flames, like Aurora's curtains,
are worked out at a quarter size and 15 times a second, then smoothed up);
with the see-through cards redrawn over it, a whole frame takes 3.5 to 5.5
ms, about a tenth of one core. It stops whenever the window is minimised, and
never runs for Standard front.

The detail is in each scene's still layer, drawn once per window size (20
to 45 ms at 1220×780): the soft things in it (glows, clouds, mist, the
Milky Way's dust) are painted at a third or a quarter of the size and
smoothed up, since they have no edges to lose, and a faint grain over it
all hides the steps an 8-bit gradient shows across a big sky.

Bigger windows cost more: at 2560×1440 with 150% scaling (a 4K screen) a
frame is 20 ms. So the background keeps watching what the app costs while
it moves, and while that is over its budget it steps its frame rate down
from the one chosen (30, 24, 20, 15, 12, 10 from 30), and back up when there
is room. The budget is a fifth of one core at 30 frames, and grows with the
rate chosen: choosing 60 is choosing twice the work. Things keep
their speed; they move in fewer steps. On that 4K screen it settles at 10
frames and about 30% of a core, where it would otherwise take all of one.
While the window is being resized, the scene is stretched and only drawn
anew once the size holds still.

## The tour

The first time the window opens, a guided tour walks through it like a
game's tutorial. The window dims except for a spotlight on one control at a
time, and a card beside it says what the control is for. The card shows the
step, a row of progress segments, and how many steps are left. Skip, Back
and Next are on the card; the arrow keys, Enter and Esc work too. A notice
that is not showing gets no step, so the count is always the real one. The
**?** button at the top, F1, or Settings → Folders shows the tour again.

## Starting from Steam

Steam starts the game by itself, so after a game update there, the mods wait
until the manager is opened. A game's launch options in Steam can put a
program in front of it, and Settings → Shortcuts has the line for this copy
of the manager, with a Copy button:

```
"C:\...\Langmod Manager.exe" launch %command%
```

Pasted into War Thunder's launch options (right-click the game, Properties,
General), it has Steam run the manager with the game's own command line in
place of `%command%`. The manager brings the mods up to date for the install
that command starts (fetching new versions first, if Settings → Updates says
so), then runs it and waits, so Steam sees the game running just as long as
before. Whatever goes wrong with the mods, the game still starts: the manager
says why in a message, and keeps it in `errors.log`.

The manager reads Steam's settings (never writes them) to show whether Steam
starts the game through it, or through a copy that has since moved. To stop,
empty the launch options again, before deleting the manager too, or Steam
cannot start the game.

## Settings

Settings (the gear, or Ctrl+,) is one window with six pages, and every
change applies at once:

* **Look**: the theme, six to a page that slides to the next (arrows, the
  page dots, a sideways swipe or the arrow keys), Standard front's dark or
  light, Your picture's picture, accent and how dark it is, whether the
  background moves and whether it follows Windows' animation setting, how
  much of it there is, and how many frames a second it moves at (10 to 60,
  30 to begin with).
* **Game**: where the Live and Dev installs are, if they are not found on
  their own, and whether Apply switches custom localization on.
* **Applying**: tell you about a game update or apply by itself; whether to
  ask before moving other files out of `lang`; how many backups to keep.
* **Updates**: when to look for new versions of mods, whether to install
  them by itself and apply after, and a Nexus Mods key (running from source).
* **Shortcuts**: make or remove *Langmod Manager* and *War Thunder with mods*
  on the desktop and in the Start menu, whether their icons follow the
  theme, and the line for Steam's launch options (below).
* **Folders**: where your mods and the backups are kept, the tour, and
  **Copy a bug report**.

## Icons

`tools/make_icon.py` (needs Pillow) draws the icon for every theme: two
speech bubbles stacked, the way language mods stack on the game, with lines
of text cut out of the front one, on a tile in the theme's colours with a
little of its scene. The `-play` variant, with a play badge, is for the
shortcut that starts the game. `tools/icon_preview.png` shows them all.

### What it found in IFN1, as a check

Against game 2.59.0.13, every IFN1 release on this machine (V74.1 from Nexus,
and the June, August and September `lang.zip`) ships a list that:

* leaves out `encyclopedia_tips.csv`. IFN1's own tips file covers 141 of its
  164 strings; the other 21 real ones (`loading/aircraft/newbie/tip11` and
  friends) have no text with IFN1 installed by hand;
* still names `_legal.csv`, which the game no longer has.

IFN1's files themselves are loose, and the manager takes them as they are:
rows of one to four fields, `""` inside unquoted text, quoted text over
several lines, 400-odd comment and spacer rows (`;`, `-- Republic of China;`),
and 37 string IDs set twice inside one file. None of it changes what gets
written: mod files are copied byte for byte and only read to report on them.

## The game folder, exactly

Apply writes into `<game>/lang/`:

* each enabled mod's files, under their own names (`modid__name.csv` when two
  mods ship a file of the same name), modules switched on included;
* `localization.blk`, the merged list;
* `langmod-manager.json`, which records what it wrote and for which game
  version.

It never changes the game's own files (`lang.vromfs.bin`, the event tables'
archive), and never writes a file under a game table's name, so a game update
cannot undo anything: the list is simply rebuilt from the game's new one.

Before writing, anything in `lang/` it did not write is moved to
`%APPDATA%\Langmod Manager\backups\`, folders of language files (modules put
there by hand) included; a mod installed by hand has become one of your mods
first (see above), so it goes on working. The one exception is exact copies of
the game's own current tables, which are deleted because the game still has
them. A file it wrote that you edited in the game folder is taken back into
its mod, so the edit survives. `config.blk` is backed up before its switch is
flipped. The game must be closed: it reads the language files when it starts.

## Using it

```
run.bat                      the window
run.bat launch               apply if needed, then start the game
run.bat launch <command...>  apply if needed, then run the game's own command (for Steam)
run.bat list                 mods in load order
run.bat add <zip|7z|folder>  add a mod, an update, or a module
run.bat module <id> [<module> on|off]  a mod's optional modules, or switch one
run.bat plan --show-list     what Apply would write, changing nothing
run.bat check                what each mod changes, and conflicts
run.bat apply | restore
run.bat remove <id>          take a mod out (kept a month, in case)
run.bat unremove [--list]    bring the last removed mod back, or list them
run.bat find <text>          search every string (see Strings, above)
run.bat get [ifn1|lop|wthlm] fetch a well-known mod
run.bat profile ...          named sets of mods (see Profiles, above)
run.bat report               a bug report, to paste into a message
run.bat upgrade [--check]    a new version of Langmod Manager itself (see Updating Langmod Manager)
run.bat shortcut create [--kind manager|play] [--where desktop|programs]
```

`--game <folder>` picks the install (found on its own otherwise) and `--home
<folder>` the manager's own data folder. The first run creates `.venv` and
installs PySide6. Python 3.14 is needed for `compression.zstd`.

## For mod authors

Nothing needs to change. Mods that keep working best:

* ship a `localization.blk` that lists your files in the order they should
  load. The manager uses only your own entries, plus any `%langRegional/`
  tables you load inside `locTable`, and takes the game's entries from the
  game. A game file your list simply does not name is taken for one the game
  added since your list, and loads; to leave one of the game's files out on
  purpose, comment its line out (`//file:t="%lang/encyclopedia_tips.csv"`,
  as IFN1 does to show only its own loading tips), and the manager leaves it
  out too while your mod is on;
* give your files a shared prefix (`IFN1_`): it is how an update or a module
  is recognised as yours;
* put optional extras in folders of their own, named in your list as
  `%lang/<folder>/<file>.csv` (as WTHLM's `Packages` are), or publish them as
  separate downloads (as IFN1's modules are): either way they become switches;
* ship your changed strings only, not full copies of the game's tables.

## Updating Langmod Manager

The manager looks for new versions of itself on its GitHub releases, once a
day (Settings → Updates → Langmod Manager itself). A newer one shows at the
top of the window. In the standalone build, **Update and restart** downloads
it, checks it (its size and GitHub's SHA-256 of it, and that it is one
Langmod Manager folder with the program in it), and closes; the new copy then
takes the old one's place in the same folder, keeps any file you put beside
the program, and starts. If it cannot (another copy still running, a folder
that needs administrator rights), the old one goes back and starts again.
Mods and settings live in `%APPDATA%\Langmod Manager`, so they are never
touched. Run from source, it only says so, with a link to the page.
`run.bat upgrade [--check]` does the same from the command line.

Releases are published on this repository's GitHub releases (`REPO` in
`langmod\core\selfupdate.py`). Each is tagged with its version (`v1.0.0`) and
carries the zip `tools\release.py` makes; a GitHub pre-release (an
early-access build such as `v1.1.0rc1ea`) is offered only to early-access and
test builds.

## Making a copy to share

```
python tools\release.py
```

builds `dist\Langmod-Manager-<version>-windows.zip` (about 25 MB): the
standalone `Langmod Manager.exe`, which needs no Python, and a `Read me.txt`
for players. It needs PyInstaller in the Python that runs it (`python -m pip
install pyinstaller`); `langmod.spec` is the build itself. Before zipping, the
script makes sure the build is fit to hand round:

* **clean**: none of a player's data in it (the manager keeps mods, settings
  and backups in `%APPDATA%\Langmod Manager`, never beside the program), and
  no path from the machine that built it, in any file;
* **complete**: the spec leaves out the parts of Qt the manager never loads
  (the software OpenGL fallback, Qt Quick, QML, PDF, Qt's translations: half
  the size), every DLL the rest imports has to be there, and so do the
  plugins Qt loads by name (the window's, and JPEG, WebP and GIF for Your
  picture);
* **working**: it runs the command line, then opens the window on screen for
  a few seconds (`LANGMOD_QUIT_AFTER`), in a throwaway home; neither may leave
  an error, or anything in the build's folder;
* **licensed**: beside the program go `License.txt`, `Third-party
  notices.txt` (Qt's and PySide6's versions and where their source is, the
  projects inside Qt, Python, PyInstaller) and `licenses\` with the LGPL 3 and
  GPL 3 texts from `licenses\` here and the licence of the Python that made
  the build; the zip must have them all.

The build writes errors to `%APPDATA%\Langmod Manager\errors.log`, since it
has no console, and the window adds the ones it catches. **Copy a bug
report** (Settings → Folders, or the link after "Something went wrong" in
the status bar) puts what a helper needs on the clipboard: the versions, the
game and how it is set up, the mods in order and the end of `errors.log`,
with the home folder written as `%USERPROFILE%` so no user name goes along. Started
from a command prompt, it prints into that prompt, so the command line works
too. It is not code signed, so Windows shows a SmartScreen box on first run
("More info", then "Run anyway"); the read-me says so.

## Development

```
.venv\Scripts\python -m unittest discover -s tests
```

The code is kept in git, and every commit is also copied, by
`.git\hooks\post-commit`, into `D:\Langmod Manager Vault`: a git repository
with no working files (nothing in it can be edited by accident) and the same
history as one file, `langmod-manager.bundle`, to copy anywhere. The vault's
`Read me.txt` says how to undo a broken change, or get the whole folder back.
`.gitattributes` keeps `run.bat` in Windows line ends, which cmd.exe needs to
find its labels, and everything else in LF.

`tests/test_sevenzip.py` reads the 7z archives in `tests/data`, which 7-Zip
itself made. `tests/test_i18n.py` checks the catalogs and every screen in
each language.

`tests/test_core.py` works on a fake game it builds on the fly (a real VRFs
archive), and never writes to a real game folder. Its IFN1 tests read the
game and the IFN1 downloads when they are on this machine, and are skipped
otherwise. `tests/test_ui.py` runs the window offscreen in a temporary home,
with the desktop and Start menu pointed at temporary folders
(`LANGMOD_DESKTOP_DIR`, `LANGMOD_PROGRAMS_DIR`), so no test puts a shortcut
on the real desktop. Set `QT_QPA_FONTDIR=C:\Windows\Fonts` for readable
screenshots, `LANGMOD_SHOT_DIR` to have the UI tests save them, and
`LANGMOD_THEME=astral` (or any theme, or `dark`/`light`) to force a look.

Reading every table the game loads takes about a second, so one language's
text for every string is kept in `%APPDATA%\Langmod Manager\cache` per game
version (and per set of event tables). The window reads the game in the
background, so it is there before it is asked for: with IFN1, the first
start after a game update takes about 1.4 s to have everything analysed,
every start after that 0.4 s. The six newest are kept.

Layout: `langmod/core` has no Qt in it (`vromf` and `blk` read the game's
archive, `langcsv` the tables, `library` the mods, `plan` works out the
merge and the picks, `analysis` the conflicts and the search, `strings` picks
and your own text, `profiles`, `catalog` the mods Get mods offers, `install`
the game folder, `prefs` the settings, `report` the bug report, `shortcuts`
the `.lnk` files, `steam` the line for Steam's launch options); `langmod/ui` is the window (`themes`, `scenes`, `widgets`, `strings_view`, `settings_dialog`,
`getmods_dialog`, `picture`, `system` for Windows' animation setting);
`langmod/cli.py` is the command line.

## Not done yet

* Mods in languages other than English: a mod with no column for the game's
  language is flagged, not translated.
* `.rar` packages, and `.7z` ones packed with PPMd or a password: extract them first.
* The translations were written with care, using the game's own words where it has them, but
  no native speaker has read them over yet.

## Licence

Langmod Manager is free software under the MIT licence (`LICENSE`). The
standalone build carries Qt and PySide6, used under the GNU LGPL version 3,
and Python; `Third-party notices.txt` beside the program says what each is
and where their source is.

It is not made by, endorsed by or connected with Gaijin Entertainment, the
makers of War Thunder.
