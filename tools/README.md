# Asset pipeline

Three scripts turn one photo into a rig. Run them in this order:

```bash
python tools/cutout.py        # background removal  (needs: pip install "rembg[cpu]")
python tools/build_assets.py  # cut parts + write rig.json
python tools/make_icon.py     # app icon from the head
```

`cutout.py` is the only step that needs rembg, and its output is committed, so the
other two run anywhere.

## How the coordinates in build_assets.py were measured

Every number in `JOINTS` and `PART_POLYS` is a pixel coordinate in the 896×1184
source photo. They were read off the matte rather than guessed, using two probes:

**Row spans** — for a given `y`, the runs of opaque pixels. This gives the silhouette
width at any height and shows exactly where the legs separate:

```python
import numpy as np
from PIL import Image
a = np.asarray(Image.open("assets/cutout.png").convert("RGBA"))[:, :, 3] > 110
for y in (325, 430, 700, 760, 886, 1046):
    row = a[y]
    runs, s = [], None
    for x in range(row.size):
        if row[x] and s is None:
            s = x
        elif not row[x] and s is not None:
            runs.append((s, x - 1)); s = None
    print(y, runs)
```

**Column spans** — the vertical extent at a given `x`. The arms are horizontal in a
T-pose, so this is what locates the elbows, wrists and sleeve thickness:

```python
for x in (120, 228, 320, 652, 776):
    col = np.nonzero(a[:, x])[0]
    print(x, (col.min(), col.max()))
```

What the measurements gave for this photo:

| Landmark | Reading |
| --- | --- |
| Head top / chin | y 156 / ~272 |
| Collar, shoulder line | y ~300, y 325 spans x 345–539 |
| Torso width | x 335–542 at y 430 |
| Belt | y ~600–620 |
| Crotch (legs separate) | y ~730 |
| Leg split | x 444, at every height |
| Knees | y ~886 |
| Ankles / shoe bottom | y ~1046 / 1128 |
| Sleeve centreline | y ~372 |
| Sleeve top at the shoulder | y 321, dropping to ~348 by x 270 |
| Left wrist / right wrist | x ~112 / ~776 |

## Gotchas worth knowing before you re-measure

* **The shoulder pivots are at the top of the sleeve, not its middle.** See the
  README's rigging section; putting them on the centreline makes wings sprout at the
  shoulders when the arms come down.
* **Arm pieces are clamped to `_ARM_TOP`** so they hold no material above the pivot,
  and they reach ~30px past the pivot into the torso, which draws over them.
* **Parts may overlap, and should.** Neighbouring pieces share a band along the bone
  so a bent elbow or knee shows no gap. `build_assets.py` reports how many opaque
  pixels ended up in no part at all; it should stay well under 1%.
* **The largest-blob filter** in each part drops slivers of a neighbouring limb that
  a polygon catches by accident, so polygons can be generous.
* **`EXTEND_UP`** exists for parts whose top is cut away by a garment and would have
  nothing left to swing with. The current photo does not need it; the mechanism is
  kept because a skirt or long coat would.

## Verifying a change

`tools/render_preview.py` and `tools/render_demo.py` both render offline with Pillow,
no display required:

```bash
python tools/render_preview.py --sheet clips.png --frames 8   # all clips
python tools/render_demo.py --out demo.gif --seed 58          # behaviour on a mock desktop
```

To check a re-cut rig reassembles exactly, composite the parts at rest in z-order and
compare against `assets/cutout.png` — at rest it should be pixel-identical apart from
the pixels no part claimed.
