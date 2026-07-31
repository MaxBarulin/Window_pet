"""Editor-authored clips."""
from __future__ import annotations

import json
import math
import pathlib

import pytest

from pet.kinematics import Skeleton
from pet.poses import CLIPS, DANCES, keyframe_clip, load_clips
from pet.rigmath import Rig

ROOT = pathlib.Path(__file__).resolve().parent.parent
RIG_JSON = ROOT / "assets" / "rig.json"

KEYS = [
    {"phase": 0.0, "body": [0, 10], "lean": 0, "arm_l_upper": 0, "foot_l": [0, 0]},
    {"phase": 0.5, "body": [0, 70], "lean": 10, "arm_l_upper": -40, "foot_l": [-30, 0]},
]


def test_a_keyframe_clip_hits_its_keys_exactly():
    clip = keyframe_clip({"name": "k", "duration": 2.0, "keys": KEYS})
    assert clip.at(0.0).body == pytest.approx((0.0, 10.0))
    assert clip.at(1.0).body == pytest.approx((0.0, 70.0))
    assert clip.at(1.0).arm_l_upper == pytest.approx(-40.0)


def test_it_eases_between_keys_rather_than_snapping():
    clip = keyframe_clip({"name": "k", "duration": 2.0, "keys": KEYS})
    quarter = clip.at(0.5).body[1]
    assert 10.0 < quarter < 70.0
    # smoothstep, so the midpoint is exactly halfway but the quarters are not linear
    assert clip.at(0.25).body[1] < 10.0 + (70.0 - 10.0) * 0.25


def test_it_loops_back_round_to_the_first_key():
    clip = keyframe_clip({"name": "k", "duration": 2.0, "keys": KEYS})
    assert clip.at(1.99).body[1] == pytest.approx(10.0, abs=1.5)


def test_a_single_key_is_a_still_pose():
    clip = keyframe_clip({"name": "k", "duration": 1.0, "keys": KEYS[:1]})
    assert clip.at(0.0).body == clip.at(0.9).body


def test_an_empty_clip_is_refused():
    with pytest.raises(ValueError, match="keyframes"):
        keyframe_clip({"name": "k", "keys": []})


def test_a_missing_or_broken_poses_file_is_simply_no_clips(tmp_path):
    assert load_clips(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_clips(bad) == {}
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"clips": [{"duration": 1.0}]}))
    assert load_clips(wrong) == {}


def test_a_loaded_clip_resolves_against_the_real_rig(tmp_path):
    path = tmp_path / "poses.json"
    path.write_text(json.dumps({"clips": [
        {"name": "authored", "duration": 1.4, "keys": KEYS},
    ]}))
    clips = load_clips(path)
    assert set(clips) == {"authored"}
    skeleton = Skeleton(Rig.load(RIG_JSON))
    for i in range(8):
        pose = skeleton.resolve(clips["authored"].at(1.4 * i / 8))
        assert math.isfinite(pose.offset[1])


def test_the_built_in_dances_are_all_real_clips():
    for name in DANCES:
        assert name in CLIPS


def test_motion_defaults_cover_every_knob():
    from pet.poses import ACTION_WEIGHTS, MOTION, MOTION_DEFAULTS
    assert set(MOTION) == set(MOTION_DEFAULTS)
    assert set(ACTION_WEIGHTS) == {"walk", "dance", "idle", "crouch", "hop"}
    assert all(v > 0 for v in ACTION_WEIGHTS.values())


def test_a_broken_motion_file_leaves_the_defaults_alone(tmp_path, monkeypatch):
    import pet.poses as poses

    bad = tmp_path / "motion.json"
    bad.write_text("{ not json")
    monkeypatch.setattr(poses, "_assets_dir", lambda: tmp_path)
    assert poses.load_motion() == poses.MOTION_DEFAULTS


def test_motion_values_are_taken_from_the_file(tmp_path, monkeypatch):
    import pet.poses as poses

    (tmp_path / "motion.json").write_text(json.dumps({"stride": 140.0, "junk": "x"}))
    monkeypatch.setattr(poses, "_assets_dir", lambda: tmp_path)
    loaded = poses.load_motion()
    assert loaded["stride"] == 140.0
    assert loaded["bounce"] == poses.MOTION_DEFAULTS["bounce"]


def test_deleting_a_clip_leaves_the_others(tmp_path):
    """The editor's delete is a rewrite of the file; check the file, not the UI."""
    path = tmp_path / "poses.json"
    path.write_text(json.dumps({"clips": [
        {"name": "mine", "duration": 1.2, "keys": KEYS},
        {"name": "other", "duration": 1.2, "keys": KEYS},
    ]}))
    doc = json.loads(path.read_text())
    doc["clips"] = [c for c in doc["clips"] if c.get("name") != "mine"]
    path.write_text(json.dumps(doc))
    assert set(load_clips(path)) == {"other"}


def test_an_override_hides_the_built_in_and_deleting_it_brings_it_back(tmp_path):
    from pet.poses import keyframe_clip

    path = tmp_path / "poses.json"
    path.write_text(json.dumps({"clips": [
        {"name": "walk", "duration": 1.0, "keys": KEYS},
    ]}))
    override = load_clips(path)["walk"]
    assert override.at(0.0).body == pytest.approx((0.0, 10.0))
    # and the shipped one is still a separate object, untouched
    assert CLIPS["walk"] is not override
    path.write_text(json.dumps({"clips": []}))
    assert load_clips(path) == {}


def test_an_edited_walk_keeps_its_travel_speed(tmp_path):
    """The bug: a keyframed clip that replaces a built-in defaulted to speed 0.

    An edited walk with no speed field must inherit the built-in walk's speed, or
    he walks on the spot instead of crossing the screen.
    """
    from pet.poses import CLIPS, load_clips

    path = tmp_path / "poses.json"
    path.write_text(json.dumps({"clips": [
        {"name": "walk", "duration": 1.02, "keys": KEYS},
    ]}))
    edited = load_clips(path)["walk"]
    assert edited.speed == pytest.approx(CLIPS["walk"].speed)
    assert edited.speed > 0.0


def test_an_explicit_speed_is_respected(tmp_path):
    from pet.poses import load_clips

    path = tmp_path / "poses.json"
    path.write_text(json.dumps({"clips": [
        {"name": "walk", "duration": 1.0, "speed": 0.0, "keys": KEYS},
    ]}))
    assert load_clips(path)["walk"].speed == 0.0


def test_a_brand_new_clip_has_no_travel(tmp_path):
    from pet.poses import load_clips

    path = tmp_path / "poses.json"
    path.write_text(json.dumps({"clips": [
        {"name": "brand_new", "duration": 1.0, "keys": KEYS},
    ]}))
    assert load_clips(path)["brand_new"].speed == 0.0


def test_a_disabled_dance_leaves_the_pool(tmp_path, monkeypatch):
    import pet.poses as poses

    (tmp_path / "motion.json").write_text(
        json.dumps({"disabled": ["kazachok"]}), encoding="utf-8")
    monkeypatch.setattr(poses, "_assets_dir", lambda: tmp_path)
    assert "kazachok" in poses.disabled_clips()


def test_a_cyrillic_clip_name_survives_the_round_trip(tmp_path):
    from pet.poses import load_clips

    path = tmp_path / "poses.json"
    path.write_text(
        json.dumps({"clips": [{"name": "пляс", "duration": 1.2, "keys": KEYS}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    assert set(load_clips(path)) == {"пляс"}


def test_a_broken_motion_file_disables_nothing(tmp_path, monkeypatch):
    import pet.poses as poses

    (tmp_path / "motion.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(poses, "_assets_dir", lambda: tmp_path)
    assert poses.disabled_clips() == set()
