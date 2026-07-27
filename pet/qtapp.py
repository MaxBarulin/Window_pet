"""The desktop overlay: a frameless, transparent, always-on-top window that draws
the posed rig and follows him around the screen.

Three things here exist purely to keep the motion smooth, because each was a
visible stutter:

* The desktop is read on a **background thread**. EnumWindows plus a DwmGetWindow-
  Attribute call per window takes real time, and doing it on the GUI thread a few
  times a second hitched the animation on every poll.
* The window is a **fixed size and only ever moved**, never resized. It is sized
  once for the largest pose any clip can reach. Resizing a translucent layered
  window every frame is far more expensive than moving it, and it judders.
* There is **no per-frame mask**. Click-through comes from answering WM_NCHITTEST
  with the alpha under the cursor, which costs nothing per frame. Rebuilding a
  QRegion from the alpha ten times a second forced the compositor to redo the
  window region each time.

Sub-pixel position is kept in the draw offset rather than being rounded away, so
slow movement does not crawl from one whole pixel to the next.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCursor,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

from . import config as C
from .behavior import Behavior, Pet, State
from .desktop import Snapshot, make_desktop
from .kinematics import Skeleton
from .poses import CLIPS
from .rigmath import Matrix, Pose, Rig

PAD = 8  # px of slack around the worst-case bounding box

WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1


def _qtransform(m: Matrix) -> QTransform:
    """Our affine transform -> QTransform, which is stored transposed."""
    return QTransform(
        m.a, m.d,
        m.b, m.e,
        m.c, m.f,
    )


class DesktopPoller:
    """Reads the desktop on a background thread so the GUI never waits for it."""

    def __init__(self, desktop, interval: float):
        self.desktop = desktop
        self.interval = interval
        self._lock = threading.Lock()
        self._snapshot: Snapshot = desktop.snapshot()  # first one, synchronously
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._loop, name="desktop-poller", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def refresh_now(self) -> Snapshot:
        snap = self.desktop.snapshot()
        with self._lock:
            self._snapshot = snap
        return snap

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                snap = self.desktop.snapshot()
            except Exception:
                continue  # a window vanished mid-enumeration; try again next tick
            with self._lock:
                self._snapshot = snap


class PetWindow(QWidget):
    def __init__(self, rig: Rig, settings: C.Settings):
        super().__init__(None)
        self.rig = rig
        self.settings = settings
        self.pixmaps = {
            name: QPixmap(str(rig.part_file(name))) for name in rig.parts
        }
        self.skeleton = Skeleton(rig)
        self.behavior = Behavior(Pet(), settings=settings)
        self.desktop = make_desktop()
        self.poller = DesktopPoller(self.desktop, C.DESKTOP_POLL)
        self._frame: QImage | None = None
        self._paused = False
        self._drag_trail: list[tuple[float, float, float]] = []
        self._last_t = time.perf_counter()
        self._canvas = (1, 1)
        self._anchor_local = (0.0, 0.0)

        self.setWindowFlags(self._flags())
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        if settings.click_through:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.setCursor(Qt.OpenHandCursor)
        self.setWindowTitle("Window Pet")

        self._resize_canvas()

        self.timer = QTimer(self)
        # the default coarse timer drifts by several ms on Windows, which is
        # plenty to make a 30-60 fps animation look uneven
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(1000 / max(1, settings.fps)))

    # -- setup ------------------------------------------------------------

    def _flags(self) -> Qt.WindowType:
        """WindowDoesNotAcceptFocus matters: a desktop toy must never steal focus
        from whatever the user is actually typing into."""
        flags = (
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        if self.settings.click_through:
            # WS_EX_TRANSPARENT. WA_TransparentForMouseEvents alone only stops *Qt*
            # reacting; the click still lands on this window instead of the app
            # underneath, which is not what click-through means.
            flags |= Qt.WindowTransparentForInput
        return flags

    def _resize_canvas(self) -> None:
        """Size the window once, for the largest pose any clip can strike.

        Kept horizontally symmetric about the ground point so that flipping him
        mirrors the drawing exactly instead of shifting it.
        """
        scale = self.settings.pet_height / self.rig.height()
        left = right = top = bottom = 0.0
        for clip in CLIPS.values():
            for i in range(8):
                pose = self.skeleton.resolve(clip.at(clip.duration * i / 8))
                tf = self.rig.compose(pose, (0.0, 0.0), scale, flip=False)
                # measure from the pose's own feet, which is what gets anchored
                foot = self.skeleton.ground_y(tf)
                x0, y0, x1, y1 = self.rig.bounds(tf)
                y0, y1 = y0 - foot, y1 - foot
                left, right = min(left, x0), max(right, x1)
                top, bottom = min(top, y0), max(bottom, y1)
        half = max(abs(left), abs(right)) + PAD
        w = int(round(half * 2))
        h = int(round(bottom - top)) + PAD * 2
        self._canvas = (max(1, w), max(1, h))
        self._anchor_local = (half, -top + PAD)
        self.resize(w, h)

    def place_initially(self) -> None:
        b = self.poller.latest().desktop_bounds()
        p = self.behavior.pet
        p.x = (b.x0 + b.x1) / 2
        p.y = b.y0 + 20
        p.airborne = True

    def register_self(self) -> None:
        """Keep our own overlay out of the terrain, or he would stand on himself."""
        fn = getattr(self.desktop, "exclude_hwnd", None)
        if fn is not None:
            try:
                fn(int(self.winId()))
                self.poller.refresh_now()
            except Exception:
                pass
        self.poller.start()

    # -- per-frame --------------------------------------------------------

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(now - self._last_t, 0.12)  # clamp, e.g. after a sleep/resume
        self._last_t = now
        if self._paused:
            return

        snap = self.poller.latest()
        if not self.settings.walk_on_windows:
            snap = Snapshot(snap.monitors, [])

        if self.behavior.pet.state is State.DRAGGED:
            gp = QCursor.pos()
            self.behavior.drag_to(gp.x(), gp.y())
            self._drag_trail.append((now, gp.x(), gp.y()))
            self._drag_trail = self._drag_trail[-6:]
        else:
            self.behavior.update(snap, dt)

        self._render()

    def _current_pose(self) -> Pose:
        p = self.behavior.pet
        clip = CLIPS.get(p.clip, CLIPS["idle"])
        return self.skeleton.resolve(clip.at(p.clip_time))

    def _render(self) -> None:
        p = self.behavior.pet
        pose = self._current_pose()
        scale = self.settings.pet_height / self.rig.height()
        flip = p.facing < 0

        # Where he actually touches down depends on the pose now that the feet are
        # driven independently, so ask the pose rather than using a fixed point.
        probe = self.rig.compose(pose, (0.0, 0.0), scale, flip)
        if p.anchor_kind == "hips":
            # sitting: hips rest on the ledge and the legs hang below it
            contact = probe["pelvis"].apply(*self.rig.pivot("pelvis"))[1]
        else:
            contact = self.skeleton.ground_y(probe)
        anchor_y = p.y - contact

        ax, ay = self._anchor_local
        # keep the fraction of a pixel in the drawing, not in the window position,
        # so slow movement glides instead of crawling pixel to pixel
        left, top = p.x - ax, anchor_y - ay
        win_x, win_y = int(left // 1), int(top // 1)
        ox, oy = ax + (left - win_x), ay + (top - win_y)

        w, h = self._canvas
        if self.width() != w or self.height() != h:
            self.resize(w, h)
        self.move(win_x, win_y)

        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        tf0 = self.rig.compose(pose, (ox, oy), scale, flip)
        for name in self.rig.draw_order:
            px, py = self.rig.parts[name]["offset"]
            painter.setTransform(
                QTransform.fromTranslate(px, py) * _qtransform(tf0[name])
            )
            painter.drawPixmap(0, 0, self.pixmaps[name])
        painter.end()

        self._frame = img
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._frame is None:
            return
        painter = QPainter(self)
        painter.drawImage(0, 0, self._frame)

    # -- interaction ------------------------------------------------------

    def _opaque_at(self, pos: QPoint) -> bool:
        if self._frame is None:
            return False
        if not (0 <= pos.x() < self._frame.width() and 0 <= pos.y() < self._frame.height()):
            return False
        return self._frame.pixelColor(pos).alpha() > 24

    def nativeEvent(self, event_type, message):  # noqa: N802
        """Per-pixel click-through, for free.

        Windows asks WM_NCHITTEST what is under the cursor; answering
        HTTRANSPARENT for a see-through pixel makes the click fall to the window
        underneath. This replaces masking the window every few frames.
        """
        if sys.platform == "win32" and self._frame is not None:
            try:
                import ctypes.wintypes

                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == WM_NCHITTEST:
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    if not self._opaque_at(self.mapFromGlobal(QPoint(x, y))):
                        return True, HTTRANSPARENT
            except Exception:
                pass  # fall through to Qt's own handling rather than break input
        return super().nativeEvent(event_type, message)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or not self._opaque_at(event.position().toPoint()):
            event.ignore()
            return
        gp = event.globalPosition().toPoint()
        self._drag_trail = [(time.perf_counter(), gp.x(), gp.y())]
        self.behavior.start_drag()
        self.setCursor(Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.behavior.pet.state is not State.DRAGGED:
            return
        vx = vy = 0.0
        if len(self._drag_trail) >= 2:
            t0, x0, y0 = self._drag_trail[0]
            t1, x1, y1 = self._drag_trail[-1]
            span = max(t1 - t0, 1e-3)
            vx, vy = (x1 - x0) / span, (y1 - y0) / span
        self.behavior.release_drag(vx, vy)
        self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._opaque_at(event.position().toPoint()):
            self.behavior.dance_now()

    def _show_menu(self, pos: QPoint) -> None:
        self.menu().exec(self.mapToGlobal(pos))

    def tray_menu(self) -> QMenu:
        """A menu that refills itself each time it opens.

        The tray keeps one menu object for the life of the app, so building it once
        would leave the size and click-through ticks showing whatever was true at
        startup.
        """
        m = QMenu(self)
        m.aboutToShow.connect(lambda: self._populate(m))
        self._populate(m)
        return m

    def menu(self) -> QMenu:
        m = QMenu(self)
        self._populate(m)
        return m

    def _populate(self, m: QMenu) -> None:
        m.clear()
        act = QAction("Dance now", m)
        act.triggered.connect(self.behavior.dance_now)
        m.addAction(act)

        pause = QAction("Pause" if not self._paused else "Resume", m)
        pause.triggered.connect(self.toggle_pause)
        m.addAction(pause)
        m.addSeparator()

        # Keep a Python reference: the QMenu that addMenu() hands back is collected
        # as soon as this method returns, which kills the submenu.
        sizes = self._size_menu = QMenu("Size", m)
        m.addMenu(sizes)
        for label, px in (("Small", 150), ("Medium", 230), ("Large", 330), ("Huge", 460)):
            a = QAction(label, sizes, checkable=True)
            a.setChecked(abs(self.settings.pet_height - px) < 6)
            a.triggered.connect(lambda _=False, v=px: self.set_height(v))
            sizes.addAction(a)

        ct = QAction("Click through", m, checkable=True)
        ct.setChecked(self.settings.click_through)
        ct.triggered.connect(lambda on: self.set_click_through(on, notify=True))
        m.addAction(ct)

        wow = QAction("Walk on windows", m, checkable=True)
        wow.setChecked(self.settings.walk_on_windows)
        wow.triggered.connect(self.set_walk_on_windows)
        m.addAction(wow)

        m.addSeparator()
        home = QAction("Bring to centre", m)
        home.triggered.connect(self.place_initially)
        m.addAction(home)

        about = QAction("About", m)
        about.triggered.connect(self._about)
        m.addAction(about)

        quit_act = QAction("Quit", m)
        quit_act.triggered.connect(QApplication.quit)
        m.addAction(quit_act)

    # -- settings ---------------------------------------------------------

    def toggle_pause(self) -> None:
        self._paused = not self._paused

    def set_height(self, px: int) -> None:
        self.settings.pet_height = px
        self.settings.clamped()
        self.settings.save()
        self._resize_canvas()

    def set_click_through(self, on: bool, notify: bool = False) -> None:
        self.settings.click_through = bool(on)
        self.settings.save()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool(on))
        # changing window flags hides the window, so it has to be shown again
        self.setWindowFlags(self._flags())
        self.show()
        if on and notify:
            # He can no longer be right-clicked, so say where the options went.
            # Shown non-modally: a modal box here would freeze the pet, and an
            # always-on-top toy has no business blocking the event loop.
            self._notice = QMessageBox(
                QMessageBox.Information, "Window Pet",
                "Click-through is on - the mouse now passes straight through him.\n\n"
                "Use the tray icon to turn it back off.",
                QMessageBox.Ok,
            )
            self._notice.setAttribute(Qt.WA_DeleteOnClose, False)
            self._notice.setWindowModality(Qt.NonModal)
            self._notice.show()

    def set_walk_on_windows(self, on: bool) -> None:
        self.settings.walk_on_windows = bool(on)
        self.settings.save()

    def _about(self) -> None:
        QMessageBox.information(
            self, "Window Pet",
            "A desktop pet that walks, dances and sits on your window edges.\n\n"
            "Drag him with the mouse, double-click to make him dance.\n"
            "Right-click, or use the tray icon, for options.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        self.poller.stop()
        super().closeEvent(event)


def run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    settings = C.Settings.load()

    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)

    rig_json = Path(__file__).resolve().parent.parent / "assets" / "rig.json"
    if not rig_json.exists():  # PyInstaller one-file layout
        rig_json = Path(getattr(sys, "_MEIPASS", ".")) / "assets" / "rig.json"
    rig = Rig.load(rig_json)

    win = PetWindow(rig, settings)
    win.show()
    win.register_self()
    win.place_initially()

    icon_path = rig_json.parent / "icon.png"
    icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("Window Pet")
    tray.setContextMenu(win.tray_menu())
    tray.activated.connect(
        lambda reason: win.behavior.dance_now()
        if reason == QSystemTrayIcon.Trigger else None
    )
    tray.show()
    app.aboutToQuit.connect(win.poller.stop)

    return app.exec()
