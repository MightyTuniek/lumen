import pytest

from lumen.backends.fakes import FakeDdcBackend, FakeGammaBackend
from lumen.engine.apply import ProfileApplier
from lumen.model.capability import parse_capabilities
from lumen.model.monitor import MonitorDescriptor
from lumen.model.profile import GammaCurve, Profile

MONITOR = MonitorDescriptor(identifier="fake:0", model="VG240Y")

CAPS = parse_capabilities(
    "vcp(04 10 12 16 18 1A 6C 6E 70 14(05 06 08 0B) 60(01 0F 11)) mccs_ver(2.2)"
)

VALUES = {
    0x10: (40, 100),
    0x12: (50, 100),
    0x16: (50, 100),
    0x18: (50, 100),
    0x1A: (50, 100),
    0x6C: (50, 100),
    0x6E: (50, 100),
    0x70: (50, 100),
    0x14: (11, 13),
}


def make_applier(**kwargs):
    backend = FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={code: () for code in CAPS.vcp_codes},
        values=dict(VALUES),
    )
    return ProfileApplier(backend, MONITOR, CAPS, **kwargs), backend


def make_profile(**kwargs):
    defaults = {
        "name": "Test",
        "created": "2026-11-11T20:00:00",
        "monitor": MONITOR,
        "ddc": {0x10: 70, 0x12: 55, 0x16: 52},
        "gamma": GammaCurve(exponent=2.2),
    }
    defaults.update(kwargs)
    return Profile(**defaults)


def test_apply_sets_the_values_in_the_profile():
    applier, backend = make_applier()
    applier.apply(make_profile())
    assert backend.get(MONITOR, 0x10).value == 70
    assert backend.get(MONITOR, 0x12).value == 55
    assert backend.get(MONITOR, 0x16).value == 52


def test_apply_is_idempotent():
    applier, backend = make_applier()
    profile = make_profile()
    applier.apply(profile)
    applier.apply(profile)
    assert backend.get(MONITOR, 0x10).value == 70


def test_apply_skips_a_code_the_monitor_does_not_support():
    applier, backend = make_applier()
    notes = applier.apply(make_profile(ddc={0x10: 70, 0xAB: 20}))
    assert backend.get(MONITOR, 0x10).value == 70
    assert any("0xAB" in note for note in notes)


def test_apply_never_touches_a_dangerous_code():
    applier, backend = make_applier()
    notes = applier.apply(make_profile(ddc={0x04: 1, 0x10: 70}))
    assert backend.get(MONITOR, 0x10).value == 70
    assert backend.get(MONITOR, 0x04).supported is False, "0x04 must never be written"
    assert any("0x04" in note for note in notes)


def test_apply_refuses_the_screen_blanking_code():
    applier, backend = make_applier()
    applier.apply(make_profile(ddc={0x8D: 1}))
    assert backend.get(MONITOR, 0x8D).supported is False


def test_apply_reports_a_failure_rather_than_raising():
    backend = FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={code: () for code in CAPS.vcp_codes},
        values=dict(VALUES),
        fail_on={0x10},
    )
    applier = ProfileApplier(backend, MONITOR, CAPS)
    notes = applier.apply(make_profile())
    assert any("0x10" in note for note in notes)


def test_capture_records_the_current_values():
    applier, _ = make_applier()
    snapshot = applier.capture()
    assert snapshot.ddc[0x10] == 40
    assert snapshot.ddc[0x12] == 50


def test_capture_records_the_gamma_ramp():
    gamma = FakeGammaBackend(size=64)
    applier, _ = make_applier(gamma_backend=gamma, gamma_output="fake-0")
    snapshot = applier.capture()
    assert snapshot.gamma is not None
    assert len(snapshot.gamma[0]) == 64
    assert snapshot.output == "fake-0"


def test_restore_puts_the_values_back():
    applier, backend = make_applier()
    snapshot = applier.capture()
    applier.apply(make_profile())
    applier.restore(snapshot)
    assert backend.get(MONITOR, 0x10).value == 40
    assert backend.get(MONITOR, 0x16).value == 50


def test_restore_puts_the_gamma_ramp_back():
    gamma = FakeGammaBackend(size=64)
    applier, _ = make_applier(gamma_backend=gamma, gamma_output="fake-0")
    snapshot = applier.capture()
    applier.apply(make_profile(gamma=GammaCurve(exponent=1.6)))
    assert gamma.read_ramp("fake-0")[0] != snapshot.gamma[0]
    applier.restore(snapshot)
    assert gamma.read_ramp("fake-0")[0] == snapshot.gamma[0]


def test_apply_writes_the_gamma_curve_when_a_gamma_backend_is_present():
    gamma = FakeGammaBackend(size=64)
    applier, _ = make_applier(gamma_backend=gamma, gamma_output="fake-0")
    applier.apply(make_profile(gamma=GammaCurve(exponent=1.8)))
    red, _green, _blue = gamma.read_ramp("fake-0")
    assert red[32] > red[16]


def test_apply_without_a_gamma_backend_notes_the_gamma_was_skipped():
    applier, _ = make_applier()
    notes = applier.apply(make_profile())
    assert any("gamma" in note.lower() for note in notes)
