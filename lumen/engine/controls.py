"""The uniform control interface.

Every adjustable value the user can touch - a DDC VCP inside the
monitor, an enumerated preset, or one channel of a software gamma ramp -
is exposed as a `Control`. The wizard and the live panel work with
`Control` objects only, so neither has to know whether a value lives in
the hardware or in the X server.

Controls refuse values outside their range rather than clamping them, so
a mistyped or out-of-range value is reported instead of quietly applied
as something else.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Protocol, runtime_checkable

from lumen.backends.base import DdcBackend, DdcError, GammaBackend, GammaError
from lumen.model.capability import supported_specs
from lumen.model.monitor import Capabilities, MonitorDescriptor
from lumen.model.vcp_registry import Safety, VcpSpec


class ControlKind(Enum):
    """Where a control's value actually lives."""

    VCP = "vcp"
    GAMMA = "gamma"
    PRESET = "preset"


@runtime_checkable
class Control(Protocol):
    """One adjustable value."""

    code: str
    label: str
    kind: ControlKind
    minimum: float
    maximum: float
    step: float

    def read(self) -> float:
        """The current value."""

    def write(self, value: float) -> None:
        """Set the value. Raises on failure or an out-of-range value."""

    def peek(self) -> float | None:
        """The last known value, without accessing hardware."""

    def available(self) -> bool:
        """Whether the control can currently be read and written."""


class VcpControl:
    """A single DDC/CI control inside the monitor."""

    kind = ControlKind.VCP

    def __init__(self, backend: DdcBackend, monitor: MonitorDescriptor, spec: VcpSpec) -> None:
        self._backend = backend
        self._monitor = monitor
        self._spec = spec
        self.code = f"vcp_0x{spec.code:02X}"
        self.label = spec.name
        self.step = 1.0
        self.minimum = float(spec.minimum)
        self.maximum = float(spec.maximum)
        self._cached_value: float | None = None

    def _reading(self):
        return self._backend.get(self._monitor, self._spec.code)

    def _refresh_range(self, reading=None) -> None:
        """Adopt the monitor's own reported range for this control.

        The registry's range is the specification's default. A real panel
        may report a different maximum - brightness 50 on some panels -
        and a slider must reflect the hardware, not the default.
        """
        if reading is None:
            try:
                reading = self._reading()
            except DdcError:
                return
        if reading.supported and reading.maximum > 0:
            self.maximum = float(reading.maximum)

    @property
    def vcp_code(self) -> int | None:
        """The VCP code this control drives, if it drives one."""
        return self._spec.code

    def available(self) -> bool:
        try:
            return self._reading().supported
        except DdcError:
            return False

    def read(self) -> float:
        """The current value.

        The value is cached, because each DDC/CI read costs about fifty
        milliseconds over the bus. A slider that read on every redraw
        would make the interface feel slow, and the monitor does not
        change its own values behind our back.

        The first read also adopts the monitor's own reported range,
        which may differ from the registry's default.
        """
        if self._cached_value is not None:
            return self._cached_value
        reading = self._reading()
        if not reading.supported:
            raise DdcError(f"the monitor does not support 0x{self._spec.code:02X}")
        self._refresh_range(reading)
        self._cached_value = float(reading.value)
        return self._cached_value

    def reported_maximum(self) -> float:
        """The maximum the monitor itself reports for this control.

        This reads the bus if the value is not known yet, so it is not
        suitable for drawing. Use `peek` and the cached `maximum` when
        redrawing.
        """
        self.read()
        return self.maximum

    def invalidate(self) -> None:
        """Forget the cached value, so the next read goes to the monitor."""
        self._cached_value = None

    def peek(self) -> float | None:
        """The cached value, or None when it is not known yet.

        Unlike `read`, this never touches the bus, so it is safe to call
        while drawing.
        """
        return self._cached_value

    def write(self, value: float) -> None:
        target = int(round(value))
        if target < self.minimum or target > self.maximum:
            raise DdcError(
                f"{self.label} must be between {int(self.minimum)} and "
                f"{int(self.maximum)}, not {target}"
            )
        self._backend.set(self._monitor, self._spec.code, target)
        self._cached_value = float(target)


class PresetControl:
    """A DDC/CI control with a fixed list of permitted values."""

    kind = ControlKind.PRESET

    def __init__(
        self,
        backend: DdcBackend,
        monitor: MonitorDescriptor,
        spec: VcpSpec,
        options: tuple[int, ...],
    ) -> None:
        self._backend = backend
        self._monitor = monitor
        self._spec = spec
        self.options = tuple(options)
        self.code = f"vcp_0x{spec.code:02X}"
        self.label = spec.name
        self.step = 1.0
        self.minimum = float(min(options)) if options else 0.0
        self.maximum = float(max(options)) if options else 0.0
        self._cached_value: float | None = None

    def available(self) -> bool:
        try:
            return self._backend.get(self._monitor, self._spec.code).supported
        except DdcError:
            return False

    def read(self) -> float:
        """The current value, cached between changes."""
        if self._cached_value is not None:
            return self._cached_value
        reading = self._backend.get(self._monitor, self._spec.code)
        if not reading.supported:
            raise DdcError(f"the monitor does not support 0x{self._spec.code:02X}")
        self._cached_value = float(reading.value)
        return self._cached_value

    def invalidate(self) -> None:
        """Forget the cached value, so the next read goes to the monitor."""
        self._cached_value = None

    def peek(self) -> float | None:
        """The cached value, or None when it is not known yet."""
        return self._cached_value

    def write(self, value: float) -> None:
        target = int(round(value))
        if target not in self.options:
            allowed = ", ".join(f"0x{option:02X}" for option in self.options)
            raise DdcError(
                f"{self.label} must be one of {allowed}, not 0x{target:02X}"
            )
        self._backend.set(self._monitor, self._spec.code, target)
        self._cached_value = float(target)


class GammaControl:
    """One channel of a display's software gamma ramp."""

    kind = ControlKind.GAMMA

    def __init__(
        self,
        backend: GammaBackend,
        output: str,
        channel: str,
        exponent: float = 2.2,
    ) -> None:
        if channel not in ("red", "green", "blue"):
            raise ValueError(f"unknown gamma channel {channel!r}")
        self._backend = backend
        self._output = output
        self._channel = channel
        self._exponent = exponent
        self._cached_value: float | None = None
        self.code = f"gamma_{channel}"
        self.label = f"Gamma {channel.capitalize()}"
        self.minimum = 1.0
        self.maximum = 3.0
        self.step = 0.01

    def _index(self) -> int:
        return {"red": 0, "green": 1, "blue": 2}[self._channel]

    def available(self) -> bool:
        try:
            self._backend.read_ramp(self._output)
        except GammaError:
            return False
        return True

    def read(self) -> float:
        ramp = self._backend.read_ramp(self._output)
        channel = ramp[self._index()]
        size = len(channel)
        if size < 4:
            raise GammaError(f"the gamma ramp for {self._output!r} is too short")

        # The ramp maps an input fraction to 65535 * fraction ** (1/g).
        # Taking two points a quarter and three quarters of the way along
        # recovers g from the logarithm of the output ratio, and is far
        # less sensitive to rounding than two adjacent entries.
        lower_index = size // 4
        upper_index = (size * 3) // 4
        lower_fraction = lower_index / (size - 1)
        upper_fraction = upper_index / (size - 1)
        lower_output = channel[lower_index]
        upper_output = channel[upper_index]
        if upper_output <= 0 or lower_output <= 0:
            raise GammaError("the gamma ramp has no usable midtones")
        # The upper point always has the larger output, so the ratios are
        # taken upper-over-lower to keep them above one.
        output_ratio = upper_output / lower_output
        if output_ratio <= 1.0:
            self._cached_value = 1.0
            return self._cached_value
        exponent = math.log(upper_fraction / lower_fraction) / math.log(output_ratio)
        self._cached_value = float(round(exponent, 3))
        return self._cached_value

    def peek(self) -> float | None:
        """The last known exponent, without reading the gamma ramp."""
        return self._cached_value

    def write(self, value: float) -> None:
        if value < self.minimum or value > self.maximum:
            raise GammaError(
                f"gamma must be between {self.minimum} and {self.maximum}, not {value}"
            )
        ramp = self._backend.read_ramp(self._output)
        channels = [list(ramp[0]), list(ramp[1]), list(ramp[2])]
        size = len(channels[0])
        target = channels[self._index()]
        for position in range(size):
            fraction = position / (size - 1) if size > 1 else 0.0
            target[position] = int(round(65535 * (fraction ** (1.0 / value))))
        channels[self._index()] = target
        self._backend.write_ramp(
            self._output,
            tuple(channels[0]),
            tuple(channels[1]),
            tuple(channels[2]),
        )
        self._cached_value = value


def build_vcp_controls(
    backend: DdcBackend,
    monitor: MonitorDescriptor,
    capabilities: Capabilities,
) -> list[Control]:
    """Build the controls a monitor supports.

    Dangerous codes are excluded, and a code whose capability string lists
    enumerated values becomes a `PresetControl` rather than a slider.
    """
    controls: list[Control] = []
    for spec in supported_specs(capabilities):
        if spec.safety is Safety.DANGEROUS:
            continue
        options = capabilities.vcp_values.get(spec.code, ())
        if options:
            controls.append(PresetControl(backend, monitor, spec, options))
        else:
            controls.append(VcpControl(backend, monitor, spec))
    return controls


def warm_controls(controls: list[Control], on_ready=None) -> None:
    """Read every control once, in the background.

    Each DDC/CI read costs about fifty milliseconds over the bus, so
    reading two dozen controls on the main thread would stall the
    interface for a second. Reading them in the background means the
    wizard opens instantly and the values arrive a moment later.

    `on_ready` is called on the main thread once every control has been
    tried, so the interface can refresh itself.
    """
    import threading

    def worker() -> None:
        for control in controls:
            try:
                control.read()
            except Exception:
                continue
        if on_ready is not None:
            on_ready()

    threading.Thread(target=worker, daemon=True).start()
