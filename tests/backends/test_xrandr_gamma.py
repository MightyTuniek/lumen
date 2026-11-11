"""The XRandR gamma backend.

The critical behaviour pinned here is the flush-and-verify rule from the
spec (section 2.4.1): XRRSetCrtcGamma is an asynchronous request, and a
write that is not flushed is silently discarded. The backend must never
report success for a write that did not land.
"""

import pytest

from lumen.backends.base import GammaError
from lumen.backends.gamma.xrandr import GammaUnavailable, XRandRGammaBackend


class _Ramp:
    def __init__(self, red, green, blue):
        self.red = red
        self.green = green
        self.blue = blue


class FakeXRandr:
    """Stands in for the RandR calls python-xlib makes."""

    def __init__(self, size=1024, accept=True):
        self.size = size
        self.accept = accept
        self.ramp = tuple(i * 65535 // (size - 1) for i in range(size))
        self.set_calls = 0
        self.flushed = 0
        self.synced = 0

    def xrandr_get_crtc_gamma_size(self, crtc):
        return self.size

    def xrandr_get_crtc_gamma(self, crtc):
        return _Ramp(self.ramp, self.ramp, self.ramp)

    def xrandr_set_crtc_gamma(self, crtc, size, red, green, blue):
        self.set_calls += 1
        if self.accept:
            self.ramp = tuple(red)

    def flush(self):
        self.flushed += 1

    def sync(self):
        self.synced += 1


class FakeDisplay:
    def __init__(self, xrandr):
        self.xrandr_get_crtc_gamma_size = xrandr.xrandr_get_crtc_gamma_size
        self.xrandr_get_crtc_gamma = xrandr.xrandr_get_crtc_gamma
        self.xrandr_set_crtc_gamma = xrandr.xrandr_set_crtc_gamma
        self._xrandr = xrandr

    def flush(self):
        self._xrandr.flush()

    def sync(self):
        self._xrandr.sync()


def make_backend(accept=True):
    xr = FakeXRandr(accept=accept)
    backend = XRandRGammaBackend.__new__(XRandRGammaBackend)
    backend._display = FakeDisplay(xr)
    backend._crtcs = {"HDMI-A-0": 78}
    backend._size = 1024
    backend._refresh_crtcs = lambda: None
    return backend, xr


def test_lists_outputs():
    backend, _ = make_backend()
    assert backend.list_outputs() == ["HDMI-A-0"]


def test_read_ramp_returns_three_channels():
    backend, _ = make_backend()
    red, green, blue = backend.read_ramp("HDMI-A-0")
    assert len(red) == 1024
    assert len(green) == 1024
    assert len(blue) == 1024


def test_write_ramp_flushes_and_syncs():
    """Without the flush the request never reaches the server and is lost."""
    backend, xr = make_backend()
    ramp = tuple(i * 65535 // 1023 for i in range(1024))
    backend.write_ramp("HDMI-A-0", ramp, ramp, ramp)
    assert xr.set_calls == 1
    assert xr.flushed >= 1
    assert xr.synced >= 1


def test_write_ramp_raises_when_the_ramp_did_not_change():
    """The server rejecting the write must not be reported as success.

    The ramp written here must differ from the ramp already in place,
    otherwise the read-back comparison could not tell the two apart.
    """
    backend, xr = make_backend(accept=False)
    ramp = tuple(min(65535, int(65535 * ((i / 1023) ** (1 / 1.8)))) for i in range(1024))
    assert ramp != xr.ramp, "the test ramp must differ from the current one"
    with pytest.raises(GammaError):
        backend.write_ramp("HDMI-A-0", ramp, ramp, ramp)


def test_write_to_unknown_output_raises():
    backend, _ = make_backend()
    ramp = tuple(i * 65535 // 1023 for i in range(1024))
    with pytest.raises(GammaError):
        backend.write_ramp("NOPE-0", ramp, ramp, ramp)


def test_write_with_a_mismatched_ramp_length_raises():
    backend, xr = make_backend()
    with pytest.raises(GammaError):
        backend.write_ramp("HDMI-A-0", (1, 2, 3), (1, 2, 3), (1, 2, 3))
    assert xr.set_calls == 0


def test_missing_display_raises_gamma_unavailable():
    with pytest.raises(GammaUnavailable):
        XRandRGammaBackend(display_name="/nonexistent-display")
