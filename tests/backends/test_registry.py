import pytest

from lumen.backends.base import DdcError
from lumen.backends.registry import (
    BackendAvailability,
    detect_backends,
    make_ddc_backend,
    make_gamma_backend,
)


def test_availability_reports_a_reason_when_ddc_is_missing():
    availability = BackendAvailability(
        ddc=False, gamma=False, ddc_reason="no backend", gamma_reason="no backend"
    )
    assert availability.ddc is False
    assert availability.ddc_reason


def test_detect_backends_never_raises():
    """Detection must degrade gracefully, not crash the application."""
    availability = detect_backends()
    assert isinstance(availability, BackendAvailability)
    assert isinstance(availability.ddc, bool)
    assert isinstance(availability.gamma, bool)


def test_make_gamma_backend_returns_none_or_a_backend():
    backend = make_gamma_backend()
    assert backend is None or hasattr(backend, "read_ramp")


def test_make_ddc_backend_raises_a_useful_error_when_nothing_works(monkeypatch):
    import lumen.backends.registry as registry

    def boom():
        raise DdcError("nothing works")

    monkeypatch.setattr(registry, "_DDC_BACKENDS", (boom,))
    with pytest.raises(DdcError) as error:
        registry.make_ddc_backend()
    assert "nothing works" in str(error.value)
