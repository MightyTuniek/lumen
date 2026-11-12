"""The slider widgets.

The important behaviour here is that showing a value must not change it.
Setting a slider's value emits "value-changed", and if that is treated
as the user moving the slider, merely displaying the controls writes to
the monitor. That bug made every wizard step take seconds and changed
the display without the user asking.
"""

import pytest

from lumen.backends.fakes import FakeDdcBackend
from lumen.engine.controls import VcpControl
from lumen.model.monitor import MonitorDescriptor
from lumen.model.vcp_registry import spec_for

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # noqa: E402

from lumen.ui.widgets import ValueSlider  # noqa: E402

pytestmark = pytest.mark.usefixtures("gtk_display")

MONITOR = MonitorDescriptor(identifier="fake:0", model="VG240Y")


@pytest.fixture
def control():
    backend = FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={0x10: ()},
        values={0x10: (40, 100)},
    )
    return VcpControl(backend, MONITOR, spec_for(0x10)), backend


def test_refreshing_a_slider_does_not_write_to_the_monitor(control):
    """Showing a value must not change it."""
    vcp_control, backend = control
    writes = []
    slider = ValueSlider(vcp_control, lambda c, v: writes.append((c.code, v)))

    before = backend.get(MONITOR, 0x10).value
    slider.refresh()
    slider.refresh()

    assert backend.get(MONITOR, 0x10).value == before
    assert writes == [], "refreshing must not write to the monitor"


def test_moving_a_slider_does_write(control):
    """The user moving the slider must reach the monitor."""
    vcp_control, backend = control
    writes = []
    slider = ValueSlider(vcp_control, lambda c, v: writes.append((c.code, v)))

    slider._scale.set_value(70)

    assert writes, "moving the slider must write the new value"


def test_a_slider_shows_the_current_value(control):
    """Once the value is known the slider is usable."""
    vcp_control, _backend = control
    vcp_control.read()  # populate the cache
    slider = ValueSlider(vcp_control)
    assert slider.get_sensitive() is True


def test_a_slider_waits_for_an_unknown_value(control):
    """Before the value is known the slider waits rather than guessing."""
    vcp_control, _backend = control
    slider = ValueSlider(vcp_control)
    assert slider.get_sensitive() is False


def test_slider_adopts_the_range_reported_after_creation(control):
    vcp_control, backend = control
    backend._values[0x10] = (40, 50)
    slider = ValueSlider(vcp_control)
    vcp_control.read()
    slider.refresh()
    assert slider._scale.get_adjustment().get_upper() == 50


def test_read_completion_waits_for_all_controls():
    import threading
    import time
    from gi.repository import GLib
    from lumen.ui.widgets import read_controls

    started = threading.Event()
    release = threading.Event()
    done = threading.Event()

    class SlowControl:
        def read(self):
            started.set()
            assert release.wait(2)

    context = GLib.MainContext.default()

    def drain():
        while context.pending():
            context.iteration(False)

    read_controls([SlowControl()], lambda: done.set())
    try:
        assert started.wait(2)
        drain()
        assert not done.is_set(), "completion ran before the read finished"
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while not done.is_set() and time.monotonic() < deadline:
        drain()
        time.sleep(0.001)
    assert done.is_set()


def test_preset_selection_writes_only_an_advertised_value():
    from lumen.engine.controls import PresetControl

    backend = FakeDdcBackend(monitors=[MONITOR], values={0x14: (5, 13)})
    control = PresetControl(backend, MONITOR, spec_for(0x14), (5, 8, 11))
    control.read()
    writes = []
    slider = ValueSlider(control, lambda c, v: writes.append(v))
    slider.refresh()
    assert writes == []
    slider._preset.set_selected(1)
    assert writes == [8]


def test_typed_value_is_applied_on_enter_and_updates_the_slider(control):
    vcp_control, backend = control
    vcp_control.read()
    slider = ValueSlider(vcp_control, lambda c, v: c.write(v))
    slider._entry.set_text("73")
    assert backend.get(MONITOR, 0x10).value == 40
    slider._entry.emit("activate")
    assert backend.get(MONITOR, 0x10).value == 73
    assert slider._scale.get_value() == 73
    slider._scale.set_value(65)
    assert slider._entry.get_text() == "65"


@pytest.mark.parametrize("text", ["", "hello", "nan", "inf", "-1", "101", "40.5"])
def test_invalid_typed_values_do_not_write(control, text):
    vcp_control, backend = control
    vcp_control.read()
    slider = ValueSlider(vcp_control, lambda c, v: c.write(v))
    slider._entry.set_text(text)
    slider._entry.emit("activate")
    assert backend.get(MONITOR, 0x10).value == 40
    assert slider._scale.get_value() == 40
    assert slider._entry.has_css_class("error")
    slider._entry.set_text("60")
    slider._entry.emit("activate")
    assert backend.get(MONITOR, 0x10).value == 60
    assert not slider._entry.has_css_class("error")


def test_typed_value_uses_the_monitor_reported_maximum(control):
    vcp_control, backend = control
    backend._values[0x10] = (40, 50)
    vcp_control.read()
    writes = []
    slider = ValueSlider(vcp_control, lambda c, v: writes.append(v))
    slider._entry.set_text("70")
    slider._entry.emit("activate")
    assert writes == []
    assert slider._entry.has_css_class("error")


def test_gamma_can_be_typed_with_decimal_precision():
    from lumen.backends.fakes import FakeGammaBackend
    from lumen.engine.controls import GammaControl

    gamma = FakeGammaBackend()
    control = GammaControl(gamma, "fake-0", "red")
    control.read()
    slider = ValueSlider(control, lambda c, v: c.write(v))
    slider._entry.set_text("1.85")
    slider._entry.emit("activate")
    assert control.peek() == pytest.approx(1.85)
    assert slider._scale.get_value() == pytest.approx(1.85)
    slider.refresh()
    assert slider._entry.get_text() == "1.85"
