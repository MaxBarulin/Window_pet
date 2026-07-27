"""Skeletal maths for the cutout rig.

Deliberately pure Python, no numpy. The rig only ever needs 2D affine transforms -
a handful of 3x3 matrix multiplies per frame - and pulling numpy into the frozen
exe for that cost ~40 MB and, more importantly, dragged in C extensions that fail
to load on some Windows machines ("Importing the numpy C-extensions failed").
The tools that build assets still use numpy; the app no longer does.

A transform is stored as the six meaningful coefficients of

    | a  b  c |
    | d  e  f |
    | 0  0  1 |

so that x' = a*x + b*y + c and y' = d*x + e*y + f. Affine transforms are closed
under composition, so the bottom row never needs storing.

Coordinates are source-photo pixels until the very last step, where `compose`
maps them to screen pixels.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Matrix:
    """A 2D affine transform. Immutable, so transforms can be shared freely."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 0.0
    e: float = 1.0
    f: float = 0.0

    def __matmul__(self, other: "Matrix") -> "Matrix":
        """self @ other - apply `other` first, then `self`."""
        return Matrix(
            self.a * other.a + self.b * other.d,
            self.a * other.b + self.b * other.e,
            self.a * other.c + self.b * other.f + self.c,
            self.d * other.a + self.e * other.d,
            self.d * other.b + self.e * other.e,
            self.d * other.c + self.e * other.f + self.f,
        )

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (self.a * x + self.b * y + self.c,
                self.d * x + self.e * y + self.f)

    def translated(self, dx: float, dy: float) -> "Matrix":
        """This transform followed by a translation."""
        return Matrix(self.a, self.b, self.c + dx, self.d, self.e, self.f + dy)

    @property
    def determinant(self) -> float:
        return self.a * self.e - self.b * self.d

    def inverse(self) -> "Matrix":
        det = self.determinant
        if det == 0.0:
            raise ZeroDivisionError("transform is singular and cannot be inverted")
        return Matrix(
            self.e / det,
            -self.b / det,
            (self.b * self.f - self.c * self.e) / det,
            -self.d / det,
            self.a / det,
            (self.c * self.d - self.a * self.f) / det,
        )

    def coeffs(self) -> tuple[float, float, float, float, float, float]:
        """(a, b, c, d, e, f) - the order Pillow's Image.AFFINE wants."""
        return (self.a, self.b, self.c, self.d, self.e, self.f)

    def approx(self, other: "Matrix", tol: float = 1e-9) -> bool:
        return all(abs(p - q) <= tol for p, q in zip(self.coeffs(), other.coeffs()))


def identity() -> Matrix:
    return Matrix()


def translate(tx: float, ty: float) -> Matrix:
    return Matrix(1.0, 0.0, tx, 0.0, 1.0, ty)


def scale(sx: float, sy: float | None = None) -> Matrix:
    if sy is None:
        sy = sx
    return Matrix(sx, 0.0, 0.0, 0.0, sy, 0.0)


def rotate(deg: float) -> Matrix:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return Matrix(c, -s, 0.0, s, c, 0.0)


def about(m: Matrix, px: float, py: float) -> Matrix:
    """Apply `m` around the point (px, py) instead of the origin."""
    return translate(px, py) @ m @ translate(-px, -py)


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return m.apply(x, y)


@dataclass
class Pose:
    """A rig pose: joint angles plus a whole-body offset and squash.

    `angles` maps part name -> degrees, clockwise-positive on screen. Missing
    parts are simply unrotated, so a pose only names what it moves.
    """

    angles: dict[str, float] = field(default_factory=dict)
    offset: tuple[float, float] = (0.0, 0.0)  # source px, moves the whole body
    squash: tuple[float, float] = (1.0, 1.0)  # about the hips, for bounce/impact

    def lerp(self, other: "Pose", t: float) -> "Pose":
        t = max(0.0, min(1.0, t))
        keys = set(self.angles) | set(other.angles)
        return Pose(
            angles={
                k: self.angles.get(k, 0.0) * (1 - t) + other.angles.get(k, 0.0) * t
                for k in keys
            },
            offset=(
                self.offset[0] * (1 - t) + other.offset[0] * t,
                self.offset[1] * (1 - t) + other.offset[1] * t,
            ),
            squash=(
                self.squash[0] * (1 - t) + other.squash[0] * t,
                self.squash[1] * (1 - t) + other.squash[1] * t,
            ),
        )

    def mirrored(self, rig: "Rig") -> "Pose":
        """Swap left/right joint angles (used to reuse one step for both legs)."""
        out: dict[str, float] = {}
        for name, ang in self.angles.items():
            out[rig.mirror_name(name)] = -ang if name == rig.root else ang
        return Pose(angles=out, offset=self.offset, squash=self.squash)


class Rig:
    """The part hierarchy loaded from assets/rig.json."""

    def __init__(self, data: dict, asset_dir: Path):
        self.asset_dir = asset_dir
        self.source_size: tuple[int, int] = tuple(data["source_size"])  # type: ignore
        self.ground: tuple[float, float] = tuple(data["ground"])  # type: ignore
        self.joints: dict[str, tuple[float, float]] = {
            k: tuple(v) for k, v in data["joints"].items()  # type: ignore
        }
        self.parts: dict[str, dict] = data["parts"]
        self.figure_bbox: tuple[int, int, int, int] = tuple(data["figure_bbox"])  # type: ignore
        # The photo is a T-pose, so the rig carries a rest offset that brings the
        # arms down. Clips are authored as deltas on top of it.
        self.rest: dict[str, float] = data.get("rest_angles", {})

        roots = [n for n, p in self.parts.items() if p["parent"] is None]
        if len(roots) != 1:
            raise ValueError(f"expected exactly one root part, got {roots}")
        self.root = roots[0]

        # parents before children, so a single pass can accumulate transforms
        self.chain_order = self._topological()
        self.draw_order = sorted(self.parts, key=lambda n: self.parts[n]["z"])

    @classmethod
    def load(cls, rig_json: Path) -> "Rig":
        data = json.loads(Path(rig_json).read_text())
        return cls(data, Path(rig_json).parent)

    def _topological(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def visit(name: str, trail: tuple[str, ...] = ()) -> None:
            if name in seen:
                return
            if name in trail:
                raise ValueError(f"cycle in rig hierarchy: {trail + (name,)}")
            parent = self.parts[name]["parent"]
            if parent is not None:
                visit(parent, trail + (name,))
            seen.add(name)
            out.append(name)

        for n in self.parts:
            visit(n)
        return out

    def part_file(self, name: str) -> Path:
        return self.asset_dir / self.parts[name]["file"]

    def pivot(self, name: str) -> tuple[float, float]:
        return tuple(self.parts[name]["pivot_src"])  # type: ignore

    @staticmethod
    def mirror_name(name: str) -> str:
        if "_l_" in name:
            return name.replace("_l_", "_r_")
        if "_r_" in name:
            return name.replace("_r_", "_l_")
        if name.endswith("_l"):
            return name[:-2] + "_r"
        if name.endswith("_r"):
            return name[:-2] + "_l"
        return name

    def height(self) -> int:
        x0, y0, x1, y1 = self.figure_bbox
        return y1 - y0

    # -- posing ------------------------------------------------------------

    def local_transforms(self, pose: Pose) -> dict[str, Matrix]:
        out = {}
        for name in self.parts:
            ang = self.rest.get(name, 0.0) + pose.angles.get(name, 0.0)
            px, py = self.pivot(name)
            out[name] = about(rotate(ang), px, py)
        return out

    def world_transforms(self, pose: Pose) -> dict[str, Matrix]:
        """Accumulate each part's transform down the bone hierarchy."""
        local = self.local_transforms(pose)
        hx, hy = self.pivot(self.root)
        root_extra = translate(*pose.offset) @ about(scale(*pose.squash), hx, hy)

        world: dict[str, Matrix] = {}
        for name in self.chain_order:
            parent = self.parts[name]["parent"]
            base = root_extra if parent is None else world[parent]
            world[name] = base @ local[name]
        return world

    def compose(
        self,
        pose: Pose,
        anchor: tuple[float, float],
        scale_factor: float,
        flip: bool = False,
    ) -> dict[str, Matrix]:
        """Full source-pixel -> screen-pixel transform per part.

        `anchor` is where the ground point (between his feet) should land.
        `flip` mirrors him horizontally so he can face the other way.
        """
        gx, gy = self.ground
        view = translate(*anchor) @ scale(scale_factor)
        if flip:
            view = view @ scale(-1.0, 1.0)
        view = view @ translate(-gx, -gy)
        return {n: view @ m for n, m in self.world_transforms(pose).items()}

    def bounds(self, transforms: dict[str, Matrix]) -> tuple[float, float, float, float]:
        """Screen-space bounding box of every posed part."""
        xs: list[float] = []
        ys: list[float] = []
        for name, m in transforms.items():
            ox, oy = self.parts[name]["offset"]
            w, h = self.parts[name]["size"]
            for cx, cy in ((ox, oy), (ox + w, oy), (ox, oy + h), (ox + w, oy + h)):
                x, y = m.apply(cx, cy)
                xs.append(x)
                ys.append(y)
        return min(xs), min(ys), max(xs), max(ys)
