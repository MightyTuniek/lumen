"""RGB channel mapping.

The DDC/CI standard defines 0x16 as red gain, 0x18 as green gain and
0x1A as blue gain. Some monitors transpose green and blue, and their
capability string lists the codes in that transposed order.

The capability string is the only reliable evidence available, because
a DDC change is applied by the monitor after the framebuffer leaves the
GPU - software cannot observe which channel actually moved. So the
mapping is detected where possible, and the wizard asks the user to
confirm it visually.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .capability import Capabilities

# The gain codes in the order the standard defines them.
_GAIN_CODES = (0x16, 0x18, 0x1A)


class ChannelOrder(Enum):
    """The order the monitor's gain codes are listed in."""

    RGB = "rgb"
    RBG = "rbg"


@dataclass(frozen=True)
class ChannelMapping:
    """How logical channels map onto physical ones."""

    order: ChannelOrder = ChannelOrder.RGB
    verified: bool = False

    @property
    def permutation(self) -> tuple[str, str, str]:
        """The physical channel behind each logical red, green and blue."""
        if self.order is ChannelOrder.RBG:
            return ("red", "blue", "green")
        return ("red", "green", "blue")

    @property
    def swapped(self) -> bool:
        """Whether green and blue are transposed."""
        return self.order is ChannelOrder.RBG


def mapping_for(order: ChannelOrder) -> ChannelMapping:
    """A mapping for a given order, not yet confirmed by the user."""
    return ChannelMapping(order=order, verified=False)


def detect_mapping_from_capabilities(capabilities: Capabilities) -> ChannelMapping | None:
    """Infer the channel order from the capability string, if it can be.

    The codes are located in the raw string so that the order they
    appear in is preserved. Returns None when the monitor does not
    advertise all three gain codes, because then the order cannot be
    established.
    """
    if not all(code in capabilities.vcp_codes for code in _GAIN_CODES):
        return None

    raw = capabilities.raw.upper()
    positions: list[tuple[int, int]] = []
    for code in _GAIN_CODES:
        token = f"{code:02X}"
        index = raw.find(token)
        if index < 0:
            return None
        positions.append((index, code))
    positions.sort()
    ordered = tuple(code for _index, code in positions)
    if ordered == _GAIN_CODES:
        return mapping_for(ChannelOrder.RGB)
    if ordered == (0x16, 0x1A, 0x18):
        return mapping_for(ChannelOrder.RBG)
    return None


def apply_permutation(values: dict[str, int], mapping: ChannelMapping) -> dict[str, int]:
    """Permute the per-channel values according to the mapping.

    Keys that are not red, green or blue are passed through unchanged.
    """
    if not mapping.swapped:
        return dict(values)
    result = dict(values)
    if "green" in values and "blue" in values:
        result["green"], result["blue"] = values["blue"], values["green"]
    return result
