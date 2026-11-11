import pytest

from lumen.model.vcp_registry import (
    Group,
    Safety,
    VCP_BY_NAME,
    VCP_REGISTRY,
    is_dangerous,
    name_key,
    registry_for,
    spec_for,
)


def test_brightness_is_registered_with_the_standard_range():
    spec = spec_for(0x10)
    assert spec is not None
    assert spec.name == "Brightness"
    assert spec.minimum == 0
    assert spec.maximum == 100
    assert spec.safety is Safety.SAFE
    assert spec.group is Group.IMAGE


@pytest.mark.parametrize(
    ("code", "expected_name"),
    [
        (0x10, "Brightness"),
        (0x12, "Contrast"),
        (0x16, "Video gain: Red"),
        (0x18, "Video gain: Green"),
        (0x1A, "Video gain: Blue"),
        (0x6C, "Video black level: Red"),
        (0x6E, "Video black level: Green"),
        (0x70, "Video black level: Blue"),
        (0x14, "Select colour preset"),
        (0x0C, "Colour temperature request"),
        (0x59, "Saturation: Red"),
        (0x5E, "Saturation: Magenta"),
        (0x9B, "Hue: Red"),
        (0xA0, "Hue: Magenta"),
        (0x62, "Audio speaker volume"),
    ],
)
def test_known_codes_have_expected_names(code: int, expected_name: str):
    spec = spec_for(code)
    assert spec is not None, f"code 0x{code:02X} must be registered"
    assert spec.name == expected_name


def test_restore_factory_defaults_is_dangerous():
    assert is_dangerous(0x04)


def test_screen_blanking_code_is_dangerous():
    """0x8D blanks the screen in MCCS 2.2 - it must never be a safe control."""
    assert is_dangerous(0x8D)


def test_power_and_dpms_codes_are_dangerous():
    for code in (0xD6, 0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE7, 0xE8):
        assert is_dangerous(code), f"0x{code:02X} must be classified dangerous"


def test_input_source_requires_confirmation():
    assert spec_for(0x60).safety is Safety.CONFIRM


def test_unknown_code_has_no_spec():
    assert spec_for(0xAB) is None


def test_registry_for_skips_unknown_codes_and_sorts_by_code():
    specs = registry_for([0x70, 0x10, 0xAB, 0x12])
    assert [s.code for s in specs] == [0x10, 0x12, 0x70]


def test_registry_for_empty_input_is_empty():
    assert registry_for([]) == []


def test_lookup_by_name_is_case_and_separator_insensitive():
    assert VCP_BY_NAME["brightness"].code == 0x10
    assert VCP_BY_NAME["video_gain_red"].code == 0x16
    assert VCP_BY_NAME["video gain red"].code == 0x16


def test_name_key_normalises_punctuation():
    assert name_key("Video gain: Red") == "video_gain_red"
    assert name_key("Brightness") == "brightness"


def test_every_registered_code_has_a_unique_derived_name():
    seen: dict[str, int] = {}
    for code, spec in VCP_REGISTRY.items():
        key = name_key(spec.name)
        assert key not in seen, f"name collision between 0x{code:02X} and 0x{seen[key]:02X}"
        seen[key] = code
