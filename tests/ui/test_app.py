import pytest

pytest.importorskip("gi")
from lumen.ui.app import LumenApplication

pytestmark = pytest.mark.usefixtures("gtk_display")


def test_startup_error_has_a_window_and_can_be_closed():
    app = LumenApplication()
    app.register(None)
    try:
        app._show_error("No display could be reached", "No backend is available")
        window = app.get_active_window()
        assert window is not None
        assert window.get_visible()
        dialog = window.get_visible_dialog()
        assert dialog.get_heading() == "No display could be reached"
        dialog.emit("response", "close")
        assert not app.get_windows()
    finally:
        for window in app.get_windows():
            window.destroy()


def test_settings_action_reuses_window_and_applies_appearance(monkeypatch, tmp_path):
    from uuid import uuid4
    from gi.repository import Adw
    from lumen.model.settings import load_settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = LumenApplication()
    app.set_application_id(f"io.github.lumen_cal.Test{uuid4().hex}")
    app.register(None)
    manager = app.get_style_manager()
    previous = manager.get_color_scheme()
    try:
        app.activate_action("settings", None)
        window = app._settings_window
        assert window is not None
        app.activate_action("settings", None)
        assert app._settings_window is window
        window._rows["appearance"].set_selected(2)
        assert manager.get_color_scheme() == Adw.ColorScheme.FORCE_DARK
        assert load_settings().appearance == "dark"
        window.close()
        assert app._settings_window is None
    finally:
        manager.set_color_scheme(previous)
        for window in app.get_windows():
            window.destroy()


@pytest.mark.parametrize("manage_night_light", [True, False])
def test_startup_preferences_choose_panel_and_night_light_policy(
    monkeypatch, tmp_path, manage_night_light,
):
    from types import SimpleNamespace
    from uuid import uuid4
    from gi.repository import Adw
    from lumen.model.settings import Settings, save_settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_settings(Settings(opening_screen="panel", manage_night_light=manage_night_light))
    ownership = []
    night_light = SimpleNamespace(available=True, scheduled=True,
                                  take_ownership=lambda: ownership.append(True))
    monkeypatch.setattr("lumen.ui.app.NightLightOwnership", lambda: night_light)
    panels = []

    def panel(application, applier):
        panels.append(applier)
        return Adw.ApplicationWindow(application=application)

    monkeypatch.setattr("lumen.ui.app.PanelWindow", panel)
    app = LumenApplication()
    app.set_application_id(f"io.github.lumen_cal.Test{uuid4().hex}")
    app.register(None)
    applier = object()
    monkeypatch.setattr(app, "_build_applier", lambda: applier)
    try:
        app.do_activate()
        assert panels == [applier]
        assert ownership == ([True] if manage_night_light else [])
    finally:
        for window in app.get_windows():
            window.destroy()
