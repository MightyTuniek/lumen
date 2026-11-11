import pytest

from lumen.backends.base import DdcError
from lumen.backends.fakes import FakeDdcBackend
from lumen.model.monitor import MonitorDescriptor

MONITOR = MonitorDescriptor(identifier="fake:0", manufacturer="Acer", model="VG240Y")


def make_backend(**kwargs):
    return FakeDdcBackend(monitors=[MONITOR], **kwargs)


def test_lists_the_monitors_it_was_given():
    backend = make_backend()
    assert [m.identifier for m in backend.list_monitors()] == ["fake:0"]


def test_get_returns_the_stored_value_and_maximum():
    backend = make_backend(values={0x10: (40, 100)})
    result = backend.get(MONITOR, 0x10)
    assert result.value == 40
    assert result.maximum == 100
    assert result.supported is True


def test_get_of_unknown_code_is_unsupported_not_an_error():
    backend = make_backend()
    result = backend.get(MONITOR, 0xAB)
    assert result.supported is False


def test_set_updates_the_stored_value():
    backend = make_backend(values={0x10: (40, 100)})
    backend.set(MONITOR, 0x10, 60)
    assert backend.get(MONITOR, 0x10).value == 60


def test_set_raises_when_the_monitor_rejects_the_write():
    backend = make_backend(values={0x10: (40, 100)}, fail_on={0x10})
    with pytest.raises(DdcError):
        backend.set(MONITOR, 0x10, 60)
    assert backend.get(MONITOR, 0x10).value == 40, "a failed write must not change the value"


def test_set_of_a_value_outside_range_is_rejected():
    backend = make_backend(values={0x10: (40, 100)})
    with pytest.raises(DdcError):
        backend.set(MONITOR, 0x10, 500)
    with pytest.raises(DdcError):
        backend.set(MONITOR, 0x10, -1)


def test_set_honours_a_maximum_below_the_registry_assumption():
    """A monitor may report brightness maximum 50, not 100."""
    backend = make_backend(values={0x10: (10, 50)})
    backend.set(MONITOR, 0x10, 50)
    assert backend.get(MONITOR, 0x10).value == 50
    with pytest.raises(DdcError):
        backend.set(MONITOR, 0x10, 51)


def test_capabilities_reflect_the_configured_codes():
    backend = FakeDdcBackend(monitors=[MONITOR], capabilities={0x10: (), 0x12: ()})
    caps = backend.capabilities(MONITOR)
    assert 0x10 in caps.vcp_codes
    assert 0x12 in caps.vcp_codes
    assert 0x16 not in caps.vcp_codes
