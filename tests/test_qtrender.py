"""Render checks for the Qt path.

These run offscreen, so they work in CI and on this Linux box. They exist because
the QTransform conversion is easy to get subtly wrong (Qt's matrix is transposed
relative to the numpy one) and a mistake there is invisible until you see the pet
sheared on screen.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

import numpy as np  # noqa: E402
from PySide6.QtGui import QImage, QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import config as C  # noqa: E402
from pet.behavior import State  # noqa: E402
from pet.poses import CLIPS  # noqa: E402
from pet.qtapp import PetWindow, _qtransform  # noqa: E402
from pet.rigmath import Rig, apply, rotate, scale, translate  # noqa: E402
from tests.test_rigmath import RIG_JSON  # noqa: E402


@pytest.fixture(scope="module")
def app():
    inst = QApplication.instance() or QApplication([])
    yield inst


@pytest.fixture(scope="module")
def rig() -> Rig:
    return Rig.load(RIG_JSON)


@pytest.mark.parametrize(
    "m",
    [
        translate(13, -7),
        scale(2.0, 0.5),
        rotate(37),
        translate(50, 60) @ rotate(-24) @ scale(1.3, 0.8),
    ],
)
def test_qtransform_matches_numpy(m):
    """Qt's matrix is transposed; this is the conversion that catches it."""
    qt = _qtransform(m)
    for x, y in ((0, 0), (10, 0), (0, 10), (37, -12)):
        want = apply(m, x, y)
        got = qt.map(float(x), float(y))
        assert got[0] == pytest.approx(want[0], abs=1e-6)
        assert got[1] == pytest.approx(want[1], abs=1e-6)


def test_qtransform_is_not_accidentally_symmetric():
    """A transposed-but-symmetric test matrix would hide the bug above."""
    m = rotate(30)
    assert not np.allclose(m[:2, :2], m[:2, :2].T)


def _window(app, rig, **kw) -> PetWindow:
    settings = C.Settings(**kw)
    win = PetWindow(rig, settings)
    win.behavior.pet.x = 600.0
    win.behavior.pet.y = 800.0
    win.behavior.pet.airborne = False
    return win


def test_every_part_loaded_as_a_pixmap(app, rig):
    win = _window(app, rig)
    assert set(win.pixmaps) == set(rig.parts)
    for name, pix in win.pixmaps.items():
        assert not pix.isNull(), name


def test_render_produces_a_visible_figure(app, rig):
    win = _window(app, rig, pet_height=240)
    win._render()
    img = win._frame
    assert img is not None
    arr = _alpha(img)
    assert arr.max() > 200, "nothing was drawn"
    # a person is taller than wide and should fill a decent part of the canvas
    assert 0.08 < (arr > 32).mean() < 0.75


def test_rendered_height_tracks_the_setting(app, rig):
    for height in (140, 240, 380):
        win = _window(app, rig, pet_height=height)
        win._render()
        arr = _alpha(win._frame)
        ys = np.nonzero((arr > 32).any(axis=1))[0]
        drawn = ys.max() - ys.min() + 1
        assert drawn == pytest.approx(height, rel=0.10), height


def test_window_is_placed_around_his_feet(app, rig):
    win = _window(app, rig, pet_height=240)
    win._render()
    g = win.geometry()
    p = win.behavior.pet
    assert g.left() < p.x < g.right()
    # the ground point sits near the bottom of the window
    assert g.top() < p.y <= g.bottom() + 2
    assert p.y - g.top() > 0.7 * g.height()


def test_flipping_mirrors_the_drawing(app, rig):
    win = _window(app, rig, pet_height=240)
    win.behavior.pet.clip = "wave"      # asymmetric, so the mirror is detectable
    win.behavior.pet.facing = 1
    win._render()
    right = _alpha(win._frame)
    win.behavior.pet.facing = -1
    win._render()
    left = _alpha(win._frame)

    def centroid_x(a):
        cols = a.sum(axis=0).astype(float)
        return float((np.arange(a.shape[1]) * cols).sum() / max(cols.sum(), 1)) / a.shape[1]

    assert abs(centroid_x(right) - (1 - centroid_x(left))) < 0.06


def test_all_clips_render_without_error(app, rig):
    win = _window(app, rig, pet_height=200)
    for name in CLIPS:
        win.behavior.pet.clip = name
        for i in range(6):
            win.behavior.pet.clip_time = CLIPS[name].duration * i / 6
            win._render()
            assert _alpha(win._frame).max() > 128, name


def test_sitting_hangs_his_feet_below_the_ledge(app, rig):
    win = _window(app, rig, pet_height=240)
    ledge_y = win.behavior.pet.y

    win.behavior._set_state(State.WALK, "walk")
    win._render()
    standing_bottom = win.geometry().bottom()

    win.behavior._set_state(State.SIT_DANCE, "sit_dance")
    win._render()
    sitting_bottom = win.geometry().bottom()

    assert sitting_bottom > standing_bottom + 40, "legs should dangle below the edge"
    assert win.geometry().top() < ledge_y


def test_hit_testing_finds_his_body_but_not_the_corner(app, rig):
    from PySide6.QtCore import QPoint

    win = _window(app, rig, pet_height=240)
    win.behavior.pet.clip = "idle"
    win.behavior.pet.clip_time = 0.0
    win._render()
    arr = _alpha(win._frame)
    ys, xs = np.nonzero(arr > 200)
    assert win._opaque_at(QPoint(int(xs[len(xs) // 2]), int(ys[len(ys) // 2])))
    assert not win._opaque_at(QPoint(0, 0))
    assert not win._opaque_at(QPoint(-5, -5))
    assert not win._opaque_at(QPoint(10**6, 10**6))


def test_paint_before_first_render_is_harmless(app, rig):
    win = PetWindow(rig, C.Settings())
    assert win._frame is None
    win.repaint()  # must not raise


def _alpha(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_ARGB32)
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return arr[:, : img.width(), 3].copy()
