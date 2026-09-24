"""``python -m langmod`` opens the window; ``python -m langmod <command>`` is the command line."""
from __future__ import annotations

import sys


def _borrow_console() -> None:
    """The standalone build has no console of its own. Started from a command prompt, it
    prints into that prompt's; started from a shortcut there is none, and output stays off."""
    if sys.platform != "win32" or sys.stdout is not None:
        return
    import ctypes
    if not ctypes.windll.kernel32.AttachConsole(-1):          # ATTACH_PARENT_PROCESS
        return
    try:
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        print()                                               # off the prompt the shell already drew
    except OSError:
        sys.stdout = sys.stderr = None


def main() -> int:
    if len(sys.argv) > 1:
        _borrow_console()
        from .cli import main as cli_main
        return cli_main(sys.argv[1:])
    from .ui.app import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
