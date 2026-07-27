"""The desktop overlay: a frameless, transparent, always-on-top window that draws
the posed rig and follows him around the screen.

The window is only as big as his current bounding box and is moved every frame,
rather than being one big full-screen surface. A 1-bit mask is rebuilt from the
rendered alpha each frame, so clicks on empty space around him fall through to
whatever is underneath instead of being swallowed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QBitmap,
    QCursor,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
    QRegion,
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
from .poses import CLIPS
from .rigmath import Rig

PAD = 6  # px of slack around his bounding box


def _qtransform(m: np.ndarray) -> QTransform:
    """3x3 row-vector-free matrix -> QTransform (which is transposed)."""
    return QTransform(
        m[0, 0], m[1, 0],
        m[0, 1], m[1, 1],
        m[0, 2], m[1, 2],
    )


class PetWindow(QWidget):
    def __init__(self, rig: Rig, settings: C.Settings):
        super().__init__(None)
        self.rig = rig
        self.settings = settings
        self.pixmaps = {
            name: QPixmap(str(rig.part_file(name))) for name in rig.parts
        }
        self.behavior = Behavior(Pet(), settings=settings)
        self.desktop = make_desktop()
        self._snapshot: Snapshot | None = None
        self._snap_age = 1e9
        self._frame: QImage | None = None
        self._paused = False
        self._drag_from: QPoint | None = None
        self._drag_trail: list[tuple[float, float, float]] = []
        self._mask_countdown = 0
        self._last_t = time.perf_counter()

        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        if settings.click_through:
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        if settings.click_through:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.setCursor(Qt.OpenHandCursor)
        self.setWindowTitle("Window Pet")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(1000 / max(1, settings.fps)))

    # -- setup ------------------------------------------------------------

    def place_initially(self) -> None:
        snap = self._refresh_desktop(force=True)
        b = snap.desktop_bounds()
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
            except Exception:
                pass

    # -- per-frame --------------------------------------------------------

    def _refresh_desktop(self, force: bool = False) -> Snapshot:
        if force or self._snapshot is None or self._snap_age >= C.DESKTOP_POLL:
            try:
                self._snapshot = self.desktop.snapshot()
            except Exception:
                if self._snapshot is None:
                    raise
            self._snap_age = 0.0
        return self._snapshot

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(now - self._last_t, 0.12)  # clamp, e.g. after a sleep/resume
        self._last_t = now
        if self._paused:
            return
        self._snap_age += dt
        snap = self._refresh_desktop()
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

    def _current_pose(self):
        p = self.behavior.pet
        clip = CLIPS.get(p.clip, CLIPS["idle"])
        return clip.at(p.clip_time)

    def _render(self) -> None:
        p = self.behavior.pet
        pose = self._current_pose()
        scale = self.settings.pet_height / self.rig.height()
        flip = p.facing < 0

        # compose about a local origin so the bbox can size the window
        tf0 = self.rig.compose(pose, (0.0, 0.0), scale, flip)
        bx0, by0, bx1, by1 = self.rig.bounds(tf0)

        anchor_y = p.y
        if p.anchor_kind == "hips":
            # sitting: hips rest on the ledge, so the feet hang below it
            hips_y = self.rig.pivot("pelvis")[1]
            anchor_y = p.y + (self.rig.ground[1] - hips_y) * scale

        win_x = int(round(p.x + bx0)) - PAD
        win_y = int(round(anchor_y + by0)) - PAD
        win_w = max(1, int(round(bx1 - bx0)) + PAD * 2)
        win_h = max(1, int(round(by1 - by0)) + PAD * 2)
        self.setGeometry(win_x, win_y, win_w, win_h)

        img = QImage(win_w, win_h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform
        )
        ox, oy = PAD - bx0, PAD - by0
        for name in self.rig.draw_order:
            m = tf0[name].copy()
            m[0, 2] += ox
            m[1, 2] += oy
            px, py = self.rig.parts[name]["offset"]
            t = _qtransform(m)
            painter.setTransform(QTransform.fromTranslate(px, py) * t)
            painter.drawPixmap(0, 0, self.pixmaps[name])
        painter.end()

        self._frame = img
        # refresh the click-through mask a few times a second, not every frame
        self._mask_countdown -= 1
        if self._mask_countdown <= 0:
            self._mask_countdown = 3
            self.setMask(QRegion(QBitmap.fromImage(img.createAlphaMask())))
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
        return (self._frame.pixelColor(pos).alpha()) > 24

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or not self._opaque_at(event.position().toPoint()):
            event.ignore()
            return
        self._drag_from = event.globalPosition().toPoint()
        self._drag_trail = [(time.perf_counter(), self._drag_from.x(), self._drag_from.y())]
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

    def menu(self) -> QMenu:
        m = QMenu(self)
        act = QAction("Dance now", m)
        act.triggered.connect(self.behavior.dance_now)
        m.addAction(act)

        pause = QAction("Pause" if not self._paused else "Resume", m)
        pause.triggered.connect(self.toggle_pause)
        m.addAction(pause)
        m.addSeparator()

        sizes = m.addMenu("Size")
        for label, px in (("Small", 150), ("Medium", 230), ("Large", 330), ("Huge", 460)):
            a = QAction(label, sizes, checkable=True)
            a.setChecked(abs(self.settings.pet_height - px) < 6)
            a.triggered.connect(lambda _=False, v=px: self.set_height(v))
            sizes.addAction(a)

        ct = QAction("Click through", m, checkable=True)
        ct.setChecked(self.settings.click_through)
        ct.triggered.connect(self.set_click_through)
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
        return m

    # -- settings ---------------------------------------------------------

    def toggle_pause(self) -> None:
        self._paused = not self._paused

    def set_height(self, px: int) -> None:
        self.settings.pet_height = px
        self.settings.clamped()
        self.settings.save()

    def set_click_through(self, on: bool) -> None:
        self.settings.click_through = bool(on)
        self.settings.save()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool(on))
        if on:
            # he can no longer be right-clicked, so say where the options went
            QMessageBox.information(
                self, "Window Pet",
                "Click-through is on - the mouse now passes straight through him.\n\n"
                "Use the tray icon to turn it back off.",
            )

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
    tray.setContextMenu(win.menu())
    tray.activated.connect(
        lambda reason: win.behavior.dance_now()
        if reason == QSystemTrayIcon.Trigger else None
    )
    tray.show()

    return app.exec()
