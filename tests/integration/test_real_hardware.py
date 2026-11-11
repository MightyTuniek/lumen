"""Read-only checks against the real display.

These tests never write to the hardware unless the caller passes
LUMEN_RUN_WRITE_TESTS=1, and even then every value they touch is
restored before the test ends.
"""

import os

import pytest

from lumen.engine.apply import ProfileApplier

pytestmark = pytest.mark.integration


def test_the_backend_lists_at_least_one_monitor(ddc_backend):
    monitors = ddc_backend.list_monitors()
    assert len(monitors) >= 1


def test_the_monitor_advertises_its_capabilities(ddc_backend, monitor):
    capabilities = ddc_backend.capabilities(monitor)
    assert capabilities.vcp_codes
    assert 0x10 in capabilities.vcp_codes, "every monitor must offer brightness"


def test_reading_brightness_succeeds(ddc_backend, monitor):
    value = ddc_backend.get(monitor, 0x10)
    assert value.supported is True
    assert 0 <= value.value <= value.maximum


def test_the_reported_maximum_is_plausible(ddc_backend, monitor):
    """A brightness maximum of zero would break every slider."""
    value = ddc_backend.get(monitor, 0x10)
    assert value.maximum > 0


def test_the_applier_can_capture_and_restore(ddc_backend, monitor):
    capabilities = ddc_backend.capabilities(monitor)
    applier = ProfileApplier(ddc_backend, monitor, capabilities)
    snapshot = applier.capture()
    assert 0x10 in snapshot.ddc
    applier.restore(snapshot)
    after = ddc_backend.get(monitor, 0x10)
    assert after.value == snapshot.ddc[0x10]


def test_the_gamma_backend_sees_the_connected_output():
    from lumen.backends.gamma.xrandr import GammaUnavailable, XRandRGammaBackend

    try:
        backend = XRandRGammaBackend()
    except GammaUnavailable:
        pytest.skip("no X display available")
    outputs = backend.list_outputs()
    if not outputs:
        pytest.skip("no gamma-capable output")
    red, green, blue = backend.read_ramp(outputs[0])
    assert len(red) == len(green) == len(blue)
    assert len(red) >= 256


@pytest.mark.skipif(
    not os.environ.get("LUMEN_RUN_WRITE_TESTS"),
    reason="set LUMEN_RUN_WRITE_TESTS=1 to allow writes to real hardware",
)
def test_a_write_and_restore_leaves_the_display_unchanged(ddc_backend, monitor):
    """Write a different value, then put the original back.

    This test changes what the user sees. It is opt-in through
    LUMEN_RUN_WRITE_TESTS, and it always restores the original value.
    """
    capabilities = ddc_backend.capabilities(monitor)
    applier = ProfileApplier(ddc_backend, monitor, capabilities)
    snapshot = applier.capture()
    original = snapshot.ddc[0x10]

    try:
        target = 50 if original != 50 else 60
        ddc_backend.set(monitor, 0x10, target)
        assert ddc_backend.get(monitor, 0x10).value == target
    finally:
        ddc_backend.set(monitor, 0x10, original)

    assert ddc_backend.get(monitor, 0x10).value == original
