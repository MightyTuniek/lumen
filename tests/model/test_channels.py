from lumen.model.capability import parse_capabilities
from lumen.model.channels import (
    ChannelOrder,
    apply_permutation,
    detect_mapping_from_capabilities,
    mapping_for,
)

REFERENCE = (
    "vcp(04 10 12 14(05 06 08 0B) 16 18 1A 59 5A 5B 5C 5D 5E 60(01 0F 11) "
    "62 6C 6E 70) mccs_ver(2.2)"
)


def test_default_mapping_is_rgb_and_unverified():
    mapping = mapping_for(ChannelOrder.RGB)
    assert mapping.order is ChannelOrder.RGB
    assert mapping.verified is False


def test_rgb_permutation_is_the_identity():
    assert mapping_for(ChannelOrder.RGB).permutation == ("red", "green", "blue")


def test_rbg_permutation_swaps_green_and_blue():
    assert mapping_for(ChannelOrder.RBG).permutation == ("red", "blue", "green")


def test_swapped_mapping_reports_itself():
    assert mapping_for(ChannelOrder.RBG).swapped is True
    assert mapping_for(ChannelOrder.RGB).swapped is False


def test_capability_string_in_rgb_order_is_detected():
    caps = parse_capabilities(REFERENCE)
    mapping = detect_mapping_from_capabilities(caps)
    assert mapping is not None
    assert mapping.order is ChannelOrder.RGB


def test_capability_string_with_swapped_gains_is_detected():
    caps = parse_capabilities("vcp(16 1A 18) mccs_ver(2.2)")
    mapping = detect_mapping_from_capabilities(caps)
    assert mapping is not None
    assert mapping.order is ChannelOrder.RBG


def test_a_capability_string_without_gain_codes_detects_nothing():
    caps = parse_capabilities("vcp(10 12) mccs_ver(2.2)")
    assert detect_mapping_from_capabilities(caps) is None


def test_apply_permutation_leaves_rgb_values_alone():
    values = {"red": 50, "green": 51, "blue": 52}
    assert apply_permutation(values, mapping_for(ChannelOrder.RGB)) == values


def test_apply_permutation_swaps_green_and_blue_for_rbg():
    values = {"red": 50, "green": 51, "blue": 52}
    result = apply_permutation(values, mapping_for(ChannelOrder.RBG))
    assert result == {"red": 50, "green": 52, "blue": 51}


def test_apply_permutation_leaves_other_keys_alone():
    values = {"red": 1, "green": 2, "blue": 3, "black_level": 9}
    result = apply_permutation(values, mapping_for(ChannelOrder.RBG))
    assert result["black_level"] == 9
