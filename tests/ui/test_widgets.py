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
