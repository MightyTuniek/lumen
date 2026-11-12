import threading
from dataclasses import replace

import pytest

from lumen.backends.displays import discover_displays, applier_for_profile, NoDdcBackend
from lumen.backends.fakes import FakeDdcBackend, FakeGammaBackend
from lumen.backends.locking import DisplayBusy, display_lock
from lumen.engine.apply import ProfileApplier
from lumen.model.colorimeter import DisplayTarget, SampledCurves
from lumen.model.monitor import MonitorDescriptor, Capabilities
from lumen.model.profile import new_profile


def test_display_lock_excludes_threads_but_allows_reentry():
    errors = []
    def competing():
        try:
            with display_lock('DP-1'):
                errors.append('unexpected success')
        except DisplayBusy:
            errors.append('busy')
    with display_lock('DP-1'):
        with display_lock('DP-1'):
            thread = threading.Thread(target=competing)
            thread.start()
            thread.join()
    assert errors == ['busy']
    with display_lock('DP-1'):
        pass


def test_no_ddc_and_unknown_pairing_never_infer_monitor_order():
    monitor = MonitorDescriptor('ddc:one')
    ddc = FakeDdcBackend(monitors=[monitor])
    gamma = FakeGammaBackend(outputs=('DP-2', 'DP-1'))
    choices, unpaired = discover_displays(ddc, gamma)
    assert len(choices) == 2
    assert all(isinstance(choice.backend, NoDdcBackend) for choice in choices)
    assert len(unpaired) == 1


def test_measured_profile_reapplication_uses_identity_after_output_reordering(monkeypatch):
    first = MonitorDescriptor('output:DP-1', connection='DP-1', edid_hash='one')
    calibrated = MonitorDescriptor('output:DP-2', connection='DP-2', edid_hash='two')
    gamma = FakeGammaBackend(outputs=('DP-1', 'DP-2'))
    gamma.describe_outputs = lambda: [first, calibrated]
    ddc = NoDdcBackend()
    choices, _ = discover_displays(ddc, gamma)
    monkeypatch.setattr('lumen.backends.displays.discover_displays', lambda: (list(reversed(choices)), []))
    profile = new_profile('Measured', calibrated)
    profile.measured_curves = SampledCurves((0, .8), (0, .9), (0, 1))
    profile.display_target = DisplayTarget('old-connector', 'two', 1)
    applier = applier_for_profile(profile)
    assert applier.gamma_output == 'DP-2'
    assert applier.apply(profile) == []
    assert gamma.read_ramp('DP-2') == profile.measured_curves.ramps(256)
    assert gamma.read_ramp('DP-1')[0][-1] == 65535


def test_measured_profile_refuses_duplicate_edids(monkeypatch):
    one = MonitorDescriptor('output:DP-1', connection='DP-1', edid_hash='same')
    two = replace(one, identifier='output:DP-2', connection='DP-2')
    gamma = FakeGammaBackend(outputs=('DP-1', 'DP-2'))
    gamma.describe_outputs = lambda: [one, two]
    profile = new_profile('Measured', one)
    profile.measured_curves = SampledCurves((0, .8), (0, .8), (0, .8))
    profile.display_target = DisplayTarget('DP-1', 'same', 1)
    a = ProfileApplier(NoDdcBackend(), one, Capabilities(), gamma, 'DP-1')
    before = gamma.read_ramp('DP-1')
    assert 'ambiguous' in a.apply(profile)[0]
    assert gamma.read_ramp('DP-1') == before


def test_restore_reports_write_failures():
    from lumen.engine.apply import Snapshot
    from lumen.backends.base import GammaError
    gamma = FakeGammaBackend()
    gamma.fail_on.add('fake-0')
    a = ProfileApplier(NoDdcBackend(), MonitorDescriptor('fake'), Capabilities(), gamma, 'fake-0')
    failures = a.restore(Snapshot({}, gamma.read_ramp('fake-0'), 'fake-0'))
    assert failures and 'restore gamma' in failures[0]
