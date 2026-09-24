"""Entry point for the standalone build (PyInstaller).

``langmod/__main__.py`` imports relative to its package, which PyInstaller
cannot follow in a file it treats as a plain script. This one imports the
package by name instead, so all of it is pulled into the build.
"""
from __future__ import annotations

import sys

from langmod.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
