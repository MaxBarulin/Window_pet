"""Leg inverse kinematics, so motion can be authored as feet and hips.

Rotating the hip and knee directly (forward kinematics) is why the old animation
read as leg-waggling: the feet slid around, nothing was ever planted, and the body
never carried its own weight. Almost everything that makes movement look real -
a step, a squat, a jump, a bounce - is really a statement about *where the foot is*
and *where the hips are*, with the joint angles falling out of that.

So clips describe a `PoseSpec` (hips here, left foot there, right foot there) and
this module solves the two-bone chain hip -> knee -> ankle for the angles.

Two consequences worth knowing:

* The photo has him standing with his legs straight, which means the legs are at
  full reach and cannot extend any further. Every standing clip therefore sits at
  `BASE_CROUCH` below that, giving the knees some bend to work with - and a body
  that can rise as well as fall.
* Because the feet move independently of the pelvis, the point he stands on is no
  longer a fixed spot in the artwork. `ground_y` finds the lower shoe of the
  current pose, and the renderer anchors that to the ledge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .rigmath import Matrix, Pose, Rig, about, rotate

# How far below the photo's straight-legged stance a standing pose sits, in source
# pixels. Without it there is no headroom for the body to rise on a bounce.
BASE_CROUCH = 10.0

# How far the ankle will bend to keep the shoe flat. A real one runs out too.
ANKLE_RANGE = 38.0


@dataclass
class PoseSpec:
    """A pose described the way an animator thinks about it.

    Feet are offsets from where that foot rests in the photo; +y is down, +x is
    screen-right. `body` moves the hips, and the legs stretch or fold to keep the
    feet where they were put.
    """

    body: tuple[float, float] = (0.0, 0.0)
    lean: float = 0.0                      # pelvis rotation, degrees
    torso: float = 0.0
    head: float = 0.0
    arm_l_upper: float = 0.0
    arm_l_fore: float = 0.0
    arm_r_upper: float = 0.0
    arm_r_fore: float = 0.0
    foot_l: tuple[float, float] = (0.0, 0.0)
    foot_r: tuple[float, float] = (0.0, 0.0)
    # set only by clips that deliberately ignore the legs (sitting, falling)
    free_legs: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class _Leg:
    hip: tuple[float, float]
    knee: tuple[float, float]
    ankle: tuple[float, float]
    thigh_len: float
    shin_len: float
    thigh_dir: float      # degrees, in the photo
    shin_dir: float
    bend: float           # +1 puts the knee to screen-left of the hip->ankle line
    sole: tuple[float, float]
    sole_part: str        # the piece the sole belongs to: the shoe, if there is one
    foot: str | None      # name of the foot part, when the rig has one


def _angle(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dy, dx))


class Skeleton:
    """Solves a PoseSpec into joint angles for one rig."""

    def __init__(self, rig: Rig):
        self.rig = rig
        self.hips_pivot = rig.pivot("pelvis")
        self.legs = {
            "l": self._build_leg(rig, "l", bend=+1.0),
            "r": self._build_leg(rig, "r", bend=-1.0),
        }

    @staticmethod
    def _build_leg(rig: Rig, side: str, bend: float) -> _Leg:
        hip = rig.joints[f"hip_{side}"]
        knee = rig.joints[f"knee_{side}"]
        ankle = rig.joints[f"ankle_{side}"]
        foot = f"foot_{side}" if f"foot_{side}" in rig.parts else None
        sole_part = foot or f"shin_{side}"
        ox, oy = rig.parts[sole_part]["offset"]
        w, h = rig.parts[sole_part]["size"]
        return _Leg(
            hip=hip,
            knee=knee,
            ankle=ankle,
            thigh_len=math.dist(hip, knee),
            shin_len=math.dist(knee, ankle),
            thigh_dir=_angle(knee[0] - hip[0], knee[1] - hip[1]),
            shin_dir=_angle(ankle[0] - knee[0], ankle[1] - knee[1]),
            bend=bend,
            # the sole is the bottom of the shoe, bottom-centre of whichever piece
            # actually holds it
            sole=(ox + w / 2.0, float(oy + h)),
            sole_part=sole_part,
            foot=foot,
        )

    @property
    def reach(self) -> float:
        """Shortest fully-extended leg; the furthest a foot can get from its hip."""
        return min(leg.thigh_len + leg.shin_len for leg in self.legs.values())

    def solve_leg(
        self, side: str, hip: tuple[float, float], foot: tuple[float, float]
    ) -> tuple[float, float]:
        """Angles (thigh, shin) that put this leg's ankle on `foot`.

        Both are in the pelvis's frame, before any pelvis rotation is applied.
        """
        leg = self.legs[side]
        dx, dy = foot[0] - hip[0], foot[1] - hip[1]
        dist = math.hypot(dx, dy)
        l1, l2 = leg.thigh_len, leg.shin_len

        # out of reach: point the leg at the target, fully extended
        limit = l1 + l2 - 1e-3
        if dist >= limit:
            direction = _angle(dx, dy)
            return (direction - leg.thigh_dir, direction - leg.shin_dir
                    - (direction - leg.thigh_dir))
        dist = max(dist, abs(l1 - l2) + 1e-3)  # folded past itself

        # law of cosines on hip -> knee -> ankle
        cos_hip = (l1 * l1 + dist * dist - l2 * l2) / (2 * l1 * dist)
        cos_knee = (l1 * l1 + l2 * l2 - dist * dist) / (2 * l1 * l2)
        hip_open = math.degrees(math.acos(max(-1.0, min(1.0, cos_hip))))
        knee_inner = math.degrees(math.acos(max(-1.0, min(1.0, cos_knee))))

        direction = _angle(dx, dy)
        thigh_world = direction + leg.bend * hip_open
        shin_world = thigh_world - leg.bend * (180.0 - knee_inner)

        thigh_local = thigh_world - leg.thigh_dir
        shin_local = shin_world - leg.shin_dir - thigh_local
        return thigh_local, shin_local

    def resolve(self, spec: PoseSpec) -> Pose:
        """PoseSpec -> the joint angles the rig actually draws."""
        bx, by = spec.body
        angles = {
            "pelvis": spec.lean,
            "torso": spec.torso,
            "head": spec.head,
            "arm_l_upper": spec.arm_l_upper,
            "arm_l_fore": spec.arm_l_fore,
            "arm_r_upper": spec.arm_r_upper,
            "arm_r_fore": spec.arm_r_fore,
        }

        if spec.free_legs:
            angles.update(spec.free_legs)
            return Pose(angles=angles, offset=(bx, by))

        lean_rot = about(rotate(spec.lean), *self.hips_pivot)
        for side, delta in (("l", spec.foot_l), ("r", spec.foot_r)):
            leg = self.legs[side]
            hip = lean_rot.apply(*leg.hip)
            # the whole body is translated by `body` afterwards, so aim the foot
            # that much higher to leave it standing where the clip asked for
            target = (leg.ankle[0] + delta[0] - bx, leg.ankle[1] + delta[1] - by)
            thigh, shin = self.solve_leg(side, hip, target)
            angles[f"thigh_{side}"] = thigh - spec.lean
            angles[f"shin_{side}"] = shin
            if leg.foot:
                # The shoe is a separate piece, so it can stay flat instead of
                # pointing wherever the shin happens to point. Cancelling the
                # accumulated leg rotation is what an ankle does; the limit is
                # there because a real one runs out, and past it the shoe reads
                # as snapped off rather than planted.
                angles[leg.foot] = max(-ANKLE_RANGE, min(ANKLE_RANGE, -(thigh + shin)))
        return Pose(angles=angles, offset=(bx, by))

    # -- where he is standing ---------------------------------------------

    def ground_y(self, transforms: dict[str, Matrix]) -> float:
        """The lower shoe of the posed rig, in the transforms' own space.

        With the feet driven independently there is no fixed 'floor' point in the
        artwork any more, so the renderer asks the pose where his feet ended up.
        """
        return max(
            transforms[leg.sole_part].apply(*leg.sole)[1]
            for leg in self.legs.values()
        )
