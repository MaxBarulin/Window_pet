"""The speech bubble he talks in, and the window where you write what he says."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .reminders import Reminder, load, save


class Bubble(QWidget):
    """A frameless speech bubble that floats above him for a few seconds.

    Its own window rather than part of his canvas: the canvas is a fixed size cut
    to the widest pose any clip reaches, and a bubble would either be clipped by
    it or force it bigger, which is the resize that used to make him stutter.
    """

    PAD = 10
    TAIL = 9
    MAX_W = 320

    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
        self._text = ""
        self._lines: list[str] = []
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)
        font = QFont()
        font.setPointSize(10)
        self.setFont(font)

    def say(self, text: str, at: QPoint, seconds: float = 6.0) -> None:
        self._text = text
        self._lines = self._wrap(text)
        fm = QFontMetrics(self.font())
        w = max(fm.horizontalAdvance(line) for line in self._lines) + self.PAD * 2
        h = fm.height() * len(self._lines) + self.PAD * 2 + self.TAIL
        self.resize(int(w), int(h))
        # centred over the given point, sitting just above it
        self.move(int(at.x() - w / 2), int(at.y() - h))
        self.show()
        self.raise_()
        self._hide.start(int(seconds * 1000))

    def _wrap(self, text: str) -> list[str]:
        fm = QFontMetrics(self.font())
        limit = self.MAX_W - self.PAD * 2
        lines, line = [], ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if line and fm.horizontalAdvance(trial) > limit:
                lines.append(line)
                line = word
            else:
                line = trial
        if line:
            lines.append(line)
        return lines or [""]

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt
        fm = QFontMetrics(self.font())
        body = QRect(0, 0, self.width() - 1, self.height() - self.TAIL - 1)
        path = QPainterPath()
        path.addRoundedRect(body, 8, 8)
        tail = QPainterPath()
        cx = self.width() / 2
        tail.moveTo(cx - self.TAIL, body.bottom())
        tail.lineTo(cx, body.bottom() + self.TAIL)
        tail.lineTo(cx + self.TAIL, body.bottom())
        tail.closeSubpath()
        path = path.united(tail)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(252, 252, 250, 242))
        p.setPen(QPen(QColor(40, 40, 46, 220), 1.4))
        p.drawPath(path)
        p.setPen(QColor(24, 24, 28))
        y = self.PAD + fm.ascent()
        for line in self._lines:
            p.drawText(int((self.width() - fm.horizontalAdvance(line)) / 2), int(y), line)
            y += fm.height()


class ReminderDialog(QDialog):
    """Add, edit and remove the things he says, and when."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Window Pet — напоминания")
        self.resize(560, 420)

        col = QVBoxLayout(self)
        col.addWidget(QLabel(
            "Он говорит это по часам этой машины, один раз в сутки."
        ))

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Время", "Что говорит", "Вкл", "Пн-Пт"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        col.addWidget(self.table)

        entry = QHBoxLayout()
        self.at = QTimeEdit()
        self.at.setDisplayFormat("HH:mm")
        entry.addWidget(self.at)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Пора на обед")
        self.text.returnPressed.connect(self.add)
        entry.addWidget(self.text, 1)
        self.weekdays = QCheckBox("только Пн-Пт")
        entry.addWidget(self.weekdays)
        add = QPushButton("Добавить")
        add.clicked.connect(self.add)
        entry.addWidget(add)
        col.addLayout(entry)

        bar = QHBoxLayout()
        for label, slot in (("Вкл/выкл", self.toggle),
                            ("Удалить", self.remove),
                            ("Проверить", self.preview)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        done = QPushButton("Сохранить и закрыть")
        done.clicked.connect(self.accept)
        bar.addWidget(done)
        col.addLayout(bar)

        self.status = QLabel("")
        col.addWidget(self.status)

        self.items: list[Reminder] = load()
        self.refresh()

    # -- the table ---------------------------------------------------------

    def refresh(self) -> None:
        self.items.sort(key=lambda r: (r.hour, r.minute))
        self.table.setRowCount(len(self.items))
        for row, r in enumerate(self.items):
            for col, value in enumerate((r.label(), r.text,
                                         "да" if r.enabled else "нет",
                                         "да" if r.weekdays_only else "")):
                cell = QTableWidgetItem(value)
                if col != 1:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, cell)
        self.status.setText(f"напоминаний: {len(self.items)}")

    def _row(self) -> int:
        return self.table.currentRow()

    def add(self) -> None:
        text = self.text.text().strip()
        if not text:
            self.status.setText("напишите, что он должен сказать")
            return
        t = self.at.time()
        self.items.append(Reminder(t.hour(), t.minute(), text,
                                   weekdays_only=self.weekdays.isChecked()))
        self.text.clear()
        self.refresh()

    def remove(self) -> None:
        row = self._row()
        if 0 <= row < len(self.items):
            del self.items[row]
            self.refresh()

    def toggle(self) -> None:
        row = self._row()
        if 0 <= row < len(self.items):
            self.items[row].enabled = not self.items[row].enabled
            self.refresh()

    def preview(self) -> None:
        """Say the selected line now, so you can see where the bubble lands."""
        row = self._row()
        if 0 <= row < len(self.items):
            parent = self.parent()
            if parent is not None and hasattr(parent, "say"):
                parent.say(self.items[row].text)

    def accept(self) -> None:  # noqa: D102 - Qt
        save(self.items)
        super().accept()
