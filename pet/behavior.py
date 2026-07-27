"""What the pet decides to do, and where he ends up.

A small state machine over the terrain from `desktop.py`. He walks along ledges,
turns at obstacles, leans on screen edges, sits on window title bars to dance,
falls when the ledge he was standing on goes away, and can be picked up and thrown.

Positions are screen pixels. `x` is the point between his feet and `y` is the
surface he is standing on, so (x, y) is his contact point with the world.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from . import config as C
from .desktop import Ledge, Snapshot
from .poses import CLIPS, DANCES


class State(Enum):
    IDLE = "idle"
    WALK = "walk"
    DANCE = "dance"
    CROUCH = "crouch"
    SIT_DANCE = "sit_dance"
    LEAN = "lean"
    WAVE = "wave"
    FALL = "fall"
    LAND = "land"
    DRAGGED = "dragged"


@dataclass
class Pet:
    x: float = 400.0
    y: float = 800.0
    vx: float = 0.0
    vy: float = 0.0
    facing: int = 1                      # +1 faces screen-right
    state: State = State.FALL
    clip: str = "fall"
    state_time: float = 0.0              # seconds spent in this state
    clip_time: float = 0.0               # seconds into the current clip
    state_limit: float = 1.0             # when to pick something new
    ledge: Ledge | None = None
    anchor_kind: str = "feet"            # "hips" while sitting on a ledge
    airborne: bool = True
    last_event: str = ""                 # for tests and the tray tooltip


class Behavior:
    """Drives one Pet against a desktop snapshot."""

    def __init__(self, pet: Pet | None = None, rng: random.Random | None = None,
                 settings: C.Settings | None = None):
        self.pet = pet or Pet()
        self.rng = rng or random.Random()
        self.settings = settings or C.Settings()

    # -- helpers -----------------------------------------------------------

    @property
    def scale(self) -> float:
        """Physics scales with his on-screen size so motion feels the same."""
        return self.settings.pet_height / 230.0

    def _half_width(self) -> float:
        return 26.0 * self.scale

    def _set_state(self, state: State, clip: str | None = None,
                   limit: float | None = None) -> None:
        p = self.pet
        p.state = state
        p.clip = clip or state.value
        p.state_time = 0.0
        p.clip_time = 0.0
        p.anchor_kind = "hips" if state is State.SIT_DANCE else "feet"
        if limit is None:
            lo, hi = C.DURATIONS.get(state.value, (2.0, 4.0))
            limit = self.rng.uniform(lo, hi)
        p.state_limit = limit

    def _start_fall(self, reason: str, clip: str = "fall") -> None:
        self.pet.airborne = True
        self.pet.ledge = None
        self.pet.last_event = reason
        self._set_state(State.FALL, clip, limit=999.0)

    def hop(self) -> None:
        """A standing jump: straight up, landing back where he took off."""
        p = self.pet
        if p.airborne:
            return
        p.vy = C.JUMP_VY * self.scale
        p.vx = 0.0
        self._start_fall("hopped", clip="jump_air")

    # -- decisions ---------------------------------------------------------

    def _choose_ground_action(self, snap: Snapshot) -> None:
        """Pick the next thing to do while standing on something."""
        p = self.pet
        lg = p.ledge
        wall_lo, wall_hi = snap.walk_span(p.x, p.y)
        near_wall = (
            p.x - wall_lo < self._half_width() * 2.2
            or wall_hi - p.x < self._half_width() * 2.2
        )
        on_window = lg is not None and lg.kind == "window"
        near_ledge_edge = lg is not None and (
            min(p.x - lg.x0, lg.x1 - p.x) < self._half_width() * 2.0
        )

        # dancing is the point of him, so it outweighs wandering and idling
        options: list[tuple[str, float]] = [
            ("walk", 2.4), ("dance", 6.0), ("idle", 0.8),
            ("crouch", 1.0), ("hop", 1.2),
        ]
        if on_window:
            options.append(("sit_dance", 3.2 if near_ledge_edge else 1.4))
        if near_wall:
            options.append(("lean", 2.6))
        options.append(("wave", 0.5))

        names = [o[0] for o in options]
        weights = [o[1] for o in options]
        choice = self.rng.choices(names, weights=weights, k=1)[0]

        if choice == "walk":
            # face away from a wall we are already up against
            if p.x - wall_lo < self._half_width() * 2.2:
                p.facing = 1
            elif wall_hi - p.x < self._half_width() * 2.2:
                p.facing = -1
            elif self.rng.random() < 0.35:
                p.facing = -p.facing
            self._set_state(State.WALK, "walk")
        elif choice == "dance":
            self._set_state(State.DANCE, self.rng.choice(DANCES))
        elif choice == "crouch":
            self._set_state(State.CROUCH, "crouch",
                            limit=CLIPS["crouch"].duration)
        elif choice == "hop":
            self.hop()
        elif choice == "sit_dance":
            self._set_state(State.SIT_DANCE, "sit_dance")
        elif choice == "lean":
            # lean into whichever wall is closer, facing it
            p.facing = -1 if (p.x - wall_lo) < (wall_hi - p.x) else 1
            self._set_state(State.LEAN, "lean")
        elif choice == "wave":
            self._set_state(State.WAVE, "wave")
        else:
            self._set_state(State.IDLE, "idle")

    def _obstacle_ahead(self, snap: Snapshot) -> Ledge | None:
        """A window edge in front of him that stands proud of his own ledge."""
        p = self.pet
        probe = p.x + p.facing * self._half_width() * 1.6
        best: Ledge | None = None
        for lg in snap.ledges:
            if lg.kind != "window" or lg.y >= p.y - 2:
                continue  # only ledges above his feet block him
            edge = lg.x0 if p.facing > 0 else lg.x1
            crossing = (p.x < edge <= probe) if p.facing > 0 else (probe <= edge < p.x)
            if crossing and (best is None or lg.y > best.y):
                best = lg
        return best

    def _walk_step(self, snap: Snapshot, dt: float) -> None:
        p = self.pet
        lg = p.ledge
        # exactly the travel his stride is authored for, so the planted foot does
        # not skate along the floor
        speed = CLIPS["walk"].speed * self.settings.pet_height
        nx = p.x + p.facing * speed * dt
        wall_lo, wall_hi = snap.walk_span(p.x, p.y)
        margin = self._half_width()

        # screen edge: lean on it for a beat, then turn round
        if nx - margin <= wall_lo or nx + margin >= wall_hi:
            p.x = min(max(nx, wall_lo + margin), wall_hi - margin)
            p.last_event = "hit screen edge"
            if self.rng.random() < 0.6:
                self._set_state(State.LEAN, "lean")
            else:
                p.facing = -p.facing
                self._set_state(State.WALK, "walk")
            return

        obstacle = self._obstacle_ahead(snap)
        if obstacle is not None:
            step_up = p.y - obstacle.y
            if step_up <= C.STEP_UP_MAX * self.scale:
                # small lip: hop up onto the window
                p.last_event = "climbed onto window"
                p.vy = C.JUMP_VY * self.scale
                # a walking pace alone will not carry him over the lip
                p.vx = p.facing * speed * C.CLIMB_LUNGE
                self.pet.airborne = True
                self.pet.ledge = None
                self._set_state(State.FALL, "fall", limit=999.0)
                return
            p.last_event = "turned at window edge"
            p.facing = -p.facing
            self._set_state(State.WALK, "walk")
            return

        # ran out of ledge?
        if lg is not None and not lg.contains_x(nx):
            nxt = self._adjacent_ledge(snap, lg, nx)
            if nxt is not None:
                p.ledge = nxt          # step across onto a touching ledge
                p.y = nxt.y
            elif self.rng.random() < 0.7:
                p.last_event = "turned at ledge end"
                p.facing = -p.facing
                self._set_state(State.WALK, "walk")
                return
            else:
                p.x = nx
                self._start_fall("walked off ledge")
                return
        p.x = nx

    def _adjacent_ledge(self, snap: Snapshot, lg: Ledge, nx: float) -> Ledge | None:
        """Another ledge at the same height that continues where this one stops."""
        for other in snap.ledges:
            if other is lg or abs(other.y - lg.y) > 2:
                continue
            if other.contains_x(nx):
                return other
        return None

    # -- main loop ---------------------------------------------------------

    def update(self, snap: Snapshot, dt: float) -> None:
        p = self.pet
        p.state_time += dt
        p.clip_time += dt

        if p.state is State.DRAGGED:
            return  # the app moves him while the mouse is down

        if p.airborne:
            self._update_airborne(snap, dt)
            return

        # the ledge under him can vanish at any moment: window moved, closed,
        # minimised, or another window came up over its title bar
        under = snap.ledge_under_feet(p.x, p.y, tol=4.0)
        if under is None:
            self._start_fall("ledge disappeared")
            return
        p.ledge = under

        if p.state is State.LAND:
            if p.clip_time >= CLIPS["land"].duration:
                self._choose_ground_action(snap)
            return

        if p.state is State.WALK:
            self._walk_step(snap, dt)

        if p.state_time >= p.state_limit and p.state is not State.LAND:
            self._choose_ground_action(snap)

    def _update_airborne(self, snap: Snapshot, dt: float) -> None:
        p = self.pet
        prev_y = p.y
        p.vy = min(p.vy + C.GRAVITY * self.scale * dt, C.TERMINAL_VY * self.scale)
        p.y += p.vy * dt
        p.x += p.vx * dt

        bounds = snap.desktop_bounds()
        margin = self._half_width()
        if p.x - margin < bounds.x0:
            p.x = bounds.x0 + margin
            p.vx = abs(p.vx) * 0.4
        elif p.x + margin > bounds.x1:
            p.x = bounds.x1 - margin
            p.vx = -abs(p.vx) * 0.4

        if p.vy > 0:  # only land while descending
            landing = self._landing_between(snap, prev_y, p.y, p.x)
            if landing is not None:
                p.y = landing.y
                p.vy = 0.0
                p.vx = 0.0
                p.airborne = False
                p.ledge = landing
                p.last_event = f"landed on {landing.kind}"
                self._set_state(State.LAND, "land", limit=CLIPS["land"].duration)
                return

        # fell past everything: put him back on the floor of the nearest monitor
        if p.y > bounds.y1 + 200:
            floor = snap.ledge_below(p.x, bounds.y0) or snap.ledges[0]
            p.y = floor.y
            p.vy = 0.0
            p.airborne = False
            p.ledge = floor
            self._set_state(State.LAND, "land", limit=CLIPS["land"].duration)

    def _landing_between(
        self, snap: Snapshot, y_from: float, y_to: float, x: float
    ) -> Ledge | None:
        """Highest ledge his feet crossed this frame."""
        best: Ledge | None = None
        for lg in snap.ledges:
            if not lg.contains_x(x):
                continue
            if y_from - 1e-6 <= lg.y <= y_to + 1e-6:
                if best is None or lg.y < best.y:
                    best = lg
        return best

    # -- outside pokes -----------------------------------------------------

    def start_drag(self) -> None:
        self.pet.vx = self.pet.vy = 0.0
        self.pet.airborne = True
        self.pet.ledge = None
        self._set_state(State.DRAGGED, "dragged", limit=999.0)

    def drag_to(self, x: float, y: float) -> None:
        self.pet.x, self.pet.y = x, y

    def release_drag(self, vx: float = 0.0, vy: float = 0.0) -> None:
        p = self.pet
        p.vx = vx * C.THROW_DAMPING
        p.vy = vy * C.THROW_DAMPING
        p.airborne = True
        p.last_event = "thrown"
        self._set_state(State.FALL, "fall", limit=999.0)

    def dance_now(self) -> None:
        if not self.pet.airborne:
            self._set_state(State.DANCE, self.rng.choice(DANCES))
