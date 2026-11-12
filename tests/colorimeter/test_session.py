import json
from dataclasses import replace

import pytest

from lumen.backends.fakes import FakeDdcBackend, FakeGammaBackend
from lumen.backends.colorimeter.argyll import Cancelled
from lumen.engine.apply import ProfileApplier
from lumen.engine.colorimeter import ColorimeterSession, adjust_hardware, meets_target
from lumen.engine.controls import build_vcp_controls
from lumen.engine.measurement import verification_patches
from lumen.model.capability import parse_capabilities
from lumen.model.colorimeter import CalibrationTarget, DisplayTarget, Instrument, Reading, SampledCurves
from lumen.model.monitor import MonitorDescriptor
from lumen.model.profile import load_profile, profile_directory
from .conftest import CAL, FastCancellation, icc_bytes

MONITOR = MonitorDescriptor('fake', model='Test', connection='DP-1', edid_hash='hash')


def applier():
    backend = FakeDdcBackend(monitors=[MONITOR], capabilities={0x10: (), 0x16: (), 0x18: (), 0x1A: ()},
        values={0x10: (50, 100), 0x16: (100, 100), 0x18: (100, 100), 0x1A: (100, 100)})
    gamma = FakeGammaBackend(outputs=["DP-1"], size=64)
    gamma.publish_icc = lambda *args: None
    return ProfileApplier(backend, MONITOR, parse_capabilities('vcp(10 16 18 1A)'), gamma, 'DP-1')


class FakeArgyll:
    def __init__(self, fail=None, bad_delta=False):
        self.calls, self.fail, self.bad_delta = [], fail, bad_delta
    def measure(self, *args):
        return Reading(114.0547, 120, 130.6869)
    def instrument_args(self, *args):
        return ['-c1']
    def run(self, tool, *args, cwd, **kwargs):
        self.calls.append((tool, args))
        if self.fail == tool:
            raise RuntimeError('instrument disconnected')
        if tool == 'dispcal':
            (cwd / 'display.cal').write_text(CAL)
            (cwd / 'display.icc').write_bytes(icc_bytes())
        elif tool == 'colprof':
            (cwd / 'profile.icc').write_bytes(icc_bytes())
        elif tool == 'dispread' and args[-1] == 'verify':
            rows = []
            for i, (r, g, b) in enumerate(verification_patches(), 1):
                y = 0.1 + 119.9 * ((r+g+b)/3)**2.2
                rows.append(f'{i} {r*100} {g*100} {b*100} {y*0.9504559} {y} {y*1.0890578}')
            text = ('CTI3\nNUMBER_OF_FIELDS 7\nBEGIN_DATA_FORMAT\nSAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z\n'
                    'END_DATA_FORMAT\nNUMBER_OF_SETS 51\nBEGIN_DATA\n' + '\n'.join(rows) + '\nEND_DATA\n')
            (cwd / 'verify.ti3').write_text(text)
        elif tool == 'profcheck':
            return 'Profile check complete, errors: max. = 6, avg. = 3' if self.bad_delta else 'Profile check complete, errors: max. = 1.2, avg. = 0.5'
        return ''


class FakeColord:
    def __init__(self):
        self.active = 'old'
        self.inhibited = False
        self.restored = []
    def current(self, device):
        return self.active
    def inhibit(self, device):
        self.inhibited = True
    def uninhibit(self, device):
        self.inhibited = False
    def install(self, device, path):
        self.active = 'new'
        return 'new'
    def restore(self, device, old, installed):
        self.active = old
        self.restored.append((old, installed))


def session(a, backend, **kwargs):
    return ColorimeterSession(a, backend, Instrument('id', 'i1', 1), DisplayTarget('DP-1', 'hash', 2),
        kwargs.pop('target', CalibrationTarget()), token=FastCancellation(), on_prompt=lambda *_: True, **kwargs)


@pytest.mark.parametrize('mode', ['quick', 'detailed'])
def test_full_workflow_saves_assets_and_restores_on_save_only(mode):
    a, backend, colord = applier(), FakeArgyll(), FakeColord()
    original = a.capture()
    with session(a, backend, target=CalibrationTarget(mode=mode), colord=colord, colord_device='display') as run:
        result = run.run()
        assert len(result.report['grayscale']) == 21
        assert result.report['average_delta_e00'] == 0.5
        assert not result.warnings
        assert colord.inhibited
        path = run.save('My calibration')
        assert run.save('Different name') == path
        profile = load_profile(path)
        assert profile.measured_curves.red == (0, 0.45, 0.95)
        assert profile.display_target.argyll_display == 2
        assert profile.calibration['instrument']['identifier'] == 'id'
        assert profile.icc_path.endswith('display.icc')
        temporary = run.directory
    assert not temporary.exists()
    assert a.capture() == original
    assert colord.active == 'old' and not colord.inhibited
    assert (profile_directory().parent / 'calibrations' / profile.calibration['id'] / 'report.json').exists()
    tools = [name for name, _ in backend.calls]
    assert ('targen' in tools) == (mode == 'detailed')
    if mode == 'detailed':
        assert '-aX' in next(args for name, args in backend.calls if name == 'colprof')
    assert '-d2' in backend.calls[0][1]


def test_activation_keeps_calibration_and_associates_icc():
    a, colord = applier(), FakeColord()
    with session(a, FakeArgyll(), colord=colord, colord_device='display') as run:
        result = run.run()
        run.save('Keep')
        run.activate()
    assert colord.active == 'new' and not colord.inhibited
    assert a.gamma_backend.read_ramp('DP-1') == result.curves.ramps(64)
    from lumen.model.settings import load_settings
    assert load_settings().default_profile == run.saved_path.name


def test_activation_failure_restores_association_and_gamma():
    a, colord = applier(), FakeColord()
    original = a.capture()
    a.gamma_backend.publish_icc = lambda *args: (_ for _ in ()).throw(RuntimeError('X disconnected'))
    with pytest.raises(RuntimeError, match='X disconnected'):
        with session(a, FakeArgyll(), colord=colord, colord_device='display') as run:
            run.run()
            run.save('Saved despite failure')
            run.activate()
    assert colord.active == 'old'
    assert colord.restored == [('old', 'new')]
    assert a.capture() == original
    assert list(profile_directory().glob('*.json'))


@pytest.mark.parametrize('tool', ['dispcal', 'dispread', 'profcheck'])
def test_measurement_failures_restore_display_and_inhibition(tool):
    a, colord = applier(), FakeColord()
    original = a.capture()
    with pytest.raises(RuntimeError, match='disconnected'):
        with session(a, FakeArgyll(fail=tool), colord=colord, colord_device='display') as run:
            run.run()
    assert a.capture() == original
    assert not colord.inhibited
    assert not list(profile_directory().glob('*.json'))


def test_warning_results_and_missing_colord_preserve_exportable_profile():
    with session(applier(), FakeArgyll(bad_delta=True)) as run:
        result = run.run()
        assert any('verification' in warning for warning in result.warnings)
        path = run.save('Needs review')
        with pytest.raises(RuntimeError, match='colord'):
            run.activate()
    assert load_profile(path).icc_path


def test_unreachable_target_has_bounded_search_and_returns_best():
    a = applier()
    controls = build_vcp_controls(a.backend, a.monitor, a.capabilities)
    calls = []
    def measure():
        calls.append(1)
        return Reading(20, 20, 20)
    result = adjust_hardware(controls, measure, CalibrationTarget(), FastCancellation())
    assert not meets_target(result, CalibrationTarget())
    assert len(calls) < 150
    assert a.backend.get(MONITOR, 0x10).value == 50


def test_brightness_converges_with_noise_and_actual_range():
    a = applier()
    controls = [c for c in build_vcp_controls(a.backend, a.monitor, a.capabilities) if c.vcp_code == 0x10]
    counter = [0]
    def measure():
        counter[0] += 1
        y = a.backend.get(MONITOR, 0x10).value * 2 + (0.1 if counter[0] % 2 else -0.1)
        return Reading(y * .9504559, y, y * 1.0890578)
    assert meets_target(adjust_hardware(controls, measure, CalibrationTarget(), FastCancellation()), CalibrationTarget())


def test_cancelled_session_rolls_back():
    a = applier()
    original = a.capture()
    with pytest.raises(Cancelled):
        with session(a, FakeArgyll()) as run:
            run.token.cancel()
            run.run()
    assert a.capture() == original


def test_rgb_coordinate_descent_reduces_measured_color_cast():
    from lumen.engine.colorimeter import score
    a = applier()
    controls = build_vcp_controls(a.backend, a.monitor, a.capabilities)
    def measure():
        values = [a.backend.get(MONITOR, c).value / 100 for c in (0x16, 0x18, 0x1A)]
        r, g, b = values[0] * 1.15, values[1], values[2] * .85
        scale = a.backend.get(MONITOR, 0x10).value * 2
        return Reading(scale * (.4124564*r + .3575761*g + .1804375*b),
                       scale * (.2126729*r + .7151522*g + .072175*b),
                       scale * (.0193339*r + .119192*g + .9503041*b))
    target = CalibrationTarget()
    before = score(measure(), target)
    result = adjust_hardware(controls, measure, target, FastCancellation())
    assert score(result, target) < before / 4


def test_permission_failure_happens_before_any_display_write():
    a = applier()
    backend = FakeArgyll()
    backend.measure = lambda *args: (_ for _ in ()).throw(RuntimeError('USB permission denied'))
    writes = []
    a.gamma_backend.write_ramp = lambda *args: writes.append(args)
    with pytest.raises(RuntimeError, match='permission denied'):
        with session(a, backend) as run:
            run.run()
    assert writes == []


def test_failed_asset_publication_removes_unpublished_files(monkeypatch):
    with session(applier(), FakeArgyll()) as run:
        run.run()
        monkeypatch.setattr('lumen.engine.colorimeter.save_profile', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(OSError):
            run.save('Cannot save')
    assert not list(profile_directory().glob('*.json'))
    assert not list((profile_directory().parent / 'calibrations').iterdir())
