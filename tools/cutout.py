"""Cut the figure out of a photo and cache it as assets/cutout.png.

Uses rembg (U^2-Net). Thresholding cannot do this job: the shirt is light grey
(~192-227) and the studio backdrop is white (~243-251), so any global brightness
cut either eats the sleeves or leaks into them.

The result is committed, so tools/autorig.py can re-cut the rig parts without
rembg or its 176 MB model. `tools/rig_editor.py` calls `cutout` directly.

    pip install "rembg[cpu]"
    python tools/cutout.py

The model is cached in models/ inside the project, downloaded on first use.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "source.png"
OUT = ROOT / "assets" / "cutout.png"

# rembg keeps its 176 MB model in ~/.u2net by default, which is invisible from
# the project and easy to lose. Point it at models/ in the repo instead: it is
# downloaded once, on the first background removal, and every run after that
# finds it already there. Respect U2NET_HOME if somebody has set it deliberately.
MODEL_DIR = ROOT / "models"
os.environ.setdefault("U2NET_HOME", str(MODEL_DIR))


def model_path() -> Path:
    """Where the matting model lives, downloaded or not."""
    return Path(os.environ["U2NET_HOME"]) / "u2net.onnx"


def model_present() -> bool:
    return model_path().exists()


def clean_matte(cut: Image.Image) -> tuple[Image.Image, int]:
    """Keep only the person, and pull the edge in by a pixel.

    Returns the cleaned image and how many stray blobs were dropped. Soft edge
    pixels still carry the backdrop in their RGB, which reads as a light halo
    against a dark window, so the alpha is hardened and eroded slightly.
    """
    a = np.asarray(cut.convert("RGBA")).astype(np.float32)
    rgb, alpha = a[..., :3], a[..., 3] / 255.0

    dropped = 0
    lab, n = ndi.label(alpha > 0.5)
    if n > 1:
        sizes = ndi.sum(alpha > 0.5, lab, range(1, n + 1))
        keep = int(np.argmax(sizes)) + 1
        alpha[(lab != keep) & (lab != 0)] = 0.0
        dropped = n - 1
    alpha = ndi.binary_fill_holes(alpha > 0.5) * np.maximum(alpha, 0.0)

    alpha = np.clip((alpha - 0.30) / 0.55, 0.0, 1.0)
    core = ndi.binary_erosion(alpha > 0.02, np.ones((3, 3), bool))
    alpha = np.minimum(alpha, ndi.gaussian_filter(core.astype(np.float32), 0.6))

    out = np.dstack([rgb, alpha * 255]).astype(np.uint8)
    return Image.fromarray(out, "RGBA"), dropped


def cutout(src: Image.Image) -> Image.Image:
    """Remove the background from a photo. Needs rembg installed."""
    Path(os.environ["U2NET_HOME"]).mkdir(parents=True, exist_ok=True)
    from rembg import new_session, remove  # imported late: heavy, optional

    return clean_matte(remove(src.convert("RGB"), session=new_session("u2net")))[0]


def main() -> None:
    src = Image.open(SRC).convert("RGB")
    print(f"source {SRC.name} {src.size}")
    print(f"model   {model_path()}"
          + ("" if model_present() else "  (will be downloaded once)"))
    out = cutout(src)
    out.save(OUT)
    alpha = np.asarray(out)[..., 3]
    ys, xs = np.nonzero(alpha > 5)
    print(f"  bbox x {xs.min()}..{xs.max()}  y {ys.min()}..{ys.max()}")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
