"""Preferences survive restarts without changing calibration profiles."""

import json

import pytest

from lumen.model.profile import new_profile, save_profile
from lumen.model.monitor import MonitorDescriptor
from lumen.model.settings import (
    Settings, load_settings, save_settings, selected_profile_path, settings_path,
)


@pytest.fixture(autouse=True)
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_settings_round_trip():
    settings = Settings(appearance="dark", opening_screen="panel",
                        default_profile="evening.json", apply_at_login=False,
                        reapply_on_wake=False, manage_night_light=False)
    save_settings(settings)
    assert load_settings() == settings
    assert settings_path().name == "settings.json"
    assert selected_profile_path() is None


@pytest.mark.parametrize("content", [None, "{", "[]", '"dark"', "null"])
def test_missing_or_corrupt_settings_use_defaults(tmp_path, content):
    path = tmp_path / "preferences.json"
    if content is not None:
        path.write_text(content)
    assert load_settings(path) == Settings()


def test_invalid_fields_do_not_discard_valid_preferences(tmp_path):
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({"appearance": "dark", "opening_screen": "invalid",
                                "apply_at_login": "false", "reapply_on_wake": 0,
                                "default_profile": "../other.json", "future": True}))
    assert load_settings(path) == Settings(appearance="dark")


@pytest.mark.parametrize("filename", ["../outside.json", "/tmp/a.json", "a\\b.json", "a", 7])
def test_profile_selection_must_be_a_json_filename(filename):
    with pytest.raises(ValueError):
        Settings(default_profile=filename)


def test_failed_atomic_save_preserves_previous_preferences(monkeypatch):
    save_settings(Settings(appearance="dark"))

    def fail(*args):
        raise OSError("Disk full")

    monkeypatch.setattr("lumen.model.settings.os.replace", fail)
    with pytest.raises(OSError, match="Disk full"):
        save_settings(Settings(appearance="light"))
    assert load_settings().appearance == "dark"
    assert list(settings_path().parent.iterdir()) == [settings_path()]


def test_explicit_profile_overrides_alphabetical_default():
    monitor = MonitorDescriptor(identifier="fake:0")
    first = save_profile(new_profile("Day", monitor))
    selected = save_profile(new_profile("Evening", monitor))
    assert selected_profile_path() == first
    save_settings(Settings(default_profile=selected.name))
    assert selected_profile_path() == selected
    selected.unlink()
    assert selected_profile_path() is None
