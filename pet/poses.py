"""Animation clips.

Each clip is a function of phase (0..1) returning a `PoseSpec`: where the hips are
and where each foot is planted, plus arm and torso angles. `kinematics.Skeleton`
turns that into joint angles.

Authoring this way is what makes the motion read as movement rather than as legs
waggling. A step is a foot that stays put on the floor while the body travels over
it; a squat is hips going down with the feet where they were; a bounce is the body
carrying its own weight. None of that is expressible by rotating joints directly,
which is what the first version did.

Rules of thumb used throughout:

* Feet are planted unless they are deliberately swinging. During a step the stance
  foot moves backwards in body space at exactly the speed the body moves forward,
  so it does not skate.
* The body drops before it rises. Every hop and bounce has an anticipation crouch.
* Arms oppose the legs, the torso opposes the hips, and the head opposes the torso.
* Nothing squashes horizontally. Pinching the character sideways to fake a turn
  reads as a rendering glitch, not as a spin.

Angles are degrees, clockwise-positive on screen; arm angles are deltas on the
rig's rest pose (the photo is a T-pose). Distances are source-photo pixels, so they
scale with him.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable

from .kinematics import BASE_CROUCH, PoseSpec

TAU = math.tau

# --- movement sizes, in source pixels (the figure is ~972 tall) -------------
STRIDE = 92.0         # one step; kept under the 104px gap between his feet
                      # so the swinging foot never crosses the planted one
STEP_LIFT = 44.0      # how high the swinging foot clears the floor
SQUAT_DEPTH = 62.0    # hips at the bottom of a full squat. A flat rig can only
                      # shorten a leg by swinging the knee out, so going deeper
                      # than this stops reading as a squat and starts reading
                      # as a frog.
BOUNCE = 30.0         # hips at the bottom of a dance bounce
SIDE_STEP = 68.0      # how far a side step travels


def _sin(phase: float, freq: float = 1.0, off: float = 0.0) -> float:
    return math.sin(TAU * (phase * freq + off))


def _cos(phase: float, freq: float = 1.0, off: float = 0.0) -> float:
    return math.cos(TAU * (phase * freq + off))


def _smooth(t: float) -> float:
    """Ease in and out; the shape a limb actually moves with."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _arc(t: float) -> float:
    """0 -> 1 -> 0 over t in 0..1, for lifts and hops."""
    return math.sin(math.pi * max(0.0, min(1.0, t)))


def _dip(phase: float, freq: float = 2.0) -> float:
    """1 at the bottom of each beat, 0 at the top."""
    return 0.5 - 0.5 * math.cos(TAU * phase * freq)


@dataclass
class Clip:
    """A named, loopable animation.

    speed is horizontal travel in pet-heights per second; 0 stays in place.
    """

    name: str
    duration: float
    fn: Callable[[float], PoseSpec]
    speed: float = 0.0
    loop: bool = True

    def at(self, t: float) -> PoseSpec:
        phase = (t / self.duration) % 1.0 if self.loop else min(t / self.duration, 1.0)
        return self.fn(phase)


# ---------------------------------------------------------------------------
# standing and walking
# ---------------------------------------------------------------------------

def _idle(p: float) -> PoseSpec:
    breath = _sin(p)
    shift = _sin(p, 0.5)
    # weight rocks gently from one foot to the other
    return PoseSpec(
        body=(4.0 * shift, BASE_CROUCH - 4.0 * breath),
        lean=1.4 * shift,
        torso=1.0 * breath - 1.2 * shift,
        head=-1.4 * breath + 2.0 * shift,
        arm_l_upper=2.0 + 1.6 * breath,
        arm_l_fore=1.6 + 1.2 * breath,
        arm_r_upper=-2.0 - 1.6 * breath,
        arm_r_fore=-1.6 - 1.2 * breath,
    )


def _step(p: float, lead: float) -> tuple[float, float]:
    """One foot's path over a full cycle. `lead` offsets which half it swings in.

    Stance runs the foot backwards at the speed the body travels forwards, so it
    stays put on the floor. Swing carries it forward again over an arc.
    """
    q = (p + lead) % 1.0
    if q < 0.5:                                  # planted
        t = q / 0.5
        return STRIDE * (0.5 - t), 0.0
    t = (q - 0.5) / 0.5                          # swinging
    return STRIDE * (_smooth(t) - 0.5), -STEP_LIFT * _arc(t)


def _walk(p: float) -> PoseSpec:
    left = _step(p, 0.0)
    right = _step(p, 0.5)
    # hips are highest over the planted leg, lowest as weight passes between feet
    rise = -6.0 * abs(_sin(p, 2.0))
    sway = 6.0 * _sin(p)
    swing = _sin(p)
    return PoseSpec(
        body=(sway, BASE_CROUCH + rise),
        lean=2.5 * swing,
        torso=-3.0 * swing,
        head=2.0 * swing - 1.5 * _sin(p, 2.0),
        arm_l_upper=-20.0 * swing,
        arm_l_fore=-8.0 * swing - 6.0,
        arm_r_upper=-20.0 * _sin(p, off=0.5),
        arm_r_fore=-8.0 * _sin(p, off=0.5) + 6.0,
        foot_l=left,
        foot_r=right,
    )


def _crouch(p: float) -> PoseSpec:
    """Down onto the haunches and back up, feet planted throughout."""
    depth = _arc(p) ** 0.85
    return PoseSpec(
        body=(0.0, BASE_CROUCH + SQUAT_DEPTH * depth),
        lean=4.0 * depth,
        torso=8.0 * depth,
        head=-12.0 * depth,
        # arms come forward for balance as he sinks
        arm_l_upper=30.0 * depth,
        arm_l_fore=20.0 * depth,
        arm_r_upper=-30.0 * depth,
        arm_r_fore=-20.0 * depth,
        # knees splay a little so the crouch reads from the front
        foot_l=(-7.0 * depth, 0.0),
        foot_r=(7.0 * depth, 0.0),
    )


# ---------------------------------------------------------------------------
# dances
# ---------------------------------------------------------------------------

def _hiphop(p: float) -> PoseSpec:
    """Two-step: weight drops onto one foot, then the other, arms pumping."""
    drop = _dip(p, 2.0)
    step = _sin(p)
    pump = _sin(p, 2.0)
    # the free foot lifts as the weight goes onto the other one
    lift_l = max(0.0, -step)
    lift_r = max(0.0, step)
    return PoseSpec(
        body=(14.0 * step, BASE_CROUCH + BOUNCE * drop),
        lean=6.0 * step,
        torso=-7.0 * step + 3.0 * pump,
        head=6.0 * step - 5.0 * pump,
        arm_l_upper=16.0 + 30.0 * max(0.0, pump),
        arm_l_fore=26.0 + 24.0 * max(0.0, pump),
        arm_r_upper=-16.0 - 30.0 * max(0.0, -pump),
        arm_r_fore=-26.0 - 24.0 * max(0.0, -pump),
        foot_l=(-6.0 * step, -26.0 * lift_l),
        foot_r=(-6.0 * step, -26.0 * lift_r),
    )


def _step_touch(p: float) -> PoseSpec:
    """Step out, bring the other foot to meet it, and back. Real weight transfer.

    This replaces the old fake spin, which pinched him sideways and read as a
    glitch rather than a turn.
    """
    # first half steps to the right, second half back to the left
    out = _sin(p)
    drop = _dip(p, 2.0)
    going_right = out >= 0
    reach = SIDE_STEP * abs(out)
    if going_right:
        foot_l = (0.0, 0.0)
        foot_r = (reach, -22.0 * _arc(abs(out)))
    else:
        foot_l = (-reach, -22.0 * _arc(abs(out)))
        foot_r = (0.0, 0.0)
    return PoseSpec(
        body=(reach * 0.45 * (1 if going_right else -1), BASE_CROUCH + 22.0 * drop),
        lean=7.0 * out,
        torso=-8.0 * out,
        head=9.0 * out,
        arm_l_upper=30.0 + 26.0 * max(0.0, out),
        arm_l_fore=34.0 + 12.0 * max(0.0, out),
        arm_r_upper=-30.0 - 26.0 * max(0.0, -out),
        arm_r_fore=-34.0 - 12.0 * max(0.0, -out),
        foot_l=foot_l,
        foot_r=foot_r,
    )


def _goat_hop(p: float) -> PoseSpec:
    """Daft little two-footed hops, knees snapping up in turn.

    Anticipation crouch, push off, tuck, land and absorb - the whole hop is in the
    body height, which is why it reads as a hop rather than a leg twitch.
    """
    # crouch for the first fifth, airborne through the middle, absorb at the end
    if p < 0.22:
        t = p / 0.22
        height = -SQUAT_DEPTH * 0.55 * _smooth(t)
        tuck = 0.0
    elif p < 0.82:
        t = (p - 0.22) / 0.60
        height = 118.0 * _arc(t) - SQUAT_DEPTH * 0.55 * (1 - _smooth(min(1.0, t * 3)))
        tuck = _arc(t)
    else:
        t = (p - 0.82) / 0.18
        height = -SQUAT_DEPTH * 0.5 * _arc(t)
        tuck = 0.0
    side = _sin(p)
    knee = max(0.0, _sin(p, 2.0))
    other = max(0.0, -_sin(p, 2.0))
    return PoseSpec(
        body=(10.0 * side, BASE_CROUCH - height),
        lean=8.0 * side,
        torso=-7.0 * side,
        head=12.0 * side,
        arm_l_upper=34.0 + 30.0 * tuck,
        arm_l_fore=38.0 + 20.0 * tuck,
        arm_r_upper=-34.0 - 30.0 * tuck,
        arm_r_fore=-38.0 - 20.0 * tuck,
        # knees come up alternately while he is off the floor
        foot_l=(-10.0 * side, -(54.0 * tuck * (0.35 + 0.65 * knee))),
        foot_r=(-10.0 * side, -(54.0 * tuck * (0.35 + 0.65 * other))),
    )


def _shimmy(p: float) -> PoseSpec:
    """Fast shoulder shake over a low heel bounce."""
    shake = _sin(p, 4.0)
    drop = _dip(p, 4.0)
    sway = _sin(p)
    return PoseSpec(
        body=(8.0 * sway, BASE_CROUCH + 20.0 * drop),
        lean=5.0 * sway,
        torso=9.0 * shake,
        head=-11.0 * shake + 4.0 * sway,
        arm_l_upper=42.0 + 16.0 * shake,
        arm_l_fore=44.0 - 14.0 * shake,
        arm_r_upper=-42.0 + 16.0 * shake,
        arm_r_fore=-44.0 - 14.0 * shake,
        # heels lift alternately, so the bounce has somewhere to go
        foot_l=(0.0, -16.0 * max(0.0, shake)),
        foot_r=(0.0, -16.0 * max(0.0, -shake)),
    )


def _contemporary(p: float) -> PoseSpec:
    """Slow, deep, extended: a long sink into one hip and a sweeping arm."""
    sway = _sin(p)
    reach = _sin(p, off=0.25)
    sink = 0.5 + 0.5 * _cos(p, 2.0)
    return PoseSpec(
        body=(22.0 * sway, BASE_CROUCH + 34.0 * sink),
        lean=10.0 * sway,
        torso=-12.0 * sway,
        head=8.0 * sway - 6.0 * reach,
        arm_l_upper=30.0 + 60.0 * max(0.0, reach),
        arm_l_fore=20.0 + 34.0 * max(0.0, reach),
        arm_r_upper=-30.0 - 60.0 * max(0.0, -reach),
        arm_r_fore=-20.0 - 34.0 * max(0.0, -reach),
        # the trailing foot slides out as he sinks
        foot_l=(-16.0 * max(0.0, -sway), 0.0),
        foot_r=(16.0 * max(0.0, sway), 0.0),
    )


# ---------------------------------------------------------------------------
# environment and reactions
# ---------------------------------------------------------------------------

def _sit_dance(p: float) -> PoseSpec:
    """Perched on a window edge: legs dangle and kick, upper body grooves.

    The legs are posed directly here - there is no floor to plant them on.
    """
    kick = _sin(p)
    groove = _sin(p, 2.0)
    return PoseSpec(
        body=(0.0, 0.0),
        lean=3.0 * kick,
        torso=-6.0 * kick + 3.0 * groove,
        head=6.0 * kick - 4.0 * groove,
        arm_l_upper=12.0 + 10.0 * groove,
        arm_l_fore=16.0 + 12.0 * groove,
        arm_r_upper=-12.0 + 10.0 * groove,
        arm_r_fore=-16.0 + 12.0 * groove,
        free_legs={
            "thigh_l": 12.0 * kick,
            "shin_l": -22.0 - 20.0 * kick,
            "thigh_r": -12.0 * kick,
            "shin_r": -22.0 + 20.0 * kick,
        },
    )


def _lean(p: float) -> PoseSpec:
    """Shoulder against the screen edge, weight on one leg, easy bob.

    Authored leaning to screen-left; the behaviour layer flips him for the other
    side.
    """
    bob = _sin(p)
    return PoseSpec(
        body=(-18.0, BASE_CROUCH + 16.0 + 5.0 * bob),
        lean=-9.0,
        torso=5.0 + 2.0 * bob,
        head=7.0 - 3.0 * bob,
        arm_l_upper=-16.0,
        arm_l_fore=-26.0 - 4.0 * bob,
        arm_r_upper=-6.0 + 3.0 * bob,
        arm_r_fore=-12.0,
        # near foot planted, far leg crossed over it
        foot_l=(-14.0, 0.0),
        foot_r=(-46.0, -8.0),
    )


def _wave(p: float) -> PoseSpec:
    wave = _sin(p, 3.0)
    shift = _sin(p, 1.0)
    return PoseSpec(
        body=(5.0 * shift, BASE_CROUCH - 2.0),
        lean=2.0 * shift,
        torso=-3.0,
        head=5.0 + 3.0 * wave,
        arm_l_upper=104.0,
        arm_l_fore=28.0 + 26.0 * wave,
        arm_r_upper=-8.0,
        arm_r_fore=-14.0,
    )


def _jump_air(p: float) -> PoseSpec:
    """Held while the physics carries him through the air."""
    flail = _sin(p, 2.0)
    return PoseSpec(
        body=(0.0, BASE_CROUCH - 10.0),
        torso=4.0 * flail,
        head=-8.0,
        arm_l_upper=74.0 + 12.0 * flail,
        arm_l_fore=34.0,
        arm_r_upper=-74.0 + 12.0 * flail,
        arm_r_fore=-34.0,
        # trailing legs, one tucked more than the other
        foot_l=(-24.0, -54.0),
        foot_r=(20.0, -30.0),
    )


def _fall(p: float) -> PoseSpec:
    flail = _sin(p, 2.0)
    return PoseSpec(
        body=(0.0, BASE_CROUCH - 6.0),
        torso=3.0 * flail,
        head=-7.0,
        arm_l_upper=88.0 + 14.0 * flail,
        arm_l_fore=30.0,
        arm_r_upper=-88.0 + 14.0 * flail,
        arm_r_fore=-30.0,
        foot_l=(-30.0, -30.0),
        foot_r=(26.0, -16.0),
    )


def _land(p: float) -> PoseSpec:
    """Absorb the impact: knees fold, then push back up to standing."""
    k = _arc(min(1.0, p)) ** 0.7
    return PoseSpec(
        body=(0.0, BASE_CROUCH + (SQUAT_DEPTH * 0.78) * k),
        lean=3.0 * k,
        torso=11.0 * k,
        head=-14.0 * k,
        arm_l_upper=40.0 * k,
        arm_l_fore=26.0 * k,
        arm_r_upper=-40.0 * k,
        arm_r_fore=-26.0 * k,
        foot_l=(-9.0 * k, 0.0),
        foot_r=(9.0 * k, 0.0),
    )


def _dragged(p: float) -> PoseSpec:
    swing = _sin(p)
    return PoseSpec(
        body=(0.0, 0.0),
        lean=6.0 * swing,
        torso=4.0 * swing,
        head=-5.0 * swing,
        arm_l_upper=76.0 + 12.0 * swing,
        arm_l_fore=26.0,
        arm_r_upper=-76.0 + 12.0 * swing,
        arm_r_fore=-26.0,
        free_legs={
            "thigh_l": 10.0 + 8.0 * swing,
            "shin_l": -20.0,
            "thigh_r": -10.0 + 8.0 * swing,
            "shin_r": -20.0,
        },
    )


# figure height in source px; used to convert a stride into travel speed
_FIGURE_H = 972.0
_WALK_DURATION = 1.02
# two strides per cycle, so he covers exactly what his feet say he does
_WALK_SPEED = (2 * STRIDE) / _FIGURE_H / _WALK_DURATION

CLIPS: dict[str, Clip] = {
    "idle": Clip("idle", 3.6, _idle),
    "walk": Clip("walk", _WALK_DURATION, _walk, speed=_WALK_SPEED),
    "crouch": Clip("crouch", 1.5, _crouch),
    "hiphop": Clip("hiphop", 1.15, _hiphop),
    "step_touch": Clip("step_touch", 1.8, _step_touch),
    "goat_hop": Clip("goat_hop", 0.92, _goat_hop),
    "shimmy": Clip("shimmy", 1.6, _shimmy),
    "contemporary": Clip("contemporary", 3.4, _contemporary),
    "sit_dance": Clip("sit_dance", 1.5, _sit_dance),
    "lean": Clip("lean", 2.8, _lean),
    "wave": Clip("wave", 1.9, _wave),
    "jump_air": Clip("jump_air", 1.0, _jump_air),
    "fall": Clip("fall", 0.9, _fall),
    "land": Clip("land", 0.5, _land, loop=False),
    "dragged": Clip("dragged", 1.4, _dragged),
}

DANCES = ("hiphop", "step_touch", "goat_hop", "shimmy", "contemporary")


def random_dance(rng: random.Random | None = None) -> str:
    return (rng or random).choice(DANCES)
