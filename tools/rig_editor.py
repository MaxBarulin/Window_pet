"""Place fifteen points on a photo and get a rigged, animatable character out.

This is the only step that needs a human. Everything else - cutting the eleven
parts, finding the joint caps, working out the rest pose, and every clip, physics
and packaging step after that - runs off `assets/rig.json`.

    python tools/rig_editor.py                 # opens on assets/cutout.png
    python tools/rig_editor.py photo.jpg

Left-click on the picture to place the joint selected on the right, or drag a
placed one to nudge it. The preview underneath is the real rig in real clips, so
a bad pivot shows up immediately as a torn shoulder or a knee in the wrong place.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet.poses import CLIPS  # noqa: E402
from pet.rigmath import Rig  # noqa: E402
from tools import autorig  # noqa: E402
from tools.render_preview import PilRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# The poses the preview shows. Chosen because between them they exercise every
# way a rig goes wrong: arms down out of the T, a deep knee bend, a hip thrown
# out sideways, and the shoulders at full stretch.
PREVIEW = [("idle", 0.0), ("walk", 0.25), ("crouch", 0.5), ("kazachok", 0.30)]

PART_COLOURS = {
    "head": (250, 250, 250), "torso": (80, 120, 255), "pelvis": (200, 80, 220),
    "arm_l_upper": (230, 60, 60), "arm_l_fore": (255, 150, 60),
    "arm_r_upper": (60, 190, 110), "arm_r_fore": (150, 230, 110),
    "thigh_l": (250, 220, 60), "shin_l": (255, 250, 160),
    "thigh_r": (90, 200, 255), "shin_r": (170, 230, 255),
}


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qim = QImage(data, img.width, img.height, img.width * 4, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qim.copy())


# ---------------------------------------------------------------------------
# Building the rig off the GUI thread
# ---------------------------------------------------------------------------

class BuildWorker(QObject):
    """Cuts a rig on a worker thread; a build takes about a second."""

    done = Signal(object, object, str)   # BuiltRig | None, previews, error

    def __init__(self, height: int = 210):
        super().__init__()
        self.height = height

    def run(self, cutout: Image.Image, joints: dict) -> None:
        try:
            built = autorig.build(cutout, joints)
        except Exception as exc:  # a bad joint set is normal here, not a crash
            self.done.emit(None, None, str(exc))
            return
        try:
            rig = Rig(built.data, ASSETS)
            ren = PilRenderer(rig, built.images)
            scale = self.height / rig.height()
            cw, ch = int(self.height * 1.25), int(self.height * 1.32)
            frames = []
            for clip_name, phase in PREVIEW:
                clip = CLIPS[clip_name]
                frames.append(
                    ren.draw(
                        clip.at(clip.duration * phase), (cw, ch),
                        (cw / 2, ch - int(self.height * 0.10)), scale,
                    )
                )
            self.done.emit(built, frames, "")
        except Exception:
            self.done.emit(built, None, traceback.format_exc(limit=3))


# ---------------------------------------------------------------------------
# The picture, with the joints on it
# ---------------------------------------------------------------------------

class Canvas(QWidget):
    """The cutout with draggable joint markers. Wheel zooms, middle-drag pans."""

    moved = Signal()          # a joint was placed or dragged
    picked = Signal(str)      # a joint marker was clicked

    HANDLE = 7

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 520)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image: Image.Image | None = None
        self.pixmap: QPixmap | None = None
        self.overlay: QPixmap | None = None
        self.show_parts = False
        self.joints: dict[str, autorig.Point] = {}
        self.selected = autorig.JOINT_ORDER[0]
        self.scale = 1.0
        self.origin = QPointF(0.0, 0.0)
        self._drag: str | None = None
        self._pan: QPoint | None = None

    # -- coordinate mapping ------------------------------------------------

    def to_image(self, p: QPointF) -> autorig.Point:
        return ((p.x() - self.origin.x()) / self.scale,
                (p.y() - self.origin.y()) / self.scale)

    def to_widget(self, p: autorig.Point) -> QPointF:
        return QPointF(p[0] * self.scale + self.origin.x(),
                       p[1] * self.scale + self.origin.y())

    def set_image(self, img: Image.Image) -> None:
        self.image = img
        self.pixmap = pil_to_qpixmap(img)
        self.overlay = None
        self.fit()

    def fit(self) -> None:
        if self.pixmap is None:
            return
        s = min(self.width() / self.pixmap.width(), self.height() / self.pixmap.height())
        self.scale = s * 0.96
        self.origin = QPointF(
            (self.width() - self.pixmap.width() * self.scale) / 2,
            (self.height() - self.pixmap.height() * self.scale) / 2,
        )
        self.update()

    def resizeEvent(self, event):  # noqa: N802 - Qt
        super().resizeEvent(event)
        if self.pixmap is not None and self.scale <= 0:
            self.fit()

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event):  # noqa: N802 - Qt
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(38, 38, 42))
        if self.pixmap is None:
            p.setPen(QColor(190, 190, 190))
            p.drawText(self.rect(), Qt.AlignCenter, "Open a photo to start")
            return
        target = QRectF(self.origin, QPointF(
            self.origin.x() + self.pixmap.width() * self.scale,
            self.origin.y() + self.pixmap.height() * self.scale))
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        if self.show_parts and self.overlay is not None:
            p.setOpacity(0.55)
            p.drawPixmap(target, self.overlay, QRectF(self.overlay.rect()))
            p.setOpacity(1.0)

        # bones first, so the markers sit on top of them
        p.setPen(QPen(QColor(255, 255, 255, 120), 1.5))
        for bone in autorig.BONES.values():
            if bone.tip and bone.pivot in self.joints and bone.tip in self.joints:
                p.drawLine(self.to_widget(self.joints[bone.pivot]),
                           self.to_widget(self.joints[bone.tip]))

        font = QFont(); font.setPointSize(8); p.setFont(font)
        for name in autorig.JOINT_ORDER:
            if name not in self.joints:
                continue
            c = self.to_widget(self.joints[name])
            on = name == self.selected
            r = self.HANDLE + (2 if on else 0)
            p.setBrush(QBrush(QColor(255, 210, 60) if on else QColor(70, 160, 255)))
            p.setPen(QPen(QColor(20, 20, 20), 1.5))
            p.drawEllipse(c, r, r)
            if on:
                p.setPen(QPen(QColor(255, 210, 60), 1, Qt.DashLine))
                p.drawLine(QPointF(0, c.y()), QPointF(self.width(), c.y()))
                p.drawLine(QPointF(c.x(), 0), QPointF(c.x(), self.height()))
                p.setPen(QColor(255, 230, 120))
                p.drawText(c + QPointF(r + 3, -r), name)

    # -- interaction -------------------------------------------------------

    def _hit(self, pos: QPointF) -> str | None:
        for name in autorig.JOINT_ORDER:
            if name in self.joints:
                d = self.to_widget(self.joints[name]) - pos
                if d.x() * d.x() + d.y() * d.y() <= (self.HANDLE + 4) ** 2:
                    return name
        return None

    def mousePressEvent(self, event):  # noqa: N802 - Qt
        if self.image is None:
            return
        if event.button() == Qt.MiddleButton:
            self._pan = event.position().toPoint()
            return
        if event.button() != Qt.LeftButton:
            return
        hit = self._hit(event.position())
        if hit:
            self._drag = hit
            self.selected = hit
            self.picked.emit(hit)
        else:
            self.joints[self.selected] = self.to_image(event.position())
            self.moved.emit()
        self.update()

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt
        if self._pan is not None:
            delta = event.position().toPoint() - self._pan
            self.origin += QPointF(delta.x(), delta.y())
            self._pan = event.position().toPoint()
            self.update()
        elif self._drag:
            self.joints[self._drag] = self.to_image(event.position())
            self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt
        if self._drag:
            self._drag = None
            self.moved.emit()
        self._pan = None

    def wheelEvent(self, event):  # noqa: N802 - Qt
        if self.pixmap is None:
            return
        before = self.to_image(event.position())
        factor = 1.0015 ** event.angleDelta().y()
        self.scale = max(0.05, min(12.0, self.scale * factor))
        after = self.to_widget(before)
        self.origin += event.position() - after
        self.update()


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

class Editor(QMainWindow):
    build_requested = Signal(object, object)

    def __init__(self, start: Path | None = None):
        super().__init__()
        self.setWindowTitle("Window Pet - rig editor")
        self.resize(1280, 860)
        self.cutout: Image.Image | None = None
        self.built = None
        self.source_name = "source.png"

        self.canvas = Canvas()
        self.canvas.moved.connect(self.on_joints_changed)
        self.canvas.picked.connect(self.select_joint)

        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(8, 8, 8, 8)
        row.addWidget(self.canvas, 3)
        row.addLayout(self._side_panel(), 2)
        self.setCentralWidget(root)

        self._start_worker()
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(250)
        self.debounce.timeout.connect(self.rebuild)

        if start is not None:
            self.load_image(start)
        elif (ASSETS / "cutout.png").exists():
            self.load_image(ASSETS / "cutout.png")

    # -- layout ------------------------------------------------------------

    def _side_panel(self) -> QVBoxLayout:
        col = QVBoxLayout()

        col.addWidget(self._heading("1 - the photo"))
        self.photo_label = QLabel("no photo")
        self.photo_label.setWordWrap(True)
        col.addWidget(self.photo_label)
        bar = QHBoxLayout()
        bar.addWidget(self._button("Open photo...", self.open_photo))
        self.cut_button = self._button("Remove background", self.remove_background)
        bar.addWidget(self.cut_button)
        col.addLayout(bar)

        col.addWidget(self._heading("2 - the joints"))
        self.list = QListWidget()
        self.list.setFixedHeight(250)
        for name in autorig.JOINT_ORDER:
            self.list.addItem(QListWidgetItem(name))
        col.addWidget(self.list)

        self.hint = QLabel(autorig.HINTS[autorig.JOINT_ORDER[0]])
        self.hint.setWordWrap(True)
        col.addWidget(self.hint)
        self.list.currentTextChanged.connect(self.select_joint)
        self.list.setCurrentRow(0)
        note = QLabel(autorig.SHOULDER_NOTE + "\nClick anywhere down the shoulder: "
                      "the pivot is lifted to the top of the sleeve for you.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        col.addWidget(note)

        bar = QHBoxLayout()
        bar.addWidget(self._button("Guess them all", self.guess))
        bar.addWidget(self._button("Load...", self.load_joints))
        bar.addWidget(self._button("Save...", self.save_joints))
        col.addLayout(bar)

        self.parts_toggle = QCheckBox("Show how it cut him up")
        self.parts_toggle.toggled.connect(self.on_toggle_parts)
        col.addWidget(self.parts_toggle)

        col.addWidget(self._heading("3 - the result"))
        self.preview = QLabel()
        self.preview.setMinimumHeight(290)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:#2a2a2e; border-radius:4px;")
        col.addWidget(self.preview)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        col.addWidget(self.status)

        col.addWidget(self._heading("4 - write it out"))
        self.export_button = self._button(
            f"Write {ASSETS.name}/rig.json + parts", self.export
        )
        col.addWidget(self.export_button)
        hint = QLabel("Then rebuild the exe, or just run <code>python -m pet</code>.")
        hint.setStyleSheet("color:#888;")
        col.addWidget(hint)
        col.addStretch(1)
        return col

    @staticmethod
    def _heading(text: str) -> QLabel:
        lab = QLabel(text.upper())
        f = lab.font(); f.setBold(True); f.setPointSize(f.pointSize() - 1)
        lab.setFont(f)
        lab.setStyleSheet("color:#6aa9ff; margin-top:6px;")
        return lab

    def _button(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.clicked.connect(slot)
        return b

    # -- the worker --------------------------------------------------------

    def _start_worker(self) -> None:
        self.thread = QThread(self)
        self.worker = BuildWorker()
        self.worker.moveToThread(self.thread)
        self.build_requested.connect(self.worker.run)
        self.worker.done.connect(self.on_built)
        self.thread.start()

    def closeEvent(self, event):  # noqa: N802 - Qt
        self.thread.quit()
        self.thread.wait(2000)
        super().closeEvent(event)

    # -- actions -----------------------------------------------------------

    def load_image(self, path: Path) -> None:
        img = Image.open(path).convert("RGBA")
        opaque = float((np.asarray(img)[..., 3] > 8).mean())
        self.cutout = img
        self.source_name = path.name
        self.canvas.set_image(img)
        self.photo_label.setText(
            f"{path.name}  {img.width}x{img.height}\n"
            + ("background already removed"
               if opaque < 0.92 else
               "looks like it still has a background - remove it first")
        )
        self.canvas.joints = {}
        self.guess()

    def open_photo(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Open a photo", str(ASSETS), "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if name:
            self.load_image(Path(name))

    def remove_background(self) -> None:
        if self.cutout is None:
            return
        from tools.cutout import cutout as run_cutout

        self.cut_button.setEnabled(False)
        self.status.setText("running rembg, this takes a few seconds...")
        QApplication.processEvents()
        try:
            self.cutout = run_cutout(self.cutout)
        except ImportError:
            QMessageBox.warning(
                self, "rembg missing",
                'Background removal needs rembg:\n\n    pip install "rembg[cpu]"',
            )
            self.status.setText("")
            self.cut_button.setEnabled(True)
            return
        finally:
            self.cut_button.setEnabled(True)
        self.canvas.set_image(self.cutout)
        self.photo_label.setText(f"{self.source_name}  background removed")
        self.guess()

    def guess(self) -> None:
        if self.cutout is None:
            return
        alpha = np.asarray(self.cutout)[..., 3].astype(np.float32) / 255.0
        try:
            self.canvas.joints = autorig.guess_joints(alpha)
        except autorig.RigError as exc:
            self.status.setText(str(exc))
            return
        self.canvas.update()
        self.on_joints_changed()

    def select_joint(self, name: str) -> None:
        if not name:
            return
        self.canvas.selected = name
        self.canvas.update()
        self.hint.setText(autorig.HINTS.get(name, ""))
        items = self.list.findItems(name, Qt.MatchExactly)
        if items and self.list.currentItem() is not items[0]:
            self.list.setCurrentItem(items[0])

    def on_joints_changed(self) -> None:
        self._refresh_list()
        self.debounce.start()

    def on_toggle_parts(self, on: bool) -> None:
        self.canvas.show_parts = on
        if on and self.canvas.overlay is None and self.built is not None:
            self._make_overlay(self.built)
        self.canvas.update()

    def _refresh_list(self) -> None:
        for i, name in enumerate(autorig.JOINT_ORDER):
            p = self.canvas.joints.get(name)
            self.list.item(i).setText(
                f"{name}    -" if p is None else f"{name}    {p[0]:.0f}, {p[1]:.0f}"
            )

    def rebuild(self) -> None:
        if self.cutout is None:
            return
        missing = [j for j in autorig.JOINT_ORDER if j not in self.canvas.joints]
        if missing:
            self.status.setText(f"{len(missing)} still to place: {missing[0]}")
            return
        self.status.setText("cutting...")
        self.build_requested.emit(self.cutout, dict(self.canvas.joints))

    def on_built(self, built, frames, error: str) -> None:
        if built is None:
            self.status.setText(error)
            self.export_button.setEnabled(False)
            return
        self.built = built
        self.export_button.setEnabled(True)
        # Show where the shoulders were lifted to, but touch nothing else: the
        # user may well have dragged another joint while this build was running.
        for bone in autorig.BONES.values():
            if bone.on_outline:
                self.canvas.joints[bone.pivot] = built.cut.joints[bone.pivot]
        self.canvas.overlay = None
        if self.canvas.show_parts:
            self._make_overlay(built)
        self._refresh_list()
        self.canvas.update()
        if frames:
            self.preview.setPixmap(pil_to_qpixmap(self._strip(frames)))
        rest = built.data["rest_angles"]
        self.status.setText(
            f"11 parts, {built.cut.unassigned * 100:.2f}% of him in none of them. "
            f"Arms rest at {rest['arm_l_upper']:.0f} deg."
            + (f"\n{error}" if error else "")
        )

    @staticmethod
    def _strip(frames: list[Image.Image]) -> Image.Image:
        w = sum(f.width for f in frames)
        h = max(f.height for f in frames)
        strip = Image.new("RGBA", (w, h), (42, 42, 46, 255))
        x = 0
        for f in frames:
            strip.alpha_composite(f, (x, 0))
            x += f.width
        return strip

    def _make_overlay(self, built) -> None:
        w, h = built.data["source_size"]
        rgba = np.zeros((h, w, 4), np.uint8)
        for name in sorted(built.images, key=lambda n: autorig.BONES[n].z):
            meta = built.data["parts"][name]
            ox, oy = meta["offset"]
            a = np.asarray(built.images[name])[..., 3]
            m = a > 10
            tile = rgba[oy:oy + a.shape[0], ox:ox + a.shape[1]]
            colour = PART_COLOURS.get(name, (160, 160, 160))
            for ch in range(3):
                tile[..., ch] = np.where(m, colour[ch], tile[..., ch])
            tile[..., 3] = np.where(m, 255, tile[..., 3])
        self.canvas.overlay = pil_to_qpixmap(Image.fromarray(rgba, "RGBA"))

    def load_joints(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Load joints", str(ASSETS), "JSON (*.json)"
        )
        if not name:
            return
        self.canvas.joints = autorig.load_joints(Path(name))
        self.canvas.update()
        self.on_joints_changed()

    def save_joints(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "Save joints", str(ASSETS / "joints.json"), "JSON (*.json)"
        )
        if name:
            autorig.save_joints(Path(name), self.canvas.joints)
            self.status.setText(f"wrote {name}")

    def export(self) -> None:
        if self.built is None:
            return
        target = QFileDialog.getExistingDirectory(
            self, "Write the rig into", str(ASSETS)
        )
        if not target:
            return
        out = Path(target)
        self.built.data["source"] = self.source_name
        rig_json = autorig.write(self.built, out)
        if out == ASSETS:
            self.cutout.save(ASSETS / "cutout.png")
            autorig.save_joints(ASSETS / "joints.json", self.canvas.joints)
        self.status.setText(f"wrote {rig_json} and 11 part PNGs")


def main() -> None:
    app = QApplication(sys.argv)
    start = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    win = Editor(start)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
