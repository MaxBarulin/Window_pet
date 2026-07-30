"""Things he says at a time of day."""

from __future__ import annotations

import json
from datetime import datetime

from pet.reminders import Reminder, Schedule, load, save


def test_a_reminder_round_trips(tmp_path):
    path = tmp_path / "r.json"
    save([Reminder(16, 15, "go home", weekdays_only=True)], path)
    back = load(path)
    assert len(back) == 1
    assert back[0].label() == "16:15"
    assert back[0].text == "go home"
    assert back[0].weekdays_only


def test_they_come_back_in_time_order(tmp_path):
    path = tmp_path / "r.json"
    save([Reminder(16, 15, "c"), Reminder(9, 0, "a"), Reminder(12, 0, "b")], path)
    assert [r.text for r in load(path)] == ["a", "b", "c"]


def test_a_missing_or_broken_file_is_no_reminders(tmp_path):
    assert load(tmp_path / "nope.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert load(bad) == []
    junk = tmp_path / "junk.json"
    junk.write_text(json.dumps({"reminders": [{"at": "nope"}, {"text": "no time"}]}))
    assert load(junk) == []


def test_one_without_words_is_dropped(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"reminders": [{"at": "09:00", "text": "  "}]}))
    assert load(path) == []


def test_it_fires_on_its_minute_and_only_once(tmp_path):
    s = Schedule([Reminder(9, 0, "up")])
    s._fired.clear()
    assert s.due(datetime(2026, 7, 30, 8, 59)) == []
    assert [r.text for r in s.due(datetime(2026, 7, 30, 9, 0))] == ["up"]
    assert s.due(datetime(2026, 7, 30, 9, 0)) == []


def test_it_fires_again_the_next_day(tmp_path):
    s = Schedule([Reminder(9, 0, "up")])
    s._fired.clear()
    assert s.due(datetime(2026, 7, 30, 9, 0))
    assert [r.text for r in s.due(datetime(2026, 7, 31, 9, 0))] == ["up"]


def test_yesterdays_records_do_not_pile_up():
    s = Schedule([Reminder(9, 0, "up")])
    s._fired.clear()
    s.due(datetime(2026, 7, 30, 9, 0))
    s.due(datetime(2026, 7, 31, 9, 0))
    assert all(key[0] == datetime(2026, 7, 31).date() for key in s._fired)


def test_a_switched_off_one_stays_quiet():
    s = Schedule([Reminder(9, 0, "up", enabled=False)])
    s._fired.clear()
    assert s.due(datetime(2026, 7, 30, 9, 0)) == []


def test_weekdays_only_skips_the_weekend():
    s = Schedule([Reminder(9, 0, "work", weekdays_only=True)])
    s._fired.clear()
    assert s.due(datetime(2026, 8, 1, 9, 0)) == []      # Saturday
    assert [r.text for r in s.due(datetime(2026, 8, 3, 9, 0))] == ["work"]


def test_starting_up_does_not_replay_the_whole_morning():
    """A machine switched on at six in the evening owes you no lunch reminder."""
    s = Schedule([Reminder(9, 0, "up"), Reminder(12, 0, "lunch")])
    now = datetime.now()
    for r in s.reminders:
        if (r.hour, r.minute) <= (now.hour, now.minute):
            assert (now.date(), r.hour, r.minute) in s._fired
