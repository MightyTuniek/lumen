import pytest

from lumen.backends.fakes import FakeDdcBackend, FakeGammaBackend
from lumen.engine.calibration import (
    CalibrationSession,
    Pattern,
    steps_for,
    steps_with_gamma,
)
from lumen.model.capability import parse_capabilities
from lumen.model.monitor import MonitorDescriptor

MONITOR = MonitorDescriptor(identifier="fake:0", model="VG240Y")

FULL = parse_capabilities(
    "vcp(04 10 12 16 18 1A 6C 6E 70 14(05 06 08 0B) 59 5A 5B 5C 5D 5E "
    "9B 9C 9D 9E 9F A0 60(01 0F 11)) mccs_ver(2.2)"
)

MINIMAL = parse_capabilities("vcp(10) mccs_ver(2.2)")

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
    0x59: (50, 100),
    0x5A: (50, 100),
    0x5B: (50, 100),
    0x5C: (50, 100),
    0x5D: (50, 100),
    0x5E: (50, 100),
    0x9B: (50, 100),
    0x9C: (50, 100),
    0x9D: (50, 100),
    0x9E: (50, 100),
    0x9F: (50, 100),
    0xA0: (50, 100),
}


def make_session(capabilities=FULL, **kwargs):
    backend = FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={code: () for code in capabilities.vcp_codes},
        values=dict(VALUES),
    )
    return CalibrationSession(backend, MONITOR, capabilities, **kwargs)


def test_a_full_monitor_gets_the_whole_wizard():
    steps = steps_for(FULL)
    keys = [step.key for step in steps]
    assert "baseline" in keys
    assert "white_point" in keys
    assert "black_point" in keys
    assert "colour_temperature" in keys
    assert "saturation" in keys
    assert "verify_channels" in keys


def test_the_black_point_is_checked_a_second_time():
    """Brightness interacts with the black level, so it is set twice."""
    steps = steps_for(FULL)
    keys = [step.key for step in steps]
    assert keys.count("black_point") == 2
    first = keys.index("black_point")
    second = keys.index("black_point", first + 1)
    assert second > keys.index("baseline")


def test_the_second_black_point_pass_says_so():
    steps = steps_for(FULL)
    black_steps = [step for step in steps if step.key == "black_point"]
    assert "again" in black_steps[1].instruction.lower()


def test_the_black_point_comes_after_the_baseline():
    """Raising brightness re-crushes shadows, so black is set after it."""
    steps = steps_for(FULL)
    keys = [step.key for step in steps]
    assert keys.index("baseline") < keys.index("black_point")
    assert keys.index("black_point") < keys.index("colour_temperature")


def test_a_minimal_monitor_skips_the_steps_it_cannot_run():
    steps = steps_for(MINIMAL)
    keys = [step.key for step in steps]
    assert "baseline" in keys
    assert "white_point" not in keys
    assert "black_point" not in keys
    assert "gamma_curve" not in keys


def test_a_monitor_with_no_usable_controls_gets_no_steps():
    assert steps_for(parse_capabilities("")) == []


def test_gamma_step_is_skipped_without_a_gamma_backend():
    session = make_session()
    keys = [step.key for step in session.steps]
    assert "gamma_curve" not in keys


def test_gamma_step_is_present_with_a_gamma_backend():
    session = make_session(gamma_backend=FakeGammaBackend(), gamma_output="fake-0")
    keys = [step.key for step in session.steps]
    assert "gamma_curve" in keys


def test_the_gamma_step_follows_the_colour_temperature_step():
    steps = steps_with_gamma(FULL)
    keys = [step.key for step in steps]
    assert keys.index("gamma_curve") == keys.index("colour_temperature") + 1


def test_the_session_starts_on_the_first_step():
    session = make_session()
    assert session.current_step is session.steps[0]


def test_advancing_moves_to_the_next_step():
    session = make_session()
    first = session.current_step
    session.advance()
    assert session.current_step is not first


def test_advancing_past_the_last_step_finishes_the_session():
    session = make_session()
    for _ in range(len(session.steps)):
        session.advance()
    assert session.finished is True


def test_going_back_returns_to_the_previous_step():
    session = make_session()
    first = session.current_step
    session.advance()
    session.back()
    assert session.current_step is first


def test_going_back_on_the_first_step_does_nothing():
    session = make_session()
    first = session.current_step
    session.back()
    assert session.current_step is first


def test_the_white_point_step_exposes_the_gain_controls():
    session = make_session()
    step = next(s for s in session.steps if s.key == "white_point")
    controls = session.controls_for(step)
    codes = {control.code for control in controls}
    assert "vcp_0x16" in codes
    assert "vcp_0x18" in codes
    assert "vcp_0x1A" in codes


def test_the_black_point_step_exposes_the_black_level_controls():
    session = make_session()
    step = next(s for s in session.steps if s.key == "black_point")
    codes = {control.code for control in session.controls_for(step)}
    assert "vcp_0x6C" in codes
    assert "vcp_0x6E" in codes
    assert "vcp_0x70" in codes


def test_the_saturation_step_exposes_the_saturation_controls():
    session = make_session()
    step = next(s for s in session.steps if s.key == "saturation")
    codes = {control.code for control in session.controls_for(step)}
    assert "vcp_0x59" in codes
    assert "vcp_0xA0" in codes


def test_the_black_point_step_uses_the_near_black_pattern():
    session = make_session()
    step = next(s for s in session.steps if s.key == "black_point")
    assert step.pattern is Pattern.NEAR_BLACK_RAMP


def test_every_step_carries_an_instruction_for_the_user():
    for step in steps_for(FULL):
        assert step.instruction.strip()
        assert step.title.strip()


def test_gamma_step_exposes_adjustable_channels():
    from lumen.engine.controls import ControlKind

    session = make_session(gamma_backend=FakeGammaBackend(), gamma_output="fake-0")
    step = next(step for step in session.steps if step.key == "gamma_curve")
    controls = session.controls_for(step)
    assert {control.code for control in controls} == {"gamma_red", "gamma_green", "gamma_blue"}
    assert all(control.kind is ControlKind.GAMMA for control in controls)
