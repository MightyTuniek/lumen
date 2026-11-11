import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: needs real display hardware; read-only by default"
    )


@pytest.fixture(scope="session")
def ddc_backend():
    """The real DDC backend, or skip when the service is not running."""
    from lumen.backends.ddc.ddccontrol_dbus import DdcControlDBusBackend

    try:
        backend = DdcControlDBusBackend()
        backend.list_monitors()
    except Exception as exc:
        pytest.skip(f"no DDC backend available: {exc}")
    return backend


@pytest.fixture(scope="session")
def monitor(ddc_backend):
    monitors = ddc_backend.list_monitors()
    if not monitors:
        pytest.skip("no monitors were found")
    return monitors[0]
