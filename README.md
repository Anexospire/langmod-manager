# Langmod Manager

Run several War Thunder language mods at once (IFN1, the Localization Overhaul
Project, WTHLM or any other) and keep them working through game updates.

**[Download the latest version](https://github.com/Anexospire/langmod-manager/releases/latest)**
· Windows · free and open source

## Why

A language mod is normally a `lang` folder pasted into the game. That means:

- **one mod at a time**, because each mod's load list replaces the other's;
- **names turning into string IDs** after a game update, until the mod's
  author catches up;
- **pasting it all in again** for every mod update, losing your own edits.

Langmod Manager does the pasting for you, rebuilds the load list from the
game's own every time, and puts everything back together after each update.

## Getting started

1. Download `Langmod-Manager-<version>-windows.zip` from
   [Releases](https://github.com/Anexospire/langmod-manager/releases) and unzip
   it anywhere, but not into the War Thunder folder.
2. Open `Langmod Manager.exe`. There is nothing to install. If Windows says it
   "protected your PC", choose **More info**, then **Run anyway**: the program
   is not signed with a paid certificate.
3. A short tour shows you round. Then add your mods: **Add → Get mods**
   fetches IFN1, LOP or WTHLM in one click, or add any mod's zip, 7z or folder
   just as it downloaded.
4. Press **Play**. It puts your mods in if anything changed, then starts the
   game.

## What it does

- **Several mods at once**, in the order you choose. Where two mods change the
  same name, the lower one in the list wins, or pick the one you want in the
  Conflicts tab.
- **Survives game updates.** New strings never show as their IDs, and the
  game's own files are never changed, so an update cannot undo anything.
- **Keeps your mods up to date** from where their authors publish them (WT Live
  or GitHub), and keeps any edits you made to them.
- **Optional modules**, such as IFN1's extra modules and WTHLM's packages, each
  with an on/off switch.
- **Already installed a mod by hand?** It is picked up by itself and keeps
  working.
- **Search any name** (Ctrl+F): see what the game says, what each mod says,
  pick the one that shows, or type your own.
- **Profiles** for different sets of mods, to switch in one click or send to a
  friend.
- **Works with Steam**: one line in War Thunder's launch options (Settings →
  Shortcuts has it, ready to copy) brings your mods up to date whenever Steam
  starts the game.
- **Restore game** takes everything out again.
- Ten themes, some of them animated, and six languages: English, Deutsch,
  Français, Polski, Русский and 简体中文. It keeps itself up to date, too.

## Good to know

- Your mods, settings and backups live in `%APPDATA%\Langmod Manager`, never
  beside the program, so updating or moving the program loses nothing.
- Whatever was in your `lang` folder before goes to a backup, and **Restore
  game** puts it back.
- Close War Thunder before applying: the game reads its language files when it
  starts.
- Something wrong? Settings → Folders → **Copy a bug report** puts what a
  helper needs on the clipboard, with no personal details, ready to paste into
  an [issue](https://github.com/Anexospire/langmod-manager/issues).

## For mod authors

Nothing needs to change. Mods work best when they:

- ship a `localization.blk` listing their own files in the order they load.
  The manager takes the game's entries from the game itself; to leave one of
  the game's files out on purpose, comment its line out;
- give their files a shared prefix (`IFN1_`), which is how updates and modules
  are recognised;
- put optional extras in folders of their own, named in the list as
  `%lang/<folder>/<file>.csv`, or publish them as separate downloads: either
  way they become switches;
- ship changed strings only, not full copies of the game's tables.

## More

[How it works](docs/how-it-works.md): exactly what it writes into the game
folder, how updates, picks and profiles work, the command line, and building it
from source.

## Licence

Free software under the [MIT licence](LICENSE). The program is built with Qt
and PySide6, used under the GNU LGPL version 3. Langmod Manager is not made by,
endorsed by or connected with Gaijin Entertainment, the makers of War Thunder.
