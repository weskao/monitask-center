import json

import pytest

from monitask_center import config, i18n
from monitask_center.config import Settings


def test_home_dir_honours_env(sandbox):
    assert config.home_dir() == sandbox / ".monitask-center"
    assert config.config_path().parent == config.home_dir()


def test_defaults_and_hours():
    s = Settings()
    assert (s.start_hour, s.end_hour, s.interval_hours) == (10, 18, 1)
    assert (s.lunch_skip, s.lunch_start_hour, s.lunch_end_hour) == (True, 12, 13)
    assert s.check_hours() == list(range(10, 19))
    assert not hasattr(s, "auto_start") and not hasattr(s, "auto_pause_at_end")


def test_is_lunch_half_open_window():
    s = Settings()
    assert [h for h in range(10, 19) if s.is_lunch(h)] == [12]
    assert not s.is_lunch(13)  # the 13:00 tick is a normal check
    assert not Settings(lunch_skip=False).is_lunch(12)
    assert [h for h in range(24) if Settings(lunch_start_hour=11, lunch_end_hour=14).is_lunch(h)] == [11, 12, 13]


@pytest.mark.parametrize("bad", [
    {"lunch_start_hour": 13, "lunch_end_hour": 13},
    {"lunch_start_hour": 14, "lunch_end_hour": 12},
    {"lunch_start_hour": -1},
    {"lunch_end_hour": 25},
    {"start_hour": 18, "end_hour": 10},
    {"end_hour": 24},
    {"interval_hours": 0},
    {"interval_hours": 9},
])
def test_validate_rejects(bad):
    with pytest.raises(ValueError):
        config.validate(Settings(**bad))


def test_validate_accepts_lunch_to_midnight_and_normalizes():
    s = config.validate(Settings(lunch_end_hour=24, language="xx", vacation=("2026/10/05", "bogus", "2026-10-05")))
    assert s.language == i18n.DEFAULT_LANGUAGE and s.vacation == ("2026-10-05",)


def test_save_load_roundtrip():
    s = Settings(start_hour=9, end_hour=17, interval_hours=2, lunch_skip=False,
                 lunch_start_hour=11, lunch_end_hour=12, telegram=False, vacation=("2026-10-09", "2026-10-05"))
    config.save(s)
    loaded = config.load()
    assert loaded == config.validate(s)
    assert loaded.vacation == ("2026-10-05", "2026-10-09")


def test_load_missing_or_garbage_gives_defaults():
    assert config.load() == Settings()
    config.config_path().parent.mkdir(parents=True)
    config.config_path().write_text("not json")
    assert config.load() == Settings()
    config.config_path().write_text("[1]")
    assert config.load() == Settings()


def test_load_drops_legacy_keys_and_bad_types():
    config.config_path().parent.mkdir(parents=True)
    config.config_path().write_text(json.dumps(
        {"auto_start": True, "auto_pause_at_end": True, "start_hour": "9", "lunch_skip": 1, "end_hour": 17}))
    s = config.load()
    assert (s.start_hour, s.end_hour, s.lunch_skip) == (10, 17, True)


def test_load_invalid_lunch_resets_only_lunch():
    config.config_path().parent.mkdir(parents=True)
    config.config_path().write_text(json.dumps({"start_hour": 9, "lunch_start_hour": 15, "lunch_end_hour": 14}))
    s = config.load()
    assert (s.start_hour, s.lunch_start_hour, s.lunch_end_hour) == (9, 12, 13)


def test_load_invalid_window_resets_only_window():
    config.config_path().parent.mkdir(parents=True)
    config.config_path().write_text(json.dumps({"start_hour": 20, "end_hour": 8, "lunch_start_hour": 11}))
    s = config.load()
    assert (s.start_hour, s.end_hour, s.lunch_start_hour) == (10, 18, 11)


def test_parse_dates():
    assert [d.isoformat() for d in config.parse_dates("2026-10-05~2026-10-07")] == [
        "2026-10-05", "2026-10-06", "2026-10-07"]
    assert len(config.parse_dates("20261005")) == 1
    for bad in ("nope", "2026-10-09~2026-10-05", "2026-01-01~2028-01-01", "a~b~c"):
        with pytest.raises(ValueError):
            config.parse_dates(bad)


def test_i18n_merges_dir_and_detects_duplicates(tmp_path, monkeypatch):
    loc = tmp_path / "locales"
    (loc / "zh-TW").mkdir(parents=True)
    (loc / "xx").mkdir()
    (loc / "zh-TW" / "a.json").write_text('{"k.a": "A {n}"}')
    (loc / "zh-TW" / "b.json").write_text('{"k.b": "B"}')
    (loc / "xx" / "a.json").write_text('{"k.a": "XA"}')
    (loc / "stray.json").write_text("{}")
    monkeypatch.setattr(i18n, "LOCALES_DIR", loc)
    i18n.catalog.cache_clear()
    assert i18n.available() == ["xx", "zh-TW"]
    assert i18n.t("k.a", n=1) == "A 1" and i18n.t("k.b") == "B"
    assert i18n.t("k.a", language="xx") == "XA" and i18n.t("k.b", language="xx") == "B"  # fallback chain
    assert i18n.t("missing.key") == "missing.key"
    (loc / "zh-TW" / "c.json").write_text('{"k.b": "dup"}')
    i18n.catalog.cache_clear()
    with pytest.raises(ValueError):
        i18n.catalog("zh-TW")


def test_shipped_catalog_has_no_duplicates_and_core_keys():
    cat = i18n.catalog("zh-TW")
    assert "workday.fail_open_notice" in cat and "launchd.sync.reinstalled" in cat


def _write(text):
    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text(text)


def test_load_keeps_valid_vacation_dates_and_drops_only_bad_ones():
    _write(json.dumps({"vacation": ["2026-10-09", "bogus", 5, "2026/10/05", "2026-10-09"]}))
    assert config.load().vacation == ("2026-10-05", "2026-10-09")


def test_load_clamps_oversized_interval_without_resetting_window():
    _write(json.dumps({"start_hour": 9, "end_hour": 12, "interval_hours": 8}))
    s = config.load()
    assert (s.start_hour, s.end_hour, s.interval_hours) == (9, 12, 3)


def test_load_bad_hours_still_reset_window():
    _write(json.dumps({"start_hour": 20, "end_hour": 8, "interval_hours": 2}))
    s = config.load()
    assert (s.start_hour, s.end_hour, s.interval_hours) == (10, 18, 1)


@pytest.mark.parametrize("text", ["not json", "[1]", '"x"'])
def test_corrupt_config_is_logged_once_and_defaults_used(text, monkeypatch, capsys):
    from monitask_center import notify
    lines = []
    monkeypatch.setattr(notify, "log", lines.append)
    _write(text)
    assert config.load() == Settings() and config.load() == Settings()
    assert len(lines) == 2 and str(config.config_path()) in lines[0]  # reason + backup notice
    assert lines[0] != "config.corrupt" and "{" not in lines[0]
    assert capsys.readouterr().out == ""


def test_unreadable_config_is_logged(monkeypatch):
    from monitask_center import notify
    lines = []
    monkeypatch.setattr(notify, "log", lines.append)
    config.config_path().mkdir(parents=True)  # a directory: read_text raises OSError
    assert config.load() == Settings()
    assert len(lines) == 1
    assert config.corrupt_backup() is None  # nothing to copy from a directory


def test_missing_config_is_silent(monkeypatch):
    from monitask_center import notify
    monkeypatch.setattr(notify, "log", lambda m: pytest.fail(m))
    assert config.load() == Settings()


def test_corrupt_config_is_backed_up_before_a_save_replaces_it(monkeypatch):
    from monitask_center import notify
    lines = []
    monkeypatch.setattr(notify, "log", lines.append)
    broken = '{"start_hour": 9,}'  # trailing comma
    _write(broken)
    path = config.config_path()
    backup = path.with_name("config.json.corrupt")
    assert config.load() == Settings()
    assert backup.read_text() == broken and config.corrupt_backup() == backup
    assert lines[-1] == i18n.t("config.corrupt_saved", path=backup) and str(backup) in lines[-1]
    config.save(Settings(start_hour=9))  # the TUI save that used to destroy the evidence
    assert json.loads(path.read_text())["start_hour"] == 9 and backup.read_text() == broken
    assert config.corrupt_backup(path) == backup


def test_corrupt_backup_never_overwrites_and_does_not_duplicate(monkeypatch):
    from monitask_center import notify
    lines = []
    monkeypatch.setattr(notify, "log", lines.append)
    path = config.config_path()
    _write("first")
    config.load()
    config.load()  # same content again: reused, not copied
    assert sorted(p.name for p in path.parent.glob("config.json.corrupt*")) == ["config.json.corrupt"]
    _write("second")
    config.load()
    assert path.with_name("config.json.corrupt").read_text() == "first"
    assert path.with_name("config.json.corrupt.1").read_text() == "second"
    assert config.corrupt_backup() == path.with_name("config.json.corrupt.1")
    assert len([ln for ln in lines if "config.json.corrupt" in ln]) == 2


def test_valid_or_missing_config_makes_no_backup():
    assert config.load() == Settings() and config.corrupt_backup() is None
    _write(json.dumps({"start_hour": 9}))
    config.load()
    assert not list(config.config_path().parent.glob("*.corrupt*")) and config.corrupt_backup() is None


def test_unwritable_backup_does_not_break_loading(monkeypatch):
    _write("not json")
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(PermissionError("ro")))
    assert config.load() == Settings() and config.corrupt_backup() is None


def test_play_hours_are_start_and_lunch_end():
    assert Settings().play_hours() == {10, 13}
    assert Settings(lunch_skip=False).play_hours() == {10}
    assert Settings(start_hour=9, lunch_end_hour=14).play_hours() == {9, 14}


@pytest.mark.parametrize("minutes,ok", [(18 * 60, True), (18 * 60 + 30, True), (20 * 60 + 30, True),
                                         (17 * 60 + 30, False), (21 * 60, False), (18 * 60 + 15, False)])
def test_overtime_time_is_half_hours_between_1800_and_2030(minutes, ok):
    assert config.valid_overtime(minutes) is ok
    if ok:
        assert config.validate(Settings(overtime_minutes=minutes)).overtime_minutes == minutes
    else:
        with pytest.raises(ValueError, match="overtime"):
            config.validate(Settings(overtime_minutes=minutes))


def test_overtime_defaults_and_bad_stored_value_falls_back():
    assert (Settings().overtime_prompt, config.clock(Settings().overtime_minutes)) == (True, "18:30")
    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text('{"overtime_minutes": 1000, "overtime_prompt": false}')
    loaded = config.load()
    assert loaded.overtime_minutes == 18 * 60 + 30 and loaded.overtime_prompt is False


def test_activity_alert_defaults_and_bad_stored_values_fall_back():
    assert (Settings().activity_alert_percent, Settings().activity_check_minutes) == (60, 10)
    assert config.ALERT_PERCENTS == tuple(range(10, 95, 5))
    assert config.ALERT_MINUTES == (10, 20, 30, 40, 50, 60)
    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text('{"activity_alert_percent": 62, "activity_check_minutes": 5}')
    assert (config.load().activity_alert_percent, config.load().activity_check_minutes) == (60, 10)
    config.config_path().write_text('{"activity_alert_percent": 85, "activity_check_minutes": 30}')
    assert (config.load().activity_alert_percent, config.load().activity_check_minutes) == (85, 30)
    with pytest.raises(ValueError, match="activity_alert"):
        config.validate(Settings(activity_alert_percent=95))
    with pytest.raises(ValueError, match="activity_alert"):
        config.validate(Settings(activity_check_minutes=15))  # off the 10-minute ticks


def test_activity_alert_switch_defaults_on_and_keeps_threshold_while_off():
    assert Settings().activity_alert is True  # an upgraded config without the key keeps alerting
    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text('{"activity_alert": "no"}')
    assert config.load().activity_alert is True
    config.save(Settings(activity_alert=False, activity_alert_percent=80, activity_check_minutes=30))
    loaded = config.load()
    assert (loaded.activity_alert, loaded.activity_alert_percent, loaded.activity_check_minutes) == (False, 80, 30)
