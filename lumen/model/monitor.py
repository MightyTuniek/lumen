"""Data describing a physical display and its capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VcpValue:
    """A single VCP reading."""

    code: int
    value: int
    maximum: int
    supported: bool = True


@dataclass(frozen=True)
class Capabilities:
    """What a monitor advertises about itself."""

    vcp_codes: frozenset[int] = frozenset()
    vcp_values: dict[int, tuple[int, ...]] = field(default_factory=dict)
    commands: frozenset[int] = frozenset()
    mccs_version: str | None = None
    raw: str = ""


@dataclass(frozen=True)
class MonitorDescriptor:
    """Identity of a display, enough to tell two monitors apart."""

    identifier: str
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    pnp_id: str | None = None
    edid_hash: str | None = None
    connection: str = ""
    backend_name: str = ""
