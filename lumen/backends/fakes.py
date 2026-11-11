"""In-memory backends used by the test suite.

The engine is developed against these. They are how the core stays
testable without hardware, and how future platform ports are developed
on machines that do not have the target hardware attached.
"""

from __future__ import annotations

from lumen.backends.base import DdcError, GammaError
from lumen.model.monitor import Capabilities, MonitorDescriptor, VcpValue


class FakeDdcBackend:
    """A DDC backend backed by a dictionary."""

    def __init__(
        self,
        monitors: list[MonitorDescriptor] | None = None,
        capabilities: dict[int, tuple[int, ...]] | None = None,
        values: dict[int, tuple[int, int]] | None = None,
        fail_on: set[int] | None = None,
    ) -> None:
        self._monitors = list(monitors or [])
        # The capability codes are the same for every monitor this fake
        # holds, keyed by VCP code rather than by monitor identifier.
        self._capabilities = dict(capabilities or {})
        self._values = dict(values or {})
        self._fail_on = set(fail_on or ())

    def list_monitors(self) -> list[MonitorDescriptor]:
        return list(self._monitors)

    def capabilities(self, monitor: MonitorDescriptor) -> Capabilities:
        return Capabilities(vcp_codes=frozenset(int(c) for c in self._capabilities))

    def get(self, monitor: MonitorDescriptor, code: int) -> VcpValue:
        if code not in self._values:
            return VcpValue(code=code, value=0, maximum=0, supported=False)
        value, maximum = self._values[code]
        return VcpValue(code=code, value=value, maximum=maximum, supported=True)

    def set(self, monitor: MonitorDescriptor, code: int, value: int) -> None:
        if code in self._fail_on:
            raise DdcError(f"monitor rejected the write to 0x{code:02X}")
        current = self._values.get(code)
        maximum = current[1] if current else 100
        if value < 0 or value > maximum:
            raise DdcError(
                f"value {value} is outside the range 0-{maximum} of 0x{code:02X}"
            )
        self._values[code] = (value, maximum)


class FakeGammaBackend:
    """A gamma backend that stores ramps in memory."""

    def __init__(self, size: int = 256, outputs: tuple[str, ...] = ("fake-0",)) -> None:
        self.size = size
        self._outputs = list(outputs)
        self._ramps: dict[
            str, tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]
        ] = {}
        for name in self._outputs:
            linear = tuple(i * 65535 // (size - 1) for i in range(size))
            self._ramps[name] = (linear, linear, linear)
        self.fail_on: set[str] = set()

    def list_outputs(self) -> list[str]:
        return list(self._outputs)

    def read_ramp(self, output: str):
        return self._ramps[output]

    def write_ramp(self, output: str, red, green, blue) -> None:
        if output in self.fail_on:
            raise GammaError(f"gamma write rejected for {output}")
        self._ramps[output] = (tuple(red), tuple(green), tuple(blue))
