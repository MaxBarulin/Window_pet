"""Render an animated GIF of the pet living on a mock desktop.

This drives the real Behavior state machine against a scripted FakeDesktop, so it
exercises the whole stack - terrain, physics, clip selection, facing, the sitting
anchor - and shows what it actually looks like. Handy for the README and as a
visual check that unit tests cannot give you.

    python tools/render_demo.py --out demo.gif --seconds 12
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet import config as C  # noqa: E402
from pet.behavior import Behavior, Pet, State  # noqa: E402
from pet.desktop import FakeDesktop, Monitor, Rect  # noqa: E402
from pet.poses import CLIPS  # noqa: E402
from pet.rigmath import Rig  # noqa: E402
from tools.render_preview import PilRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

SCREEN = Rect(0, 0, 1280, 720)
TASKBAR = 34

# Laid out so he has somewhere to go: two tall windows whose title bars are
# ledges, a low box he can hop up onto, and open floor in between. Overlapping
# them all would leave him boxed in, turning on the spot.
WINDOWS = [
    Rect(150, 300, 520, 690),
    Rect(800, 360, 1170, 690),
    Rect(560, 661, 745, 745),
]


def scene(size: tuple[int, int]) -> Image.Image:
    """A flat mock desktop to stand on."""
    img = Image.new("RGB", size, (32, 38, 52))
    d = ImageDraw.Draw(img)
    for i in range(0, size[1], 3):  # subtle wallpaper gradient
        t = i / size[1]
        d.line([(0, i), (size[0], i)], fill=(int(30 + 26 * t), int(36 + 30 * t), int(54 + 34 * t)))
    for w in WINDOWS:
        d.rectangle([w.x0, w.y0, w.x1, w.y1], fill=(246, 247, 250), outline=(120, 128, 145))
        d.rectangle([w.x0, w.y0, w.x1, w.y0 + 30], fill=(224, 228, 236), outline=(120, 128, 145))
        for k, col in enumerate(((236, 106, 94), (240, 190, 90), (120, 200, 120))):
            cx = w.x0 + 16 + k * 18
            d.ellipse([cx - 5, w.y0 + 10, cx + 5, w.y0 + 20], fill=col)
    d.rectangle([0, SCREEN.y1 - TASKBAR, SCREEN.x1, SCREEN.y1], fill=(22, 26, 38))
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="demo.gif")
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--fps", type=int, default=16)
    ap.add_argument("--height", type=int, default=180, help="pet height in desktop px")
    ap.add_argument("--width", type=int, default=640, help="output width")
    ap.add_argument("--seed", type=int, default=6)
    args = ap.parse_args()

    rig = Rig.load(ROOT / "assets" / "rig.json")
    ren = PilRenderer(rig)
    settings = C.Settings(pet_height=args.height)
    desktop = FakeDesktop(
        [Monitor(SCREEN, Rect(SCREEN.x0, SCREEN.y0, SCREEN.x1, SCREEN.y1 - TASKBAR))],
        list(WINDOWS),
    )
    beh = Behavior(Pet(x=640.0, y=60.0, airborne=True), rng=random.Random(args.seed),
                   settings=settings)

    sub = 3  # physics substeps per rendered frame, for stable motion
    dt = 1.0 / (args.fps * sub)
    scale = args.height / rig.height()
    backdrop = scene((SCREEN.x1, SCREEN.y1))
    out_size = (args.width, int(args.width * SCREEN.y1 / SCREEN.x1))

    frames: list[Image.Image] = []
    events: list[str] = []
    for _ in range(int(args.seconds * args.fps)):
        for _ in range(sub):
            beh.update(desktop.snapshot(), dt)
        p = beh.pet
        if p.last_event and (not events or events[-1] != p.last_event):
            events.append(p.last_event)

        pose = CLIPS.get(p.clip, CLIPS["idle"]).at(p.clip_time)
        anchor_y = p.y
        if p.anchor_kind == "hips":
            anchor_y = p.y + (rig.ground[1] - rig.pivot("pelvis")[1]) * scale

        layer = ren.draw(pose, (SCREEN.x1, SCREEN.y1), (p.x, anchor_y), scale,
                         flip=p.facing < 0)
        frame = backdrop.copy().convert("RGBA")
        frame.alpha_composite(layer)
        frames.append(frame.convert("RGB").resize(out_size, Image.LANCZOS))

    out = Path(args.out)
    frames[0].save(
        out, save_all=True, append_images=frames[1:],
        duration=int(1000 / args.fps), loop=0, optimize=True,
    )
    kb = out.stat().st_size / 1024
    print(f"{out}  {len(frames)} frames  {out_size[0]}x{out_size[1]}  {kb:.0f} KB")
    print("events seen: " + ", ".join(events[:14]))


if __name__ == "__main__":
    main()
