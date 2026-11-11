"""The login service."""

import pytest

from lumen.backends.fakes import FakeDdcBackend
from lumen.engine.apply import ProfileApplier
from lumen.model.capability import parse_capabilities
from lumen.model.monitor import MonitorDescriptor
from lumen.model.profile import new_profile, save_profile
from lumen.service.daemon import active_profile_path, apply_active_profile

MONITOR = MonitorDescriptor(identifier="fake:0", model="VG240Y")
CAPS = parse_capabilities("vcp(10 12) mccs_ver(2.2)")


def make_applier():
    backend = FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={0x10: (), 0x12: ()},
        values={0x10: (40, 100), 0x12: (50, 100)},
    )
    return ProfileApplier(backend, MONITOR, CAPS), backend


def test_no_profile_means_nothing_to_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert active_profile_path() is None
    notes = apply_active_profile()
    assert notes


def test_a_saved_profile_is_found(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_profile(
        new_profile("Evening", MONITOR), tmp_path / "lumen" / "profiles"
    )
    assert active_profile_path() is not None


def test_applying_the_profile_sets_the_values(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    profile = new_profile("Evening", MONITOR, ddc={0x10: 77})
    save_profile(profile, tmp_path / "lumen" / "profiles")
    applier, backend = make_applier()
    apply_active_profile(applier)
    assert backend.get(MONITOR, 0x10).value == 77
