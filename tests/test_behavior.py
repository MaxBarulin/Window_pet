import random

import pytest

from pet import config as C
from pet.behavior import Behavior, Pet, State
from pet.desktop import Monitor, Rect, Snapshot


def mon(x0=0, y0=0, x1=1920, y1=1080, taskbar=40) -> Monitor:
    return Monitor(Rect(x0, y0, x1, y1), Rect(x0, y0, x1, y1 - taskbar))


def snap(windows=None) -> Snapshot:
    return Snapshot([mon()], list(windows or []))


def beh(**kw) -> Behavior:
    pet = Pet(**kw)
    return Behavior(pet, rng=random.Random(1234))


def run(b: Behavior, s: Snapshot, seconds: float, dt: float = 1 / 60) -> None:
    for _ in range(int(seconds / dt)):
        b.update(s, dt)


def test_he_falls_and_lands_on_the_floor():
    b = beh(x=500, y=100, airborne=True)
    run(b, snap(), 3.0)
    assert not b.pet.airborne
    assert b.pet.y == 1040
    assert "landed" in b.pet.last_event


def test_he_lands_on_a_window_title_bar():
    s = snap([Rect(300, 500, 900, 900)])
    b = beh(x=600, y=100, airborne=True)
    run(b, s, 3.0)
    assert b.pet.y == 500
    assert b.pet.ledge is not None and b.pet.ledge.kind == "window"


def test_he_misses_the_window_when_he_is_not_above_it():
    s = snap([Rect(300, 500, 900, 900)])
    b = beh(x=1500, y=100, airborne=True)
    run(b, s, 3.0)
    assert b.pet.y == 1040


def test_landing_plays_the_land_clip_first():
    b = beh(x=500, y=1000, airborne=True)
    run(b, snap(), 0.25)
    assert b.pet.state is State.LAND
    assert b.pet.clip == "land"


def test_he_does_not_fall_through_a_ledge_at_high_speed():
    """A ledge is a zero-thickness line, so landing has to be a swept test.

    Worst realistic case: terminal velocity across the app's largest clamped
    frame time, which covers ~228px in one step.
    """
    b = beh(x=500, y=900, airborne=True)
    b.pet.vy = C.TERMINAL_VY
    b.update(snap(), 0.12)
    assert not b.pet.airborne, "stepped straight over the floor"
    assert b.pet.y == 1040


def test_a_window_ledge_is_not_tunnelled_either():
    s = snap([Rect(300, 500, 900, 900)])
    b = beh(x=600, y=400, airborne=True)
    b.pet.vy = C.TERMINAL_VY
    b.update(s, 0.12)
    assert not b.pet.airborne
    assert b.pet.y == 500


def test_he_falls_when_his_window_vanishes():
    s = snap([Rect(300, 500, 900, 900)])
    b = beh(x=600, y=100, airborne=True)
    run(b, s, 3.0)
    assert b.pet.y == 500

    b.update(snap(), 1 / 60)  # window gone
    assert b.pet.airborne
    assert b.pet.last_event == "ledge disappeared"
    run(b, snap(), 3.0)
    assert b.pet.y == 1040


def test_he_falls_when_another_window_covers_his_ledge():
    ledge = Rect(300, 500, 900, 900)
    b = beh(x=600, y=100, airborne=True)
    run(b, snap([ledge]), 3.0)
    assert b.pet.y == 500

    covering = Rect(200, 300, 1000, 950)
    b.update(snap([covering, ledge]), 1 / 60)
    assert b.pet.airborne


def test_walking_moves_him_and_stays_on_the_ledge():
    s = snap()
    b = beh(x=900, y=1040, airborne=False, facing=1)
    b._set_state(State.WALK, "walk", limit=99.0)
    start = b.pet.x
    run(b, s, 1.0)
    assert b.pet.x > start + 20
    assert b.pet.y == 1040


def test_he_turns_or_leans_at_the_screen_edge_instead_of_leaving():
    s = snap()
    b = beh(x=1880, y=1040, airborne=False, facing=1)
    b._set_state(State.WALK, "walk", limit=99.0)
    run(b, s, 2.0)
    assert b.pet.x <= 1920
    assert b.pet.last_event == "hit screen edge"
    assert b.pet.state in (State.LEAN, State.WALK)


def test_he_stays_inside_the_screen_over_a_long_run():
    s = snap([Rect(300, 500, 900, 900), Rect(1100, 400, 1700, 800)])
    b = beh(x=960, y=100, airborne=True)
    run(b, s, 90.0)
    assert 0 <= b.pet.x <= 1920
    assert b.pet.y <= 1041


def test_a_tall_window_ahead_turns_him_round():
    wall = Rect(1000, 300, 1600, 1040)   # top far above the floor
    s = snap([wall])
    b = beh(x=940, y=1040, airborne=False, facing=1)
    b._set_state(State.WALK, "walk", limit=99.0)
    run(b, s, 1.0)
    assert b.pet.last_event == "turned at window edge"
    assert b.pet.facing == -1


def test_a_low_lip_gets_climbed_instead():
    # top edge only ~40px above the floor, but tall enough to count as terrain
    step = Rect(1000, 1040 - int(C.STEP_UP_MAX * 0.95), 1600, 1300)
    s = snap([step])
    top = 1040 - int(C.STEP_UP_MAX * 0.95)
    b = beh(x=960, y=1040, airborne=False, facing=1)
    b._set_state(State.WALK, "walk", limit=99.0)
    run(b, s, 0.6)
    assert b.pet.last_event == "climbed onto window"
    # ...and the hop actually gets him up onto it
    run(b, s, 1.5)
    assert not b.pet.airborne
    assert b.pet.y == top
    assert b.pet.ledge is not None and b.pet.ledge.kind == "window"


def test_he_walks_across_a_monitor_seam():
    two = Snapshot([mon(0, 0, 1920, 1080), mon(1920, 0, 3840, 1080)])
    b = beh(x=1900, y=1040, airborne=False, facing=1)
    b._set_state(State.WALK, "walk", limit=99.0)
    run(b, two, 2.0)
    assert b.pet.x > 1920, "should carry on onto the second screen"
    assert not b.pet.airborne


def test_sitting_anchors_by_the_hips():
    b = beh(x=600, y=500, airborne=False)
    b._set_state(State.SIT_DANCE, "sit_dance")
    assert b.pet.anchor_kind == "hips"
    b._set_state(State.WALK, "walk")
    assert b.pet.anchor_kind == "feet"


def test_he_only_sits_on_windows_never_on_the_floor():
    s = snap()
    b = beh(x=900, y=1040, airborne=False)
    for _ in range(400):
        b._choose_ground_action(s)
        assert b.pet.state is not State.SIT_DANCE


def test_he_does_sit_when_perched_on_a_window_edge():
    win = Rect(300, 500, 900, 900)
    s = snap([win])
    b = beh(x=870, y=500, airborne=False)
    b.pet.ledge = s.ledge_under_feet(870, 500)
    states = set()
    for _ in range(400):
        b._choose_ground_action(s)
        states.add(b.pet.state)
    assert State.SIT_DANCE in states


def test_dance_now_picks_a_dance_on_the_ground():
    b = beh(x=500, y=1040, airborne=False)
    b.dance_now()
    assert b.pet.state is State.DANCE
    from pet.poses import DANCES

    assert b.pet.clip in DANCES


def test_dance_now_is_ignored_mid_air():
    b = beh(x=500, y=100, airborne=True)
    b._set_state(State.FALL, "fall")
    b.dance_now()
    assert b.pet.state is State.FALL


def test_dragging_holds_him_still_until_released():
    s = snap()
    b = beh(x=500, y=1040, airborne=False)
    b.start_drag()
    assert b.pet.state is State.DRAGGED
    b.drag_to(800, 200)
    run(b, s, 1.0)                     # updates must not move him
    assert (b.pet.x, b.pet.y) == (800, 200)

    b.release_drag(vx=300, vy=-100)
    assert b.pet.state is State.FALL
    assert b.pet.vx == pytest.approx(300 * C.THROW_DAMPING)
    run(b, s, 4.0)
    assert not b.pet.airborne


def test_a_throw_off_screen_is_clamped_back():
    b = beh(x=1900, y=400, airborne=True)
    b.release_drag(vx=5000, vy=0)
    run(b, snap(), 3.0)
    assert b.pet.x <= 1920


def test_falling_past_everything_is_recovered():
    """No terrain under him at all: he must not sail off forever."""
    s = Snapshot([mon()], [])
    b = beh(x=500, y=1039, airborne=True)
    b.pet.vy = 500.0
    b.pet.x = -5000  # outside every ledge's x span
    run(b, s, 6.0)
    assert not b.pet.airborne


def test_scale_tracks_the_configured_height():
    s = C.Settings(pet_height=460)
    b = Behavior(Pet(), rng=random.Random(0), settings=s)
    assert b.scale == pytest.approx(2.0)


def test_bigger_pets_walk_proportionally_faster():
    def distance(height: int) -> float:
        s = C.Settings(pet_height=height)
        b = Behavior(Pet(x=900, y=1040, airborne=False, facing=1),
                     rng=random.Random(5), settings=s)
        b._set_state(State.WALK, "walk", limit=99.0)
        start = b.pet.x
        run(b, snap(), 1.0)
        return b.pet.x - start

    assert distance(460) > distance(230) * 1.6


@pytest.mark.parametrize("seed", range(6))
def test_long_run_never_wedges_or_goes_nan(seed: int):
    s = snap([Rect(300, 500, 900, 900), Rect(1000, 350, 1500, 700)])
    b = Behavior(Pet(x=960, y=80, airborne=True), rng=random.Random(seed))
    seen: set[State] = set()
    for _ in range(60 * 120):
        b.update(s, 1 / 60)
        seen.add(b.pet.state)
        assert b.pet.x == b.pet.x and b.pet.y == b.pet.y  # not NaN
        assert -100 <= b.pet.x <= 2020
    # over two minutes he should have done more than one thing
    assert len(seen) >= 3
