"""Clips, and whether the motion they describe is actually good motion.

Clips return a `PoseSpec` - hips here, feet there - which `Skeleton` resolves into
joint angles. The tests worth having are about the *movement*: that a planted foot
stays planted, that a squat lowers the hips without lifting the feet, that a hop
leaves the floor. Those are the things that were wrong when it looked like leg
twitching rather than dancing.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pet.kinematics import BASE_CROUCH, PoseSpec, Skeleton
from pet.poses import CLIPS, DANCES, STRIDE, Clip, random_dance
from pet.rigmath import Rig

RIG_JSON = Path(__file__).resolve().parent.parent / "assets" / "rig.json"


@pytest.fixture(scope="module")
def rig() -> Rig:
    return Rig.load(RIG_JSON)


@pytest.fixture(scope="module")
def skel(rig: Rig) -> Skeleton:
    return Skeleton(rig)


def sample(clip: Clip, n: int = 48) -> list[PoseSpec]:
    return [clip.at(clip.duration * i / n) for i in range(n)]


def foot_world(skel: Skeleton, rig: Rig, spec: PoseSpec, side: str) -> tuple[float, float]:
    """Where a foot actually ends up once the pose is resolved."""
    # compose() subtracts the rig's ground point, so anchor there to get
    # plain source coordinates back
    tf = rig.compose(skel.resolve(spec), rig.ground, 1.0)
    return tf[f"shin_{side}"].apply(*skel.legs[side].ankle)


# ---------------------------------------------------------------------------
# basic sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(CLIPS))
def test_clip_values_are_finite_and_sane(name: str):
    clip = CLIPS[name]
    assert clip.duration > 0
    for spec in sample(clip):
        for field in ("lean", "torso", "head",
                      "arm_l_upper", "arm_l_fore", "arm_r_upper", "arm_r_fore"):
            v = getattr(spec, field)
            assert math.isfinite(v), (name, field)
            assert abs(v) <= 120.0, (name, field, v)
        for field in ("body", "foot_l", "foot_r"):
            for v in getattr(spec, field):
                assert math.isfinite(v) and abs(v) <= 260.0, (name, field, v)


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_every_pose_resolves_to_sane_joint_angles(name: str, skel: Skeleton):
    for spec in sample(CLIPS[name]):
        pose = skel.resolve(spec)
        for joint, ang in pose.angles.items():
            assert math.isfinite(ang), (name, joint)
            assert abs(ang) <= 150.0, (name, joint, ang)


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_nothing_is_ever_squashed(name: str, skel: Skeleton):
    """Pinching him sideways is what the fake spin did, and it read as a glitch."""
    for spec in sample(CLIPS[name]):
        assert skel.resolve(spec).squash == (1.0, 1.0), name


@pytest.mark.parametrize("name", sorted(n for n in CLIPS if CLIPS[n].loop))
def test_looping_clips_join_up(name: str, skel: Skeleton):
    clip = CLIPS[name]
    start = skel.resolve(clip.at(0.0))
    end = skel.resolve(clip.at(clip.duration * (1 - 1e-6)))
    for joint in set(start.angles) | set(end.angles):
        a, b = start.angles.get(joint, 0.0), end.angles.get(joint, 0.0)
        assert abs(a - b) < 3.0, (name, joint, a, b)
    for i in range(2):
        assert abs(start.offset[i] - end.offset[i]) < 1.5


# A hop launch and an impact absorb are *meant* to snap; everything else moving
# that fast would read as a glitch.
_IMPULSIVE = {"goat_hop", "land", "jump_air"}


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_motion_is_continuous(name: str, skel: Skeleton):
    """No frame-to-frame snap: a cutout rig with a jump looks broken."""
    limit = 30.0 if name in _IMPULSIVE else 16.0
    poses = [skel.resolve(s) for s in sample(CLIPS[name], 96)]
    for i in range(1, len(poses)):
        prev, cur = poses[i - 1], poses[i]
        for joint in set(prev.angles) | set(cur.angles):
            delta = abs(prev.angles.get(joint, 0.0) - cur.angles.get(joint, 0.0))
            assert delta < limit, (name, joint, i, delta)


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_clip_actually_animates(name: str, skel: Skeleton):
    poses = [skel.resolve(s) for s in sample(CLIPS[name], 24)]
    joints = {j for p in poses for j in p.angles}
    moved = any(
        max(p.angles.get(j, 0.0) for p in poses) - min(p.angles.get(j, 0.0) for p in poses) > 0.5
        for j in joints
    )
    if not moved:
        ys = [p.offset[1] for p in poses]
        moved = max(ys) - min(ys) > 0.5
    assert moved, name


def test_phase_wraps_for_looping_clips():
    clip = CLIPS["walk"]
    assert clip.at(0.25).body == pytest.approx(clip.at(0.25 + clip.duration).body)


def test_one_shot_clip_clamps_at_the_end():
    land = CLIPS["land"]
    assert not land.loop
    assert land.at(land.duration * 2).body == pytest.approx(land.at(land.duration * 9).body)


# ---------------------------------------------------------------------------
# the movement itself
# ---------------------------------------------------------------------------

def test_walk_keeps_the_stance_foot_planted(skel: Skeleton, rig: Rig):
    """The whole point of the rewrite.

    While a foot is in stance the body travels forward, so in body space the foot
    must slide backwards by exactly as much. Get that wrong and he moon-walks.
    """
    clip = CLIPS["walk"]
    travel_per_cycle = clip.speed * rig.height() * clip.duration
    n = 40
    for side, stance in (("l", (0.02, 0.48)), ("r", (0.52, 0.98))):
        positions = []
        for i in range(n):
            p = stance[0] + (stance[1] - stance[0]) * i / (n - 1)
            x, _ = foot_world(skel, rig, clip.at(clip.duration * p), side)
            # add how far the body has travelled by this phase
            positions.append(x + travel_per_cycle * p)
        drift = max(positions) - min(positions)
        assert drift < 26.0, f"{side} foot skates {drift:.1f}px during stance"


def test_walk_lifts_the_swinging_foot_clear(skel: Skeleton, rig: Rig):
    clip = CLIPS["walk"]
    lifts = []
    for i in range(40):
        p = 0.5 + 0.48 * i / 39          # left foot's swing
        _, y = foot_world(skel, rig, clip.at(clip.duration * p), "l")
        lifts.append(y)
    assert max(lifts) - min(lifts) > 25.0, "the swinging foot barely leaves the floor"


def test_walk_legs_alternate(skel: Skeleton):
    """Both legs swinging together would read as a hop, not a walk."""
    opposed = 0
    specs = sample(CLIPS["walk"], 32)
    for spec in specs:
        if spec.foot_l[0] * spec.foot_r[0] < 0:
            opposed += 1
    assert opposed > len(specs) * 0.6


def test_crouch_lowers_the_hips_without_lifting_the_feet(skel: Skeleton, rig: Rig):
    clip = CLIPS["crouch"]
    top = clip.at(0.0)
    bottom = clip.at(clip.duration * 0.5)
    assert bottom.body[1] - top.body[1] > 40.0, "barely a knee bend"

    # the feet stay on the floor throughout
    for p in (0.0, 0.25, 0.5, 0.75, 1.0):
        spec = clip.at(clip.duration * p)
        for side in ("l", "r"):
            _, y = foot_world(skel, rig, spec, side)
            assert abs(y - skel.legs[side].ankle[1]) < 6.0, (p, side, y)


def test_crouch_actually_bends_the_knees(skel: Skeleton):
    """Hips down with planted feet is only possible by folding the legs."""
    clip = CLIPS["crouch"]
    standing = skel.resolve(clip.at(0.0))
    squatting = skel.resolve(clip.at(clip.duration * 0.5))
    for side in ("l", "r"):
        bend = abs(squatting.angles[f"shin_{side}"] - standing.angles[f"shin_{side}"])
        assert bend > 12.0, (side, bend)


def test_hop_leaves_the_floor(skel: Skeleton, rig: Rig):
    """goat_hop must be a hop: both feet off the ground at the same time."""
    clip = CLIPS["goat_hop"]
    lowest = []
    for i in range(48):
        spec = clip.at(clip.duration * i / 48)
        pose = skel.resolve(spec)
        tf = rig.compose(pose, (0.0, 0.0), 1.0)
        lowest.append(skel.ground_y(tf))
    # relative to standing, his lower shoe must clearly rise at some point
    assert max(lowest) - min(lowest) > 60.0, "no air under him at any point"


def test_hop_crouches_before_it_launches(skel: Skeleton):
    """Anticipation: nobody jumps without dipping first."""
    clip = CLIPS["goat_hop"]
    early = [clip.at(clip.duration * p).body[1] for p in (0.0, 0.08, 0.16, 0.22)]
    assert max(early) > early[0] + 10.0, "no anticipation dip before the hop"


def test_dances_move_the_body_much_more_than_idle(skel: Skeleton):
    def travel(name: str) -> float:
        specs = sample(CLIPS[name], 48)
        xs = [s.body[0] for s in specs]
        ys = [s.body[1] for s in specs]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    idle = travel("idle")
    for name in DANCES:
        assert travel(name) > idle * 2.0, name


def test_dances_carry_weight_on_the_legs(skel: Skeleton):
    """A dance that never bends a knee is the twitching this replaced."""
    for name in DANCES:
        poses = [skel.resolve(s) for s in sample(CLIPS[name], 32)]
        for side in ("l", "r"):
            vals = [p.angles[f"shin_{side}"] for p in poses]
            assert max(vals) - min(vals) > 6.0, (name, side)


def test_standing_clips_sit_below_full_leg_extension(skel: Skeleton):
    """There has to be headroom for the body to rise, not only fall."""
    for name in ("idle", "walk", "hiphop", "shimmy", "step_touch"):
        lowest = min(s.body[1] for s in sample(CLIPS[name], 48))
        assert lowest >= BASE_CROUCH - 12.0, (name, lowest)
        assert lowest > 0.0, name


def test_walk_is_the_only_clip_that_travels():
    assert CLIPS["walk"].speed > 0
    for name, clip in CLIPS.items():
        if name != "walk":
            assert clip.speed == 0.0, name


def test_walk_speed_matches_its_stride(rig: Rig):
    """If travel and stride disagree the planted foot slides; keep them tied."""
    clip = CLIPS["walk"]
    travel = clip.speed * rig.height() * clip.duration
    assert travel == pytest.approx(2 * STRIDE, rel=0.02)


def test_stride_cannot_cross_his_feet(skel: Skeleton):
    gap = abs(skel.legs["r"].ankle[0] - skel.legs["l"].ankle[0])
    assert STRIDE < gap, "the swinging foot would cross the planted one"


def test_dance_arms_stay_outward():
    for name in DANCES:
        for spec in sample(CLIPS[name], 24):
            assert spec.arm_l_upper >= -22.0, (name, spec.arm_l_upper)
            assert spec.arm_r_upper <= 22.0, (name, spec.arm_r_upper)


def test_land_absorbs_then_recovers():
    land = CLIPS["land"]
    mid = land.at(land.duration * 0.5).body[1]
    end = land.at(land.duration).body[1]
    assert mid > end + 25.0, "no impact absorb"
    assert end == pytest.approx(BASE_CROUCH, abs=2.0)


def test_random_dance_only_returns_dances():
    import random

    rng = random.Random(7)
    for _ in range(50):
        assert random_dance(rng) in DANCES
        assert random_dance(rng) in CLIPS


def test_the_fake_spin_is_gone():
    assert "spin_step" not in CLIPS
    assert "spin_step" not in DANCES
