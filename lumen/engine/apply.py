"""Applying, capturing and restoring a calibration.

The applier is deliberately forgiving and deliberately honest:

- a code the monitor does not support is skipped, and said so
- a write the monitor refuses is reported, not raised, so one bad
  control does not abandon the whole calibration
- dangerous codes are never written, whatever a profile asks for
- applying the same profile twice produces the same state

The snapshot is what makes a cancelled wizard safe. It records every
writable control and the whole gamma ramp, so restoring puts the display
back exactly as it was.
"""

from __future__ import annotations

from dataclasses import dataclass

from lumen.backends.base import DdcError, GammaError
from lumen.engine.controls import build_vcp_controls
from lumen.engine.curves import build_channel_ramps
from lumen.model.capability import supported_specs
from lumen.model.monitor import Capabilities, MonitorDescriptor
from lumen.model.profile import Profile
from lumen.model.vcp_registry import Safety


@dataclass(frozen=True)
class Snapshot:
    """A record of the display's state, used to restore it."""

    ddc: dict[int, int]
    gamma: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]] | None = None
    output: str | None = None


class ProfileApplier:
    """Applies profiles to one monitor."""

    def __init__(
        self,
        backend,
        monitor: MonitorDescriptor,
        capabilities: Capabilities,
        gamma_backend=None,
        gamma_output: str | None = None,
    ) -> None:
        self._backend = backend
        self._monitor = monitor
        self._capabilities = capabilities
        self._gamma_backend = gamma_backend
        self._gamma_output = gamma_output
        self._dangerous = self._dangerous_codes()
        self._pending_snapshot: Snapshot | None = None

    # -- reading ------------------------------------------------------------

    @property
    def monitor(self) -> MonitorDescriptor:
        """The monitor this applier controls."""
        return self._monitor

    @property
    def capabilities(self) -> Capabilities:
        """What the monitor advertises."""
        return self._capabilities

    @property
    def backend(self):
        """The DDC backend in use."""
        return self._backend

    @property
    def gamma_backend(self):
        """The gamma backend in use, if any."""
        return self._gamma_backend

    @property
    def gamma_output(self) -> str | None:
        """The output whose gamma ramp is controlled, if any."""
        return self._gamma_output

    def _writable_codes(self) -> list[int]:
        return [
            spec.code
            for spec in supported_specs(self._capabilities)
            if spec.safety is not Safety.DANGEROUS
        ]

    def _dangerous_codes(self) -> set[int]:
        """Codes that must never be written, whatever a profile asks for."""
        return {
            spec.code
            for spec in supported_specs(self._capabilities)
            if spec.safety is Safety.DANGEROUS
        }

    def current_values(self, controls=None) -> dict[int, int]:
        """Every writable control's current value.

        When controls are supplied their cached values are used, which
        avoids a second trip over the bus for values already known.
        """
        if controls is not None:
            values: dict[int, int] = {}
            for control in controls:
                value = control.peek()
                code = getattr(control, "vcp_code", None)
                if value is not None and code is not None:
                    values[code] = int(value)
            return values

        values = {}
        for code in self._writable_codes():
            try:
                reading = self._backend.get(self._monitor, code)
            except DdcError:
                continue
            if reading.supported:
                values[code] = reading.value
        return values

    def capture(self) -> Snapshot:
        """Record the current state so it can be restored later.

        This reads every writable control over the bus, which takes about
        a second. It is called once when the wizard opens, so it is done
        in the background; until it finishes the snapshot is empty and
        cancelling restores what is known so far.
        """
        gamma = None
        output = None
        if self._gamma_backend is not None and self._gamma_output is not None:
            try:
                gamma = self._gamma_backend.read_ramp(self._gamma_output)
                output = self._gamma_output
            except GammaError:
                gamma = None
        return Snapshot(ddc=self.current_values(), gamma=gamma, output=output)

    def snapshot_from(self, controls) -> Snapshot:
        """Build a snapshot from controls that already know their values."""
        return Snapshot(
            ddc=self.current_values(controls),
            gamma=self.capture_gamma(),
            output=self._gamma_output,
        )

    def capture_gamma(self):
        """The current gamma ramp, or None when there is no gamma backend."""
        if self._gamma_backend is None or self._gamma_output is None:
            return None
        try:
            return self._gamma_backend.read_ramp(self._gamma_output)
        except GammaError:
            return None

    def capture_in_background(self, on_ready=None) -> None:
        """Capture the state in the background, then call `on_ready`.

        The wizard calls this instead of `capture`, so it opens at once
        rather than pausing for a second on the bus.

        `on_ready` is called from the worker thread. A caller that needs
        to touch the interface must move the call onto the main thread
        itself; the engine layer knows nothing about the interface.
        """
        import threading

        def worker() -> None:
            self._pending_snapshot = self.capture()
            if on_ready is not None:
                on_ready()

        threading.Thread(target=worker, daemon=True).start()

    def take_snapshot(self) -> Snapshot | None:
        """The snapshot from the last background capture, if it is ready."""
        return self._pending_snapshot

    # -- writing ------------------------------------------------------------

    def apply(self, profile: Profile) -> list[str]:
        """Apply a profile, returning notes about anything skipped."""
        notes: list[str] = []
        supported = set(self._writable_codes())

        for code, value in sorted(profile.ddc.items()):
            if code in self._dangerous:
                notes.append(
                    f"refused 0x{code:02X}: that control can blank the screen "
                    "or destroy the monitor's settings"
                )
                continue
            if code not in supported:
                notes.append(
                    f"skipped 0x{code:02X}: this monitor does not support it"
                )
                continue
            try:
                self._backend.set(self._monitor, code, value)
            except DdcError as exc:
                notes.append(f"could not set 0x{code:02X}: {exc}")

        if self._gamma_backend is not None and self._gamma_output is not None:
            size = self._ramp_size()
            if size:
                red, green, blue = build_channel_ramps(size, profile.gamma)
                try:
                    self._gamma_backend.write_ramp(
                        self._gamma_output, red, green, blue
                    )
                except GammaError as exc:
                    notes.append(f"could not set the gamma curve: {exc}")
        else:
            notes.append("skipped the gamma curve: no software gamma is available")

        return notes

    def restore(self, snapshot: Snapshot) -> None:
        """Put the display back the way the snapshot found it."""
        for code, value in sorted(snapshot.ddc.items()):
            try:
                self._backend.set(self._monitor, code, value)
            except DdcError:
                continue

        if (
            snapshot.gamma is not None
            and self._gamma_backend is not None
            and snapshot.output is not None
        ):
            try:
                self._gamma_backend.write_ramp(snapshot.output, *snapshot.gamma)
            except GammaError:
                pass

    def _ramp_size(self) -> int:
        if self._gamma_backend is None or self._gamma_output is None:
            return 0
        try:
            ramp = self._gamma_backend.read_ramp(self._gamma_output)
        except GammaError:
            return 0
        return len(ramp[0]) if ramp and ramp[0] else 0
