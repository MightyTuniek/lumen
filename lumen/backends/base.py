"""Backend protocols.

This is the boundary between the portable core and real hardware. Every
platform-specific adapter implements these protocols, so the engine can
be tested against fakes and ported by writing new adapters.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lumen.model.monitor import Capabilities, MonitorDescriptor, VcpValue


class DdcError(Exception):
    """A DDC/CI operation failed.

    Raised rather than swallowed: a calibration tool that silently fails
    is worse than one that admits it.
    """


class GammaError(Exception):
    """A software gamma operation failed."""


@runtime_checkable
class DdcBackend(Protocol):
    """Hardware control over DDC/CI."""

    def list_monitors(self) -> list[MonitorDescriptor]:
        """Every monitor this backend can reach."""

    def capabilities(self, monitor: MonitorDescriptor) -> Capabilities:
        """What the monitor advertises about itself."""

    def get(self, monitor: MonitorDescriptor, code: int) -> VcpValue:
        """Read one VCP."""

    def set(self, monitor: MonitorDescriptor, code: int, value: int) -> None:
        """Write one VCP.

        Must verify the write took effect and raise `DdcError` if it did
        not. Never leave a failed write looking like a success.
        """


@runtime_checkable
class GammaBackend(Protocol):
    """Software control over a display's gamma ramp."""

    def list_outputs(self) -> list[str]:
        """Names of the outputs whose gamma ramp can be controlled."""

    def read_ramp(
        self, output: str
    ) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
        """The red, green and blue ramps, each a tuple of 16-bit values."""

    def write_ramp(
        self,
        output: str,
        red: tuple[int, ...],
        green: tuple[int, ...],
        blue: tuple[int, ...],
    ) -> None:
        """Write the ramp.

        Must flush the request and verify the result, raising
        `GammaError` if the ramp did not change.
        """
