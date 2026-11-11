"""Taking ownership of the gamma ramp."""

import pytest

from lumen.engine.conflict import NightLightController
from lumen.ui.nightlight import NightLightOwnership


class FakeSettings:
    def __init__(self, initial: str = "true") -> None:
        self.value = initial

    def runner(self, *args):
        if args and args[0] == "get":
            return self.value
        self.value = args[3]
        return ""


def test_ownership_is_available_when_the_settings_can_be_read():
    settings = FakeSettings()
    ownership = NightLightOwnership(NightLightController(settings.runner))
    assert ownership.available is True
    assert ownership.scheduled is True


def test_taking_ownership_switches_the_schedule_off():
    settings = FakeSettings("true")
    ownership = NightLightOwnership(NightLightController(settings.runner))
    assert ownership.take_ownership() is True
    assert settings.value == "false"
    assert ownership.scheduled is False


def test_releasing_puts_the_schedule_back():
    settings = FakeSettings("true")
    ownership = NightLightOwnership(NightLightController(settings.runner))
    ownership.take_ownership()
    assert ownership.release() is True
    assert settings.value == "true"


def test_releasing_without_taking_ownership_does_nothing():
    settings = FakeSettings("true")
    ownership = NightLightOwnership(NightLightController(settings.runner))
    assert ownership.release() is False
    assert settings.value == "true"


def test_taking_ownership_twice_is_harmless():
    settings = FakeSettings("true")
    ownership = NightLightOwnership(NightLightController(settings.runner))
    assert ownership.take_ownership() is True
    assert ownership.take_ownership() is False
    assert settings.value == "false"


def test_an_unavailable_night_light_is_reported_honestly():
    ownership = NightLightOwnership(
        NightLightController(lambda *args: "")
    )
    assert ownership.available is False
    assert ownership.take_ownership() is False
