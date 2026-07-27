"""Cut the rig parts out of assets/cutout.png and write assets/rig.json.

The source photo is a T-pose, which is close to ideal for a cutout rig: nothing is
occluded, both hands are fully visible and the arms are clear of the torso, so no
part has to be reconstructed.

Two details make the joints hold up:

* Each arm piece reaches ~30px *past* its shoulder pivot, into the torso, and the
  arms draw behind the torso. The pivot therefore sits under solid torso pixels
  and swinging an arm down from the T cannot tear the shoulder open.
* Neighbouring pieces overlap along the bone, so a bent elbow or knee shows no gap.

Coordinates are source pixels, measured off the matte (see tools/README.md).

    python tools/build_assets.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parent.parent
CUTOUT = ROOT / "assets" / "cutout.png"
OUT_DIR = ROOT / "assets" / "parts"
RIG_JSON = ROOT / "assets" / "rig.json"

# ---------------------------------------------------------------------------
# Anatomy in source pixels (896x1184 photo)
# ---------------------------------------------------------------------------

JOINTS = {
    "neck": (442, 300),
    "waist": (443, 600),
    "hips": (443, 700),
    # Deliberately at the *top* of the sleeve, not on its centreline. Swinging the
    # arm down from the T maps "outward" to "downward", so any arm material above
    # the pivot swings outboard and pokes out past the shoulder, while material
    # below it swings inboard and stays hidden behind the torso.
    "shoulder_l": (356, 332),
    "shoulder_r": (528, 332),
    "elbow_l": (228, 372),
    "elbow_r": (652, 372),
    "wrist_l": (112, 366),
    "wrist_r": (776, 364),
    "hip_l": (400, 718),
    "hip_r": (487, 718),
    "knee_l": (393, 886),
    "knee_r": (494, 886),
    "ankle_l": (390, 1046),
    "ankle_r": (494, 1046),
}

# Bottom centre between the two shoes: the point that rests on a window edge.
GROUND = (446, 1128)

# The photo is a T-pose, so "arms down" is a rest offset the clips build on top of.
REST_ANGLES = {
    "arm_l_upper": -68.0,
    "arm_l_fore": -6.0,
    "arm_r_upper": 68.0,
    "arm_r_fore": 6.0,
}

# Arm pieces are clamped to start at this y so they carry no material above the
# shoulder pivots; the torso's shoulder cap owns the sleeve tops instead.
_ARM_TOP = 330

_LEG_SPLIT = 444  # the gap between the legs sits here at every height

PART_POLYS: dict[str, list[tuple[int, int]]] = {
    "head": [
        (404, 144), (438, 140), (462, 142), (482, 156), (492, 184), (495, 216),
        (489, 246), (478, 266), (476, 290), (478, 320), (406, 320), (408, 290),
        (405, 266), (394, 244), (387, 212), (390, 174),
    ],
    # Collar down to just below the belt. The shoulders follow the body's own
    # slope and stop where the sleeve begins - taking the full T-pose width here
    # leaves a flat shelf sticking out once the arm swings down. The cap sits a
    # few px wider than his real silhouette so it covers each arm root.
    "torso": [
        (400, 292), (484, 292), (506, 302), (524, 318), (540, 340), (546, 372),
        (548, 440), (538, 500), (534, 560), (536, 606), (538, 640), (348, 640),
        (350, 606), (352, 560), (348, 500), (338, 440), (340, 372), (346, 340),
        (362, 318), (380, 302),
    ],
    "pelvis": [
        (348, 592), (540, 592), (546, 640), (546, 700), (540, 748), (500, 756),
        (444, 752), (388, 756), (348, 748), (342, 700), (342, 640),
    ],
    # Arms are simple boxes clipped by the matte: the inner edge reaches well past
    # the shoulder pivot (hidden behind the torso) and the top is clamped to
    # _ARM_TOP. The forearm boxes overlap the upper arms across the elbow.
    "arm_l_upper": [(394, _ARM_TOP), (394, 426), (200, 426), (200, _ARM_TOP)],
    "arm_l_fore": [(254, 300), (254, 426), (0, 426), (0, 300)],
    "arm_r_upper": [(490, _ARM_TOP), (490, 426), (684, 426), (684, _ARM_TOP)],
    "arm_r_fore": [(628, 282), (628, 426), (896, 426), (896, 282)],
    "thigh_l": [
        (344, 690), (_LEG_SPLIT, 690), (_LEG_SPLIT, 800), (_LEG_SPLIT, 910),
        (352, 910), (344, 800),
    ],
    "thigh_r": [
        (_LEG_SPLIT, 690), (546, 690), (540, 800), (536, 910), (_LEG_SPLIT, 910),
        (_LEG_SPLIT, 800),
    ],
    "shin_l": [
        (348, 856), (_LEG_SPLIT, 856), (_LEG_SPLIT, 1000), (_LEG_SPLIT, 1140),
        (330, 1140), (330, 1060), (340, 960),
    ],
    "shin_r": [
        (_LEG_SPLIT, 856), (540, 856), (546, 960), (560, 1060), (560, 1140),
        (_LEG_SPLIT, 1140), (_LEG_SPLIT, 1000),
    ],
}

# name -> (parent, pivot joint, draw order). Lower z draws first, so both arms sit
# behind the torso and the shirt covers each shoulder pivot.
RIG = {
    "arm_r_upper": ("torso", "shoulder_r", 0),
    "arm_r_fore": ("arm_r_upper", "elbow_r", 1),
    "arm_l_upper": ("torso", "shoulder_l", 2),
    "arm_l_fore": ("arm_l_upper", "elbow_l", 3),
    "thigh_r": ("pelvis", "hip_r", 4),
    "shin_r": ("thigh_r", "knee_r", 5),
    "thigh_l": ("pelvis", "hip_l", 6),
    "shin_l": ("thigh_l", "knee_l", 7),
    "pelvis": (None, "hips", 8),
    "torso": ("pelvis", "waist", 9),
    "head": ("torso", "neck", 10),
}


def poly_mask(poly: list[tuple[int, int]], shape: tuple[int, int]) -> np.ndarray:
    img = Image.new("L", (shape[1], shape[0]), 0)
    ImageDraw.Draw(img).polygon(poly, fill=255)
    return np.asarray(img) > 127


def main() -> None:
    if not CUTOUT.exists():
        raise SystemExit(f"missing {CUTOUT}; run tools/cutout.py first")
    cut = Image.open(CUTOUT).convert("RGBA")
    arr = np.asarray(cut)
    rgb = arr[..., :3]
    alpha = arr[..., 3].astype(np.float32) / 255.0
    print(f"cutout {cut.size}  coverage {(alpha > 0.02).mean():.4f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.png"):
        old.unlink()

    solid = alpha > 0.02
    claimed = np.zeros_like(solid)
    parts_meta = {}
    for name in sorted(PART_POLYS, key=lambda n: RIG[n][2]):
        m = poly_mask(PART_POLYS[name], alpha.shape) & solid

        lab, n = ndi.label(m)
        if n > 1:  # drop slivers of a neighbouring limb caught by the polygon
            sizes = ndi.sum(m, lab, range(1, n + 1))
            m = lab == (int(np.argmax(sizes)) + 1)
        if not m.any():
            raise SystemExit(f"part {name} came out empty")
        claimed |= m

        ys, xs = np.nonzero(m)
        x0, y0 = int(xs.min()), int(ys.min())
        x1, y1 = int(xs.max()) + 1, int(ys.max()) + 1
        pa = np.where(m, alpha, 0.0)[y0:y1, x0:x1]
        rgba = np.dstack([rgb[y0:y1, x0:x1], (pa * 255).astype(np.uint8)])
        Image.fromarray(rgba, "RGBA").save(OUT_DIR / f"{name}.png")

        parent, joint, z = RIG[name]
        px, py = JOINTS[joint]
        parts_meta[name] = {
            "file": f"parts/{name}.png",
            "offset": [x0, y0],
            "size": [x1 - x0, y1 - y0],
            "pivot": [px - x0, py - y0],
            "pivot_src": [px, py],
            "parent": parent,
            "z": z,
        }
        print(f"  {name:12s} {x1-x0:4d}x{y1-y0:4d} at ({x0:4d},{y0:4d})  px={int(m.sum())}")

    missed = int((solid & ~claimed).sum())
    print(f"  unassigned pixels: {missed} ({missed / solid.sum() * 100:.2f}%)")

    fys, fxs = np.nonzero(solid)
    rig = {
        "source": "source.png",
        "source_size": [int(rgb.shape[1]), int(rgb.shape[0])],
        "figure_bbox": [int(fxs.min()), int(fys.min()), int(fxs.max()) + 1, int(fys.max()) + 1],
        "ground": list(GROUND),
        "rest_angles": REST_ANGLES,
        "joints": {k: list(v) for k, v in JOINTS.items()},
        "parts": parts_meta,
    }
    RIG_JSON.write_text(json.dumps(rig, indent=2) + "\n")
    print(f"wrote {RIG_JSON.relative_to(ROOT)} and {len(parts_meta)} part PNGs")


if __name__ == "__main__":
    main()
