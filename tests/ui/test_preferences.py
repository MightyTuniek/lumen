"""Exercise preference signals, persistence, reset and failed writes."""

import pytest

from lumen.model.monitor import MonitorDescriptor
from lumen.model.profile import new_profile, save_profile
from lumen.model.settings import Settings, load_settings, save_settings

pytest.importorskip("gi")
from lumen.ui.settings import SettingsWindow


@pytest.fixture
def preferences(gtk_display, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    changes = []
    window = SettingsWindow(on_changed=changes.append)
    yield window, changes
    window.destroy()


def test_preferences_persist_and_notify(preferences):
    window, changes = preferences
    window._rows["appearance"].set_selected(2)
    window._rows["opening_screen"].set_selected(1)
    window._rows["apply_at_login"].set_active(False)
    window._rows["reapply_on_wake"].set_active(False)
    window._rows["manage_night_light"].set_active(False)
    expected = Settings(appearance="dark", opening_screen="panel", apply_at_login=False,
                        reapply_on_wake=False, manage_night_light=False)
    assert load_settings() == expected
    assert changes[-1] == expected


def test_saved_and_missing_profile_choices(gtk_display, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_profile(new_profile("Evening", MonitorDescriptor(identifier="fake:0")))
    save_settings(Settings(default_profile="missing.json"))
    window = SettingsWindow()
    try:
        row = window._rows["default_profile"]
        assert row.get_selected() == 2
        row.set_selected(1)
        assert load_settings().default_profile == "evening.json"
    finally:
        window.destroy()


def test_failed_save_reverts_row_and_does_not_notify(preferences, monkeypatch):
    window, changes = preferences

    def fail(*args):
        raise OSError("Disk full")

    monkeypatch.setattr("lumen.ui.settings.save_settings", fail)
    window.present()
    window._rows["appearance"].set_selected(2)
    assert window._rows["appearance"].get_selected() == 0
    assert changes == []
    assert load_settings() == Settings()
    dialog = window.get_visible_dialog()
    assert dialog.get_heading() == "Preferences could not be saved"
    dialog.emit("response", "close")


def test_reset_requires_confirmation_and_preserves_profiles(preferences):
    window, _ = preferences
    path = save_profile(new_profile("Keep", MonitorDescriptor(identifier="fake:0")))
    window._rows["appearance"].set_selected(2)
    window.present()
    window._reset_button.emit("clicked")
    window.get_visible_dialog().emit("response", "cancel")
    assert load_settings().appearance == "dark"
    window._reset_button.emit("clicked")
    window.get_visible_dialog().emit("response", "reset")
    assert load_settings() == Settings()
    assert window._rows["appearance"].get_selected() == 0
    assert path.exists()
