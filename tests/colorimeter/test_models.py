import json
import math

import pytest

from lumen.model.colorimeter import CalibrationTarget, DisplayTarget, Reading, SampledCurves
from lumen.model.profile import Profile, load_profile, new_profile, save_profile
from lumen.model.monitor import MonitorDescriptor
from lumen.model.settings import Settings, load_settings, save_settings
from lumen.engine.measurement import load_calibration, validate_icc, verification_patches, write_verification_chart, read_cgats
from .conftest import CAL, icc_bytes


@pytest.mark.parametrize('values', [((), (), ()), ((0, 1), (0, 1), (0,)),
    ((0, math.nan), (0, 1), (0, 1)), ((0, 1.1), (0, 1), (0, 1)),
    ((1, 0), (0, 1), (0, 1))])
def test_invalid_curves_rejected(values):
    with pytest.raises(ValueError):
        SampledCurves(*values)


def test_curves_preserve_endpoints_and_resample_linearly():
    curve = SampledCurves((0.1, 0.3, 0.9), (0, 0.5, 1), (0, 0.25, 0.8))
    red, green, blue = curve.ramps(5)
    assert red == tuple(round(v * 65535) for v in (0.1, 0.2, 0.3, 0.6, 0.9))
    assert green[-1] == 65535
    assert blue[-1] == round(0.8 * 65535)


def test_measured_profile_roundtrip_and_legacy_compatibility(tmp_path):
    profile = new_profile('Measured', MonitorDescriptor('output:DP-1'))
    profile.measured_curves = SampledCurves((0, 1), (0, 1), (0, 1))
    profile.display_target = DisplayTarget('DP-1', 'edid', 2)
    profile.calibration = {'targets': CalibrationTarget().to_dict()}
    profile.icc_path = '/tmp/display.icc'
    assert load_profile(save_profile(profile, tmp_path)).to_dict() == profile.to_dict()
    legacy = profile.to_dict()
    for key in ('measured_curves', 'display_target', 'calibration', 'icc_path'):
        legacy.pop(key)
    assert Profile.from_dict(legacy).measured_curves is None


@pytest.mark.parametrize('key,value', [('colorimeter_luminance', 401), ('colorimeter_gamma', 0),
    ('colorimeter_temperature', math.nan), ('colorimeter_white_point', 'purple'),
    ('colorimeter_auto_ddc', 1), ('colorimeter_correction', 'bad.exe'), ('colorimeter_display_type', '-x')])
def test_bad_preferences_fall_back_individually(tmp_path, key, value):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({key: value, 'colorimeter_mode': 'detailed'}))
    settings = load_settings(path)
    assert getattr(settings, key) == getattr(Settings(), key)
    assert settings.colorimeter_mode == 'detailed'


def test_preferences_persist_targets():
    settings = Settings(colorimeter_luminance=150, colorimeter_response='srgb', colorimeter_white_point='native')
    save_settings(settings)
    assert load_settings() == settings
    assert settings.colorimeter_target.xy is None


def test_cal_parser_and_chart(tmp_path):
    path = tmp_path / 'display.cal'
    path.write_text(CAL)
    curve = load_calibration(path)
    assert curve.red == (0, 0.45, 0.95)
    path.write_text(CAL.replace('0.5 0.45', '0.6 0.45'))
    with pytest.raises(ValueError, match='evenly spaced'):
        load_calibration(path)
    patches = verification_patches()
    assert len(patches) == len(set(patches)) == 51
    write_verification_chart(path)
    _, rows = read_cgats(path)
    assert len(rows) == 51
    assert float(rows[20]['RGB_R']) == 100


def test_icc_validation_rejects_missing_and_truncated_calibration():
    validate_icc(icc_bytes())
    with pytest.raises(ValueError):
        validate_icc(icc_bytes()[:-1])
    with pytest.raises(ValueError):
        validate_icc(b'vcgt acsp' * 100)


def test_atomic_profile_write_keeps_existing_file_on_error(monkeypatch, tmp_path):
    profile = new_profile('Original', MonitorDescriptor('fake'))
    path = save_profile(profile, tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr('lumen.model.profile.os.replace', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        save_profile(profile, tmp_path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
