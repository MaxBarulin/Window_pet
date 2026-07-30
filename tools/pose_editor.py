"""Pose him by hand, keyframe it, and save it as a dance he will actually do.

Clips in `pet/poses.py` are procedural - functions of phase - because that is how
you get motion that reads as weight rather than as a slideshow. This is the other
way in: drag him into a shape, drop a keyframe, repeat, save. The result lands in
`assets/poses.json`, `pet/poses.py` picks it up at import, and it joins the pool
he picks dances from.

The sliders are a `PoseSpec`, so the legs are still driven by *where the feet are*
and solved with IK. You are placing feet and hips, not bending knees.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from pet.kinematics import BASE_CROUCH, PoseSpec
from pet.poses import CLIPS, DANCES, MOTION_DEFAULTS, keyframe_clip, load_clips

# The standard movements as they ship, captured before anything in poses.json has
# had a chance to replace one of them by name. Without this snapshot there is no
# original left to sample or to go back to.
BUILT_IN = dict(CLIPS)
BUILT_IN_DANCES = set(DANCES)


def spec_to_values(spec) -> dict[str, float]:
    """A PoseSpec back into slider values, for sampling a built-in clip."""
    return {
        "body_x": spec.body[0], "body_y": spec.body[1],
        "lean": spec.lean, "torso": spec.torso, "head": spec.head,
        "arm_l_upper": spec.arm_l_upper, "arm_l_fore": spec.arm_l_fore,
        "arm_r_upper": spec.arm_r_upper, "arm_r_fore": spec.arm_r_fore,
        "foot_l_x": spec.foot_l[0], "foot_l_y": spec.foot_l[1],
        "foot_r_x": spec.foot_r[0], "foot_r_y": spec.foot_r[1],
    }
from pet.rigmath import Rig
from tools.render_preview import PilRenderer

# name, label, min, max, default, in source pixels and degrees. Every angle goes
# the whole way round: a cutout past about 40 degrees at a joint starts to look
# rubbery, but that is a judgement for whoever is posing him, not a limit to
# enforce - and some poses genuinely need an arm all the way over.
CONTROLS = [
    ("body_x", "Таз влево/вправо", -90, 90, 0),
    ("body_y", "Таз вверх/вниз", -60, 150, int(BASE_CROUCH)),
    ("lean", "Наклон таза", -180, 180, 0),
    ("torso", "Корпус", -180, 180, 0),
    ("head", "Голова", -180, 180, 0),
    ("arm_l_upper", "Левое плечо", -180, 180, 0),
    ("arm_l_fore", "Левое предплечье", -180, 180, 0),
    ("arm_r_upper", "Правое плечо", -180, 180, 0),
    ("arm_r_fore", "Правое предплечье", -180, 180, 0),
    ("foot_l_x", "Левая стопа влево/вправо", -200, 200, 0),
    ("foot_l_y", "Левая стопа вверх", -180, 40, 0),
    ("foot_r_x", "Правая стопа влево/вправо", -200, 200, 0),
    ("foot_r_y", "Правая стопа вверх", -180, 40, 0),
]


def values_to_spec(v: dict[str, float]) -> PoseSpec:
    return PoseSpec(
        body=(v["body_x"], v["body_y"]),
        lean=v["lean"], torso=v["torso"], head=v["head"],
        arm_l_upper=v["arm_l_upper"], arm_l_fore=v["arm_l_fore"],
        arm_r_upper=v["arm_r_upper"], arm_r_fore=v["arm_r_fore"],
        foot_l=(v["foot_l_x"], v["foot_l_y"]),
        foot_r=(v["foot_r_x"], v["foot_r_y"]),
    )


def values_to_key(v: dict[str, float], phase: float) -> dict:
    return {
        "phase": round(phase, 3),
        "body": [v["body_x"], v["body_y"]],
        "lean": v["lean"], "torso": v["torso"], "head": v["head"],
        "arm_l_upper": v["arm_l_upper"], "arm_l_fore": v["arm_l_fore"],
        "arm_r_upper": v["arm_r_upper"], "arm_r_fore": v["arm_r_fore"],
        "foot_l": [v["foot_l_x"], v["foot_l_y"]],
        "foot_r": [v["foot_r_x"], v["foot_r_y"]],
    }


def key_to_values(key: dict) -> dict[str, float]:
    body = key.get("body", [0, 0])
    fl = key.get("foot_l", [0, 0])
    fr = key.get("foot_r", [0, 0])
    out = {"body_x": body[0], "body_y": body[1],
           "foot_l_x": fl[0], "foot_l_y": fl[1],
           "foot_r_x": fr[0], "foot_r_y": fr[1]}
    for name in ("lean", "torso", "head", "arm_l_upper", "arm_l_fore",
                 "arm_r_upper", "arm_r_fore"):
        out[name] = key.get(name, 0.0)
    return out


# The built-in clips are procedural - functions of phase - so they cannot be
# keyframed here. What can be changed is how big they are, and how often he picks
# them. Anything that would change the *shape* of a move stays in code.
MOTION_LABELS = {
    "stride": "Длина шага, px",
    "step_lift": "Подъём ноги при шаге, px",
    "squat_depth": "Глубина приседа, px",
    "bounce": "Качание в танце, px",
    "side_step": "Шаг в сторону, px",
    "kazachok_depth": "Глубина казачка, px",
    "kick_reach": "Вылет ноги в казачке, px",
    "walk_seconds": "Секунд на цикл ходьбы",
    "weight_walk": "Как часто: ходит",
    "weight_dance": "Как часто: танцует",
    "weight_idle": "Как часто: стоит",
    "weight_crouch": "Как часто: приседает",
    "weight_hop": "Как часто: подпрыгивает",
}

# How many keyframes a built-in clip is sampled into when you fork it. Enough to
# keep the shape recognisable, few enough to be editable by hand.
FORK_KEYS = 8


class Dial(QSlider):
    """A slider whose wheel moves it one step, not three.

    Qt scrolls a slider by `wheelScrollLines` steps a notch, which is three by
    default. On a control measured in degrees that makes fine adjustment
    impossible - which is the only kind of adjustment posing needs.
    """

    def wheelEvent(self, event):  # noqa: N802 - Qt
        notches = event.angleDelta().y() / 120.0
        if notches:
            self.setValue(int(round(self.value() + notches)))
            event.accept()
        else:
            super().wheelEvent(event)


class PoseEditor(QDialog):
    """Keyframe editor for one clip."""

    saved = Signal(str)

    def __init__(self, rig: Rig, images: dict[str, Image.Image], poses_file: Path,
                 parent: QWidget | None = None, height: int = 300):
        super().__init__(parent)
        self.setWindowTitle("Window Pet — движения")
        self.resize(940, 720)
        self.poses_file = Path(poses_file)
        self.renderer = PilRenderer(rig, images)
        self.scale = height / rig.height()
        self.canvas_size = (int(height * 1.5), int(height * 1.45))
        self.anchor = (self.canvas_size[0] / 2, self.canvas_size[1] - height * 0.10)
        self.keys: list[dict] = []
        self.builtin = ""
        self.sliders: dict[str, QSlider] = {}
        self.readouts: dict[str, QLabel] = {}

        row = QHBoxLayout(self)
        left = QVBoxLayout()
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:#2a2a2e; border-radius:4px;")
        self.preview.setMinimumSize(*self.canvas_size)
        left.addWidget(self.preview)

        left.addWidget(QLabel("Все его движения. Свои — правятся ключами, "
                              "стандартные — сначала «Взять как основу»."))
        self.cliplist = QListWidget()
        self.cliplist.setFixedHeight(120)
        self.cliplist.currentRowChanged.connect(self.pick_clip)
        left.addWidget(self.cliplist)

        left.addWidget(QLabel("Где в цикле стоит эта поза"))
        self.phase = QSlider(Qt.Horizontal)
        self.phase.setRange(0, 100)
        self.phase.valueChanged.connect(self.on_phase)
        left.addWidget(self.phase)

        self.keylist = QListWidget()
        self.keylist.setFixedHeight(130)
        self.keylist.currentRowChanged.connect(self.load_key)
        left.addWidget(self.keylist)

        clipbar = QHBoxLayout()
        for text, slot in (("Новое движение", self.new_clip),
                           ("Взять как основу", self.fork_builtin),
                           ("Удалить моё движение", self.delete_clip),
                           ("Вернуть стандартное", self.delete_clip)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            clipbar.addWidget(b)
        left.addLayout(clipbar)

        bar = QHBoxLayout()
        for text, slot in (("Поставить ключ", self.add_key),
                           ("Заменить", self.replace_key),
                           ("Удалить ключ", self.delete_key),
                           ("Проиграть", self.play)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        left.addLayout(bar)

        meta = QHBoxLayout()
        meta.addWidget(QLabel("название"))
        self.name = QLineEdit("my_dance")
        meta.addWidget(self.name)
        meta.addWidget(QLabel("секунд"))
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.2, 12.0)
        self.duration.setSingleStep(0.1)
        self.duration.setValue(1.6)
        meta.addWidget(self.duration)
        save = QPushButton("Сохранить движение")
        save.clicked.connect(self.save)
        meta.addWidget(save)
        left.addLayout(meta)

        self.status = QLabel("Поставьте хотя бы два ключа и сохраните.")
        self.status.setWordWrap(True)
        left.addWidget(self.status)
        row.addLayout(left, 3)

        # One row per control: the label above, then the slider and a box you can
        # type an exact number into. Thirteen bare sliders in a column was not
        # something anyone could aim with.
        form = QVBoxLayout()
        form.addWidget(QLabel("<b>Поза</b>"))
        for name, label, lo, hi, default in CONTROLS:
            form.addWidget(QLabel(label))
            line = QHBoxLayout()
            slider = Dial(Qt.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(default)
            slider.setSingleStep(1)
            slider.setPageStep(10)
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(max(1, (hi - lo) // 12))
            box = QSpinBox()
            box.setRange(lo, hi)
            box.setValue(default)
            box.setFixedWidth(64)
            slider.valueChanged.connect(box.setValue)
            box.valueChanged.connect(slider.setValue)
            slider.valueChanged.connect(self.on_slider)
            self.sliders[name] = slider
            self.readouts[name] = box
            line.addWidget(slider, 1)
            line.addWidget(box)
            form.addLayout(line)
        reset = QPushButton("Вернуть в стойку")
        reset.clicked.connect(self.reset)
        form.addWidget(reset)
        form.addStretch(1)
        holder = QWidget()
        holder.setLayout(form)
        row.addWidget(holder, 2)

        self.motion_boxes: dict[str, QDoubleSpinBox] = {}
        row.addLayout(self._motion_panel(), 1)

        self.load_existing()
        self.refresh_clips()
        self.render()

    def _motion_panel(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.addWidget(QLabel("<b>Размеры стандартных движений</b>"))
        col.addWidget(QLabel("Насколько крупное движение и как часто он его выбирает."))
        grid = QGridLayout()
        current = self._motion_document()
        for i, (key, label) in enumerate(MOTION_LABELS.items()):
            box = QDoubleSpinBox()
            box.setRange(0.0, 400.0)
            box.setKeyboardTracking(False)
            box.setSingleStep(0.1 if key.startswith(("weight", "walk_s")) else 2.0)
            box.setDecimals(2 if key.startswith(("weight", "walk_s")) else 0)
            box.setValue(float(current.get(key, MOTION_DEFAULTS[key])))
            self.motion_boxes[key] = box
            grid.addWidget(QLabel(label), i, 0)
            grid.addWidget(box, i, 1)
        holder = QWidget()
        holder.setLayout(grid)
        col.addWidget(holder)
        bar = QHBoxLayout()
        for text, slot in (("Сохранить размеры", self.save_motion),
                           ("Сбросить всё к стандарту", self.reset_motion)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        col.addLayout(bar)
        self.motion_status = QLabel("Применится при следующем запуске.")
        self.motion_status.setWordWrap(True)
        col.addWidget(self.motion_status)
        col.addStretch(1)
        return col

    # -- the clip list -----------------------------------------------------

    def refresh_clips(self) -> None:
        """Everything he can do: the authored clips first, then the built-ins."""
        mine = list(self._document()["clips"])
        self.mine = [c["name"] for c in mine]
        built_in = [n for n in sorted(CLIPS) if n not in self.mine]
        self.cliplist.blockSignals(True)
        self.cliplist.clear()
        for name in self.mine:
            tag = "своё, заменяет стандартное" if name in BUILT_IN else "своё"
            self.cliplist.addItem(QListWidgetItem(f"{name}   ({tag})"))
        for name in built_in:
            self.cliplist.addItem(QListWidgetItem(f"{name}   (стандартное)"))
        self.cliplist.blockSignals(False)

    def pick_clip(self, row: int) -> None:
        if row < 0:
            return
        if row < len(self.mine):
            for clip in self._document()["clips"]:
                if clip["name"] == self.mine[row]:
                    self.name.setText(clip["name"])
                    self.duration.setValue(float(clip.get("duration", 1.6)))
                    self.keys = list(clip.get("keys", []))
                    self.refresh_keys()
                    self.load_key(0)
                    return
        else:
            name = self.cliplist.item(row).text().split()[0]
            clip = BUILT_IN.get(name) or CLIPS.get(name)
            if clip:
                self.builtin = name
                self.keys = []
                self.name.setText(name)
                self.duration.setValue(clip.duration)
                self.refresh_keys()
                self.on_phase()
                self.status.setText(
                    f"«{name}» — стандартное. Проигрывается кнопкой «Проиграть». "
                    "Чтобы менять — «Взять как основу»: оно разложится на ключи."
                )

    def _selected_clip(self):
        """What Play should play: the keys if there are any, else the built-in."""
        if len(self.keys) >= 2:
            return self._clip()
        return BUILT_IN.get(self.builtin) or CLIPS.get(self.builtin)

    def fork_builtin(self) -> None:
        """Turn the selected standard movement into keyframes you can edit.

        Built-in clips are functions of phase, so there is nothing to hand to a
        keyframe editor - but they can be sampled. The result carries the same
        name, which is what makes it *replace* the standard one when saved, and
        what makes "Вернуть стандартное" put the original back by deleting it.
        """
        name = self.builtin or (self.name.text() or "").strip()
        clip = BUILT_IN.get(name)
        if clip is None:
            self.status.setText("выберите стандартное движение в списке")
            return
        self.keys = []
        for i in range(FORK_KEYS):
            phase = i / FORK_KEYS
            self.keys.append(values_to_key(spec_to_values(clip.at(clip.duration * phase)),
                                           phase))
        self.name.setText(name)
        self.duration.setValue(clip.duration)
        self.refresh_keys()
        self.load_key(0)
        self.status.setText(
            f"«{name}» разложено на {FORK_KEYS} ключей. Правьте и сохраняйте — "
            "оно заменит стандартное. «Вернуть стандартное» отменит замену."
        )

    def new_clip(self) -> None:
        """Start a fresh clip rather than editing whatever was loaded."""
        self.builtin = ""
        self.keys = []
        self.name.setText("my_dance")
        self.duration.setValue(1.6)
        self.phase.setValue(0)
        self.reset()
        self.cliplist.setCurrentRow(-1)
        self.refresh_keys()

    def delete_clip(self) -> None:
        """Remove an authored clip. Two buttons, one job.

        Deleting your own movement and putting a standard one back are the same
        operation - both drop an entry from poses.json - but they are not the same
        intention, and one label cannot describe both. Hence two buttons.
        """
        name = (self.name.text() or "").strip()
        doc = self._document()
        if not any(c.get("name") == name for c in doc["clips"]):
            self.status.setText(
                f"«{name}» нет среди ваших движений — удалять нечего. "
                "Выберите своё движение в списке."
            )
            return
        doc["clips"] = [c for c in doc["clips"] if c.get("name") != name]
        try:
            self.poses_file.write_text(
                json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            self.status.setText(f"не удалось записать {self.poses_file}: {exc}")
            return
        back = " Стандартное вернулось." if name in BUILT_IN else ""
        # after new_clip(), because that resets the keys and rewrites the status
        self.new_clip()
        self.refresh_clips()
        self.status.setText(
            f"«{name}» удалено. Своих движений осталось: {len(doc['clips'])}.{back}"
        )
        self.saved.emit(name)

    # -- the motion file ---------------------------------------------------

    def _motion_document(self) -> dict:
        path = self.poses_file.with_name("motion.json")
        try:
            doc = json.loads(path.read_text())
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}

    def reset_motion(self) -> None:
        for key, box in self.motion_boxes.items():
            box.setValue(float(MOTION_DEFAULTS[key]))

    def save_motion(self) -> None:
        path = self.poses_file.with_name("motion.json")
        doc = {k: round(b.value(), 2) for k, b in self.motion_boxes.items()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2) + "\n")
        self.motion_status.setText(
            f"записано в {path.name}. Применится при следующем запуске."
        )

    # -- values ------------------------------------------------------------

    def values(self) -> dict[str, float]:
        return {n: float(s.value()) for n, s in self.sliders.items()}

    def set_values(self, v: dict[str, float]) -> None:
        for name, slider in self.sliders.items():
            slider.blockSignals(True)
            slider.setValue(int(round(v.get(name, 0.0))))
            slider.blockSignals(False)
            self.readouts[name].blockSignals(True)
            self.readouts[name].setValue(slider.value())
            self.readouts[name].blockSignals(False)
        self.render()

    def reset(self) -> None:
        self.set_values({n: d for n, _l, _lo, _hi, d in CONTROLS})

    def on_slider(self) -> None:
        self.render()

    # -- preview -----------------------------------------------------------

    def render(self, spec: PoseSpec | None = None) -> None:
        from tools.rig_editor import pil_to_qpixmap

        spec = spec or values_to_spec(self.values())
        img = self.renderer.draw(spec, self.canvas_size, self.anchor, self.scale)
        flat = Image.new("RGBA", self.canvas_size, (42, 42, 46, 255))
        flat.alpha_composite(img)
        self.preview.setPixmap(pil_to_qpixmap(flat))

    def on_phase(self) -> None:
        """Scrubbing shows the clip so far, so you can key against what is there."""
        clip = self._selected_clip()
        if clip is None:
            return
        self.render(clip.at(clip.duration * self.phase.value() / 100.0))

    def play(self) -> None:
        clip = self._selected_clip()
        if clip is None:
            self.status.setText("нечего проигрывать — поставьте два ключа")
            return
        from PySide6.QtCore import QEventLoop, QTimer

        for i in range(40):
            self.render(clip.at(clip.duration * i / 40.0))
            loop = QEventLoop()
            QTimer.singleShot(int(clip.duration * 1000 / 40), loop.quit)
            loop.exec()

    def _clip(self):
        return keyframe_clip({
            "name": self.name.text() or "preview",
            "duration": self.duration.value(),
            "keys": self.keys,
        })

    # -- keyframes ---------------------------------------------------------

    def refresh_keys(self) -> None:
        self.keylist.clear()
        for k in self.keys:
            self.keylist.addItem(QListWidgetItem(f"фаза {k['phase']:.2f}"))
        self.status.setText(
            f"ключей: {len(self.keys)}. "
            + ("Можно сохранять." if len(self.keys) >= 2 else "Нужен ещё хотя бы один.")
        )

    def add_key(self) -> None:
        phase = self.phase.value() / 100.0
        self.keys = [k for k in self.keys if abs(k["phase"] - phase) > 1e-3]
        self.keys.append(values_to_key(self.values(), phase))
        self.keys.sort(key=lambda k: k["phase"])
        self.refresh_keys()

    def replace_key(self) -> None:
        row = self.keylist.currentRow()
        if 0 <= row < len(self.keys):
            self.keys[row] = values_to_key(self.values(), self.keys[row]["phase"])
            self.refresh_keys()

    def delete_key(self) -> None:
        row = self.keylist.currentRow()
        if 0 <= row < len(self.keys):
            del self.keys[row]
            self.refresh_keys()

    def load_key(self, row: int) -> None:
        if 0 <= row < len(self.keys):
            self.phase.blockSignals(True)
            self.phase.setValue(int(self.keys[row]["phase"] * 100))
            self.phase.blockSignals(False)
            self.set_values(key_to_values(self.keys[row]))

    # -- the file ----------------------------------------------------------

    def _document(self) -> dict:
        if self.poses_file.exists():
            try:
                doc = json.loads(self.poses_file.read_text())
                if isinstance(doc.get("clips"), list):
                    return doc
            except (OSError, ValueError):
                pass
        return {"clips": []}

    def load_existing(self) -> None:
        clips = self._document()["clips"]
        if not clips:
            self.refresh_keys()
            return
        first = clips[0]
        self.name.setText(str(first.get("name", "my_dance")))
        self.duration.setValue(float(first.get("duration", 1.6)))
        self.keys = list(first.get("keys", []))
        self.refresh_keys()

    def save(self) -> None:
        name = (self.name.text() or "").strip()
        if not name.replace("_", "").isalnum():
            self.status.setText("простое имя: буквы, цифры, подчёркивания")
            return
        if len(self.keys) < 2:
            self.status.setText("нужно минимум два ключа")
            return
        try:
            keyframe_clip({"name": name, "duration": self.duration.value(),
                           "keys": self.keys})
        except ValueError as exc:
            self.status.setText(str(exc))
            return

        # When this clip replaces a standard one, it has to keep what the standard
        # one did beyond its shape: how fast it carries him across the screen, and
        # whether it counts as a dance or as getting-about. A walk with no speed
        # walks on the spot - which is exactly the bug this guards against.
        original = BUILT_IN.get(name)
        doc = self._document()
        doc["clips"] = [c for c in doc["clips"] if c.get("name") != name]
        clip = {
            "name": name,
            "duration": round(self.duration.value(), 2),
            "loop": original.loop if original else True,
            "dance": (name in BUILT_IN_DANCES) if original else True,
            "keys": self.keys,
        }
        if original and original.speed:
            clip["speed"] = round(original.speed, 6)
        doc["clips"].append(clip)
        self.poses_file.parent.mkdir(parents=True, exist_ok=True)
        self.poses_file.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self.refresh_clips()
        self.status.setText(
            f"записано в {self.poses_file.name}, своих движений: {len(doc['clips'])}. "
            "Применится при следующем запуске."
        )
        self.saved.emit(name)
