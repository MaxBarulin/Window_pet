"""Animation clips for the pet.

Clips are procedural: each is a function of phase (0..1) returning a Pose. That
keeps motion smooth at any frame rate, makes it cheap to vary, and means the whole
animation set is testable without rendering anything.

Angles are *deltas on top of the rig's rest pose*. The source photo is a T-pose, so
the rig itself carries the "arms down" offset (see rest_angles in rig.json) and a
clip angle of 0 means a relaxed standing arm.

Sign convention, with screen y pointing down: a positive angle rotates a part
clockwise, so a limb hanging below its pivot swings towards screen-left. For the
arms the clips use +ve on the left arm and -ve on the right, which moves both
outwards, away from the body.

Amplitudes are deliberately moderate. A 2D cutout rig starts to look rubbery past
roughly 40 degrees at a joint, and the shoulder is the first thing to give.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable

from .rigmath import Pose

TAU = math.tau


def _sin(phase: float, freq: float = 1.0, off: float = 0.0) -> float:
    return math.sin(TAU * (phase * freq + off))


def _cos(phase: float, freq: float = 1.0, off: float = 0.0) -> float:
    return math.cos(TAU * (phase * freq + off))


def _bounce(phase: float, freq: float = 2.0) -> float:
    """0..1, peaking like a footfall - sharper than a sine."""
    return abs(math.sin(math.pi * phase * freq))


@dataclass
class Clip:
    """A named, loopable animation.

    speed is horizontal travel in pet-heights per second; 0 stays in place.
    """

    name: str
    duration: float
    fn: Callable[[float], Pose]
    speed: float = 0.0
    loop: bool = True

    def at(self, t: float) -> Pose:
        phase = (t / self.duration) % 1.0 if self.loop else min(t / self.duration, 1.0)
        return self.fn(phase)


# ---------------------------------------------------------------------------
# idle / locomotion
# ---------------------------------------------------------------------------

def _idle(p: float) -> Pose:
    breath = _sin(p)
    sway = _sin(p, 0.5)
    return Pose(
        angles={
            "torso": 1.2 * breath,
            "head": -1.6 * breath + 1.2 * sway,
            "arm_l_upper": 1.6 + 1.4 * breath,
            "arm_l_fore": 1.2 + 1.0 * breath,
            "arm_r_upper": -1.6 - 1.4 * breath,
            "arm_r_fore": -1.2 - 1.0 * breath,
            "pelvis": 0.5 * sway,
        },
        offset=(0.0, -1.2 * breath),
        squash=(1.0, 1.0 + 0.004 * breath),
    )


def _walk(p: float) -> Pose:
    # one full cycle = two steps
    swing = _sin(p)
    opp = _sin(p, off=0.5)
    lift = _bounce(p, 2.0)
    return Pose(
        angles={
            "thigh_l": 16.0 * swing,
            "shin_l": -max(0.0, 20.0 * _sin(p, off=0.12)),
            "thigh_r": 16.0 * opp,
            "shin_r": -max(0.0, 20.0 * _sin(p, off=0.62)),
            "arm_l_upper": -10.0 * swing,
            "arm_l_fore": -4.0 * swing - 3.0,
            "arm_r_upper": -10.0 * opp,
            "arm_r_fore": -4.0 * opp + 3.0,
            "torso": 2.0 * _sin(p, 2.0),
            "head": -1.4 * _sin(p, 2.0),
            "pelvis": 1.4 * swing,
        },
        offset=(0.0, -3.0 * lift),
    )


# ---------------------------------------------------------------------------
# dances
# ---------------------------------------------------------------------------

def _hiphop(p: float) -> Pose:
    """Two-step with a hard bounce on the beat and alternating arm pumps."""
    beat = _bounce(p, 2.0)
    step = _sin(p)
    pump = _sin(p, 2.0)
    return Pose(
        angles={
            "pelvis": 4.0 * step,
            "torso": -5.0 * step + 2.0 * pump,
            "head": 4.0 * step - 3.5 * pump,
            # elbows stay bent, like an actual two-step
            "arm_l_upper": 12.0 + 16.0 * max(0.0, pump),
            "arm_l_fore": 20.0 + 14.0 * max(0.0, pump),
            "arm_r_upper": -12.0 - 16.0 * max(0.0, -pump),
            "arm_r_fore": -20.0 - 14.0 * max(0.0, -pump),
            "thigh_l": 8.0 * step,
            "shin_l": -14.0 * beat,
            "thigh_r": -8.0 * step,
            "shin_r": -14.0 * (1.0 - beat),
        },
        offset=(3.0 * step, -9.0 * beat),
        squash=(1.0 + 0.03 * (1 - beat), 1.0 - 0.045 * (1 - beat)),
    )


def _contemporary(p: float) -> Pose:
    """Slow, extended, one arm sweeping up and over."""
    sway = _sin(p)
    reach = _sin(p, off=0.25)
    rise = 0.5 + 0.5 * _cos(p)
    return Pose(
        angles={
            "pelvis": 6.0 * sway,
            "torso": -8.0 * sway,
            "head": 6.0 * sway - 4.0 * reach,
            "arm_l_upper": 24.0 + 42.0 * max(0.0, reach),
            "arm_l_fore": 14.0 + 26.0 * max(0.0, reach),
            "arm_r_upper": -24.0 - 42.0 * max(0.0, -reach),
            "arm_r_fore": -14.0 - 26.0 * max(0.0, -reach),
            "thigh_l": 10.0 * sway,
            "shin_l": -8.0 * rise,
            "thigh_r": 10.0 * sway,
            "shin_r": -8.0 * (1.0 - rise),
        },
        offset=(5.0 * sway, -4.0 * rise),
        squash=(1.0, 1.0 + 0.012 * rise),
    )


def _playful(p: float) -> Pose:
    """Bouncy little hops with the arms up and the head bobbing."""
    hop = _bounce(p, 2.0)
    tilt = _sin(p)
    return Pose(
        angles={
            "pelvis": 3.0 * tilt,
            "torso": 4.0 * tilt,
            "head": -7.0 * tilt,
            "arm_l_upper": 46.0 + 16.0 * hop,
            "arm_l_fore": 26.0 + 16.0 * hop,
            "arm_r_upper": -46.0 - 16.0 * hop,
            "arm_r_fore": -26.0 - 16.0 * hop,
            "thigh_l": 6.0 * tilt - 4.0,
            "shin_l": -24.0 * (1.0 - hop),
            "thigh_r": -6.0 * tilt - 4.0,
            "shin_r": -24.0 * (1.0 - hop),
        },
        offset=(2.0 * tilt, -14.0 * hop),
        squash=(1.0 + 0.05 * (1 - hop), 1.0 - 0.07 * (1 - hop)),
    )


def _goat_hop(p: float) -> Pose:
    """Daft little side-to-side hop, knees snapping up in turn.

    The silliest thing the rig can do without the joints going rubbery: the knee
    lift does the work, so the arms can stay loose rather than flailing.
    """
    hop = _bounce(p, 2.0)
    side = _sin(p)
    lift = max(0.0, _sin(p, 2.0))
    lift_other = max(0.0, -_sin(p, 2.0))
    return Pose(
        angles={
            "pelvis": 7.0 * side,
            "torso": -6.0 * side,
            "head": 10.0 * side - 4.0 * hop,
            "arm_l_upper": 30.0 + 18.0 * lift,
            "arm_l_fore": 34.0 + 16.0 * lift,
            "arm_r_upper": -30.0 - 18.0 * lift_other,
            "arm_r_fore": -34.0 - 16.0 * lift_other,
            # one knee up, then the other
            "thigh_l": 26.0 * lift,
            "shin_l": -38.0 * lift,
            "thigh_r": -26.0 * lift_other,
            "shin_r": -38.0 * lift_other,
        },
        offset=(9.0 * side, -13.0 * hop),
        squash=(1.0 + 0.04 * (1 - hop), 1.0 - 0.05 * (1 - hop)),
    )


def _shimmy(p: float) -> Pose:
    """Fast shoulder shimmy with a low bounce - reads as showing off."""
    shake = _sin(p, 4.0)
    bounce = _bounce(p, 4.0)
    sway = _sin(p)
    return Pose(
        angles={
            "pelvis": 5.0 * sway,
            "torso": 7.0 * shake,
            "head": -9.0 * shake + 3.0 * sway,
            "arm_l_upper": 38.0 + 14.0 * shake,
            "arm_l_fore": 40.0 - 12.0 * shake,
            "arm_r_upper": -38.0 + 14.0 * shake,
            "arm_r_fore": -40.0 - 12.0 * shake,
            "thigh_l": 7.0 * sway,
            "shin_l": -12.0 * bounce,
            "thigh_r": -7.0 * sway,
            "shin_r": -12.0 * (1.0 - bounce),
        },
        offset=(3.0 * sway, -6.0 * bounce),
        squash=(1.0 + 0.025 * (1 - bounce), 1.0 - 0.035 * (1 - bounce)),
    )


def _spin_step(p: float) -> Pose:
    """A cheeky pivot: narrows at the halfway point to suggest a turn.

    A cutout cannot really rotate in 3D, so the squash stays mild - pinching it
    much further stops reading as a spin and starts reading as a glitch.
    """
    turn = abs(_cos(p, 0.5))
    step = _sin(p, 2.0)
    return Pose(
        angles={
            "pelvis": 6.0 * step,
            "torso": -6.0 * step,
            "head": 8.0 * step,
            "arm_l_upper": 26.0 + 16.0 * step,
            "arm_l_fore": 20.0,
            "arm_r_upper": -26.0 + 16.0 * step,
            "arm_r_fore": -20.0,
            "thigh_l": 12.0 * step,
            "shin_l": -10.0,
            "thigh_r": -12.0 * step,
            "shin_r": -10.0,
        },
        offset=(0.0, -5.0 * abs(step)),
        squash=(0.62 + 0.38 * turn, 1.0),
    )


# ---------------------------------------------------------------------------
# environment interactions
# ---------------------------------------------------------------------------

def _sit_dance(p: float) -> Pose:
    """Perched on a window edge: legs dangle and kick, upper body grooves.

    The behaviour layer anchors his hips to the ledge for this clip, so the legs
    hang below the edge instead of standing on it.
    """
    kick = _sin(p)
    groove = _sin(p, 2.0)
    return Pose(
        angles={
            "pelvis": 2.5 * kick,
            "torso": -5.0 * kick + 2.0 * groove,
            "head": 5.0 * kick - 3.0 * groove,
            "arm_l_upper": 10.0 + 8.0 * groove,
            "arm_l_fore": 12.0 + 10.0 * groove,
            "arm_r_upper": -10.0 + 8.0 * groove,
            "arm_r_fore": -12.0 + 10.0 * groove,
            "thigh_l": 10.0 * kick,
            "shin_l": -18.0 - 16.0 * kick,
            "thigh_r": -10.0 * kick,
            "shin_r": -18.0 + 16.0 * kick,
        },
    )


def _lean(p: float) -> Pose:
    """Shoulder against the screen edge, one knee bent, easy bob.

    Authored for leaning to screen-left; the behaviour layer flips him when the
    wall is on the other side.
    """
    bob = _sin(p)
    return Pose(
        angles={
            "pelvis": -9.0,
            "torso": 4.0 + 1.5 * bob,
            "head": 6.0 - 2.0 * bob,
            "arm_l_upper": -14.0,
            "arm_l_fore": -24.0 - 3.0 * bob,
            "arm_r_upper": -5.0 + 2.0 * bob,
            "arm_r_fore": -10.0,
            "thigh_l": 6.0,
            "shin_l": -10.0,
            "thigh_r": -12.0,
            "shin_r": -24.0,
        },
        offset=(0.0, -1.0 * bob),
    )


def _wave(p: float) -> Pose:
    wave = _sin(p, 3.0)
    return Pose(
        angles={
            "torso": -2.0,
            "head": 4.0 + 2.0 * wave,
            "arm_l_upper": 100.0,
            "arm_l_fore": 26.0 + 22.0 * wave,
            "arm_r_upper": -6.0,
            "arm_r_fore": -10.0,
            "thigh_l": 2.0,
            "thigh_r": -2.0,
        },
    )


def _fall(p: float) -> Pose:
    flail = _sin(p, 2.0)
    return Pose(
        angles={
            "torso": 3.0 * flail,
            "head": -6.0,
            "arm_l_upper": 84.0 + 10.0 * flail,
            "arm_l_fore": 26.0,
            "arm_r_upper": -84.0 + 10.0 * flail,
            "arm_r_fore": -26.0,
            "thigh_l": 15.0,
            "shin_l": -18.0,
            "thigh_r": -13.0,
            "shin_r": -9.0,
        },
    )


def _land(p: float) -> Pose:
    """One-shot squash-and-recover on impact."""
    k = math.sin(math.pi * min(1.0, p)) ** 1.5
    return Pose(
        angles={
            "torso": 7.0 * k,
            "head": -9.0 * k,
            "arm_l_upper": 18.0 + 24.0 * k,
            "arm_l_fore": 14.0 + 14.0 * k,
            "arm_r_upper": -18.0 - 24.0 * k,
            "arm_r_fore": -14.0 - 14.0 * k,
            "thigh_l": 12.0 * k,
            "shin_l": -28.0 * k,
            "thigh_r": -12.0 * k,
            "shin_r": -28.0 * k,
        },
        offset=(0.0, 13.0 * k),
        squash=(1.0 + 0.09 * k, 1.0 - 0.13 * k),
    )


def _dragged(p: float) -> Pose:
    swing = _sin(p)
    return Pose(
        angles={
            "pelvis": 5.0 * swing,
            "torso": 3.0 * swing,
            "head": -4.0 * swing,
            "arm_l_upper": 70.0 + 10.0 * swing,
            "arm_l_fore": 22.0,
            "arm_r_upper": -70.0 + 10.0 * swing,
            "arm_r_fore": -22.0,
            "thigh_l": 8.0 + 6.0 * swing,
            "shin_l": -16.0,
            "thigh_r": -8.0 + 6.0 * swing,
            "shin_r": -16.0,
        },
    )


CLIPS: dict[str, Clip] = {
    "idle": Clip("idle", 3.6, _idle),
    "walk": Clip("walk", 1.05, _walk, speed=0.42),
    "hiphop": Clip("hiphop", 1.15, _hiphop),
    "contemporary": Clip("contemporary", 3.4, _contemporary),
    "playful": Clip("playful", 0.95, _playful),
    "goat_hop": Clip("goat_hop", 1.0, _goat_hop),
    "shimmy": Clip("shimmy", 1.6, _shimmy),
    "spin_step": Clip("spin_step", 1.6, _spin_step),
    "sit_dance": Clip("sit_dance", 1.5, _sit_dance),
    "lean": Clip("lean", 2.8, _lean),
    "wave": Clip("wave", 1.9, _wave),
    "fall": Clip("fall", 0.9, _fall),
    "land": Clip("land", 0.42, _land, loop=False),
    "dragged": Clip("dragged", 1.4, _dragged),
}

DANCES = ("hiphop", "contemporary", "playful", "goat_hop", "shimmy", "spin_step")


def random_dance(rng: random.Random | None = None) -> str:
    return (rng or random).choice(DANCES)
