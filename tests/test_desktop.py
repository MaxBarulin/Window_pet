import pytest

from pet.desktop import (
    MIN_LEDGE_W,
    FakeDesktop,
    Monitor,
    Rect,
    Snapshot,
    default_fake,
    subtract_intervals,
)


def mon(x0=0, y0=0, x1=1920, y1=1080, taskbar=40) -> Monitor:
    return Monitor(Rect(x0, y0, x1, y1), Rect(x0, y0, x1, y1 - taskbar))


def test_rect_geometry():
    r = Rect(10, 20, 110, 220)
    assert (r.w, r.h) == (100, 200)
    assert r.contains(50, 50) and r.contains_x(110)
    assert not r.contains(5, 50)


def test_subtract_no_overlap():
    assert subtract_intervals((0, 10), [(20, 30)]) == [(0, 10)]


def test_subtract_middle_splits_in_two():
    assert subtract_intervals((0, 100), [(40, 60)]) == [(0, 40), (60, 100)]


def test_subtract_covering_everything():
    assert subtract_intervals((10, 20), [(0, 100)]) == []


def test_subtract_edges_and_multiple_cuts():
    assert subtract_intervals((0, 100), [(0, 30)]) == [(30, 100)]
    assert subtract_intervals((0, 100), [(70, 200)]) == [(0, 70)]
    assert subtract_intervals((0, 100), [(10, 20), (50, 60)]) == [
        (0, 10), (20, 50), (60, 100)
    ]


def test_floor_ledge_sits_above_the_taskbar():
    snap = Snapshot([mon(taskbar=40)])
    floors = [lg for lg in snap.ledges if lg.kind == "floor"]
    assert len(floors) == 1
    assert floors[0].y == 1040
    assert (floors[0].x0, floors[0].x1) == (0, 1920)


def test_window_top_edge_becomes_a_ledge():
    snap = Snapshot([mon()], [Rect(300, 400, 900, 900)])
    wins = [lg for lg in snap.ledges if lg.kind == "window"]
    assert len(wins) == 1
    assert (wins[0].y, wins[0].x0, wins[0].x1) == (400, 300, 900)


def test_tiny_windows_are_not_terrain():
    snap = Snapshot([mon()], [Rect(300, 400, 340, 430)])
    assert [lg for lg in snap.ledges if lg.kind == "window"] == []


def test_a_window_in_front_hides_the_ledge_behind_it():
    """The front window covers the middle of the back window's title bar."""
    front = Rect(400, 300, 700, 800)
    back = Rect(300, 500, 900, 900)
    snap = Snapshot([mon()], [front, back])  # topmost first
    pieces = sorted(
        (lg.x0, lg.x1) for lg in snap.ledges if lg.kind == "window" and lg.y == 500
    )
    assert pieces == [(300, 400), (700, 900)]


def test_fully_covered_ledge_disappears():
    front = Rect(200, 300, 1000, 900)
    back = Rect(300, 500, 900, 880)
    snap = Snapshot([mon()], [front, back])
    assert [lg for lg in snap.ledges if lg.y == 500] == []


def test_slivers_below_the_minimum_width_are_dropped():
    front = Rect(300 + MIN_LEDGE_W - 10, 400, 2000, 900)
    back = Rect(300, 500, 900, 900)
    snap = Snapshot([mon()], [front, back])
    assert [lg for lg in snap.ledges if lg.y == 500] == []


def test_a_window_that_does_not_span_the_edge_does_not_cut_it():
    """A window whose body is below the other's title bar must not occlude it."""
    front = Rect(400, 600, 700, 900)
    back = Rect(300, 500, 900, 950)
    snap = Snapshot([mon()], [front, back])
    pieces = [(lg.x0, lg.x1) for lg in snap.ledges if lg.y == 500]
    assert pieces == [(300, 900)]


def test_ledge_below_picks_the_highest_surface_underneath():
    # side by side, so neither occludes the other
    snap = Snapshot([mon()], [Rect(300, 400, 900, 900), Rect(1000, 700, 1600, 1000)])
    lg = snap.ledge_below(500, 100)
    assert lg is not None and lg.y == 400          # lands on the upper window
    lg2 = snap.ledge_below(1200, 100)
    assert lg2 is not None and lg2.y == 700        # the lower one, further right
    lg3 = snap.ledge_below(500, 500)
    assert lg3 is not None and lg3.kind == "floor"  # already below that title bar


def test_ledge_below_skips_a_title_bar_hidden_behind_a_window():
    front = Rect(300, 400, 900, 900)
    behind = Rect(200, 700, 1000, 1000)   # its top edge is covered by `front`
    snap = Snapshot([mon()], [front, behind])
    lg = snap.ledge_below(500, 500)
    assert lg is not None and lg.kind == "floor"
    # but the uncovered sliver of that same edge is still landable
    edge = snap.ledge_below(250, 500)
    assert edge is not None and edge.y == 700


def test_ledge_below_ignores_ledges_outside_the_x_span():
    snap = Snapshot([mon()], [Rect(300, 400, 900, 900)])
    lg = snap.ledge_below(1500, 100)
    assert lg is not None and lg.kind == "floor"


def test_ledge_below_returns_none_when_there_is_nothing():
    snap = Snapshot([], [])
    assert snap.ledge_below(10, 10) is None


def test_ledge_under_feet_needs_to_be_within_tolerance():
    snap = Snapshot([mon()], [Rect(300, 400, 900, 900)])
    assert snap.ledge_under_feet(500, 400) is not None
    assert snap.ledge_under_feet(500, 402, tol=3) is not None
    assert snap.ledge_under_feet(500, 430, tol=3) is None


def test_monitor_and_bounds_lookup_across_two_screens():
    left, right = mon(0, 0, 1920, 1080), mon(1920, 0, 3840, 1080)
    snap = Snapshot([left, right])
    assert snap.monitor_at(100, 100) is left
    assert snap.monitor_at(2500, 100) is right
    assert snap.bounds_for(2500, 100).x0 == 1920
    both = snap.desktop_bounds()
    assert (both.x0, both.x1) == (0, 3840)


def test_monitor_at_falls_back_when_y_is_off_screen():
    m = mon()
    snap = Snapshot([m])
    assert snap.monitor_at(500, -400) is m


def test_two_monitors_give_two_touching_floors():
    snap = Snapshot([mon(0, 0, 1920, 1080), mon(1920, 0, 3840, 1080)])
    floors = sorted((lg.x0, lg.x1) for lg in snap.ledges if lg.kind == "floor")
    assert floors == [(0, 1920), (1920, 3840)]


def test_ledges_are_cached():
    snap = Snapshot([mon()])
    assert snap.ledges is snap.ledges


def test_fake_desktop_snapshots_are_independent():
    fake = FakeDesktop([mon()], [Rect(0, 100, 500, 600)])
    a = fake.snapshot()
    fake.windows.append(Rect(600, 200, 900, 700))
    assert len(a.windows) == 1
    assert len(fake.snapshot().windows) == 2


def test_default_fake_is_usable_terrain():
    snap = default_fake().snapshot()
    assert snap.monitors
    assert any(lg.kind == "window" for lg in snap.ledges)
    assert any(lg.kind == "floor" for lg in snap.ledges)
