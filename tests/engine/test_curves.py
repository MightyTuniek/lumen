import pytest

from lumen.engine.curves import build_channel_ramps, build_ramp, is_linear
from lumen.model.profile import GammaCurve


def test_ramp_has_the_requested_number_of_entries():
    assert len(build_ramp(256, 2.2)) == 256


def test_a_linear_ramp_is_detected():
    ramp = build_ramp(256, 1.0)
    assert is_linear(ramp, 256)


def test_a_gamma_two_point_two_ramp_is_not_linear():
    ramp = build_ramp(256, 2.2)
    assert not is_linear(ramp, 256)


def test_ramp_starts_at_black_and_ends_at_white():
    ramp = build_ramp(256, 2.2)
    assert ramp[0] == 0
    assert ramp[-1] == 65535


def test_ramp_is_monotonically_increasing():
    ramp = build_ramp(256, 1.8)
    assert all(ramp[i] <= ramp[i + 1] for i in range(len(ramp) - 1))


def test_a_higher_exponent_lightens_the_midtones():
    """Gamma 2.2 should sit above gamma 1.8 in the midtones.

    The ramp maps an input value to an output value, so a larger
    exponent raises the output for a given input - the midtones get
    brighter, not darker.
    """
    lower = build_ramp(256, 1.8)
    higher = build_ramp(256, 2.2)
    midpoint = 128
    assert higher[midpoint] > lower[midpoint]


def test_black_lift_raises_the_shadow_end():
    plain = build_ramp(256, 2.2)
    lifted = build_ramp(256, 2.2, black_lift=1000)
    assert lifted[0] == 1000
    assert lifted[-1] == plain[-1]


def test_white_trim_lowers_the_highlight_end():
    plain = build_ramp(256, 2.2)
    trimmed = build_ramp(256, 2.2, white_trim=2000)
    assert trimmed[-1] == 65535 - 2000


def test_black_lift_and_white_trim_never_cross():
    ramp = build_ramp(256, 2.2, black_lift=20000, white_trim=20000)
    assert all(ramp[i] <= ramp[i + 1] for i in range(len(ramp) - 1))


def test_channel_ramps_use_per_channel_exponents():
    curve = GammaCurve(exponent=2.2, red_exponent=1.8)
    red, green, blue = build_channel_ramps(64, curve)
    assert red != green
    assert green == blue


def test_channel_ramps_default_to_the_shared_exponent():
    red, green, blue = build_channel_ramps(64, GammaCurve(exponent=2.2))
    assert red == green == blue


def test_a_ramp_needs_at_least_two_entries():
    with pytest.raises(ValueError):
        build_ramp(1, 2.2)


def test_the_exponent_must_be_positive():
    with pytest.raises(ValueError):
        build_ramp(256, 0)
