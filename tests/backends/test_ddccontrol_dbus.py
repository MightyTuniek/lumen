"""The ddccontrol D-Bus backend, exercised against a fake bus.

The real service is exercised by the integration test (Task 12). These
tests pin the request and response handling, including the read-back
verification the spec requires.
"""

import pytest

from lumen.backends.base import DdcError
from lumen.backends.ddc.ddccontrol_dbus import DdcControlDBusBackend


class FakeBus:
    """Minimal stand-in for dbus.SystemBus()."""

    def __init__(self, values, capabilities="vcp(10 12 16) mccs_ver(2.2)"):
        self.values = values
        self.capabilities = capabilities
        self.written = []

    def get_object(self, service, path):
        return self

    def _dispatch(self, method, args):
        if method == "GetMonitors":
            # The real service returns arrays of one-byte structs.
            return (["dev:/dev/i2c-4"], [(1,)], ["Test Monitor"], [(0x80,)])
        if method == "OpenMonitor":
            return ("ACR0673", self.capabilities)
        if method == "GetControl":
            device, code = args
            if code not in self.values:
                # The real service reports result 0 for a code the
                # monitor does not support.
                return (0, 0, 0)
            value, maximum = self.values[code]
            return (1, value, maximum)
        if method == "SetControl":
            device, code, value = args
            self.written.append((code, value))
            self.values[code] = (value, self.values.get(code, (0, 100))[1])
            return None
        raise AssertionError(f"unexpected method {method}")


class FakeInterface:
    def __init__(self, bus):
        self.bus = bus

    def __getattr__(self, name):
        def call(*args, **kwargs):
            return self.bus._dispatch(name, args)

        return call


def make_backend(values=None, bus=None):
    bus = bus or FakeBus(values or {0x10: (40, 100), 0x12: (50, 100)})
    backend = DdcControlDBusBackend(bus=bus)
    backend._interface = FakeInterface(bus)
    return backend, bus


def test_lists_monitors_from_get_monitors():
    backend, _ = make_backend()
    monitors = backend.list_monitors()
    assert len(monitors) == 1
    assert monitors[0].identifier == "dev:/dev/i2c-4"
    assert monitors[0].backend_name == "ddccontrol"


def test_capabilities_are_parsed_from_open_monitor():
    backend, _ = make_backend()
    caps = backend.capabilities(backend.list_monitors()[0])
    assert 0x10 in caps.vcp_codes
    assert 0x12 in caps.vcp_codes


def test_get_returns_value_and_maximum():
    backend, _ = make_backend()
    result = backend.get(backend.list_monitors()[0], 0x10)
    assert result.value == 40
    assert result.maximum == 100


def test_get_of_an_unsupported_code_is_marked_unsupported():
    backend, _ = make_backend()
    result = backend.get(backend.list_monitors()[0], 0x50)
    assert result.supported is False


def test_set_writes_and_verifies():
    backend, bus = make_backend()
    backend.set(backend.list_monitors()[0], 0x10, 55)
    assert bus.written == [(0x10, 55)]


def test_set_raises_when_the_value_does_not_take():
    class RejectingBus(FakeBus):
        def _dispatch(self, method, args):
            if method == "SetControl":
                self.written.append((args[1], args[2]))
                return None  # the value does not change
            return super()._dispatch(method, args)

    bus = RejectingBus({0x10: (40, 100)})
    backend, _ = make_backend(bus=bus)
    monitor = backend.list_monitors()[0]
    with pytest.raises(DdcError):
        backend.set(monitor, 0x10, 55)
    assert bus.values[0x10][0] == 40


def test_set_rejects_a_value_above_the_reported_maximum():
    backend, _ = make_backend()
    monitor = backend.list_monitors()[0]
    with pytest.raises(DdcError):
        backend.set(monitor, 0x10, 500)
