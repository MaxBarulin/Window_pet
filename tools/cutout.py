"""Cut the figure out of the source photo and cache it as assets/cutout.png.

Uses rembg (U^2-Net). Thresholding cannot do this job: the shirt is light grey
(~192-227) and the studio backdrop is white (~243-251), so any global brightness
cut either eats the sleeves or leaks into them.

The result is committed, so tools/build_assets.py can re-cut the rig parts without
rembg or its 176 MB model.

    pip install "rembg[cpu]"
    python tools/cutout.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "source.png"
OUT = ROOT / "assets" / "cutout.png"


def main() -> None:
    from rembg import new_session, remove  # imported late: heavy, optional

    src = Image.open(SRC).convert("RGB")
    print(f"source {SRC.name} {src.size}")
    cut = remove(src, session=new_session("u2net"))

    a = np.asarray(cut).astype(np.float32)
    rgb, alpha = a[..., :3], a[..., 3] / 255.0

    # keep only the person, drop any stray specks
    lab, n = ndi.label(alpha > 0.5)
    if n > 1:
        sizes = ndi.sum(alpha > 0.5, lab, range(1, n + 1))
        keep = int(np.argmax(sizes)) + 1
        alpha[(lab != keep) & (lab != 0)] = 0.0
        print(f"  dropped {n - 1} stray blob(s)")
    alpha = ndi.binary_fill_holes(alpha > 0.5) * np.maximum(alpha, 0.0)

    # Harden the matte a little and pull it in by a pixel. Soft edge pixels still
    # carry the white backdrop in their RGB, which would read as a light halo
    # against a dark window.
    alpha = np.clip((alpha - 0.30) / 0.55, 0.0, 1.0)
    core = ndi.binary_erosion(alpha > 0.02, np.ones((3, 3), bool))
    alpha = np.minimum(alpha, ndi.gaussian_filter(core.astype(np.float32), 0.6))

    out = np.dstack([rgb, alpha * 255]).astype(np.uint8)
    Image.fromarray(out, "RGBA").save(OUT)
    ys, xs = np.nonzero(alpha > 0.02)
    print(f"  bbox x {xs.min()}..{xs.max()}  y {ys.min()}..{ys.max()}")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
