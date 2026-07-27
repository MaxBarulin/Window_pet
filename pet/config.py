"""Tunables, plus loading/saving the user's settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def settings_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / "WindowPet" / "settings.json"


@dataclass
class Settings:
    pet_height: int = 230          # on-screen height in px
    fps: int = 60
    click_through: bool = False    # ignore the mouse entirely
    always_on_top: bool = True
    walk_on_windows: bool = True   # treat window title bars as ledges
    sound: bool = False            # reserved; the pet is silent today

    def clamped(self) -> "Settings":
        self.pet_height = max(90, min(600, int(self.pet_height)))
        self.fps = max(10, min(60, int(self.fps)))
        return self

    @classmethod
    def load(cls) -> "Settings":
        p = settings_path()
        if p.exists():
            try:
                raw = json.loads(p.read_text())
            except (OSError, ValueError):
                return cls()
            known = {f for f in cls.__dataclass_fields__}
            return cls(**{k: v for k, v in raw.items() if k in known}).clamped()
        return cls()

    def save(self) -> None:
        p = settings_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(asdict(self), indent=2))
        except OSError:
            pass  # read-only profile: run with defaults rather than crash


# --- physics, in px/s and px/s^2 at a reference pet height of 230 px --------
GRAVITY = 1500.0
TERMINAL_VY = 1900.0
WALK_SPEED = 96.0            # scaled by clip speed
JUMP_VY = -560.0
STEP_UP_MAX = 42.0           # ledge lip he will hop up onto
THROW_DAMPING = 0.55         # velocity kept when released from a drag
BOUNCE = 0.0                 # landing is dead, no bounce

# how long each idle-time behaviour lasts, in seconds (min, max)
DURATIONS = {
    "idle": (1.2, 2.6),
    "walk": (1.8, 5.5),
    "dance": (4.5, 9.0),
    "sit_dance": (4.0, 9.0),
    "lean": (2.2, 4.5),
    "wave": (1.9, 1.9),
}

# desktop is re-read this often (seconds); EnumWindows is cheap but not free
DESKTOP_POLL = 0.35
