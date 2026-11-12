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


def test_service_applies_selected_profile(tmp_path, monkeypatch):
    from lumen.model.settings import Settings, save_settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_profile(new_profile("Day", MONITOR, ddc={0x10: 25}))
    save_profile(new_profile("Evening", MONITOR, ddc={0x10: 65}))
    save_settings(Settings(default_profile="evening.json"))
    applier, backend = make_applier()
    apply_active_profile(applier)
    assert backend.get(MONITOR, 0x10).value == 65


@pytest.mark.parametrize("enabled", [True, False])
def test_service_start_respects_login_preference(tmp_path, monkeypatch, enabled):
    from lumen.model.settings import Settings, save_settings
    from lumen.service import daemon

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_settings(Settings(apply_at_login=enabled))
    applier, _ = make_applier()
    applied = []
    monkeypatch.setattr(daemon, "build_applier", lambda: applier)
    monkeypatch.setattr(daemon, "apply_active_profile", applied.append)
    monkeypatch.setattr(daemon, "watch_display_wake", lambda *args: None)
    assert daemon.main() == 0
    assert applied == ([applier] if enabled else [])


@pytest.mark.parametrize("enabled", [True, False])
def test_wake_respects_current_preferences(tmp_path, monkeypatch, enabled):
    import sys
    from types import SimpleNamespace

    from lumen.model.settings import Settings, save_settings
    from lumen.service import daemon

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_settings(Settings(reapply_on_wake=not enabled))
    states = iter([0, 1])
    display = SimpleNamespace(has_extension=lambda extension: True)
    dpms = SimpleNamespace(DPMSStateOff=0,
                           get_state=lambda _: SimpleNamespace(state=next(states)))
    monkeypatch.setitem(sys.modules, "Xlib", SimpleNamespace(
        display=SimpleNamespace(Display=lambda: display)))
    monkeypatch.setitem(sys.modules, "Xlib.ext", SimpleNamespace(dpms=dpms))
    applied = []
    monkeypatch.setattr(daemon, "apply_active_profile", applied.append)

    def sleep(_interval):
        # A running service must notice preferences changed after it started.
        if sleep.called:
            raise KeyboardInterrupt
        sleep.called = True
        save_settings(Settings(reapply_on_wake=enabled))

    sleep.called = False
    monkeypatch.setattr(daemon.time, "sleep", sleep)
    applier, _ = make_applier()
    with pytest.raises(KeyboardInterrupt):
        daemon.watch_display_wake(applier)
    assert applied == ([applier] if enabled else [])
