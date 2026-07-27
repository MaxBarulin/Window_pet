"""Entry point:  python -m pet   (or the packaged WindowPet.exe)"""

from __future__ import annotations

import os
import sys


def main() -> int:
    # The world model is in physical pixels, because that is what the Win32 window
    # and monitor rects are in. Turn Qt's own DPI scaling off so its geometry means
    # the same thing, otherwise he lands next to ledges instead of on them on any
    # display that is not at 100%.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

    from .qtapp import run

    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
