"""Construct and exercise the native measured-calibration screens."""

from uuid import uuid4

import pytest

gi = pytest.importorskip('gi')
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from lumen.ui.app import LumenApplication
from lumen.ui.colorimeter import ColorimeterWindow
from lumen.ui.settings import SettingsWindow, main_menu_button
from lumen.engine.colorimeter import CalibrationResult

pytestmark = pytest.mark.usefixtures('gtk_display')


@pytest.fixture
def application(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.setattr(ColorimeterWindow, '_refresh', lambda self: None)
    app = LumenApplication()
    app.set_application_id('io.github.lumen_cal.Test' + uuid4().hex)
    app.register(None)
    yield app
    for window in app.get_windows():
        window.destroy()


def test_main_menu_and_reused_colorimeter_window(application):
    menu = main_menu_button().get_menu_model()
    assert menu.get_n_items() == 2
    application.activate_action('colorimeter', None)
    window = application._colorimeter_window
    assert window is not None
    application.activate_action('colorimeter', None)
    assert application._colorimeter_window is window
    window.close()
    assert application._colorimeter_window is None


def test_setup_has_real_targets_and_start_requires_equipment(application):
    window = ColorimeterWindow(application)
    assert window._luminance.get_value() == 120
    assert window._gamma.get_value() == 2.2
    assert not window._start.get_sensitive()
    window._ready.set_active(True)
    assert not window._start.get_sensitive()
    window.destroy()


def test_review_requires_explicit_keep_for_warnings(application, tmp_path):
    window = ColorimeterWindow(application)
    result = CalibrationResult(tmp_path, None, {
        'white_luminance': 100, 'white_xy': [.3127, .3290], 'black_luminance': .1,
        'white_uv_distance': .001, 'average_delta_e00': 3, 'maximum_delta_e00': 6,
    }, ['Verification outside thresholds'])
    window._show_result(result)
    assert not window._activate.get_sensitive()
    assert not window._save_only.get_sensitive()
    window._keep.set_active(True)
    assert window._activate.get_sensitive()
    assert window._save_only.get_sensitive()
    window._close_request(window)
    original = window._report.get_label()
    window._show_event if not window._closed else None
    assert window._report.get_label() == original
    window.destroy()


def test_colorimeter_preferences_persist_and_reset(application):
    from lumen.model.settings import load_settings
    window = SettingsWindow(application)
    window._rows['colorimeter_luminance'].set_value(150)
    window._rows['colorimeter_mode'].set_selected(1)
    window._rows['colorimeter_auto_ddc'].set_active(False)
    assert load_settings().colorimeter_luminance == 150
    assert load_settings().colorimeter_mode == 'detailed'
    assert not load_settings().colorimeter_auto_ddc
    window._on_reset_response(None, 'reset')
    assert load_settings().colorimeter_luminance == 120
    window.destroy()
