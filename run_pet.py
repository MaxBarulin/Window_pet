"""Launcher used as the PyInstaller entry point.

`python -m pet` is the normal way to start it from a checkout; PyInstaller wants a
plain script, so this is that script.
"""

from pet.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
