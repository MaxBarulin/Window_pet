"""Two editors in one window: the standard movements, and the ones you make.

Clips in `pet/poses.py` are procedural - functions of phase - because that is how
you get motion that reads as weight rather than as a slideshow. This window has
two tabs:

* **Стандартные** - the built-in movements. Each can be switched out of the pool
  he picks from, retuned (how big, how often), or forked into keyframes to edit.
* **Мои движения** - the keyframe editor. Pose him with sliders, drop keyframes,
  save. A saved clip lands in `assets/poses.json` and joins the pool; giving it
  the name of a standard one replaces that standard one.

The sliders are a `PoseSpec`, so the legs are driven by *where the feet are* and
solved with IK. You place feet and hips, not knee angles.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pet.kinematics import BASE_CROUCH, PoseSpec
from pet.poses import CLIPS, DANCES, MOTION_DEFAULTS, keyframe_clip
from pet.rigmath import Rig
from tools.render_preview import PilRenderer

# The standard movements as they ship, captured before anything in poses.json has
# had a chance to replace one of them by name. Without this snapshot there is no
# original left to sample, to play, or to go back to.
BUILT_IN = dict(CLIPS)
BUILT_IN_DANCES = set(DANCES)

# Names shown in Russian; the JSON keys and the code stay ASCII.
CLIP_NAMES_RU = {
    "idle": "стоит", "walk": "ходьба", "crouch": "присед",
    "hiphop": "хип-хоп", "step_touch": "шаг в сторону", "kazachok": "казачок",
    "hop_step": "подскок", "shimmy": "шимми", "contemporary": "контемп",
    "sit_dance": "танец сидя", "lean": "облокотиться", "wave": "машет",
    "jump_air": "прыжок", "fall": "падение", "land": "приземление",
    "dragged": "когда несут",
}

# name, label, min, max, default, in source pixels and degrees. Every angle turns
# the whole way round - a cutout past ~40 degrees at a joint looks rubbery, but
# that is a judgement for whoever poses him, not a limit to enforce.
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

# The size knobs, grouped by which movement they belong to. Grouping is the whole
# point: a flat list of thirteen numbers said nothing about what each one did.
MOTION_GROUPS = [
    ("Ходьба", [
        ("stride", "Длина шага, px"),
        ("step_lift", "Подъём ноги при шаге, px"),
        ("walk_seconds", "Секунд на один цикл"),
    ]),
    ("Присед", [
        ("squat_depth", "Глубина приседа, px"),
    ]),
    ("Танцы — размах", [
        ("bounce", "Качание вверх-вниз, px"),
        ("side_step", "Шаг в сторону, px"),
    ]),
    ("Казачок", [
        ("kazachok_depth", "Глубина, px"),
        ("kick_reach", "Вылет ноги, px"),
    ]),
    ("Как часто он это выбирает (вес, 0 = никогда)", [
        ("weight_walk", "Ходить"),
        ("weight_dance", "Танцевать"),
        ("weight_idle", "Стоять"),
        ("weight_crouch", "Приседать"),
        ("weight_hop", "Подпрыгивать"),
    ]),
]

# How many keyframes a built-in clip is sampled into when you fork it.
FORK_KEYS = 8


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


class Dial(QSlider):
    """A slider whose wheel moves it one step, not three.

    Qt scrolls a slider by `wheelScrollLines` steps a notch, three by default. On
    a control in degrees that makes fine adjustment - the only kind posing needs -
    impossible.
    """

    def wheelEvent(self, event):  # noqa: N802 - Qt
        notches = event.angleDelta().y() / 120.0
        if notches:
            self.setValue(int(round(self.value() + notches)))
            event.accept()
        else:
            super().wheelEvent(event)


class PoseEditor(QDialog):
    saved = Signal(str)

    def __init__(self, rig: Rig, images: dict[str, Image.Image], poses_file: Path,
                 parent: QWidget | None = None, height: int = 300):
        super().__init__(parent)
        self.setWindowTitle("Window Pet — движения")
        self.resize(1040, 760)
        self.poses_file = Path(poses_file)
        self.motion_file = self.poses_file.with_name("motion.json")
        self.renderer = PilRenderer(rig, images)
        self.scale = height / rig.height()
        self.canvas_size = (int(height * 1.5), int(height * 1.45))
        self.anchor = (self.canvas_size[0] / 2, self.canvas_size[1] - height * 0.10)

        self.keys: list[dict] = []
        self.builtin = ""                       # standard clip selected on tab 1
        self.sliders: dict[str, QSlider] = {}
        self.readouts: dict[str, QSpinBox] = {}
        self.motion_boxes: dict[str, QDoubleSpinBox] = {}

        outer = QHBoxLayout(self)

        # left column: the live preview, shared by both tabs
        left = QVBoxLayout()
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:#2a2a2e; border-radius:4px;")
        self.preview.setMinimumSize(*self.canvas_size)
        left.addWidget(self.preview)
        left.addWidget(QLabel("Фаза цикла (для проигрывания и ключей)"))
        self.phase = QSlider(Qt.Horizontal)
        self.phase.setRange(0, 100)
        self.phase.valueChanged.connect(self.on_phase)
        left.addWidget(self.phase)
        play = QPushButton("Проиграть выбранное")
        play.clicked.connect(self.play)
        left.addWidget(play)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        left.addWidget(self.status)
        left.addStretch(1)
        outer.addLayout(left, 3)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._standard_tab(), "Стандартные")
        self.tabs.addTab(self._custom_tab(), "Мои движения")
        self.tabs.currentChanged.connect(lambda _i: self.on_phase())
        outer.addWidget(self.tabs, 5)

        self.refresh_standard()
        self.refresh_mine()
        self.reset()
        self.render()

    # ------------------------------------------------------------------ tab 1
    def _standard_tab(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.addWidget(QLabel(
            "Галочка — участвует ли движение в наборе. Снимите её у стандартного, "
            "и вместо него будут чаще выпадать ваши."))
        self.stdlist = QListWidget()
        self.stdlist.setFixedHeight(210)
        self.stdlist.currentRowChanged.connect(self.pick_standard)
        self.stdlist.itemChanged.connect(self._toggle_enabled)
        col.addWidget(self.stdlist)

        bar = QHBoxLayout()
        for text, slot in (("Редактировать (в «Мои движения»)", self.fork_to_custom),
                           ("Вернуть стандартное", self.revert_builtin)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        col.addLayout(bar)

        col.addWidget(QLabel("<b>Размеры и частота движений</b>"))
        col.addWidget(QLabel("В пикселях фото и секундах. Больше — крупнее/дольше."))
        grid = QGridLayout()
        current = self._motion_document()
        r = 0
        for group, keys in MOTION_GROUPS:
            head = QLabel(f"<b>{group}</b>")
            grid.addWidget(head, r, 0, 1, 2)
            r += 1
            for key, label in keys:
                box = QDoubleSpinBox()
                box.setKeyboardTracking(False)
                weightish = key.startswith("weight") or key == "walk_seconds"
                box.setRange(0.0, 20.0 if key.startswith("weight") else 400.0)
                box.setSingleStep(0.1 if weightish else 2.0)
                box.setDecimals(2 if weightish else 0)
                box.setValue(float(current.get(key, MOTION_DEFAULTS[key])))
                self.motion_boxes[key] = box
                grid.addWidget(QLabel(label), r, 0)
                grid.addWidget(box, r, 1)
                r += 1
        holder = QWidget()
        holder.setLayout(grid)
        col.addWidget(holder)

        mbar = QHBoxLayout()
        for text, slot in (("Сохранить настройки", self.save_motion),
                           ("Сбросить к стандарту", self.reset_motion)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            mbar.addWidget(b)
        col.addLayout(mbar)
        self.motion_status = QLabel("Применится при следующем запуске.")
        self.motion_status.setWordWrap(True)
        col.addWidget(self.motion_status)
        col.addStretch(1)
        return w

    # ------------------------------------------------------------------ tab 2
    def _custom_tab(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)

        col = QVBoxLayout()
        col.addWidget(QLabel("Ваши движения:"))
        self.cliplist = QListWidget()
        self.cliplist.setFixedHeight(140)
        self.cliplist.currentRowChanged.connect(self.pick_mine)
        col.addWidget(self.cliplist)

        cbar = QHBoxLayout()
        for text, slot in (("Новое", self.new_clip),
                           ("Удалить моё", self.delete_clip)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            cbar.addWidget(b)
        col.addLayout(cbar)

        col.addWidget(QLabel("Ключи движения:"))
        self.keylist = QListWidget()
        self.keylist.setFixedHeight(120)
        self.keylist.currentRowChanged.connect(self.load_key)
        col.addWidget(self.keylist)

        kbar = QHBoxLayout()
        for text, slot in (("Поставить ключ", self.add_key),
                           ("Заменить", self.replace_key),
                           ("Удалить ключ", self.delete_key)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            kbar.addWidget(b)
        col.addLayout(kbar)

        meta = QHBoxLayout()
        meta.addWidget(QLabel("название"))
        self.name = QLineEdit("moy_tanec")
        meta.addWidget(self.name)
        meta.addWidget(QLabel("секунд"))
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.2, 12.0)
        self.duration.setSingleStep(0.1)
        self.duration.setValue(1.6)
        meta.addWidget(self.duration)
        col.addLayout(meta)
        save = QPushButton("Сохранить движение")
        save.clicked.connect(self.save)
        col.addWidget(save)
        col.addStretch(1)
        row.addLayout(col, 2)

        # the pose: one row per control, label above, slider plus a typed box
        form = QVBoxLayout()
        form.addWidget(QLabel("<b>Поза</b>"))
        for name, label, lo, hi, default in CONTROLS:
            form.addWidget(QLabel(label))
            line = QHBoxLayout()
            slider = Dial(Qt.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(default)
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
        return w

    # ------------------------------------------------------- standard clip list
    def refresh_standard(self) -> None:
        overrides = {c["name"] for c in self._document()["clips"]}
        off = set(self._motion_document().get("disabled", []))
        self.stdlist.blockSignals(True)
        self.stdlist.clear()
        for name in sorted(BUILT_IN):
            ru = CLIP_NAMES_RU.get(name, name)
            note = " — заменено вашим" if name in overrides else ""
            item = QListWidgetItem(f"{ru}  ({name}){note}")
            item.setData(Qt.UserRole, name)
            # Only the movements he randomly picks from - the dances - can be
            # switched out of the pool; walk, idle and the like are structural.
            if name in BUILT_IN_DANCES:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked if name in off else Qt.Checked)
            self.stdlist.addItem(item)
        self.stdlist.blockSignals(False)

    def pick_standard(self, row: int) -> None:
        if row < 0:
            return
        name = self.stdlist.item(row).data(Qt.UserRole)
        self.builtin = name
        self.keys = []
        clip = CLIPS.get(name) or BUILT_IN.get(name)
        if clip:
            self.on_phase()
        ru = CLIP_NAMES_RU.get(name, name)
        self.status.setText(
            f"«{ru}» — стандартное. «Проиграть» покажет его. "
            "«Редактировать» разложит на ключи в «Мои движения»."
        )

    def _toggle_enabled(self, item: QListWidgetItem) -> None:
        """A checkbox went on or off: write the disabled set to motion.json now."""
        name = item.data(Qt.UserRole)
        doc = self._motion_document()
        off = set(doc.get("disabled", []))
        if item.checkState() == Qt.Unchecked:
            off.add(name)
        else:
            off.discard(name)
        doc["disabled"] = sorted(off)
        self._write_motion(doc)
        ru = CLIP_NAMES_RU.get(name, name)
        state = "убрано из набора" if name in off else "вернулось в набор"
        self.motion_status.setText(f"«{ru}» {state}. Применится при запуске.")

    def fork_to_custom(self) -> None:
        """Sample the selected standard clip into keyframes and switch to tab 2.

        A built-in clip is a function of phase, so there is nothing to hand a
        keyframe editor - but it can be sampled. The result keeps the name, which
        is what makes saving it replace the standard one.
        """
        name = self.builtin
        clip = BUILT_IN.get(name)
        if clip is None:
            self.status.setText("выберите стандартное движение в списке слева")
            return
        self.keys = [
            values_to_key(spec_to_values(clip.at(clip.duration * i / FORK_KEYS)),
                          i / FORK_KEYS)
            for i in range(FORK_KEYS)
        ]
        self.name.setText(name)
        self.duration.setValue(clip.duration)
        self.tabs.setCurrentIndex(1)
        self.cliplist.setCurrentRow(-1)
        self.refresh_keys()
        self.load_key(0)
        ru = CLIP_NAMES_RU.get(name, name)
        self.status.setText(
            f"«{ru}» разложено на {FORK_KEYS} ключей. Правьте и сохраните — "
            "оно заменит стандартное."
        )

    def revert_builtin(self) -> None:
        """Drop an override so the standard movement comes back."""
        name = self.builtin
        if not name:
            self.status.setText("выберите стандартное движение в списке слева")
            return
        doc = self._document()
        if not any(c.get("name") == name for c in doc["clips"]):
            self.status.setText(f"«{name}» и так стандартное — заменять нечем")
            return
        doc["clips"] = [c for c in doc["clips"] if c.get("name") != name]
        self._write_poses(doc)
        self.refresh_standard()
        self.refresh_mine()
        self.status.setText(f"«{name}»: стандартное вернулось.")
        self.saved.emit(name)

    # ----------------------------------------------------------- my clip list
    def refresh_mine(self) -> None:
        self.mine = [c["name"] for c in self._document()["clips"]]
        self.cliplist.blockSignals(True)
        self.cliplist.clear()
        for name in self.mine:
            tag = " (заменяет стандартное)" if name in BUILT_IN else ""
            self.cliplist.addItem(QListWidgetItem(f"{name}{tag}"))
        self.cliplist.blockSignals(False)

    def pick_mine(self, row: int) -> None:
        if not (0 <= row < len(self.mine)):
            return
        for clip in self._document()["clips"]:
            if clip["name"] == self.mine[row]:
                self.builtin = ""
                self.name.setText(clip["name"])
                self.duration.setValue(float(clip.get("duration", 1.6)))
                self.keys = list(clip.get("keys", []))
                self.refresh_keys()
                self.load_key(0)
                return

    def new_clip(self) -> None:
        self.builtin = ""
        self.keys = []
        self.name.setText("moy_tanec")
        self.duration.setValue(1.6)
        self.phase.setValue(0)
        self.reset()
        self.cliplist.setCurrentRow(-1)
        self.refresh_keys()

    def delete_clip(self) -> None:
        name = (self.name.text() or "").strip()
        doc = self._document()
        if not any(c.get("name") == name for c in doc["clips"]):
            self.status.setText(f"«{name}» нет среди ваших — выберите своё в списке")
            return
        doc["clips"] = [c for c in doc["clips"] if c.get("name") != name]
        if not self._write_poses(doc):
            return
        back = " Стандартное вернулось." if name in BUILT_IN else ""
        self.new_clip()
        self.refresh_mine()
        self.refresh_standard()
        self.status.setText(
            f"«{name}» удалено. Своих осталось: {len(doc['clips'])}.{back}"
        )
        self.saved.emit(name)

    # ------------------------------------------------------------- the pose
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

    # ------------------------------------------------------------- preview
    def render(self, spec: PoseSpec | None = None) -> None:
        from tools.rig_editor import pil_to_qpixmap

        spec = spec or values_to_spec(self.values())
        img = self.renderer.draw(spec, self.canvas_size, self.anchor, self.scale)
        flat = Image.new("RGBA", self.canvas_size, (42, 42, 46, 255))
        flat.alpha_composite(img)
        self.preview.setPixmap(pil_to_qpixmap(flat))

    def _preview_clip(self):
        """What Play and the phase slider show, depending on the active tab."""
        if self.tabs.currentIndex() == 0 and self.builtin:
            return CLIPS.get(self.builtin) or BUILT_IN.get(self.builtin)
        if len(self.keys) >= 2:
            return self._clip()
        return None

    def on_phase(self) -> None:
        clip = self._preview_clip()
        if clip is not None:
            self.render(clip.at(clip.duration * self.phase.value() / 100.0))

    def play(self) -> None:
        clip = self._preview_clip()
        if clip is None:
            self.status.setText("нечего проигрывать — выберите движение или поставьте ключи")
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

    # ------------------------------------------------------------- keyframes
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

    # ------------------------------------------------------------- the files
    def _document(self) -> dict:
        if self.poses_file.exists():
            try:
                doc = json.loads(self.poses_file.read_text(encoding="utf-8"))
                if isinstance(doc.get("clips"), list):
                    return doc
            except (OSError, ValueError):
                pass
        return {"clips": []}

    def _write_poses(self, doc: dict) -> bool:
        try:
            self.poses_file.parent.mkdir(parents=True, exist_ok=True)
            self.poses_file.write_text(
                json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            return True
        except OSError as exc:
            self.status.setText(f"не удалось записать {self.poses_file}: {exc}")
            return False

    def _motion_document(self) -> dict:
        try:
            doc = json.loads(self.motion_file.read_text(encoding="utf-8"))
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_motion(self, doc: dict) -> None:
        try:
            self.motion_file.parent.mkdir(parents=True, exist_ok=True)
            self.motion_file.write_text(
                json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            self.motion_status.setText(f"не удалось записать {self.motion_file}: {exc}")

    def reset_motion(self) -> None:
        for key, box in self.motion_boxes.items():
            box.blockSignals(True)
            box.setValue(float(MOTION_DEFAULTS[key]))
            box.blockSignals(False)
        self.motion_status.setText("Значения возвращены к стандарту. Нажмите «Сохранить».")

    def save_motion(self) -> None:
        doc = self._motion_document()   # keep the disabled set the checkboxes wrote
        doc.update({k: round(b.value(), 2) for k, b in self.motion_boxes.items()})
        self._write_motion(doc)
        self.motion_status.setText(
            f"записано в {self.motion_file.name}. Применится при следующем запуске."
        )

    def save(self) -> None:
        name = (self.name.text() or "").strip()
        if not name or not all(c.isalnum() or c == "_" for c in name):
            self.status.setText("имя: буквы, цифры и подчёркивания")
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

        # A clip that replaces a standard one keeps what the standard one did
        # beyond its shape: its travel speed and whether it is a dance. A walk with
        # no speed walks on the spot.
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
        if not self._write_poses(doc):
            return
        self.refresh_mine()
        self.refresh_standard()
        self.status.setText(
            f"«{name}» сохранено, своих движений: {len(doc['clips'])}. "
            "Применится при следующем запуске."
        )
        self.saved.emit(name)
