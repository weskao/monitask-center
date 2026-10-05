import pytest

from monitask_center import check
from monitask_center.config import Settings

WORK = (True, "workday")


def decide(hour, state="paused", settings=None, workday=WORK):
    return check.decide(hour, settings or Settings(), workday, state)


def test_lunch_window_is_silent_and_13_runs():
    assert decide(12, "paused") == "skip_lunch"
    assert decide(12, "not-running") == "skip_lunch"
    assert decide(13, "paused") == "play"


def test_lunch_follows_shifted_window_hours_and_interval():
    s = Settings(start_hour=9, end_hour=20, interval_hours=2, lunch_start_hour=11, lunch_end_hour=13)
    assert [h for h in s.check_hours() if decide(h, "paused", s) == "skip_lunch"] == [11]
    assert decide(13, "paused", s) == "play"
    assert decide(9, "paused", s) == "play"
    assert decide(15, "paused", s) == "remind"


def test_lunch_skip_off_checks_normally():
    assert decide(12, "paused", Settings(lunch_skip=False)) == "remind"


@pytest.mark.parametrize("state", ["timing", "running", "hidden"])
def test_quiet_states(state):
    assert decide(14, state) == "ok"


@pytest.mark.parametrize("state", ["paused", "not-running"])
def test_remind_states(state):
    assert decide(14, state) == "remind"


def test_unknown_warns():
    assert decide(14, "unknown") == "warn_unknown"


def test_vacation_beats_calendar_and_holiday_is_separate():
    assert decide(14, "paused", workday=(False, "vacation")) == "skip_vacation"
    assert decide(14, "paused", workday=(False, "holiday:weekend")) == "skip_holiday"


def test_outside_window_wins_over_everything():
    assert decide(9, "paused") == "outside"
    assert decide(19, "paused") == "outside"
    assert decide(9, "paused", workday=(False, "vacation")) == "outside"
    assert decide(18, "paused") == "remind"  # end hour is inside, and no stop reminder
    assert decide(18, "timing") == "ok"


def test_vacation_beats_lunch_and_state():
    assert decide(12, "unknown", workday=(False, "vacation")) == "skip_vacation"


def test_last_check_roundtrip_and_corrupt():
    assert check.last_check() is None
    check.record_last_check("remind", "paused")
    got = check.last_check()
    assert got["action"] == "remind" and got["state"] == "paused" and got["time"]
    assert set(got) == {"time", "action", "state"}
    check.last_check_path().write_text("not json")
    assert check.last_check() is None


@pytest.mark.parametrize("hour", [10, 13])
@pytest.mark.parametrize("state", ["paused", "not-running"])
def test_session_start_hours_auto_play(hour, state):
    assert decide(hour, state) == "play"  # start hour and the tick when lunch ends


def test_session_start_hours_stay_quiet_when_timing():
    assert decide(10, "timing") == "ok"
    assert decide(13, "hidden") == "ok"


def test_other_hours_only_remind_never_play():
    assert decide(11, "paused") == "remind"
    assert decide(15, "not-running") == "remind"


def test_lunch_without_skip_has_no_second_play_hour():
    assert decide(13, "paused", Settings(lunch_skip=False)) == "remind"


@pytest.mark.parametrize("state", ["timing", "running", "hidden"])
def test_lunch_pauses_a_running_timer(state):
    assert decide(12, state) == "pause"


def test_lunch_pause_is_off_when_lunch_skip_is_off():
    assert decide(12, "timing", Settings(lunch_skip=False)) == "ok"


def test_unknown_still_warns_at_session_start():
    assert decide(10, "unknown") == "warn_unknown"
