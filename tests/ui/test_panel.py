import pytest

from lumen.backends.fakes import FakeDdcBackend
from lumen.engine.apply import ProfileApplier
from lumen.model.monitor import MonitorDescriptor

pytest.importorskip("gi")
from lumen.ui.panel import PanelWindow


def test_panel_buttons_show_messages(gtk_display, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monitor = MonitorDescriptor(identifier="fake:0")
    backend = FakeDdcBackend(monitors=[monitor])
    panel = PanelWindow(None, ProfileApplier(backend, monitor, backend.capabilities(monitor)))
    try:
        panel._profile_button.emit("clicked")
        assert panel._toast.get_title() == "No saved calibrations yet"
        panel._restore_button.emit("clicked")
        assert panel._toast.get_title() == "No snapshot to restore"
        assert panel._header.get_root() is panel
    finally:
        panel.destroy()


def test_panel_applies_selected_default(gtk_display, monkeypatch, tmp_path):
    from lumen.model.profile import new_profile, save_profile
    from lumen.model.settings import Settings, save_settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monitor = MonitorDescriptor(identifier="fake:0")
    backend = FakeDdcBackend(monitors=[monitor], capabilities={0x10: ()},
                             values={0x10: (40, 100)})
    save_profile(new_profile("Day", monitor, ddc={0x10: 25}))
    save_profile(new_profile("Evening", monitor, ddc={0x10: 65}))
    save_settings(Settings(default_profile="evening.json"))
    panel = PanelWindow(None, ProfileApplier(backend, monitor, backend.capabilities(monitor)))
    try:
        panel._profile_button.emit("clicked")
        assert backend.get(monitor, 0x10).value == 65
    finally:
        panel.destroy()
