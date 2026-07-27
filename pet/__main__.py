"""Entry point:  python -m pet   (or the packaged WindowPet.exe)"""

from __future__ import annotations

import os
import sys
import traceback


def _crash_log_path():
    from .config import settings_path

    return settings_path().parent / "crash.log"


def _report_crash(exc: BaseException) -> None:
    """Write the full traceback somewhere the user can actually find it.

    The exe is built without a console, so a startup failure shows only
    PyInstaller's dialog, which truncates the message. A log file gives the whole
    thing - which is how a numpy import failure got diagnosed.
    """
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    header = (
        f"Window Pet crashed\n"
        f"python  : {sys.version}\n"
        f"frozen  : {getattr(sys, 'frozen', False)}\n"
        f"exe     : {sys.executable}\n"
        f"bundle  : {getattr(sys, '_MEIPASS', '-')}\n\n"
    )
    path = None
    try:
        path = _crash_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header + detail, encoding="utf-8")
    except Exception:
        path = None

    sys.stderr.write(header + detail)
    try:  # a dialog, if Qt got far enough to be usable
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        where = f"\n\nFull details written to:\n{path}" if path else ""
        QMessageBox.critical(
            None, "Window Pet failed to start",
            f"{type(exc).__name__}: {exc}{where}",
        )
        del app
    except Exception:
        pass


def main() -> int:
    # The world model is in physical pixels, because that is what the Win32 window
    # and monitor rects are in. Turn Qt's own DPI scaling off so its geometry means
    # the same thing, otherwise he lands next to ledges instead of on them on any
    # display that is not at 100%.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

    try:
        from .qtapp import run

        return run(sys.argv)
    except BaseException as exc:  # noqa: BLE001 - last resort, then re-raised
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        _report_crash(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
