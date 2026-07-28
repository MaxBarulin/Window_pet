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
