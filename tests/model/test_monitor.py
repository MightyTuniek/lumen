from lumen.model.capability import parse_capabilities, supported_specs
from lumen.model.monitor import Capabilities, MonitorDescriptor

REFERENCE = (
    "cmds(01 02 03)vcp(04 10 12 16 18 1A 6C 6E 70 14(05 0B) 60(01 0F 11))"
    "mccs_ver(2.2)"
)


def test_supported_specs_excludes_dangerous_codes():
    caps = parse_capabilities(REFERENCE)
    codes = [spec.code for spec in supported_specs(caps)]
    assert 0x10 in codes
    assert 0x6C in codes
    assert 0x04 not in codes, "dangerous codes must never be offered"


def test_supported_specs_are_sorted_by_code():
    caps = parse_capabilities(REFERENCE)
    codes = [spec.code for spec in supported_specs(caps)]
    assert codes == sorted(codes)


def test_monitor_descriptor_is_hashable_and_comparable():
    a = MonitorDescriptor(
        identifier="dev:/dev/i2c-4",
        manufacturer="Acer",
        model="VG240Y",
        serial="0x01122d3a",
        pnp_id="ACR0673",
        edid_hash="abc123",
        connection="HDMI",
        backend_name="ddccontrol",
    )
    b = MonitorDescriptor(**a.__dict__)
    assert a == b
    assert len({a, b}) == 1


def test_capabilities_with_no_codes_produce_no_specs():
    assert supported_specs(Capabilities(vcp_codes=frozenset())) == []
