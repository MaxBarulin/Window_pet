"""Turn a background-free figure plus ~15 joint points into a rig.

This is the photo-specific half of the pipeline, and it used to be 81 hand-measured
coordinates in `tools/build_assets.py`. Everything downstream - the clips, the leg
IK, the behaviour, the overlay, the exe - only ever reads `assets/rig.json`, so this
module is the only thing a new photo needs.

The user supplies the joints (see `tools/rig_editor.py` for the UI, or
`guess_joints` for a first stab at them). The parts are cut automatically:

1. **Assignment.** Every opaque pixel goes to the bone it is nearest to, with each
   distance divided by that bone's own thickness. Without that normalisation the
   trunk loses its flanks to the arms - a pixel on the side of the ribcage really is
   closer to the arm bone than to the spine.

2. **Clipping.** A part may not hold material behind its own pivot. Material behind
   the pivot swings *outboard* when the bone rotates, which is what made wings
   sprout from the shoulders when the arms came down out of the T-pose. Anything
   clipped off simply falls through to the next-best bone, which is the parent.

3. **Caps.** Each part gets a disc centred on its own pivot, of exactly the radius
   that still fits inside the silhouette there. A disc centred on the pivot has a
   silhouette that does not change as the piece rotates, so it can neither tear a
   gap open on the outside of a bend nor poke out as a corner - the two ways the
   knee was wrong before. Its radius is also, by construction, exactly half the
   limb's width at the joint, so it covers the parent's cut end.

4. **Cover.** Where a pivot sits on the outline rather than inside it - the
   shoulders, where the pivot belongs at the *top* of the sleeve - the cap is
   necessarily tiny, so the joint would open up when the arm swings down. There the
   parent takes a disc of the limb's own width instead. The parent draws over the
   child at exactly those joints, so it hides the seam.

    python tools/autorig.py --joints my-joints.json
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# The skeleton. None of this depends on the photo.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bone:
    parent: str | None
    pivot: str          # joint the part rotates about
    tip: str | None     # joint at the far end; None for the trunk pieces
    z: int              # draw order, low first
    on_outline: bool = False    # pivot belongs on the silhouette, not inside it


BONES: dict[str, Bone] = {
    # The shoulders are the one pair of pivots that belong on the outline rather
    # than inside the limb - at the *top* of each sleeve. Swinging an arm down from
    # the T-pose maps "outward" to "downward", so anything above the pivot swings
    # out past the shoulder as a wing. `snap_joints` puts them there.
    "arm_r_upper": Bone("torso", "shoulder_r", "elbow_r", 0, on_outline=True),
    "arm_r_fore": Bone("arm_r_upper", "elbow_r", "wrist_r", 1),
    "hand_r": Bone("arm_r_fore", "wrist_r", "fingers_r", 2),
    "arm_l_upper": Bone("torso", "shoulder_l", "elbow_l", 3, on_outline=True),
    "arm_l_fore": Bone("arm_l_upper", "elbow_l", "wrist_l", 4),
    "hand_l": Bone("arm_l_fore", "wrist_l", "fingers_l", 5),
    "thigh_r": Bone("pelvis", "hip_r", "knee_r", 6),
    "shin_r": Bone("thigh_r", "knee_r", "ankle_r", 7),
    "foot_r": Bone("shin_r", "ankle_r", "toe_r", 8),
    "thigh_l": Bone("pelvis", "hip_l", "knee_l", 9),
    "shin_l": Bone("thigh_l", "knee_l", "ankle_l", 10),
    "foot_l": Bone("shin_l", "ankle_l", "toe_l", 11),
    "pelvis": Bone(None, "hips", None, 12),
    "torso": Bone("pelvis", "waist", "neck", 13),
    "head": Bone("torso", "neck", None, 14),
}

# Where each tip is guessed from, when nobody has placed it yet: the direction
# the limb was already going, carried on into whatever material lies beyond.
TIP_GUESS_FROM = {
    "fingers_l": ("elbow_l", "wrist_l"),
    "fingers_r": ("elbow_r", "wrist_r"),
    "toe_l": ("knee_l", "ankle_l"),
    "toe_r": ("knee_r", "ankle_r"),
}

# The order the editor asks for them in: trunk first, so the centreline and scale
# are established before the limbs hang off it.
JOINT_ORDER = [
    "neck", "waist", "hips",
    "shoulder_l", "elbow_l", "wrist_l", "fingers_l",
    "shoulder_r", "elbow_r", "wrist_r", "fingers_r",
    "hip_l", "knee_l", "ankle_l", "toe_l",
    "hip_r", "knee_r", "ankle_r", "toe_r",
]

HINTS = {
    "neck": "Where the head meets the collar.",
    "waist": "Centre of the body at the belt.",
    "hips": "Centre of the pelvis, just above the crotch.",
    "shoulder_l": "TOP of the sleeve, not its middle - see the note below.",
    "shoulder_r": "TOP of the sleeve, not its middle.",
    "elbow_l": "Middle of the sleeve at the elbow.",
    "elbow_r": "Middle of the sleeve at the elbow.",
    "wrist_l": "Where the hand meets the arm.",
    "wrist_r": "Where the hand meets the arm.",
    "hip_l": "Hip socket: inside the body, level with the crotch.",
    "hip_r": "Hip socket: inside the body, level with the crotch.",
    "knee_l": "Middle of the leg at the knee.",
    "knee_r": "Middle of the leg at the knee.",
    "ankle_l": "The ankle bone, where the leg meets the shoe.",
    "ankle_r": "The ankle bone, where the leg meets the shoe.",
    "fingers_l": "The fingertips. Sets which way the hand points.",
    "fingers_r": "The fingertips. Sets which way the hand points.",
    "toe_l": "The toe of the shoe. Sets which way the foot points.",
    "toe_r": "The toe of the shoe. Sets which way the foot points.",
}

SHOULDER_NOTE = (
    "Put the shoulders at the top of each sleeve. Swinging an arm down from the "
    "T-pose maps 'outward' to 'downward', so anything above the pivot swings out "
    "past the shoulder as a wing."
)

# --- tunables, as fractions of the figure's height -------------------------

# How far a pivot marked `on_outline` may be moved to reach the outline.
SNAP_FRAC = 0.07
# How far outboard the parent's cover reaches over an on-outline joint, as a
# fraction of the limb's own reach.
COVER = 0.3
# How far a part may run past its far joint, under its child's cap.
DISTAL_OVERLAP_FRAC = 0.008

# Rest pose: the photo is a T-pose, so the rig carries an offset that brings the
# arms down and the clips are authored on top of it.
REST_ARM_OUT = 5.0     # degrees the hanging upper arm sits off vertical, outward
REST_FORE_FLARE = 14.0  # extra outward angle at the elbow


class RigError(RuntimeError):
    """Something about the joints or the matte makes a usable rig impossible."""


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

Point = tuple[float, float]


def _norm(dx: float, dy: float) -> Point:
    n = math.hypot(dx, dy)
    if n < 1e-6:
        return (1.0, 0.0)
    return (dx / n, dy / n)


def _wrap(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def _grid(shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    h, w = shape
    xs = np.arange(w, dtype=np.float32)[None, :]
    ys = np.arange(h, dtype=np.float32)[:, None]
    return xs, ys


def _segment_distance(shape, p: Point, q: Point) -> np.ndarray:
    """Distance from every pixel to the segment p-q."""
    xs, ys = _grid(shape)
    vx, vy = q[0] - p[0], q[1] - p[1]
    ll = vx * vx + vy * vy
    if ll < 1e-6:
        return np.hypot(xs - p[0], ys - p[1]).astype(np.float32)
    t = ((xs - p[0]) * vx + (ys - p[1]) * vy) / ll
    np.clip(t, 0.0, 1.0, out=t)
    return np.hypot(xs - (p[0] + t * vx), ys - (p[1] + t * vy)).astype(np.float32)


def _along(shape, p: Point, d: Point) -> np.ndarray:
    """Signed distance along the bone's direction, measured from its pivot."""
    xs, ys = _grid(shape)
    return ((xs - p[0]) * d[0] + (ys - p[1]) * d[1]).astype(np.float32)


def _disc(shape, c: Point, r: float) -> np.ndarray:
    xs, ys = _grid(shape)
    return ((xs - c[0]) ** 2 + (ys - c[1]) ** 2) <= r * r


def _sample(field: np.ndarray, p: Point) -> float:
    h, w = field.shape
    x = int(round(min(max(p[0], 0), w - 1)))
    y = int(round(min(max(p[1], 0), h - 1)))
    return float(field[y, x])


# ---------------------------------------------------------------------------
# Bones in photo pixels
# ---------------------------------------------------------------------------

def bone_segments(joints: dict[str, Point], solid: np.ndarray) -> dict[str, tuple[Point, Point]]:
    """The line segment each part is built around.

    The limbs are simply pivot -> tip. The three trunk pieces have no far joint of
    their own, so they get one: the head runs from the neck up to the crown, the
    torso from the waist up to the neck, and the pelvis is the short span just
    above the hip sockets.
    """
    out: dict[str, tuple[Point, Point]] = {}
    for name, bone in BONES.items():
        if bone.tip is not None:
            out[name] = (joints[bone.pivot], joints[bone.tip])
    neck = joints["neck"]
    waist = joints["waist"]
    hips = joints["hips"]

    # crown: the top of the silhouette in a band around the neck
    band = max(6, int(abs(neck[1] - waist[1]) * 0.25))
    x0 = max(0, int(neck[0]) - band)
    x1 = min(solid.shape[1], int(neck[0]) + band + 1)
    rows = np.nonzero(solid[:, x0:x1].any(axis=1))[0]
    crown_y = float(rows.min()) if rows.size else neck[1] - band
    out["head"] = (neck, (neck[0], crown_y))
    out["pelvis"] = (((waist[0] + hips[0]) / 2, (waist[1] + hips[1]) / 2), hips)
    return out


def fill_missing_tips(solid: np.ndarray, joints: dict[str, Point]) -> dict[str, Point]:
    """Guess any tip nobody has placed, so an older joints file still loads."""
    out = dict(joints)
    for tip, (before, pivot) in TIP_GUESS_FROM.items():
        if tip not in out and before in out and pivot in out:
            out[tip] = _tip_beyond(solid, out[before], out[pivot])
    return out


def _raised_limbs(joints: dict[str, Point]) -> set[str]:
    """On-outline pivots whose limb does not hang below them.

    Lifting a shoulder to the top of the sleeve, and then refusing the arm any
    material above that line, is right for an arm held out or down - which is what
    a T-pose is. It is catastrophic for an arm held *up*: every pixel of the sleeve
    is above the pivot, so the whole piece is clipped away and the part comes out
    empty. Those pivots are left where they are and treated as ordinary joints.
    """
    out = set()
    for bone in BONES.values():
        if not bone.on_outline or bone.tip is None:
            continue
        if bone.tip in joints and bone.pivot in joints:
            if joints[bone.tip][1] < joints[bone.pivot][1] - 2.0:
                out.add(bone.pivot)
    return out


def snap_joints(solid: np.ndarray, joints: dict[str, Point],
                free: set[str] | None = None) -> dict[str, Point]:
    """Lift the on-outline pivots onto the outline.

    Only the shoulders are marked that way, and only ever move straight up: the
    pivot belongs at the top of the sleeve. Eleven pixels of sleeve left above it
    is enough to sprout a wing - the sliver has to go somewhere, and whichever
    piece takes it either swings it outboard or leaves it stuck to the torso as an
    epaulette. Clicking anywhere down the shoulder is therefore good enough.
    """
    h = float(np.count_nonzero(solid.any(axis=1)))
    limit = int(SNAP_FRAC * h)
    out = dict(joints)
    free = set(free or ()) | _raised_limbs(joints)
    for bone in BONES.values():
        if not bone.on_outline or bone.pivot in free:
            continue
        x, y = joints[bone.pivot]
        ix = int(round(min(max(x, 0), solid.shape[1] - 1)))
        iy = int(round(min(max(y, 0), solid.shape[0] - 1)))
        top = iy
        while top - 1 >= 0 and iy - top < limit and solid[top - 1, ix]:
            top -= 1
        out[bone.pivot] = (float(x), float(top))
    return out


def _tip_beyond(solid: np.ndarray, before: Point, pivot: Point) -> Point:
    """Point through the middle of whatever sticks out past `pivot`.

    A hand and a foot are the last thing on their limb, so there is no joint after
    them to aim at. What there is instead is material: the shoe below the ankle,
    the fingers past the wrist. Take the part of it that hangs together with the
    pivot - so the other shoe cannot be mistaken for this one - and point at its
    middle.
    """
    d = _norm(pivot[0] - before[0], pivot[1] - before[1])
    span = math.dist(before, pivot)
    ahead = (
        solid
        & (_along(solid.shape, pivot, d) > 1.0)
        & _disc(solid.shape, pivot, max(span * 0.9, 20.0))
    )
    lab, n = ndi.label(ahead)
    if n == 0:
        return (pivot[0] + d[0] * 20.0, pivot[1] + d[1] * 20.0)
    if n > 1:  # keep the blob the pivot itself is standing on
        want = lab[int(round(pivot[1])), int(round(pivot[0]))]
        if want == 0:
            sizes = ndi.sum(ahead, lab, range(1, n + 1))
            want = int(np.argmax(sizes)) + 1
        ahead = lab == want
    ys, xs = np.nonzero(ahead)
    mid = (float(xs.mean()), float(ys.mean()))
    # the centroid is halfway along; carry on to the end of it
    return (pivot[0] + 2.0 * (mid[0] - pivot[0]),
            pivot[1] + 2.0 * (mid[1] - pivot[1]))


def _half_chord(solid: np.ndarray, at: Point, direction: Point, limit: float) -> float:
    """Half the opaque width across `direction`, right at `at`."""
    h, w = solid.shape
    nx, ny = -direction[1], direction[0]
    steps = np.arange(1, int(limit) + 1, dtype=np.float32)
    total = 0.0
    for sign in (1.0, -1.0):
        ix = np.clip(np.round(at[0] + sign * nx * steps).astype(int), 0, w - 1)
        iy = np.clip(np.round(at[1] + sign * ny * steps).astype(int), 0, h - 1)
        gap = np.nonzero(~solid[iy, ix])[0]
        total += float(gap[0]) if gap.size else limit
    return total / 2.0


def _bone_reach(solid: np.ndarray, seg: tuple[Point, Point], limit: float) -> float:
    """How far this bone's own material sits from it, perpendicular.

    This is what every distance gets divided by, and getting it wrong loses a limb.
    Two things it is deliberately *not*:

    * Not the largest disc that fits at the bone. A bone need not run down the
      middle of its own limb - the shoulder pivots belong at the *top* of each
      sleeve - and the biggest disc there is a fraction of the sleeve's thickness.
    * Not half the chord across the bone. For the same reason: measure the two
      sides separately and keep the larger, or the sleeve's far edge ends up
      further from the arm than from the spine, and the torso claims the armpit.
    """
    h, w = solid.shape
    p, q = seg
    dx, dy = _norm(q[0] - p[0], q[1] - p[1])
    nx, ny = -dy, dx
    steps = np.arange(1, int(limit) + 1, dtype=np.float32)
    reaches = []
    for t in np.linspace(0.15, 0.85, 15):
        sx, sy = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
        sides = []
        for sign in (1.0, -1.0):
            ix = np.clip(np.round(sx + sign * nx * steps).astype(int), 0, w - 1)
            iy = np.clip(np.round(sy + sign * ny * steps).astype(int), 0, h - 1)
            gap = np.nonzero(~solid[iy, ix])[0]
            sides.append(float(gap[0]) if gap.size else limit)
        reaches.append(max(sides))
    return max(float(np.median(reaches)), 1.0)


# ---------------------------------------------------------------------------
# Cutting the parts
# ---------------------------------------------------------------------------

@dataclass
class Cut:
    """The result of segmenting one matte: a mask per part, plus what it measured."""

    masks: dict[str, np.ndarray]
    joints: dict[str, Point]   # after snapping; what rig.json should record
    cap_radius: dict[str, float]
    widths: dict[str, float]
    unassigned: float          # fraction of opaque pixels in no part at all


def cut_parts(
    alpha: np.ndarray,
    joints: dict[str, Point],
    caps_override: dict[str, float] | None = None,
    splits: dict[str, float] | None = None,
    free: set[str] | None = None,
) -> Cut:
    """Cut the parts. `caps_override` and `splits` are the hand on the tiller.

    Both are keyed by joint name and both default to what gets measured off the
    matte. A cap radius is how far a part reaches back around its own pivot - the
    automatic value is the biggest disc that fits, which is right when the joint
    sits in the middle of the limb and conservative when it does not. A split
    moves the seam at a joint along the bone: positive gives the child more.
    """
    joints = fill_missing_tips(alpha > 0.02, joints)
    missing = [j for j in JOINT_ORDER if j not in joints]
    if missing:
        raise RigError("missing joints: " + ", ".join(missing))
    caps_override = caps_override or {}
    splits = splits or {}
    free = set(free or ())

    solid = alpha > 0.02
    if not solid.any():
        raise RigError("the matte is empty - remove the background first")
    ys, xs = np.nonzero(solid)
    height = float(ys.max() - ys.min() + 1)

    joints = snap_joints(solid, joints, free)
    # A shoulder pinned inside the figure is not on the outline any more, so it
    # stops needing the treatment that goes with that - no horizon clip, no cover
    # from the torso - and behaves like every other joint: a cap of its own that
    # turns with it. Pinning one is the way out when the top of a sleeve is the
    # wrong place for it.
    pinned = free | _raised_limbs(joints)
    outline = {n: b.on_outline and b.pivot not in pinned for n, b in BONES.items()}
    edt = ndi.distance_transform_edt(solid).astype(np.float32)
    segs = bone_segments(joints, solid)
    widths = {n: _bone_reach(solid, s, height * 0.5) for n, s in segs.items()}
    # A cap has to reach right across the parent's cut end, or a gap opens on the
    # outside of every bend. The inscribed disc is that wide when the joint sits in
    # the middle of its limb, and short when it does not - at an ankle the shoe's
    # curve pulls it in well under half the leg's width, and the shoe tore away
    # from the shin. So take whichever is larger. Past the inscribed radius the cap
    # is no longer a full disc and does change shape a little as it turns; a
    # visible gap is worse than that, and the wheel is there to retune it.
    caps = {}
    for n, bone in BONES.items():  # noqa: B007 - `outline` is keyed the same way
        auto = _sample(edt, joints[bone.pivot])
        if bone.parent is not None and not outline[n]:
            pseg = segs[bone.parent]
            pdir = _norm(pseg[1][0] - pseg[0][0], pseg[1][1] - pseg[0][1])
            auto = max(auto, _half_chord(solid, joints[bone.pivot], pdir, height * 0.3))
        caps[n] = float(caps_override.get(bone.pivot, auto))

    def split_at(name: str) -> float:
        """How far the seam at this part's far joint has been dragged."""
        bone = BONES[name]
        return float(splits.get(bone.tip or bone.pivot, 0.0))

    overlap = DISTAL_OVERLAP_FRAC * height
    has_child = {n: any(b.parent == n for b in BONES.values()) for n in BONES}

    # 1. assignment, weighted by each bone's own thickness
    names = list(BONES)
    raw: dict[str, np.ndarray] = {}
    best = np.full(solid.shape, np.inf, np.float32)
    labels = np.full(solid.shape, -1, np.int16)
    _, ys_grid = _grid(solid.shape)
    for i, name in enumerate(names):
        bone = BONES[name]
        seg = segs[name]
        pivot = joints[bone.pivot]
        raw[name] = _segment_distance(solid.shape, *seg) / widths[name]
        d = raw[name].copy()

        # 2. clipping
        direction = _norm(seg[1][0] - seg[0][0], seg[1][1] - seg[0][1])
        along = _along(solid.shape, seg[0], direction)
        elig = np.ones(solid.shape, bool)
        if bone.parent is not None:
            # nothing behind the pivot beyond the cap that swings with it
            elig &= along >= -(caps[name] + float(splits.get(bone.pivot, 0.0)))
        if has_child[name]:
            # nothing past the far joint but a sliver, hidden under the child's cap
            elig &= along <= math.dist(*seg) + overlap - split_at(name)
        if outline[name] and bone.parent is not None:
            # Belt and braces after the snap: the limb gets nothing above its own
            # pivot, because that material would swing outboard as a wing.
            below = joints[BONES[bone.parent].pivot][1] > pivot[1]
            elig &= (ys_grid >= pivot[1]) if below else (ys_grid <= pivot[1])
        d = np.where(elig, d, np.inf)

        upd = d < best
        best = np.where(upd, d, best)
        labels = np.where(upd, np.int16(i), labels)

    # anything every bone refused goes to whichever is simply nearest, so the
    # clipping can never punch a hole in him
    orphan = solid & (labels < 0)
    if orphan.any():
        second = np.full(solid.shape, np.inf, np.float32)
        for i, name in enumerate(names):
            upd = orphan & (raw[name] < second)
            second = np.where(upd, raw[name], second)
            labels = np.where(upd, np.int16(i), labels)
    labels[~solid] = -1

    masks = {n: labels == i for i, n in enumerate(names)}

    # 3. caps, and 4. cover for the pivots that sit on the outline
    for name, bone in BONES.items():
        pivot = joints[bone.pivot]
        masks[name] |= _disc(solid.shape, pivot, caps[name]) & solid
        if outline[name] and bone.parent is not None:
            # The cap is necessarily nothing here - the pivot is on the outline -
            # so the joint would open up as the limb swings. The parent draws over
            # the child at exactly these joints, so it keeps the shoulder instead.
            #
            # Not as a disc: a disc bump has nothing under it once the armpit goes
            # to the arm, and it swings away leaving a pointed tab at the shoulder.
            # The parent takes a wedge instead - all the way down the sleeve's
            # inner side to the armpit, but only a little way outboard.
            seg = segs[name]
            direction = _norm(seg[1][0] - seg[0][0], seg[1][1] - seg[0][1])
            outboard = _along(solid.shape, pivot, direction)
            masks[bone.parent] |= (
                _disc(solid.shape, pivot, widths[name])
                & (outboard <= COVER * widths[name])
                & solid
            )

    # drop slivers of a neighbour that a cap happened to reach across
    for name, m in masks.items():
        lab, n = ndi.label(m)
        if n > 1:
            sizes = ndi.sum(m, lab, range(1, n + 1))
            masks[name] = lab == (int(np.argmax(sizes)) + 1)
        if not masks[name].any():
            hint = (
                " Try pinning that pivot, or move it inside him."
                if BONES[name].on_outline else
                " Its joints may be on top of each other, or on the wrong limb."
            )
            raise RigError(
                f"part '{name}' came out empty - check the joints around "
                f"{BONES[name].pivot} and {BONES[name].tip or 'its far end'}." + hint
            )

    claimed = np.zeros_like(solid)
    for m in masks.values():
        claimed |= m
    missed = float((solid & ~claimed).sum()) / float(solid.sum())
    return Cut(masks, joints, caps, widths, missed)


# ---------------------------------------------------------------------------
# The rest pose
# ---------------------------------------------------------------------------

def rest_angles(joints: dict[str, Point]) -> dict[str, float]:
    """How far each arm has to swing to hang by his side.

    The photo is a T-pose, so the arms have to come down before any clip touches
    them. Everything else rests where it was photographed.
    """
    centre = joints["waist"][0]
    out: dict[str, float] = {}
    for side in ("l", "r"):
        shoulder = joints[f"shoulder_{side}"]
        elbow = joints[f"elbow_{side}"]
        wrist = joints[f"wrist_{side}"]
        # +1 when this arm is on the screen-left of the body
        s = 1.0 if shoulder[0] < centre else -1.0

        upper_src = math.degrees(math.atan2(elbow[1] - shoulder[1], elbow[0] - shoulder[0]))
        fore_src = math.degrees(math.atan2(wrist[1] - elbow[1], wrist[0] - elbow[0]))
        upper_rest = _wrap(90.0 + s * REST_ARM_OUT - upper_src)
        fore_world = 90.0 + s * (REST_ARM_OUT + REST_FORE_FLARE)
        out[f"arm_{side}_upper"] = round(upper_rest, 1)
        out[f"arm_{side}_fore"] = round(_wrap(fore_world - fore_src - upper_rest), 1)
    return out


def ground_point(alpha: np.ndarray, joints: dict[str, Point]) -> Point:
    """Where he touches the floor: between the shoes, at the bottom of the matte."""
    solid = alpha > 0.02
    ys, _ = np.nonzero(solid)
    x = (joints["ankle_l"][0] + joints["ankle_r"][0]) / 2.0
    return (round(x, 1), float(ys.max()) + 1.0)


# ---------------------------------------------------------------------------
# Building a whole rig
# ---------------------------------------------------------------------------

@dataclass
class BuiltRig:
    data: dict                        # exactly what goes into rig.json
    images: dict[str, Image.Image]    # part name -> RGBA crop
    cut: Cut


def build(
    cutout: Image.Image,
    joints: dict[str, Point],
    source_name: str = "source.png",
    caps: dict[str, float] | None = None,
    splits: dict[str, float] | None = None,
    free: set[str] | None = None,
) -> BuiltRig:
    """Cut a background-free RGBA image into parts and describe the skeleton."""
    arr = np.asarray(cutout.convert("RGBA"))
    rgb = arr[..., :3]
    alpha = arr[..., 3].astype(np.float32) / 255.0
    cut = cut_parts(alpha, joints, caps, splits, free)
    joints = cut.joints  # the shoulders may have been lifted onto the outline

    solid = alpha > 0.02
    parts_meta: dict[str, dict] = {}
    images: dict[str, Image.Image] = {}
    for name in sorted(BONES, key=lambda n: BONES[n].z):
        m = cut.masks[name]
        ys, xs = np.nonzero(m)
        x0, y0 = int(xs.min()), int(ys.min())
        x1, y1 = int(xs.max()) + 1, int(ys.max()) + 1
        pa = np.where(m, alpha, 0.0)[y0:y1, x0:x1]
        images[name] = Image.fromarray(
            np.dstack([rgb[y0:y1, x0:x1], (pa * 255).astype(np.uint8)]), "RGBA"
        )
        bone = BONES[name]
        px, py = joints[bone.pivot]
        parts_meta[name] = {
            "file": f"parts/{name}.png",
            "offset": [x0, y0],
            "size": [x1 - x0, y1 - y0],
            "pivot": [px - x0, py - y0],
            "pivot_src": [px, py],
            "parent": bone.parent,
            "z": bone.z,
        }

    fys, fxs = np.nonzero(solid)
    data = {
        "source": source_name,
        "source_size": [int(rgb.shape[1]), int(rgb.shape[0])],
        "figure_bbox": [int(fxs.min()), int(fys.min()), int(fxs.max()) + 1, int(fys.max()) + 1],
        "ground": list(ground_point(alpha, joints)),
        "rest_angles": rest_angles(joints),
        "joints": {k: [float(v[0]), float(v[1])] for k, v in joints.items()},
        "parts": parts_meta,
    }
    return BuiltRig(data, images, cut)


def write(built: BuiltRig, asset_dir: Path) -> Path:
    """Write parts/*.png and rig.json, replacing whatever was there."""
    asset_dir = Path(asset_dir)
    parts_dir = asset_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    for old in parts_dir.glob("*.png"):
        old.unlink()
    for name, img in built.images.items():
        img.save(parts_dir / f"{name}.png")
    rig_json = asset_dir / "rig.json"
    rig_json.write_text(json.dumps(built.data, indent=2) + "\n")
    return rig_json


# ---------------------------------------------------------------------------
# Guessing the joints
# ---------------------------------------------------------------------------

def _runs(row: np.ndarray) -> list[tuple[int, int]]:
    """Opaque spans in one row, as [start, end) pairs."""
    idx = np.nonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))[0]
    return list(zip(idx[0::2].tolist(), idx[1::2].tolist()))


def _run_at(row: np.ndarray, x: float) -> tuple[int, int] | None:
    for a, b in _runs(row):
        if a <= x < b:
            return (a, b)
    return None


def guess_joints(alpha: np.ndarray) -> dict[str, Point]:
    """A first stab at the 15 joints, for the user to nudge.

    Reads the silhouette the way the coordinates used to be measured by hand: the
    trunk is the band of columns tall enough to run head-to-foot, the arms are what
    sticks out sideways of it, and the crotch is the highest row where the trunk
    splits in two.
    """
    solid = alpha > 0.02
    if not solid.any():
        raise RigError("the matte is empty - remove the background first")
    h, w = solid.shape
    ys, xs = np.nonzero(solid)
    top, bottom = int(ys.min()), int(ys.max())
    height = bottom - top + 1

    # trunk: the columns tall enough to be body rather than arm
    col = solid.sum(axis=0)
    trunk_cols = np.nonzero(col > 0.55 * height)[0]
    if trunk_cols.size < 8:
        trunk_cols = np.nonzero(col > 0.35 * height)[0]
    if trunk_cols.size < 8:
        raise RigError("cannot find a trunk - is the figure standing upright?")
    tx0, tx1 = int(trunk_cols.min()), int(trunk_cols.max())
    cx = (tx0 + tx1) / 2.0
    trunk_w = tx1 - tx0

    # crotch: the highest row in the lower half where the trunk is two runs
    crotch = None
    for y in range(int(top + 0.45 * height), bottom):
        runs = [r for r in _runs(solid[y]) if r[1] > tx0 - 4 and r[0] < tx1 + 4]
        if len(runs) >= 2:
            crotch = y
            break
    if crotch is None:
        raise RigError("the legs never separate - the pose needs a gap between them")

    # neck: the narrowest row between the crown and the shoulder line
    shoulder_y = int(top + 0.20 * height)
    lo = int(top + 0.10 * height)
    widths = []
    for y in range(lo, max(lo + 2, shoulder_y + int(0.06 * height))):
        r = _run_at(solid[y], cx)
        widths.append((r[1] - r[0], y) if r else (10**6, y))
    # the neck is narrow over a stretch, and the pivot wants to be at the bottom
    # of it, where the collar is, rather than up under the jaw
    narrow = min(w for w, _ in widths) * 1.15
    neck_y = max(y for w, y in widths if w <= narrow)

    # shoulders: just inboard of the trunk edge, at the top of the sleeve there
    joints: dict[str, Point] = {
        "neck": (cx, float(neck_y)),
        "waist": (cx, float(crotch - 0.13 * height)),
        "hips": (cx, float(crotch - 0.03 * height)),
    }
    for side, sx in (("l", tx0 + 0.03 * trunk_w), ("r", tx1 - 0.03 * trunk_w)):
        rows = np.nonzero(solid[:, int(sx)])[0]
        joints[f"shoulder_{side}"] = (float(sx), float(rows.min()))

    # arms: fingertip to shoulder, with the elbow and wrist at the usual fractions
    for side, tip in (("l", int(xs.min())), ("r", int(xs.max()))):
        sx, _sy = joints[f"shoulder_{side}"]
        reach = tip - sx
        for name, frac in (("elbow", 0.42), ("wrist", 0.72)):
            x = sx + reach * frac
            rows = np.nonzero(solid[:, int(round(min(max(x, 0), w - 1)))])[0]
            y = float(rows.mean()) if rows.size else joints[f"shoulder_{side}"][1]
            joints[f"{name}_{side}"] = (float(round(x, 1)), round(y, 1))

    # legs: hips level with the crotch, ankles just above the shoe
    ankle_y = int(bottom - 0.085 * height)
    knee_y = int((crotch + ankle_y) / 2)
    for side, sign in (("l", -1.0), ("r", 1.0)):
        joints[f"hip_{side}"] = (cx + sign * 0.21 * trunk_w, float(crotch - 0.012 * height))
        for name, y in (("knee", knee_y), ("ankle", ankle_y)):
            runs = [r for r in _runs(solid[y]) if r[1] - r[0] > 0.02 * trunk_w]
            leg = None
            for a, b in runs:
                if (sign < 0 and (a + b) / 2 <= cx) or (sign > 0 and (a + b) / 2 > cx):
                    leg = (a, b) if leg is None else leg
            if leg is None and runs:
                leg = runs[0] if sign < 0 else runs[-1]
            mid = (leg[0] + leg[1]) / 2 if leg else cx + sign * 0.21 * trunk_w
            joints[f"{name}_{side}"] = (round(float(mid), 1), float(y))
    for tip, (before, pivot) in TIP_GUESS_FROM.items():
        joints[tip] = _tip_beyond(solid, joints[before], joints[pivot])
    return {k: (round(v[0], 1), round(v[1], 1)) for k, v in joints.items()}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_joints(path: Path):
    """Read a joints file: points, hand-set caps and seams, and pinned pivots."""
    raw = json.loads(Path(path).read_text())
    points = raw.get("joints", raw)
    return (
        {k: (float(v[0]), float(v[1])) for k, v in points.items()},
        {k: float(v) for k, v in raw.get("caps", {}).items()},
        {k: float(v) for k, v in raw.get("splits", {}).items()},
        set(raw.get("free", ())),
    )


def save_joints(
    path: Path,
    joints: dict[str, Point],
    caps: dict[str, float] | None = None,
    splits: dict[str, float] | None = None,
    free: set[str] | None = None,
) -> None:
    doc: dict = {"joints": {k: list(v) for k, v in joints.items()}}
    if caps:
        doc["caps"] = {k: round(v, 1) for k, v in sorted(caps.items())}
    if splits:
        doc["splits"] = {k: round(v, 1) for k, v in sorted(splits.items())}
    if free:
        doc["free"] = sorted(free)
    Path(path).write_text(json.dumps(doc, indent=2) + "\n")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cutout", type=Path, default=ROOT / "assets" / "cutout.png")
    ap.add_argument("--joints", type=Path, help="joints .json; omit to auto-guess")
    ap.add_argument("--out", type=Path, default=ROOT / "assets")
    ap.add_argument("--save-joints", type=Path, help="write the joints back out")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    img = Image.open(args.cutout).convert("RGBA")
    alpha = np.asarray(img)[..., 3].astype(np.float32) / 255.0
    if args.joints:
        joints, caps, splits, free = load_joints(args.joints)
    else:
        joints, caps, splits, free = guess_joints(alpha), {}, {}, set()
        print("guessed joints:")
        for name in JOINT_ORDER:
            print(f"  {name:12s} {joints[name]}")

    built = build(img, joints, caps=caps, splits=splits, free=free)
    cut = built.cut
    print(f"cutout {img.size}")
    for name in sorted(BONES, key=lambda n: BONES[n].z):
        meta = built.data["parts"][name]
        w, h = meta["size"]
        flag = "  cover<-parent" if BONES[name].on_outline else ""
        print(f"  {name:12s} {w:4d}x{h:4d} at {tuple(meta['offset'])}"
              f"  cap={cut.cap_radius[name]:5.1f}  width={cut.widths[name]:5.1f}{flag}")
    print(f"  unassigned pixels: {cut.unassigned * 100:.2f}%")
    print(f"  rest angles: {built.data['rest_angles']}")

    if args.save_joints:
        save_joints(args.save_joints, built.cut.joints, caps, splits, free)
        print(f"wrote {args.save_joints}")
    if not args.dry_run:
        print(f"wrote {write(built, args.out)}")


if __name__ == "__main__":
    main()
