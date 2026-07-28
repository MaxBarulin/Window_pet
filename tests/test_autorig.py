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
def loaded():
    return autorig.load_joints(JOINTS)


@pytest.fixture(scope="module")
def joints(loaded) -> dict:
    return loaded[0]


@pytest.fixture(scope="module")
def built(cutout, loaded):
    points, caps, splits, free, _front = loaded
    return autorig.build(cutout, points, caps=caps, splits=splits, free=free)


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

def test_every_part_comes_out_non_empty(built):
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


def test_a_cap_reaches_across_its_parents_cut_end(built, alpha):
    """The gap rule.

    A part is cut square at its far joint, so the child's cap has to be at least
    half the limb's width there or a gap opens on the outside of every bend. That
    is what tore the shoe away from the shin: at an ankle the shoe's curve pulls
    the inscribed disc well under half the leg's width.
    """
    from scipy import ndimage as ndi

    solid = alpha > 0.02
    edt = ndi.distance_transform_edt(solid)
    segs = autorig.bone_segments(built.cut.joints, solid)
    height = float(built.data["source_size"][1])
    for name, bone in autorig.BONES.items():
        if bone.parent is None or bone.on_outline:
            continue
        pivot = built.cut.joints[bone.pivot]
        pseg = segs[bone.parent]
        pdir = autorig._norm(pseg[1][0] - pseg[0][0], pseg[1][1] - pseg[0][1])
        cseg = segs[name]
        cdir = autorig._norm(cseg[1][0] - cseg[0][0], cseg[1][1] - cseg[0][1])
        # a chord only means anything where the limb is separate from the body,
        # so the cap stops at whichever is smallest: across the parent, across the
        # limb itself, or 1.3x the disc that fits
        need = min(
            autorig._half_chord(solid, pivot, pdir, height * 0.3),
            autorig._half_chord(solid, pivot, cdir, height * 0.3),
            1.3 * autorig._sample(edt, pivot),
        )
        assert built.cut.cap_radius[name] >= need - 1.0, name


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
        # the tips are read off the silhouette rather than measured, so they only
        # have to point the right way, not land on the exact pixel
        allow = 0.13 if name in autorig.TIP_GUESS_FROM else 0.06
        assert err < allow * height, f"{name} guessed {err:.0f}px out"


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


# --- hands, feet, and the hand on the tiller -------------------------------

def test_the_hands_and_feet_come_off_as_their_own_pieces(built):
    for name in ("hand_l", "hand_r", "foot_l", "foot_r"):
        assert name in built.data["parts"], name
        assert built.cut.masks[name].sum() > 500, name


def test_a_foot_points_down_and_a_hand_points_outward(built, alpha):
    """The tips are read off the silhouette, so check they went the right way."""
    solid = alpha > 0.02
    segs = autorig.bone_segments(built.cut.joints, solid)
    for side in ("l", "r"):
        (ax, ay), (bx, by) = segs[f"foot_{side}"]
        assert by > ay, "the foot should be below the ankle"
        (ax, ay), (bx, by) = segs[f"hand_{side}"]
        wrist = built.cut.joints[f"wrist_{side}"]
        elbow = built.cut.joints[f"elbow_{side}"]
        assert (bx - ax) * (wrist[0] - elbow[0]) > 0, "the hand should carry on outward"


def test_a_foot_never_steals_the_other_leg(built):
    """The tip search keeps to the blob the ankle is standing on."""
    assert not (built.cut.masks["foot_l"] & built.cut.masks["foot_r"]).any()


def test_a_bigger_cap_makes_a_joint_claim_more(cutout, loaded):
    points, caps, splits, _free, _front = loaded
    small = autorig.build(cutout, points, caps=caps, splits=splits)
    wide = dict(caps, knee_l=small.cut.cap_radius["shin_l"] + 24.0)
    big = autorig.build(cutout, points, caps=wide, splits=splits)
    assert big.cut.masks["shin_l"].sum() > small.cut.masks["shin_l"].sum()
    assert big.cut.cap_radius["shin_l"] > small.cut.cap_radius["shin_l"]


def test_a_split_moves_the_seam_along_the_bone(cutout, loaded):
    points, caps, splits, _free, _front = loaded
    base = autorig.build(cutout, points, caps=caps, splits=splits)
    moved = autorig.build(cutout, points, caps=caps,
                          splits=dict(splits, knee_l=40.0))
    # the child takes more, so its parent must give some up
    assert moved.cut.masks["thigh_l"].sum() < base.cut.masks["thigh_l"].sum()


def test_overrides_survive_a_round_trip(tmp_path, loaded):
    points, _caps, _splits, _free, _front = loaded
    path = tmp_path / "j.json"
    autorig.save_joints(path, points, {"knee_l": 41.5}, {"knee_l": -7.0})
    again, caps, splits, _free, _front = autorig.load_joints(path)
    assert caps == {"knee_l": 41.5}
    assert splits == {"knee_l": -7.0}
    assert again["knee_l"] == points["knee_l"]


def test_a_pinned_shoulder_stays_where_it_was_put(alpha, joints):
    """Snapping to the sleeve top is the default, not the only option."""
    solid = alpha > 0.02
    inside = dict(joints)
    inside["shoulder_l"] = (joints["shoulder_l"][0], joints["shoulder_l"][1] + 30)
    assert autorig.snap_joints(solid, inside)["shoulder_l"][1] < inside["shoulder_l"][1]
    pinned = autorig.snap_joints(solid, inside, free={"shoulder_l"})
    assert pinned["shoulder_l"] == inside["shoulder_l"]


def test_a_pinned_shoulder_gets_a_cap_like_any_other_joint(alpha, joints):
    inside = dict(joints)
    inside["shoulder_l"] = (joints["shoulder_l"][0], joints["shoulder_l"][1] + 30)
    snapped = autorig.cut_parts(alpha, inside)
    pinned = autorig.cut_parts(alpha, inside, free={"shoulder_l"})
    assert snapped.cap_radius["arm_l_upper"] < 3.0
    assert pinned.cap_radius["arm_l_upper"] > 15.0


def test_an_arm_held_up_does_not_empty_its_own_part(alpha, joints):
    """The horizon clip is for arms that hang, and would eat one that is raised.

    Every pixel of a raised sleeve is above its shoulder, so clipping there took
    the whole piece away and the cut failed with 'came out empty'.
    """
    # drop the shoulder below the elbow, so the arm rises from its pivot while
    # every point stays on real sleeve
    raised = dict(joints)
    raised["shoulder_r"] = (joints["shoulder_r"][0], joints["elbow_r"][1] + 40)
    assert "shoulder_r" in autorig._raised_limbs(raised)
    cut = autorig.cut_parts(alpha, raised)
    assert cut.masks["arm_r_upper"].sum() > 500
    # and it is an ordinary joint now, so it has a cap to turn on
    assert cut.cap_radius["arm_r_upper"] > 5.0


def test_the_draw_order_can_put_the_arms_in_front():
    behind = autorig.draw_order(False)
    front = autorig.draw_order(True)
    assert behind["arm_l_upper"] < behind["torso"]
    assert front["arm_l_upper"] > front["torso"]
    assert sorted(front.values()) == list(range(len(autorig.BONES)))
