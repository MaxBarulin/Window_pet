"""The automatic cutter, checked against the photo that is actually shipped.

These assert the properties the rig has to have rather than exact pixel counts,
because the whole point of the tool is that it works on a photo it has never seen.
The two that matter most - a rotation-safe cap on every joint, and no limb holding
material behind its own pivot - are the two failures that took three attempts each
to get right by hand.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pet.kinematics import Skeleton
from pet.poses import CLIPS
from pet.rigmath import Rig
from tools import autorig

ROOT = Path(__file__).resolve().parent.parent
CUTOUT = ROOT / "assets" / "cutout.png"
JOINTS = ROOT / "assets" / "joints.json"


@pytest.fixture(scope="module")
def cutout() -> Image.Image:
    return Image.open(CUTOUT).convert("RGBA")


@pytest.fixture(scope="module")
def alpha(cutout) -> np.ndarray:
    return np.asarray(cutout)[..., 3].astype(np.float32) / 255.0


@pytest.fixture(scope="module")
def joints() -> dict:
    return autorig.load_joints(JOINTS)


@pytest.fixture(scope="module")
def built(cutout, joints):
    return autorig.build(cutout, joints)


# --- the skeleton is self-consistent ---------------------------------------

def test_every_bone_has_a_reachable_parent():
    for name, bone in autorig.BONES.items():
        assert bone.parent is None or bone.parent in autorig.BONES
        assert bone.pivot in autorig.JOINT_ORDER
        assert bone.tip is None or bone.tip in autorig.JOINT_ORDER
    roots = [n for n, b in autorig.BONES.items() if b.parent is None]
    assert roots == ["pelvis"]


def test_draw_order_is_a_permutation():
    assert sorted(b.z for b in autorig.BONES.values()) == list(range(len(autorig.BONES)))


def test_a_child_never_draws_under_a_parent_it_needs_covering_by():
    """Every on-outline joint is one where the parent draws over the child.

    That is the whole reason the parent can cover the shoulder: the cap there is
    necessarily nothing, so the seam has to be hidden by whatever is on top.
    """
    for name, bone in autorig.BONES.items():
        if bone.on_outline:
            assert autorig.BONES[bone.parent].z > bone.z


# --- cutting ---------------------------------------------------------------

def test_all_eleven_parts_come_out_non_empty(built):
    assert set(built.images) == set(autorig.BONES)
    for name, img in built.images.items():
        assert np.asarray(img)[..., 3].max() > 200, name


def test_almost_every_opaque_pixel_lands_in_some_part(built):
    assert built.cut.unassigned < 0.01


def test_parts_reassemble_into_the_original_silhouette(built, alpha):
    """Nothing is lost and nothing is invented: the union is the matte."""
    solid = alpha > 0.02
    union = np.zeros_like(solid)
    for m in built.cut.masks.values():
        union |= m
    assert not (union & ~solid).any(), "a part claims background"
    missing = int((solid & ~union).sum())
    assert missing / solid.sum() < 0.01


def test_no_limb_holds_material_behind_its_own_pivot(built, alpha):
    """The wing rule.

    Material behind a pivot swings outboard as the bone rotates, which is what
    sprouted wings from the shoulders. Only the joint cap may sit behind, and it
    is a disc centred on the pivot, so it cannot change shape as it turns.
    """
    joints = built.cut.joints
    solid = alpha > 0.02
    segs = autorig.bone_segments(joints, solid)
    for name, bone in autorig.BONES.items():
        if bone.parent is None:
            continue
        px, py = joints[bone.pivot]
        (ax, ay), (bx, by) = segs[name]
        dx, dy = autorig._norm(bx - ax, by - ay)
        ys, xs = np.nonzero(built.cut.masks[name])
        along = (xs - px) * dx + (ys - py) * dy
        # measured along the bone, not as a radius: material level with the pivot
        # but out to the side of it is the limb's own width, not a wing
        assert along.min() >= -(built.cut.cap_radius[name] + 1.5), name


def test_every_joint_cap_fits_inside_the_silhouette(built, alpha):
    """A cap has to be a full disc to be rotation-safe.

    A disc trimmed by the outline changes shape as it turns, which is how the
    knee ended up either torn open or poking out black corners.
    """
    solid = alpha > 0.02
    h, w = solid.shape
    yy, xx = np.mgrid[0:h, 0:w]
    for name, bone in autorig.BONES.items():
        r = built.cut.cap_radius[name]
        if r < 2.0:
            continue  # on-outline pivot: there is no cap, the parent covers it
        px, py = built.cut.joints[bone.pivot]
        disc = (xx - px) ** 2 + (yy - py) ** 2 <= (r - 1) ** 2
        assert not (disc & ~solid).any(), name


def test_a_cap_is_wide_enough_to_cover_its_parents_cut_end(built):
    """The cap is the inscribed radius, so it is half the limb's width there."""
    for name, bone in autorig.BONES.items():
        if bone.parent is None or bone.on_outline:
            continue
        # a limb is cut square at its far joint; the child's cap has to reach
        # across the full width of that cut
        child = [n for n, b in autorig.BONES.items() if b.parent == name]
        for c in child:
            if autorig.BONES[c].on_outline:
                continue
            assert built.cut.cap_radius[c] > 1.0, c


def test_the_pelvis_stops_at_the_hips_so_the_legs_bend_where_they_should(built):
    """The bend has to read at the hip, not at the bottom edge of the pelvis.

    When the pelvis hangs below the hip pivots it draws over the top of the
    thighs, so the visible articulation happens along its lower edge - well below
    the crotch - and he looks like he has a second knee.
    """
    joints = built.cut.joints
    hips_y = joints["hips"][1]
    ys = np.nonzero(built.cut.masks["pelvis"].any(axis=1))[0]
    # its own pivot, plus its own cap - and nothing beyond that
    assert ys.max() <= hips_y + built.cut.cap_radius["pelvis"] + 2
    # in particular it stops above the hip sockets the thighs turn about
    assert ys.max() < max(joints["hip_l"][1], joints["hip_r"][1]) + 20


def test_the_shoulders_get_lifted_onto_the_top_of_the_sleeve(alpha, joints):
    solid = alpha > 0.02
    snapped = autorig.snap_joints(solid, joints)
    for side in ("l", "r"):
        x, y = snapped[f"shoulder_{side}"]
        assert y <= joints[f"shoulder_{side}"][1]
        assert solid[int(y), int(x)], "the pivot fell off him"
        assert not solid[int(y) - 1, int(x)], "there is still sleeve above the pivot"


# --- the rest pose ---------------------------------------------------------

def test_the_arms_come_down_out_of_the_t_pose(joints):
    rest = autorig.rest_angles(joints)
    assert set(rest) == {"arm_l_upper", "arm_l_fore", "arm_r_upper", "arm_r_fore"}
    # mirrored, and big enough to actually lower a horizontal arm
    assert rest["arm_l_upper"] < -40.0
    assert rest["arm_r_upper"] > 40.0
    assert math.isclose(rest["arm_l_upper"], -rest["arm_r_upper"], abs_tol=8.0)


def test_a_rest_arm_hangs_roughly_downward(joints):
    rest = autorig.rest_angles(joints)
    for side in ("l", "r"):
        sx, sy = joints[f"shoulder_{side}"]
        ex, ey = joints[f"elbow_{side}"]
        src = math.degrees(math.atan2(ey - sy, ex - sx))
        world = src + rest[f"arm_{side}_upper"]
        assert abs(autorig._wrap(world - 90.0)) < 20.0, side


def test_the_ground_point_sits_between_the_feet(built, joints):
    gx, gy = built.data["ground"]
    assert min(joints["ankle_l"][0], joints["ankle_r"][0]) < gx
    assert gx < max(joints["ankle_l"][0], joints["ankle_r"][0])
    assert gy > max(joints["ankle_l"][1], joints["ankle_r"][1])


# --- what comes out is a rig the rest of the app can use -------------------

def test_the_result_loads_as_a_rig_and_poses(built):
    rig = Rig(built.data, ROOT / "assets")
    skeleton = Skeleton(rig)
    for name, clip in CLIPS.items():
        for i in range(6):
            pose = skeleton.resolve(clip.at(clip.duration * i / 6))
            transforms = rig.compose(pose, (0.0, 0.0), 0.25)
            assert math.isfinite(skeleton.ground_y(transforms)), name


def test_the_shipped_rig_is_what_the_joints_produce(built):
    """assets/rig.json has to stay in step with assets/joints.json."""
    shipped = json.loads((ROOT / "assets" / "rig.json").read_text())
    assert shipped["parts"].keys() == built.data["parts"].keys()
    assert shipped["ground"] == built.data["ground"]
    assert shipped["rest_angles"] == built.data["rest_angles"]
    for name, meta in shipped["parts"].items():
        assert meta["offset"] == built.data["parts"][name]["offset"], name
        assert meta["size"] == built.data["parts"][name]["size"], name


# --- guessing --------------------------------------------------------------

def test_guessed_joints_are_close_enough_to_start_from(alpha, joints):
    guess = autorig.guess_joints(alpha)
    assert set(guess) == set(autorig.JOINT_ORDER)
    height = built_height(alpha)
    for name in autorig.JOINT_ORDER:
        err = math.dist(guess[name], joints[name])
        assert err < 0.06 * height, f"{name} guessed {err:.0f}px out"


def test_a_rig_built_purely_from_guesses_still_works(cutout, alpha):
    guess = autorig.guess_joints(alpha)
    built = autorig.build(cutout, guess)
    assert built.cut.unassigned < 0.01
    skeleton = Skeleton(Rig(built.data, ROOT / "assets"))
    assert skeleton.reach > 0


def built_height(alpha: np.ndarray) -> float:
    ys = np.nonzero((alpha > 0.02).any(axis=1))[0]
    return float(ys.max() - ys.min())


# --- failure modes ---------------------------------------------------------

def test_an_empty_matte_is_reported_not_crashed():
    with pytest.raises(autorig.RigError, match="empty"):
        autorig.guess_joints(np.zeros((40, 40), np.float32))


def test_missing_joints_are_named(alpha, joints):
    partial = {k: v for k, v in joints.items() if k != "knee_r"}
    with pytest.raises(autorig.RigError, match="knee_r"):
        autorig.cut_parts(alpha, partial)


def test_legs_that_never_separate_are_reported():
    blob = np.zeros((200, 100), np.float32)
    blob[20:180, 30:70] = 1.0
    with pytest.raises(autorig.RigError):
        autorig.guess_joints(blob)
