# Asset pipeline

One photo, fifteen points, one rig.

```bash
python tools/rig_editor.py your-photo.jpg   # the whole thing, with a live preview
```

Or the same steps from a shell, if the points already exist:

```bash
python tools/cutout.py                          # background removal  (pip install "rembg[cpu]")
python tools/autorig.py --joints assets/joints.json
python tools/make_icon.py                       # app icon from the head
```

`cutout.py` is the only step that needs rembg, and its output is committed, so the
other two run anywhere. `autorig.py --dry-run` reports what it would cut without
writing anything, and with no `--joints` it guesses them from the silhouette.

## What you have to place, and where

| Point | Where |
| --- | --- |
| `neck` | Where the head meets the collar. |
| `waist` | Centre of the body at the belt. |
| `hips` | Centre of the pelvis, just above the crotch. |
| `shoulder_l` / `shoulder_r` | The **top** of each sleeve — see below. |
| `elbow_*`, `wrist_*` | Middle of the sleeve at the elbow; where the hand starts. |
| `hip_*` | The hip socket: inside the body, level with the crotch. |
| `knee_*`, `ankle_*` | Middle of the leg at the knee; where the leg meets the shoe. |

`_l` is screen-left. Everything else — the part outlines, the joint caps, the rest
pose that brings the arms down out of the T, the ground point, the draw order — is
derived. The editor guesses all fifteen to within about 3% of his height, so most
of the work is nudging.

## Gotchas worth knowing

* **The shoulders are the only pivots that belong on the outline**, at the top of
  each sleeve rather than on its centreline. Swinging an arm down maps "outward" to
  "downward", so material above the pivot swings out past the shoulder as a wing.
  `snap_joints` lifts the point there for you; you cannot get this one wrong by
  clicking too low, only by clicking on a different limb.
* **The hip sockets sit high**, level with the crotch, not down where the legs
  visibly separate. Put them low and the pelvis hangs over the top of the thighs;
  the bend then reads along the bottom edge of the pelvis instead of at the hip,
  and he looks like he has a second knee.
* **The wheel is the tuning knob.** Over a selected joint it resizes that joint's
  cap; shift plus wheel slides the seam along the bone. Both land in
  `assets/joints.json` under `caps` and `splits`, so they survive a rebuild.
* **Hands and feet cost no clicks.** There is no joint past a wrist or an ankle,
  so their bones are read off the material beyond the pivot.
* **Parts overlap, and should.** Neighbouring pieces share a band along the bone so
  a bent elbow or knee shows no gap. `autorig.py` reports how many opaque pixels
  ended up in no part at all; it should stay well under 1%.
* **A photo where limbs touch cannot be rigged**, by this tool or any other of this
  kind. Two overlapping arms are one region in one layer, and there is nothing
  underneath to uncover. If `autorig` reports a part that "came out empty", or the
  preview shows a limb fused to the body, the photo is the problem.

## Verifying a change

`tools/render_preview.py` and `tools/render_demo.py` both render offline with Pillow,
no display required:

```bash
python tools/render_preview.py --sheet clips.png --frames 8   # all clips
python tools/render_demo.py --out demo.gif --seed 58          # behaviour on a mock desktop
```

`tests/test_autorig.py` asserts the properties rather than the pixels — that no part
holds material behind its own pivot, that every cap is a disc that fits inside the
silhouette, that the pelvis stops at the hips — so it still means something on a
photo it has never seen.
