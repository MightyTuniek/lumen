import pytest

from lumen.backends.fakes import FakeGammaBackend
from lumen.engine.conflict import GammaOwner, NightLightController


def linear(size):
    ramp = tuple(i * 65535 // (size - 1) for i in range(size))
    return ramp


def test_claim_writes_the_expected_ramp():
    backend = FakeGammaBackend(size=64)
    owner = GammaOwner(backend, "fake-0", (linear(64), linear(64), linear(64)))
    owner.claim()
    assert backend.read_ramp("fake-0")[0] == linear(64)


def test_no_drift_when_nothing_else_touches_the_ramp():
    backend = FakeGammaBackend(size=64)
    owner = GammaOwner(backend, "fake-0", (linear(64), linear(64), linear(64)))
    owner.claim()
    assert owner.has_drifted() is False


def test_drift_is_detected_when_another_program_rewrites_the_ramp():
    backend = FakeGammaBackend(size=64)
    owner = GammaOwner(backend, "fake-0", (linear(64), linear(64), linear(64)))
    owner.claim()
    other = tuple(0 for _ in range(64))
    backend.write_ramp("fake-0", other, other, other)
    assert owner.has_drifted() is True


def test_reassert_puts_the_ramp_back():
    backend = FakeGammaBackend(size=64)
    expected = (linear(64), linear(64), linear(64))
    owner = GammaOwner(backend, "fake-0", expected)
    owner.claim()
    other = tuple(0 for _ in range(64))
    backend.write_ramp("fake-0", other, other, other)
    assert owner.reassert() is True
    assert backend.read_ramp("fake-0")[0] == linear(64)
    assert owner.has_drifted() is False


def test_reassert_does_nothing_when_there_is_no_drift():
    backend = FakeGammaBackend(size=64)
    owner = GammaOwner(backend, "fake-0", (linear(64), linear(64), linear(64)))
    owner.claim()
    assert owner.reassert() is False


def test_night_light_is_detected_as_available():
    controller = NightLightController(gsettings_runner=lambda *a: "false")
    assert controller.is_available() is True


def test_night_light_schedule_is_read():
    controller = NightLightController(gsettings_runner=lambda *a: "true")
    assert controller.is_scheduled() is True


def test_night_light_schedule_can_be_disabled_and_restored():
    values = {"current": "true"}

    def runner(*args):
        if args and args[0] == "get":
            return values["current"]
        values["current"] = args[3]
        return ""

    controller = NightLightController(gsettings_runner=runner)
    changed, previous = controller.disable_schedule()
    assert changed is True
    assert previous == "true"
    assert values["current"] == "false"
    assert controller.restore_schedule(previous) is True
    assert values["current"] == "true"


def test_a_system_without_gsettings_is_unavailable():
    """A runner that answers nothing models a machine with no gsettings."""
    controller = NightLightController(gsettings_runner=lambda *a: "")
    assert controller.is_available() is False
    assert controller.is_scheduled() is False


def test_disabling_an_unavailable_schedule_is_a_no_op():
    controller = NightLightController(gsettings_runner=lambda *a: "")
    changed, previous = controller.disable_schedule()
    assert changed is False
    assert previous == ""
