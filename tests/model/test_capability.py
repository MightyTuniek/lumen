from lumen.model.capability import parse_capabilities

REFERENCE = (
    "cmds(01 02 03 07 0C E3 F3)"
    "vcp(04 10 12 14(05 06 08 0B) 16 18 1A 59 5A 5B 5C 5D 5E 60(01 0F 11) 62"
    "    6C 6E 70 8D 9B 9C 9D 9E 9F A0 D6 E0(00 04 05) E1(00 01 02)"
    "    E2(00 01 02 03 04 05 06 07 10 11 12) E3 E4 E5 E7(00 01 02)"
    "    E8(00 01 02 03 04))"
    "mccs_ver(2.2) mswhql(1) asset_eep(40)"
)


def test_parses_the_reference_monitor_capability_string():
    caps = parse_capabilities(REFERENCE)
    assert 0x10 in caps.vcp_codes
    assert 0x12 in caps.vcp_codes
    assert 0x6C in caps.vcp_codes
    assert 0x70 in caps.vcp_codes
    assert 0x04 in caps.vcp_codes
    assert caps.mccs_version == "2.2"


def test_parses_enumerated_values_for_a_code():
    caps = parse_capabilities(REFERENCE)
    assert caps.vcp_values[0x14] == (0x05, 0x06, 0x08, 0x0B)
    assert caps.vcp_values[0x60] == (0x01, 0x0F, 0x11)
    assert caps.vcp_values[0xE1] == (0x00, 0x01, 0x02)


def test_codes_without_enumerated_values_have_no_value_entry():
    caps = parse_capabilities(REFERENCE)
    assert 0x10 not in caps.vcp_values


def test_parses_command_codes():
    caps = parse_capabilities(REFERENCE)
    assert 0x0C in caps.commands
    assert 0xE3 in caps.commands


def test_empty_string_yields_no_codes():
    caps = parse_capabilities("")
    assert caps.vcp_codes == frozenset()
    assert caps.vcp_values == {}


def test_capability_string_without_vcp_section_is_tolerated():
    caps = parse_capabilities("mccs_ver(2.0)")
    assert caps.vcp_codes == frozenset()
    assert caps.mccs_version == "2.0"


def test_lowercase_hex_codes_are_parsed():
    caps = parse_capabilities("vcp(10 12)")
    assert caps.vcp_codes == frozenset({0x10, 0x12})


def test_a_code_with_a_parenthesised_value_list_keeps_its_values():
    """The vcp section nests parentheses, so the parser must not split it."""
    caps = parse_capabilities("vcp(10 12 14(05 06 08 0B) 16 18 1A)")
    assert caps.vcp_codes == frozenset({0x10, 0x12, 0x14, 0x16, 0x18, 0x1A})
    assert caps.vcp_values[0x14] == (0x05, 0x06, 0x08, 0x0B)
    assert 0x10 not in caps.vcp_values


def test_every_readable_control_of_the_reference_monitor_is_found():
    caps = parse_capabilities(REFERENCE)
    for code in (0x10, 0x12, 0x16, 0x18, 0x1A, 0x6C, 0x6E, 0x70):
        assert code in caps.vcp_codes
