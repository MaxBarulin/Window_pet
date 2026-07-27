import math
from pathlib import Path

import numpy as np
import pytest

from pet.rigmath import Pose, Rig, about, apply, rotate, scale, translate

RIG_JSON = Path(__file__).resolve().parent.parent / "assets" / "rig.json"


@pytest.fixture(scope="module")
def rig() -> Rig:
    return Rig.load(RIG_JSON)


def test_translate_and_scale():
    assert apply(translate(5, -3), 1, 1) == (6, -2)
    assert apply(scale(2, 3), 2, 2) == (4, 6)


def test_rotate_is_clockwise_on_screen():
    # y points down, so +90 degrees takes +x to +y
    x, y = apply(rotate(90), 1, 0)
    assert x == pytest.approx(0, abs=1e-9)
    assert y == pytest.approx(1, abs=1e-9)


def test_about_leaves_the_pivot_alone():
    m = about(rotate(37), 100, 250)
    assert apply(m, 100, 250) == pytest.approx((100, 250))


def test_limb_below_pivot_swings_left_for_positive_angle():
    m = about(rotate(30), 0, 0)
    x, y = apply(m, 0, 100)
    assert x < 0 and y > 0


def test_rig_loads_with_one_root_and_full_hierarchy(rig: Rig):
    assert rig.root == "pelvis"
    assert len(rig.parts) == 11
    assert set(rig.draw_order) == set(rig.parts)
    # every parent appears before its children
    seen: set[str] = set()
    for name in rig.chain_order:
        parent = rig.parts[name]["parent"]
        assert parent is None or parent in seen
        seen.add(name)


def test_every_part_file_exists(rig: Rig):
    for name in rig.parts:
        assert rig.part_file(name).exists(), name


def test_rig_rejects_a_cycle():
    data = {
        "source_size": [10, 10],
        "figure_bbox": [0, 0, 10, 10],
        "ground": [5, 10],
        "joints": {"a": [1, 1], "b": [2, 2]},
        "parts": {
            "a": {"file": "a.png", "offset": [0, 0], "size": [1, 1],
                  "pivot": [0, 0], "pivot_src": [1, 1], "parent": "b", "z": 0},
            "b": {"file": "b.png", "offset": [0, 0], "size": [1, 1],
                  "pivot": [0, 0], "pivot_src": [2, 2], "parent": "a", "z": 1},
        },
    }
    with pytest.raises(ValueError, match="root"):
        Rig(data, Path("."))


def test_rest_angles_are_applied_without_a_pose(rig: Rig):
    # the photo is a T-pose, so at rest the wrist must sit well below the shoulder
    tf = rig.world_transforms(Pose())
    sx, sy = rig.pivot("arm_l_upper")
    wx, wy = apply(tf["arm_l_fore"], *rig.joints["wrist_l"])
    assert wy > sy + 100, "arms should hang down at rest, not stay in the T"
    assert abs(wx - sx) < 160


def test_zero_pose_with_no_rest_is_identity():
    data = {
        "source_size": [10, 10], "figure_bbox": [0, 0, 10, 10], "ground": [5, 10],
        "joints": {"a": [5, 5]},
        "parts": {"a": {"file": "a.png", "offset": [0, 0], "size": [1, 1],
                        "pivot": [5, 5], "pivot_src": [5, 5], "parent": None, "z": 0}},
    }
    tf = Rig(data, Path(".")).world_transforms(Pose())
    assert np.allclose(tf["a"], np.eye(3))


def test_child_inherits_parent_rotation(rig: Rig):
    """Rotating the torso must carry the head with it."""
    head_pivot = rig.joints["neck"]
    still = apply(rig.world_transforms(Pose())["head"], *head_pivot)
    turned = apply(rig.world_transforms(Pose(angles={"torso": 25.0}))["head"], *head_pivot)
    assert not np.allclose(still, turned)


def test_compose_puts_the_ground_point_on_the_anchor(rig: Rig):
    tf = rig.compose(Pose(), (640.0, 900.0), 0.25)
    # ground is on the root chain, so map it through the root part
    gx, gy = apply(tf["pelvis"], *rig.ground)
    assert (gx, gy) == pytest.approx((640.0, 900.0), abs=1e-6)


def test_compose_scales_height(rig: Rig):
    target = 200
    tf = rig.compose(Pose(), (0.0, 0.0), target / rig.height())
    _, y0, _, y1 = rig.bounds(tf)
    assert y1 - y0 == pytest.approx(target, rel=0.06)


def test_flip_mirrors_about_the_anchor(rig: Rig):
    a = rig.compose(Pose(), (500.0, 0.0), 0.25, flip=False)
    b = rig.compose(Pose(), (500.0, 0.0), 0.25, flip=True)
    ax0, _, ax1, _ = rig.bounds(a)
    bx0, _, bx1, _ = rig.bounds(b)
    assert (ax0 + ax1) / 2 == pytest.approx(1000 - (bx0 + bx1) / 2, abs=2.0)


def test_squash_shrinks_vertically_about_the_hips(rig: Rig):
    tall = rig.bounds(rig.compose(Pose(), (0.0, 0.0), 0.25))
    squat = rig.bounds(
        rig.compose(Pose(squash=(1.1, 0.8)), (0.0, 0.0), 0.25)
    )
    assert (squat[3] - squat[1]) < (tall[3] - tall[1])


def test_pose_lerp_endpoints_and_midpoint():
    a = Pose(angles={"head": 0.0}, offset=(0, 0))
    b = Pose(angles={"head": 10.0, "torso": 4.0}, offset=(2, 6))
    assert a.lerp(b, 0.0).angles["head"] == 0.0
    assert a.lerp(b, 1.0).angles["head"] == 10.0
    mid = a.lerp(b, 0.5)
    assert mid.angles["head"] == pytest.approx(5.0)
    assert mid.angles["torso"] == pytest.approx(2.0)  # missing key counts as 0
    assert mid.offset == pytest.approx((1.0, 3.0))


def test_pose_lerp_clamps_out_of_range_t():
    a = Pose(angles={"head": 0.0})
    b = Pose(angles={"head": 10.0})
    assert a.lerp(b, 5.0).angles["head"] == 10.0
    assert a.lerp(b, -5.0).angles["head"] == 0.0


def test_mirror_name_round_trips(rig: Rig):
    assert rig.mirror_name("arm_l_upper") == "arm_r_upper"
    assert rig.mirror_name("thigh_r") == "thigh_l"
    assert rig.mirror_name("head") == "head"
    for name in rig.parts:
        assert rig.mirror_name(rig.mirror_name(name)) == name


def test_bounds_cover_every_part(rig: Rig):
    tf = rig.compose(Pose(), (0.0, 0.0), 0.3)
    x0, y0, x1, y1 = rig.bounds(tf)
    for name in rig.parts:
        ox, oy = rig.parts[name]["offset"]
        w, h = rig.parts[name]["size"]
        for cx, cy in ((ox, oy), (ox + w, oy + h)):
            px, py = apply(tf[name], cx, cy)
            assert x0 - 1e-6 <= px <= x1 + 1e-6
            assert y0 - 1e-6 <= py <= y1 + 1e-6
