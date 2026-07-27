"""Build assets/icon.png and assets/icon.ico from the rig's head part.

    python tools/make_icon.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> None:
    rig = json.loads((ASSETS / "rig.json").read_text())
    head = Image.open(ASSETS / rig["parts"]["head"]["file"]).convert("RGBA")

    a = np.asarray(head)[..., 3]
    ys, xs = np.nonzero(a > 16)
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    head = head.crop(box)

    # square canvas with a little breathing room
    side = int(max(head.size) * 1.18)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(
        head, ((side - head.width) // 2, (side - head.height) // 2)
    )

    png = canvas.resize((256, 256), Image.LANCZOS)
    png.save(ASSETS / "icon.png")
    png.save(ASSETS / "icon.ico", sizes=[(s, s) for s in SIZES])
    print(f"wrote {ASSETS/'icon.png'} and icon.ico ({len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
