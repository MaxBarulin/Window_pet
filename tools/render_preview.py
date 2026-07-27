"""Render rig animations offline, with Pillow, for eyeballing and for the README.

    python tools/render_preview.py --clip hiphop --gif out.gif
    python tools/render_preview.py --sheet all.png        # every clip, one row each

No Qt and no display needed, so this also runs in CI.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet.kinematics import Skeleton  # noqa: E402
from pet.poses import CLIPS  # noqa: E402
from pet.rigmath import Pose, Rig, translate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RIG_JSON = ROOT / "assets" / "rig.json"


class PilRenderer:
    """Draws a posed rig into an RGBA canvas."""

    def __init__(self, rig: Rig):
        self.rig = rig
        self.skeleton = Skeleton(rig)
        self.images = {
            name: Image.open(rig.part_file(name)).convert("RGBA")
            for name in rig.parts
        }

    def draw(self, spec, size, anchor, scale, flip=False) -> Image.Image:
        """`spec` is a PoseSpec; the feet of the resolved pose land on `anchor`."""
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        pose = self.skeleton.resolve(spec) if not isinstance(spec, Pose) else spec
        probe = self.rig.compose(pose, (0.0, 0.0), scale, flip)
        contact = self.skeleton.ground_y(probe)
        transforms = self.rig.compose(
            pose, (anchor[0], anchor[1] - contact), scale, flip
        )
        for name in self.rig.draw_order:
            ox, oy = self.rig.parts[name]["offset"]
            forward = transforms[name] @ translate(ox, oy)
            try:
                # Pillow maps output -> input, so it wants the inverse
                coeffs = forward.inverse().coeffs()
            except ZeroDivisionError:
                continue  # fully squashed away, e.g. mid spin
            layer = self.images[name].transform(
                size, Image.AFFINE, coeffs, resample=Image.BICUBIC
            )
            canvas.alpha_composite(layer)
        return canvas


def on_checker(img: Image.Image, tile: int = 12) -> Image.Image:
    w, h = img.size
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.where((((yy // tile) + (xx // tile)) % 2)[..., None], 208, 236)
    bg = Image.fromarray(np.repeat(base, 3, axis=2).astype(np.uint8), "RGB").convert("RGBA")
    bg.alpha_composite(img)
    return bg.convert("RGB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", default="hiphop")
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--height", type=int, default=260, help="pet height in px")
    ap.add_argument("--gif")
    ap.add_argument("--sheet")
    ap.add_argument("--fps", type=int, default=24)
    args = ap.parse_args()

    rig = Rig.load(RIG_JSON)
    ren = PilRenderer(rig)
    scale = args.height / rig.height()
    cw, ch = int(args.height * 1.30), int(args.height * 1.34)
    anchor = (cw / 2, ch - int(args.height * 0.09))

    def frames_for(clip_name: str, n: int) -> list[Image.Image]:
        clip = CLIPS[clip_name]
        out = []
        for i in range(n):
            pose = clip.at(clip.duration * i / n)
            out.append(ren.draw(pose, (cw, ch), anchor, scale))
        return out

    if args.sheet:
        names = list(CLIPS)
        n = args.frames
        sheet = Image.new("RGB", (cw * n, ch * len(names)), (250, 250, 250))
        for r, name in enumerate(names):
            for c, fr in enumerate(frames_for(name, n)):
                sheet.paste(on_checker(fr), (c * cw, r * ch))
        sheet.save(args.sheet)
        print(f"{args.sheet}  {sheet.size}  clips={names}")

    if args.gif:
        n = max(args.frames, 16)
        frs = [on_checker(f) for f in frames_for(args.clip, n)]
        frs[0].save(
            args.gif, save_all=True, append_images=frs[1:],
            duration=int(1000 / args.fps), loop=0, optimize=True,
        )
        print(f"{args.gif}  {n} frames  clip={args.clip}")


if __name__ == "__main__":
    main()
