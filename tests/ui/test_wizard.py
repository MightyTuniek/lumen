"""Exercise actual GTK signals, rather than calling handlers directly."""

import pytest

from lumen.backends.fakes import FakeDdcBackend
from lumen.engine.apply import ProfileApplier
from lumen.engine.calibration import CalibrationSession
from lumen.model.monitor import MonitorDescriptor
from lumen.model.profile import load_profile

pytest.importorskip("gi")
from lumen.ui.wizard import WizardWindow


@pytest.fixture
def wizard(gtk_display, monkeypatch):
    monitor = MonitorDescriptor(identifier="fake:0")
    backend = FakeDdcBackend(
        monitors=[monitor], capabilities={0x10: (), 0x0C: ()},
        values={0x10: (40, 100), 0x0C: (50, 100)},
    )
    caps = backend.capabilities(monitor)
    session = CalibrationSession(backend, monitor, caps)
    callbacks = []
    monkeypatch.setattr("lumen.ui.wizard.read_controls", lambda controls, done: callbacks.append(done))
    window = WizardWindow(None, session, ProfileApplier(backend, monitor, caps))
    yield window, backend, monitor, callbacks
    window.destroy()


def ready(window):
    for control in window._session.controls:
        control.read()
    window._on_controls_ready_main()


def test_next_and_back_buttons_navigate(wizard):
    window, _, _, _ = wizard
    ready(window)
    assert not window._back_button.get_sensitive()
    window._next_button.emit("clicked")
    assert window._session.current_step is window._session.steps[1]
    assert window._next_button.get_label() == "Finish"
    window._back_button.emit("clicked")
    assert window._session.current_step is window._session.steps[0]


def test_adjustment_waits_for_the_rollback_snapshot(wizard):
    window, _, _, _ = wizard
    assert not window._controls_scroll.get_sensitive()
    assert not window._next_button.get_sensitive()
    assert window._cancel_button.get_sensitive()
    ready(window)
    assert window._controls_scroll.get_sensitive()
    assert window._next_button.get_sensitive()


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_cancelling_or_closing_restores_original_values(wizard, action):
    window, backend, monitor, _ = wizard
    ready(window)
    window.present()
    window._sliders[0]._scale.set_value(70)
    assert backend.get(monitor, 0x10).value == 70
    if action == "cancel":
        window._cancel_button.emit("clicked")
    else:
        window.close()
    assert backend.get(monitor, 0x10).value == 40


def test_finish_saves_changes_without_rolling_them_back(wizard, monkeypatch, tmp_path):
    window, backend, monitor, _ = wizard
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    ready(window)
    window.present()
    window._sliders[0]._scale.set_value(70)
    window._next_button.emit("clicked")
    window._next_button.emit("clicked")
    paths = list(tmp_path.rglob("*.json"))
    assert len(paths) == 1
    assert load_profile(paths[0]).ddc[0x10] == 70
    assert backend.get(monitor, 0x10).value == 70


@pytest.mark.parametrize("finish", [True, False])
def test_gamma_adjustments_are_saved_or_restored(gtk_display, monkeypatch, tmp_path, finish):
    from lumen.backends.fakes import FakeGammaBackend

    monitor = MonitorDescriptor(identifier="fake:0")
    backend = FakeDdcBackend(monitors=[monitor])
    caps = backend.capabilities(monitor)
    gamma = FakeGammaBackend()
    original = gamma.read_ramp("fake-0")
    session = CalibrationSession(backend, monitor, caps, gamma, "fake-0")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("lumen.ui.wizard.read_controls", lambda *args: None)
    window = WizardWindow(None, session, ProfileApplier(backend, monitor, caps, gamma, "fake-0"))
    try:
        ready(window)
        window.present()
        assert len(window._sliders) == 3
        window._sliders[0]._scale.set_value(1.8)
        assert gamma.read_ramp("fake-0") != original
        if finish:
            window._next_button.emit("clicked")
            profile = load_profile(next(tmp_path.rglob("*.json")))
            assert profile.gamma.effective_exponents == (1.8, 1.0, 1.0)
            assert gamma.read_ramp("fake-0") != original
        else:
            window.close()
            assert gamma.read_ramp("fake-0") == original
            assert not list(tmp_path.rglob("*.json"))
    finally:
        window.destroy()
