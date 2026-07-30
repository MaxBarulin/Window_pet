"""Place nineteen points on a photo and get a rigged, animatable character out.

This is the only step that needs a human. Everything else - cutting the fifteen
parts, finding the joint caps, working out the rest pose, and every clip, physics
and packaging step after that - runs off `assets/rig.json`.

    python tools/rig_editor.py                 # opens on assets/cutout.png
    python tools/rig_editor.py photo.jpg

Left-click on the picture to place the joint selected on the right, or drag a
placed one to nudge it. The preview underneath is the real rig in real clips, so
a bad pivot shows up immediately as a torn shoulder or a knee in the wrong place.

The cut is not take-it-or-leave-it. Over a joint the wheel grows or shrinks what
that joint claims - the green circle - and shift plus the wheel slides the seam
along the bone. The circle's own handle drags too. The automatic values are the
biggest disc that fits inside him, which is right when a joint sits in the middle
of a limb and shy when it does not.
"""

from __future__ import annotations

import json
import math
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
    "hand_l": (255, 90, 140), "hand_r": (120, 255, 190),
    "foot_l": (255, 200, 120), "foot_r": (140, 160, 255),
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

    # Interactive builds run on a shrunk copy. The cut is identical - everything
    # it measures is a fraction of the figure - and it is roughly six times
    # faster, which is the difference between dragging a joint and waiting for it.
    QUICK_SIDE = 420

    def __init__(self, height: int = 210):
        super().__init__()
        self.height = height

    def run(self, cutout: Image.Image, joints: dict, caps: dict, splits: dict,
            free: object) -> None:
        try:
            built = autorig.build(cutout, joints, caps=caps, splits=splits,
                                  free=set(free), max_side=self.QUICK_SIDE)
        except Exception as exc:  # a bad joint set is normal here, not a crash
            self.done.emit(None, None, str(exc))
            return
        try:
            rig = Rig(built.data, ASSETS)
            ren = PilRenderer(rig, built.images)
            scale = self.height / rig.height()
            cw, ch = int(self.height * 1.25), int(self.height * 1.32)
            frames = []
            for clip_name, phase in PREVIEW[:3]:
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

    moved = Signal()          # a joint was placed, dragged, or its cut retuned
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
        # Hand overrides on the cut. `cap_shown` is what the last build actually
        # used, measured or overridden, so the circle on screen is the real one.
        self.caps: dict[str, float] = {}
        self.splits: dict[str, float] = {}
        self.cap_shown: dict[str, float] = {}
        # pivots the user has pinned where they put them, instead of on the outline
        self.free: set[str] = set()
        self.selected = autorig.JOINT_ORDER[0]
        self.scale = 1.0
        self.origin = QPointF(0.0, 0.0)
        self._drag: str | None = None
        self._cap_drag: str | None = None
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
                self._draw_cut_handles(p, name, c)

    def _draw_cut_handles(self, p: QPainter, name: str, centre: QPointF) -> None:
        """The cap circle, and a tick where the seam sits along the bone."""
        cap = self.caps.get(name, self.cap_shown.get(name, 0.0))
        if cap > 0.5:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(120, 255, 190, 200), 1.5, Qt.DashLine))
            p.drawEllipse(centre, cap * self.scale, cap * self.scale)
            handle = centre + QPointF(cap * self.scale, 0)
            p.setBrush(QBrush(QColor(120, 255, 190)))
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.drawEllipse(handle, 4, 4)

        split = self.splits.get(name, 0.0)
        for bone in autorig.BONES.values():
            if bone.pivot != name or bone.tip not in self.joints:
                continue
            tip = self.joints[bone.tip]
            dx, dy = autorig._norm(tip[0] - self.joints[name][0],
                                   tip[1] - self.joints[name][1])
            at = self.to_widget((self.joints[name][0] + dx * split,
                                 self.joints[name][1] + dy * split))
            p.setPen(QPen(QColor(255, 130, 200), 2))
            p.drawLine(at + QPointF(-dy * 14, dx * 14), at + QPointF(dy * 14, -dx * 14))
            break

    # -- retuning the cut --------------------------------------------------

    def adjust(self, name: str, cap_delta: float, split_delta: float) -> None:
        if cap_delta:
            base = self.caps.get(name, self.cap_shown.get(name, 6.0))
            self.caps[name] = max(0.0, base + cap_delta)
        if split_delta:
            self.splits[name] = self.splits.get(name, 0.0) + split_delta
            if abs(self.splits[name]) < 0.5:
                self.splits.pop(name)
        self.update()
        self.moved.emit()

    def reset_cut(self, name: str) -> None:
        self.caps.pop(name, None)
        self.splits.pop(name, None)
        self.update()
        self.moved.emit()

    # -- interaction -------------------------------------------------------

    def _hit(self, pos: QPointF) -> str | None:
        for name in autorig.JOINT_ORDER:
            if name in self.joints:
                d = self.to_widget(self.joints[name]) - pos
                if d.x() * d.x() + d.y() * d.y() <= (self.HANDLE + 4) ** 2:
                    return name
        return None

    def _near_selected(self, pos: QPointF) -> bool:
        """Inside the selected joint's cap circle, where the wheel means 'resize'."""
        if self.selected not in self.joints:
            return False
        cap = self.caps.get(self.selected, self.cap_shown.get(self.selected, 0.0))
        d = self.to_widget(self.joints[self.selected]) - pos
        reach = max(cap * self.scale, self.HANDLE + 6)
        return d.x() * d.x() + d.y() * d.y() <= reach * reach

    def mousePressEvent(self, event):  # noqa: N802 - Qt
        if self.image is None:
            return
        if event.button() == Qt.MiddleButton:
            self._pan = event.position().toPoint()
            return
        if event.button() != Qt.LeftButton:
            return
        if self._on_cap_handle(event.position()):
            self._cap_drag = self.selected
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
        elif self._cap_drag:
            d = self.to_widget(self.joints[self._cap_drag]) - event.position()
            self.caps[self._cap_drag] = max(
                0.0, math.hypot(d.x(), d.y()) / max(self.scale, 1e-6)
            )
            self.update()
        elif self._drag:
            self.joints[self._drag] = self.to_image(event.position())
            self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt
        if self._drag or self._cap_drag:
            self._drag = self._cap_drag = None
            self.moved.emit()
        self._pan = None

    def _on_cap_handle(self, pos: QPointF) -> bool:
        if self.selected not in self.joints:
            return False
        cap = self.caps.get(self.selected, self.cap_shown.get(self.selected, 0.0))
        if cap <= 0.5:
            return False
        handle = self.to_widget(self.joints[self.selected]) + QPointF(cap * self.scale, 0)
        d = handle - pos
        return d.x() * d.x() + d.y() * d.y() <= 49

    def wheelEvent(self, event):  # noqa: N802 - Qt
        if self.pixmap is None:
            return
        # Over a joint, the wheel resizes what that joint claims rather than
        # zooming: that is the knob you actually want under your finger while
        # looking at a seam. Shift slides the seam along the bone instead.
        target = self._hit(event.position()) or (
            self.selected if self._near_selected(event.position()) else None
        )
        if target:
            notches = event.angleDelta().y() / 120.0
            # one notch, one pixel - it was three, which made the knob feel like
            # it was moving on its own
            if event.modifiers() & Qt.ShiftModifier:
                self.adjust(target, 0.0, notches)
            else:
                self.adjust(target, notches, 0.0)
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
    build_requested = Signal(object, object, object, object, object)

    def __init__(self, start: Path | None = None):
        super().__init__()
        self.setWindowTitle("Window Pet — разметка персонажа")
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
        self._busy = False
        self._pending = False
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(90)
        self.debounce.timeout.connect(self.rebuild)

        if start is not None:
            self.load_image(start)
        elif (ASSETS / "cutout.png").exists():
            self.load_image(ASSETS / "cutout.png")

    # -- layout ------------------------------------------------------------

    def _side_panel(self) -> QVBoxLayout:
        col = QVBoxLayout()

        col.addWidget(self._heading("1 — фото"))
        self.photo_label = QLabel("no photo")
        self.photo_label.setWordWrap(True)
        col.addWidget(self.photo_label)
        bar = QHBoxLayout()
        bar.addWidget(self._button("Открыть фото...", self.open_photo))
        self.cut_button = self._button("Убрать фон", self.remove_background)
        bar.addWidget(self.cut_button)
        col.addLayout(bar)

        col.addWidget(self._heading("2 — суставы"))
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
        bar.addWidget(self._button("Расставить автоматически", self.guess))
        bar.addWidget(self._button("Загрузить...", self.load_joints))
        bar.addWidget(self._button("Сохранить...", self.save_joints))
        col.addLayout(bar)

        self.pin_toggle = QCheckBox("Закрепить ось там, где поставил")
        self.pin_toggle.setToolTip(
            "Shoulders are lifted to the top of the sleeve by default, because "
            "material above the pivot swings out as a wing. Pin one to keep it "
            "inside the figure instead; it then behaves like any other joint, "
            "with a cap of its own."
        )
        self.pin_toggle.toggled.connect(self.on_pin)
        col.addWidget(self.pin_toggle)

        self.parts_toggle = QCheckBox("Показать нарезку по деталям")
        self.parts_toggle.toggled.connect(self.on_toggle_parts)
        col.addWidget(self.parts_toggle)

        col.addWidget(self._heading("3 — результат"))
        self.preview = QLabel()
        self.preview.setMinimumHeight(290)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:#2a2a2e; border-radius:4px;")
        col.addWidget(self.preview)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        col.addWidget(self.status)

        col.addWidget(self._heading("4 — записать"))
        self.export_button = self._button(
            "Записать риг (rig.json + детали)", self.export
        )
        col.addWidget(self.export_button)
        self.pose_button = self._button("Движения и танцы...", self.open_poses)
        col.addWidget(self.pose_button)
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
        from tools.cutout import cutout as run_cutout, model_path, model_present

        self.cut_button.setEnabled(False)
        self.status.setText(
            "running rembg, this takes a few seconds..."
            if model_present() else
            f"downloading the matting model once into {model_path().parent}, "
            "about 176 MB - after this it is instant"
        )
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
        if not hasattr(self, "pin_toggle"):
            return  # still building the panel
        pinnable = any(b.on_outline and b.pivot == name for b in autorig.BONES.values())
        self.pin_toggle.setEnabled(pinnable)
        self.pin_toggle.blockSignals(True)
        self.pin_toggle.setChecked(name in self.canvas.free)
        self.pin_toggle.blockSignals(False)
        items = self.list.findItems(name, Qt.MatchExactly)
        if items and self.list.currentItem() is not items[0]:
            self.list.setCurrentItem(items[0])

    def on_pin(self, on: bool) -> None:
        name = self.canvas.selected
        (self.canvas.free.add if on else self.canvas.free.discard)(name)
        self.on_joints_changed()

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
        if self._busy:
            # A build is already running. Remember that the world moved on and
            # re-fire once - queuing every drag event would just build a backlog
            # of results nobody wants any more.
            self._pending = True
            return
        self._busy = True
        self.status.setText("cutting...")
        self.build_requested.emit(
            self.cutout, dict(self.canvas.joints),
            dict(self.canvas.caps), dict(self.canvas.splits),
            set(self.canvas.free),
        )

    def on_built(self, built, frames, error: str) -> None:
        self._busy = False
        if self._pending:
            self._pending = False
            self.debounce.start()
        if built is None:
            self.status.setText(error)
            self.export_button.setEnabled(False)
            return
        self.built = built
        self.export_button.setEnabled(True)
        # the quick build ran on a shrunk copy, so everything it reports comes
        # back in those pixels; the canvas works in the photo's own
        k = self.cutout.width / float(built.data["source_size"][0])
        self.canvas.cap_shown = {
            bone.pivot: built.cut.cap_radius[name] * k
            for name, bone in autorig.BONES.items()
        }
        # Show where the shoulders were lifted to, but touch nothing else: the
        # user may well have dragged another joint while this build was running.
        for bone in autorig.BONES.values():
            if bone.on_outline and bone.pivot not in self.canvas.free:
                x, y = built.cut.joints[bone.pivot]
                self.canvas.joints[bone.pivot] = (x * k, y * k)
        self.canvas.overlay = None
        if self.canvas.show_parts:
            self._make_overlay(built)
        self._refresh_list()
        self.canvas.update()
        if frames:
            self.preview.setPixmap(pil_to_qpixmap(self._strip(frames)))
        sel = self.canvas.selected
        cap = self.canvas.cap_shown.get(sel, 0.0)
        split = self.canvas.splits.get(sel, 0.0)
        self.status.setText(
            f"{len(built.images)} parts, {built.cut.unassigned * 100:.2f}% unclaimed.\n"
            f"{sel}: cap {cap:.0f}px"
            + (f", seam {split:+.0f}px" if split else "")
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

    def open_poses(self) -> None:
        """Keyframe a dance against the rig as it currently stands."""
        if self.built is None:
            self.status.setText("cut him up first - the pose editor needs a rig")
            return
        from tools.pose_editor import PoseEditor

        dlg = PoseEditor(Rig(self.built.data, ASSETS), self.built.images,
                         ASSETS / "poses.json", self)
        dlg.saved.connect(lambda n: self.status.setText(f"saved the clip '{n}'"))
        dlg.exec()

    def load_joints(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Load joints", str(ASSETS), "JSON (*.json)"
        )
        if not name:
            return
        points, caps, splits, free = autorig.load_joints(Path(name))
        (self.canvas.joints, self.canvas.caps,
         self.canvas.splits, self.canvas.free) = points, caps, splits, free
        self.canvas.update()
        self.on_joints_changed()

    def save_joints(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "Save joints", str(ASSETS / "joints.json"), "JSON (*.json)"
        )
        if name:
            autorig.save_joints(Path(name), self.canvas.joints,
                                self.canvas.caps, self.canvas.splits,
                                self.canvas.free)
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
        # everything on screen was cut from a shrunk copy; write the real one
        self.status.setText("cutting at full size...")
        QApplication.processEvents()
        full = autorig.build(
            self.cutout, dict(self.canvas.joints), self.source_name,
            caps=dict(self.canvas.caps), splits=dict(self.canvas.splits),
            free=set(self.canvas.free),
        )
        self.built = full
        rig_json = autorig.write(full, out)
        # The matte and the points go with it. Without them the rig cannot be
        # rebuilt or re-edited, and they were only being written when the target
        # happened to be the repository's own assets folder.
        self.cutout.save(out / "cutout.png")
        autorig.save_joints(out / "joints.json", self.canvas.joints,
                            self.canvas.caps, self.canvas.splits, self.canvas.free)
        self.status.setText(f"wrote cutout.png, joints.json, {rig_json.name} and {len(full.images)} part PNGs")


def main() -> None:
    app = QApplication(sys.argv)
    start = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    win = Editor(start)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
