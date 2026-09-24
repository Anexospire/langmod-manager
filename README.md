# Langmod Manager

Allows you to Run several War Thunder language mods at once and keep them working through game updates.

**[Download the latest version](https://github.com/Anexospire/langmod-manager/releases/latest)**
· Windows · free and open source

## Reason

Usually you would have to manually insert the Lang mod into a folder, and update it manually, the
Lang Manager is intended to do that for you. It is created to help, and not for me to profit off
of anybody.

## Getting started

1. Download `Langmod-Manager-<version>-windows.zip` from
   [Releases](https://github.com/Anexospire/langmod-manager/releases) and unzip
   it anywhere, but not into the War Thunder folder.
2. Open `Langmod Manager.exe`. There is nothing to install. If Windows says it
   "protected your PC", choose **More info**, then **Run anyway**: the program
   is not signed with a paid certificate.
basically that's it.

## What it does

- **keep multiple mods at once present**
- **persist thru Game Updates**
- **Keeps them up-to-date**
- **Allows the Usage of Optional modules**
- **Search any name (CTRL + F)**
- **Profiles** for different sets of mods
- **Works with Steam**
- **Restore game** takes everything out again.
- **and six languages:** English, Deutsch,
  Français, Polski, Русский and 简体中文.

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

## Mods work best when they:

- ship a `localization.blk` listing their own files in the order they load.
  The manager takes the game's entries from the game itself; to leave one of
  the game's files out on purpose, comment its line out;
- give their files a shared prefix (`IFN1_`), which is how updates and modules
  are recognised;
- put optional extras in folders of their own, named in the list as
  `%lang/<folder>/<file>.csv`, or publish them as separate downloads: either
  way they become switches;
- ship changed strings only, not full copies of the game's tables.

## Misc

[How it works](docs/how-it-works.md): exactly what it writes into the game
folder, how updates, picks and profiles work, the command line, and building it
from source.

## Licence

Free software under the [MIT licence](LICENSE). The program is built with Qt
and PySide6, used under the GNU LGPL version 3.
this is fully open source, you can do what you wish with it.
Also yes, this has been co-made with Claude to save time.
