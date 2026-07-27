import math

import pytest

from pet.poses import CLIPS, DANCES, Clip, random_dance
from pet.rigmath import Pose


def sample(clip: Clip, n: int = 48):
    return [clip.at(clip.duration * i / n) for i in range(n)]


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_clip_values_are_finite_and_sane(name: str):
    clip = CLIPS[name]
    assert clip.duration > 0
    for pose in sample(clip):
        assert isinstance(pose, Pose)
        for joint, ang in pose.angles.items():
            assert math.isfinite(ang), (name, joint)
            # a cutout rig tears past ~120 degrees at a single joint
            assert abs(ang) <= 120.0, (name, joint, ang)
        for v in pose.offset:
            assert math.isfinite(v) and abs(v) <= 60.0
        sx, sy = pose.squash
        assert 0.5 <= sx <= 1.6 and 0.5 <= sy <= 1.6, (name, pose.squash)


@pytest.mark.parametrize("name", sorted(n for n in CLIPS if CLIPS[n].loop))
def test_looping_clips_join_up(name: str):
    """The last frame must flow into the first, or the loop visibly jumps."""
    clip = CLIPS[name]
    start = clip.at(0.0)
    end = clip.at(clip.duration * (1 - 1e-6))
    for joint in set(start.angles) | set(end.angles):
        a = start.angles.get(joint, 0.0)
        b = end.angles.get(joint, 0.0)
        assert abs(a - b) < 2.0, (name, joint, a, b)
    for i in range(2):
        assert abs(start.offset[i] - end.offset[i]) < 1.0
        assert abs(start.squash[i] - end.squash[i]) < 0.02


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_motion_is_continuous(name: str):
    """No frame-to-frame snap: a cutout rig with a jump looks broken."""
    clip = CLIPS[name]
    poses = sample(clip, 96)
    for i in range(1, len(poses)):
        prev, cur = poses[i - 1], poses[i]
        for joint in set(prev.angles) | set(cur.angles):
            delta = abs(prev.angles.get(joint, 0.0) - cur.angles.get(joint, 0.0))
            assert delta < 14.0, (name, joint, i, delta)


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_clip_actually_animates(name: str):
    """Every clip has to move something, or it is dead weight."""
    poses = sample(CLIPS[name], 24)
    moved = False
    for joint in {j for p in poses for j in p.angles}:
        vals = [p.angles.get(joint, 0.0) for p in poses]
        if max(vals) - min(vals) > 0.5:
            moved = True
            break
    if not moved:
        offs = [p.offset for p in poses]
        moved = max(o[1] for o in offs) - min(o[1] for o in offs) > 0.5
    assert moved, name


def test_phase_wraps_for_looping_clips():
    clip = CLIPS["walk"]
    a = clip.at(0.25)
    b = clip.at(0.25 + clip.duration)
    assert a.angles == pytest.approx(b.angles)


def test_one_shot_clip_clamps_at_the_end():
    land = CLIPS["land"]
    assert not land.loop
    a = land.at(land.duration * 2)
    b = land.at(land.duration * 9)
    assert a.angles == pytest.approx(b.angles)


def test_walk_is_the_only_clip_that_travels():
    assert CLIPS["walk"].speed > 0
    for name, clip in CLIPS.items():
        if name != "walk":
            assert clip.speed == 0.0, name


def test_walk_legs_are_in_opposition():
    """Both thighs swinging together would read as a hop, not a walk."""
    clip = CLIPS["walk"]
    products = []
    for pose in sample(clip, 32):
        products.append(pose.angles["thigh_l"] * pose.angles["thigh_r"])
    assert sum(1 for p in products if p < 0) > len(products) * 0.6


def test_dance_arms_stay_mirrored_outwards():
    """Left arm +ve, right arm -ve: both move away from the body."""
    for name in DANCES:
        for pose in sample(CLIPS[name], 24):
            left = pose.angles.get("arm_l_upper", 0.0)
            right = pose.angles.get("arm_r_upper", 0.0)
            assert left >= -20.0, (name, left)
            assert right <= 20.0, (name, right)


def test_dances_are_livelier_than_idle():
    def span(name: str) -> float:
        poses = sample(CLIPS[name], 48)
        joints = {j for p in poses for j in p.angles}
        return max(
            max(p.angles.get(j, 0.0) for p in poses)
            - min(p.angles.get(j, 0.0) for p in poses)
            for j in joints
        )

    idle_span = span("idle")
    for name in DANCES:
        assert span(name) > idle_span * 2.5, name


def test_land_squashes_then_recovers():
    land = CLIPS["land"]
    mid = land.at(land.duration * 0.5)
    end = land.at(land.duration)
    assert mid.squash[1] < 0.95, "should compress on impact"
    assert end.squash[1] == pytest.approx(1.0, abs=0.02)


def test_random_dance_only_returns_dances():
    import random

    rng = random.Random(7)
    for _ in range(50):
        assert random_dance(rng) in DANCES
        assert random_dance(rng) in CLIPS
