import pytest

from lumen.backends.base import DdcError, GammaError
from lumen.backends.fakes import FakeDdcBackend, FakeGammaBackend
from lumen.engine.controls import (
    ControlKind,
    GammaControl,
    PresetControl,
    VcpControl,
    build_vcp_controls,
)
from lumen.model.capability import parse_capabilities
from lumen.model.monitor import MonitorDescriptor
from lumen.model.vcp_registry import spec_for

MONITOR = MonitorDescriptor(identifier="fake:0", model="VG240Y")

CAPS = parse_capabilities(
    "vcp(04 10 12 16 18 1A 6C 6E 70 14(05 06 08 0B) 60(01 0F 11)) mccs_ver(2.2)"
)

VALUES = {
    0x10: (40, 100),
    0x12: (50, 100),
    0x16: (50, 100),
    0x18: (50, 100),
    0x1A: (50, 100),
    0x6C: (50, 100),
    0x6E: (50, 100),
    0x70: (50, 100),
    0x14: (11, 13),
    0x60: (17, 0x11),
}


def make_ddc(**kwargs):
    return FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={code: () for code in CAPS.vcp_codes},
        values=dict(VALUES),
        **kwargs,
    )


def test_build_vcp_controls_excludes_dangerous_codes():
    controls = build_vcp_controls(make_ddc(), MONITOR, CAPS)
    codes = [control.code for control in controls]
    assert "vcp_0x10" in codes
    assert "vcp_0x04" not in codes


def test_build_vcp_controls_uses_the_reported_maximum_not_the_registry_default():
    """The registry says brightness goes to 100, but a real panel may not."""
    backend = FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={0x10: ()},
        values={0x10: (10, 50)},
    )
    controls = build_vcp_controls(backend, MONITOR, parse_capabilities("vcp(10)"))
    control = controls[0]
    # The range is adopted when the value is first read, because reading
    # the bus is what costs time and must not happen while drawing.
    assert control.reported_maximum() == 50
    assert control.maximum == 50
    assert control.minimum == 0


def test_build_vcp_controls_makes_a_preset_for_enumerated_codes():
    controls = build_vcp_controls(make_ddc(), MONITOR, CAPS)
    preset = next(c for c in controls if c.code == "vcp_0x14")
    assert isinstance(preset, PresetControl)
    assert preset.options == (0x05, 0x06, 0x08, 0x0B)


def test_a_non_enumerated_code_is_a_plain_control():
    controls = build_vcp_controls(make_ddc(), MONITOR, CAPS)
    brightness = next(c for c in controls if c.code == "vcp_0x10")
    assert isinstance(brightness, VcpControl)
    assert brightness.kind is ControlKind.VCP


def test_reading_a_control_returns_its_current_value():
    backend = make_ddc()
    control = VcpControl(backend, MONITOR, spec_for(0x10))
    assert control.read() == 40


def test_writing_a_control_changes_the_backend():
    backend = make_ddc()
    control = VcpControl(backend, MONITOR, spec_for(0x10))
    control.write(70)
    assert backend.get(MONITOR, 0x10).value == 70


def test_writing_a_control_below_the_minimum_is_refused():
    backend = make_ddc()
    control = VcpControl(backend, MONITOR, spec_for(0x10))
    with pytest.raises(DdcError):
        control.write(-1)


def test_writing_a_control_above_the_maximum_is_refused():
    backend = make_ddc()
    control = VcpControl(backend, MONITOR, spec_for(0x10))
    with pytest.raises(DdcError):
        control.write(101)


def test_an_unsupported_control_is_marked_unavailable():
    backend = FakeDdcBackend(monitors=[MONITOR], values={})
    control = VcpControl(backend, MONITOR, spec_for(0x10))
    assert control.available() is False


def test_a_preset_control_rejects_a_value_outside_its_options():
    backend = make_ddc()
    control = PresetControl(backend, MONITOR, spec_for(0x14), (0x05, 0x06, 0x08, 0x0B))
    with pytest.raises(DdcError):
        control.write(0x77)


def test_a_preset_control_writes_an_allowed_option():
    backend = make_ddc()
    control = PresetControl(backend, MONITOR, spec_for(0x14), (0x05, 0x06, 0x08, 0x0B))
    control.write(0x05)
    assert backend.get(MONITOR, 0x14).value == 0x05


def test_gamma_control_reads_and_writes_one_channel():
    backend = FakeGammaBackend(size=64)
    control = GammaControl(backend, "fake-0", "red")
    assert control.kind is ControlKind.GAMMA
    control.write(1.8)
    red, green, _blue = backend.read_ramp("fake-0")
    assert red != green  # only the red channel changed


def test_gamma_control_reads_back_the_exponent_it_wrote():
    """The exponent must survive a write and read cycle."""
    backend = FakeGammaBackend(size=256)
    control = GammaControl(backend, "fake-0", "red")
    control.write(1.8)
    assert abs(control.read() - 1.8) < 0.05


def test_gamma_control_reads_back_a_neutral_exponent():
    backend = FakeGammaBackend(size=256)
    control = GammaControl(backend, "fake-0", "green")
    assert abs(control.read() - 1.0) < 0.05


def test_gamma_control_is_unavailable_without_a_gamma_backend():
    class NoGamma:
        def list_outputs(self):
            return []

        def read_ramp(self, output):
            raise GammaError("no gamma")

        def write_ramp(self, output, red, green, blue):
            raise GammaError("no gamma")

    control = GammaControl(NoGamma(), "fake-0", "red")
    assert control.available() is False
