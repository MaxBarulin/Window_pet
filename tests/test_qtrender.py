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
from PySide6.QtCore import Qt  # noqa: E402
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
    assert m.b != pytest.approx(m.d), "rotation must have asymmetric off-diagonals"


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


def test_the_window_is_only_ever_moved_never_resized(app, rig):
    """This is the stutter fix.

    Resizing a translucent always-on-top window every frame judders visibly. The
    canvas is sized once for the widest pose, and frames only move it.
    """
    win = _window(app, rig, pet_height=240)
    sizes = set()
    positions = set()
    for name in CLIPS:
        win.behavior.pet.clip = name
        for i in range(8):
            win.behavior.pet.clip_time = CLIPS[name].duration * i / 8
            win.behavior.pet.x += 3.5
            win._render()
            g = win.geometry()
            sizes.add((g.width(), g.height()))
            positions.add((g.left(), g.top()))
    assert len(sizes) == 1, f"window resized while animating: {sorted(sizes)}"
    assert len(positions) > 10, "window should still follow him around"


def test_every_pose_fits_the_fixed_canvas(app, rig):
    """A pose reaching outside the canvas would be clipped mid-dance."""
    win = _window(app, rig, pet_height=240)
    w, h = win._canvas
    for name in CLIPS:
        for i in range(12):
            pose = CLIPS[name].at(CLIPS[name].duration * i / 12)
            resolved = win.skeleton.resolve(pose)
            for flip in (False, True):
                probe = rig.compose(resolved, (0.0, 0.0), 240 / rig.height(), flip)
                contact = win.skeleton.ground_y(probe)
                tf = rig.compose(
                    resolved,
                    (win._anchor_local[0], win._anchor_local[1] - contact),
                    240 / rig.height(), flip,
                )
                x0, y0, x1, y1 = rig.bounds(tf)
                assert x0 >= -0.5 and y0 >= -0.5, (name, i, flip, x0, y0)
                assert x1 <= w + 0.5 and y1 <= h + 0.5, (name, i, flip, x1, y1)


def test_sub_pixel_movement_shifts_the_drawing(app, rig):
    """Rounding position to whole pixels makes slow walking crawl."""
    win = _window(app, rig, pet_height=240)
    win.behavior.pet.clip = "idle"
    win.behavior.pet.clip_time = 0.0
    win.behavior.pet.x = 600.0
    win._render()
    before = _alpha(win._frame).astype(float)
    win.behavior.pet.x = 600.4          # same whole pixel, different fraction
    win._render()
    after = _alpha(win._frame).astype(float)
    import math

    # the window itself has not moved: 600.0 and 600.4 floor to the same pixel...
    assert win.geometry().left() == math.floor(600.4 - win._anchor_local[0])
    # ...so the 0.4px has to show up in the drawing instead
    assert abs(after - before).sum() > 0, "sub-pixel motion was rounded away"


def test_canvas_is_resized_when_the_pet_size_changes(app, rig):
    win = _window(app, rig, pet_height=150)
    small = win._canvas
    win.set_height(330)
    assert win._canvas[0] > small[0] and win._canvas[1] > small[1]
    win._render()
    assert (win.geometry().width(), win.geometry().height()) == win._canvas


def test_paint_before_first_render_is_harmless(app, rig):
    win = PetWindow(rig, C.Settings())
    assert win._frame is None
    win.repaint()  # must not raise


def test_click_through_mask_is_not_inverted(app, rig):
    """An inverted mask would either hide him or swallow every click.

    The mask is what lets clicks on empty space reach the window underneath, so it
    has to cover his body and nothing else.
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QBitmap, QRegion

    win = _window(app, rig, pet_height=240)
    win.behavior.pet.clip = "idle"
    win.behavior.pet.clip_time = 0.0
    win._render()
    img = win._frame
    region = QRegion(QBitmap.fromImage(img.createAlphaMask()))
    assert not region.isEmpty()

    arr = _alpha(img)
    agree = total = 0
    for y in range(0, img.height(), 3):
        for x in range(0, img.width(), 3):
            total += 1
            agree += region.contains(QPoint(x, y)) == bool(arr[y, x] > 0)
    assert agree / total > 0.93, "mask does not track the rendered alpha"

    ys, xs = np.nonzero(arr > 200)
    body = QPoint(int(xs[len(xs) // 2]), int(ys[len(ys) // 2]))
    assert region.contains(body)
    assert not region.contains(QPoint(0, 0))


def test_click_through_sets_the_os_level_flag(app, rig):
    win = _window(app, rig)
    assert not (win.windowFlags() & Qt.WindowTransparentForInput)
    win.set_click_through(True)
    assert win.windowFlags() & Qt.WindowTransparentForInput
    assert win.testAttribute(Qt.WA_TransparentForMouseEvents)
    win.set_click_through(False)
    assert not (win.windowFlags() & Qt.WindowTransparentForInput)


def test_toggling_click_through_from_the_menu_does_not_block(app, rig):
    """The 'you turned it on' notice must not be modal.

    A modal QMessageBox here freezes the whole pet until it is dismissed. If this
    ever regresses the test does not fail, it hangs - which CI surfaces as a
    timeout, and is exactly how this was caught in the first place.
    """
    win = _window(app, rig)
    action = next(a for a in win.menu().actions() if a.text() == "Click through")
    assert not action.isChecked()
    action.trigger()   # toggles it on; returns only if the notice is non-modal
    assert win.settings.click_through is True
    notice = getattr(win, "_notice", None)
    assert notice is not None and not notice.isModal()


def test_the_overlay_never_takes_focus(app, rig):
    win = _window(app, rig)
    assert win.windowFlags() & Qt.WindowDoesNotAcceptFocus


def test_tray_menu_ticks_follow_the_current_settings(app, rig):
    """The tray keeps one menu for the whole session, so it must refill on open."""
    win = _window(app, rig, pet_height=230)
    menu = win.tray_menu()

    def ticked_sizes(m):
        sizes = next(a.menu() for a in m.actions() if a.text() == "Size")
        return {a.text() for a in sizes.actions() if a.isChecked()}

    assert ticked_sizes(menu) == {"Medium"}
    win.set_height(330)
    menu.aboutToShow.emit()
    assert ticked_sizes(menu) == {"Large"}


def test_menu_offers_the_expected_actions(app, rig):
    win = _window(app, rig)
    labels = [a.text() for a in win.menu().actions() if a.text()]
    for expected in ("Dance now", "Size", "Click through", "Walk on windows", "Quit"):
        assert expected in labels


def _alpha(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_ARGB32)
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return arr[:, : img.width(), 3].copy()
