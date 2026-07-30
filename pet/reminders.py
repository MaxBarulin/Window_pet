"""Things he says at a time of day, read off the machine's own clock.

A reminder is a wall-clock time and a line to say. They live in
`%APPDATA%\\WindowPet\\reminders.json` next to the settings, not in the exe, so
they survive a rebuild and can be edited while he is running.

Firing is deliberately dumb: local time, to the minute, once per day per
reminder. `Schedule` keeps track of what has already gone off today so a reminder
does not repeat every frame for a whole minute, and so that one missed because the
machine was asleep is simply missed rather than fired late.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time as clock
from pathlib import Path

from .config import settings_path


def reminders_path() -> Path:
    return settings_path().parent / "reminders.json"


@dataclass
class Reminder:
    hour: int
    minute: int
    text: str
    enabled: bool = True
    weekdays_only: bool = False

    @property
    def at(self) -> clock:
        return clock(self.hour % 24, self.minute % 60)

    def label(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    def as_dict(self) -> dict:
        return {
            "at": self.label(),
            "text": self.text,
            "enabled": self.enabled,
            "weekdays_only": self.weekdays_only,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Reminder | None":
        try:
            hh, mm = str(raw["at"]).split(":")[:2]
            text = str(raw.get("text", "")).strip()
            if not text:
                return None
            return cls(
                hour=max(0, min(23, int(hh))),
                minute=max(0, min(59, int(mm))),
                text=text[:200],
                enabled=bool(raw.get("enabled", True)),
                weekdays_only=bool(raw.get("weekdays_only", False)),
            )
        except (KeyError, ValueError, TypeError):
            return None


def load(path: Path | None = None) -> list[Reminder]:
    """Read the reminders. A broken file is no reminders, never a crash."""
    p = path or reminders_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("reminders", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out = [Reminder.from_dict(r) for r in items if isinstance(r, dict)]
    return sorted((r for r in out if r), key=lambda r: (r.hour, r.minute))


def save(reminders: list[Reminder], path: Path | None = None) -> None:
    p = path or reminders_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"reminders": [r.as_dict() for r in reminders]}, indent=2,
                       ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # read-only profile: he just will not remember them next time


class Schedule:
    """Decides when a reminder is due, once per day."""

    def __init__(self, reminders: list[Reminder] | None = None):
        self.reminders = reminders if reminders is not None else load()
        self._fired: set[tuple[date, int, int]] = set()
        # Anything already past when he starts is water under the bridge: nobody
        # wants "time for lunch" at six in the evening because that is when the
        # machine came on.
        self._catch_up(datetime.now())

    def _catch_up(self, now: datetime) -> None:
        for r in self.reminders:
            if (r.hour, r.minute) <= (now.hour, now.minute):
                self._fired.add((now.date(), r.hour, r.minute))

    def reload(self) -> None:
        self.reminders = load()
        self._fired.clear()
        self._catch_up(datetime.now())

    def due(self, now: datetime | None = None) -> list[Reminder]:
        """Reminders whose minute has arrived and that have not gone off today."""
        now = now or datetime.now()
        out = []
        for r in self.reminders:
            if not r.enabled:
                continue
            if r.weekdays_only and now.weekday() >= 5:
                continue
            if (r.hour, r.minute) != (now.hour, now.minute):
                continue
            key = (now.date(), r.hour, r.minute)
            if key in self._fired:
                continue
            self._fired.add(key)
            out.append(r)
        # forget yesterday, so the set cannot grow without bound
        self._fired = {k for k in self._fired if k[0] == now.date()}
        return out
