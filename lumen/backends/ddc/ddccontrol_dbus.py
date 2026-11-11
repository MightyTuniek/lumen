"""DDC/CI over the `ddccontrol` D-Bus service.

The service runs as root, and its system bus policy allows any user to
call it. That means Lumen needs no sudo, no udev rule and no group
membership - which is the reason this backend is the primary one on
Linux.

The `dbus` module is imported lazily so that this file can be imported
on a system without dbus-python installed.
"""

from __future__ import annotations

import time

from lumen.backends.base import DdcError
from lumen.model.capability import parse_capabilities
from lumen.model.monitor import Capabilities, MonitorDescriptor, VcpValue
from lumen.model.vcp_registry import spec_for

SERVICE = "ddccontrol.DDCControl"
PATH = "/ddccontrol/DDCControl"

# How many times to retry a write the monitor did not accept.
_WRITE_RETRIES = 3
_RETRY_DELAY_SECONDS = 0.25


def _first_byte(array, index: int) -> int:
    """Read the first byte of an element of a `a(y)` reply."""
    try:
        entry = array[index]
    except (IndexError, TypeError):
        return 0
    if isinstance(entry, (bytes, bytearray)):
        return entry[0] if entry else 0
    if isinstance(entry, (tuple, list)):
        return int(entry[0]) if entry else 0
    return int(entry)


class DdcControlDBusBackend:
    """Talks to `ddccontrol.DDCControl` over the system bus."""

    def __init__(self, bus=None, service: str = SERVICE, path: str = PATH) -> None:
        if bus is None:
            try:
                import dbus  # optional dependency, imported lazily
            except ImportError as exc:
                raise DdcError(
                    "dbus-python is required for the ddccontrol backend; "
                    "install python3-dbus or use another backend"
                ) from exc
            bus = dbus.SystemBus()
        self._bus = bus
        self._service = service
        self._path = path
        self._object = bus.get_object(service, path)
        # The interface must be named explicitly. A bare proxy looks the
        # method up by introspecting the object, and this service does not
        # answer introspection at that path.
        try:
            import dbus

            self._interface = dbus.Interface(self._object, service)
        except ImportError:  # pragma: no cover - only for fake buses
            self._interface = self._object
        self._capability_cache: dict[str, Capabilities] = {}
        self._identifiers: dict[str, str] = {}

    def _call(self, method: str, *args):
        return getattr(self._interface, method)(*args)

    def list_monitors(self) -> list[MonitorDescriptor]:
        devices, supported, names, digital = self._call("GetMonitors")
        monitors: list[MonitorDescriptor] = []
        for index, device in enumerate(devices):
            # The supported and digital arrays are arrays of one-byte
            # structs, so each entry needs unwrapping.
            is_supported = bool(_first_byte(supported, index))
            is_digital = bool(_first_byte(digital, index))
            name = names[index] if index < len(names) else ""
            monitors.append(
                MonitorDescriptor(
                    identifier=str(device),
                    model=str(name),
                    connection="digital" if is_digital else "analog",
                    backend_name="ddccontrol",
                )
            )
        return monitors

    def capabilities(self, monitor: MonitorDescriptor) -> Capabilities:
        cached = self._capability_cache.get(monitor.identifier)
        if cached is not None:
            return cached
        try:
            pnp_id, raw = self._call("OpenMonitor", monitor.identifier)
        except Exception as exc:
            raise DdcError(
                f"could not read capabilities from {monitor.identifier}: {exc}"
            ) from exc
        caps = parse_capabilities(str(raw))
        self._capability_cache[monitor.identifier] = caps
        # The PNP id identifies the panel: the first three characters are
        # the manufacturer, the rest the model code.
        pnp = str(pnp_id)
        if pnp and monitor.pnp_id is None:
            self._identifiers[monitor.identifier] = pnp
        return caps

    def describe(self, monitor: MonitorDescriptor) -> MonitorDescriptor:
        """A descriptor enriched with the monitor's PNP identity.

        The D-Bus service reports the monitor as a generic name, so the
        PNP id is used to recover the real manufacturer and model.
        """
        pnp = self._identifiers.get(monitor.identifier)
        if not pnp:
            self.capabilities(monitor)
            pnp = self._identifiers.get(monitor.identifier)
        if not pnp:
            return monitor
        manufacturer = pnp[:3].title() if len(pnp) > 3 else ""
        model = pnp[3:] if len(pnp) > 3 else pnp
        return MonitorDescriptor(
            identifier=monitor.identifier,
            manufacturer=manufacturer,
            model=model or monitor.model,
            serial=monitor.serial,
            pnp_id=pnp,
            edid_hash=monitor.edid_hash,
            connection=monitor.connection,
            backend_name=monitor.backend_name,
        )

    def get(self, monitor: MonitorDescriptor, code: int) -> VcpValue:
        try:
            result, value, maximum = self._call("GetControl", monitor.identifier, code)
        except Exception as exc:
            raise DdcError(
                f"could not read 0x{code:02X} from {monitor.identifier}: {exc}"
            ) from exc
        return VcpValue(
            code=code,
            value=int(value),
            maximum=int(maximum),
            supported=int(result) != 0,
        )

    def set(self, monitor: MonitorDescriptor, code: int, value: int) -> None:
        # Refuse an out-of-range value before sending it: some monitors
        # accept a value beyond their reported maximum and then behave
        # unpredictably. The minimum comes from the registry, because the
        # monitor only reports a maximum.
        reading = self.get(monitor, code)
        if reading.maximum:
            spec = spec_for(code)
            minimum = spec.minimum if spec is not None else 0
            if not (minimum <= value <= reading.maximum):
                raise DdcError(
                    f"0x{code:02X} must be between {minimum} and "
                    f"{reading.maximum}, not {value}"
                )

        last_error: Exception | None = None
        for _attempt in range(_WRITE_RETRIES):
            try:
                self._call("SetControl", monitor.identifier, code, int(value))
            except Exception as exc:
                last_error = exc
            else:
                time.sleep(_RETRY_DELAY_SECONDS)
                confirmed = self.get(monitor, code)
                if confirmed.value == int(value):
                    return
                last_error = DdcError(
                    f"monitor did not accept 0x{code:02X}={value} "
                    f"(read back {confirmed.value})"
                )
            time.sleep(_RETRY_DELAY_SECONDS)
        raise DdcError(
            f"could not set 0x{code:02X}={value} on {monitor.identifier}: {last_error}"
        )
