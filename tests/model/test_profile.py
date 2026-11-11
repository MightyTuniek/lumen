import json

import pytest

from lumen.model.monitor import MonitorDescriptor
from lumen.model.profile import (
    GammaCurve,
    Profile,
    load_profile,
    profile_directory,
    profile_matches_monitor,
    save_profile,
)

MONITOR = MonitorDescriptor(
    identifier="dev:/dev/i2c-4",
    manufacturer="Acer",
    model="VG240Y",
    serial="0x01122d3a",
    pnp_id="ACR0673",
    edid_hash="abc123",
    connection="HDMI",
    backend_name="ddccontrol",
)


def make_profile(**kwargs):
    defaults = {
        "name": "Evening",
        "created": "2026-11-11T20:00:00",
        "monitor": MONITOR,
        "ddc": {0x10: 60, 0x12: 55, 0x16: 52},
        "gamma": GammaCurve(exponent=2.1),
    }
    defaults.update(kwargs)
    return Profile(**defaults)


def test_profile_round_trips_through_a_dict():
    profile = make_profile()
    restored = Profile.from_dict(profile.to_dict())
    assert restored.name == profile.name
    assert restored.ddc == profile.ddc
    assert restored.gamma.exponent == profile.gamma.exponent
    assert restored.monitor == profile.monitor


def test_profile_round_trips_through_a_file(tmp_path):
    profile = make_profile()
    path = save_profile(profile, tmp_path)
    assert path.exists()
    restored = load_profile(path)
    assert restored.ddc == profile.ddc


def test_saved_profile_is_readable_json(tmp_path):
    path = save_profile(make_profile(), tmp_path)
    data = json.loads(path.read_text())
    assert data["name"] == "Evening"
    assert data["ddc"]["16"] == 52


def test_profile_filename_is_a_slug(tmp_path):
    path = save_profile(make_profile(name="Evening Reading"), tmp_path)
    assert path.name == "evening-reading.json"


def test_a_profile_matches_its_own_monitor():
    assert profile_matches_monitor(make_profile(), MONITOR)


def test_a_profile_does_not_match_a_different_monitor():
    other = MonitorDescriptor(identifier="other", model="Something Else")
    assert not profile_matches_monitor(make_profile(), other)


def test_a_profile_matches_a_monitor_with_the_same_model_and_serial():
    same_look = MonitorDescriptor(
        identifier="dev:/dev/i2c-9",
        manufacturer="Acer",
        model="VG240Y",
        serial="0x01122d3a",
        pnp_id="ACR0673",
        edid_hash="abc123",
        connection="HDMI",
        backend_name="ddccontrol",
    )
    assert profile_matches_monitor(make_profile(), same_look)


def test_profile_directory_follows_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert profile_directory() == tmp_path / "lumen" / "profiles"


def test_profile_directory_defaults_under_home(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert profile_directory().parts[-2:] == ("lumen", "profiles")


def test_gamma_curve_falls_back_to_the_shared_exponent():
    curve = GammaCurve(exponent=2.2, red_exponent=1.8)
    assert curve.effective_exponents == (1.8, 2.2, 2.2)
