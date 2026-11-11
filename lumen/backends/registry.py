"""Backend discovery.

Lumen must work on machines where only some of the hardware paths are
available. Discovery therefore never raises: it reports what it found
and why, so the application can tell the user plainly rather than
failing with a stack trace.

The ddccontrol D-Bus service is tried first because it needs no
privileges. A ddcutil-based backend is the fallback for systems where
that daemon is not installed.
"""

from __future__ import annotations

from dataclasses import dataclass

from lumen.backends.base import DdcBackend, DdcError, GammaBackend


@dataclass(frozen=True)
class BackendAvailability:
    """What this machine offers."""

    ddc: bool
    gamma: bool
    ddc_reason: str = ""
    gamma_reason: str = ""


def _try_ddccontrol():
    from lumen.backends.ddc.ddccontrol_dbus import DdcControlDBusBackend

    backend = DdcControlDBusBackend()
    backend.list_monitors()
    return backend


def _try_ddcutil():
    import shutil
    import subprocess

    executable = shutil.which("ddcutil")
    if executable is None:
        raise DdcError("ddcutil is not installed")

    class DdcUtilBackend:
        """Drives the ddcutil command line tool."""

        def __init__(self) -> None:
            self._executable = executable

        def _run(self, *args: str) -> str:
            result = subprocess.run(
                [self._executable, *args],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0:
                raise DdcError(result.stderr.strip() or "ddcutil failed")
            return result.stdout

        def list_monitors(self):
            from lumen.model.monitor import MonitorDescriptor

            output = self._run("detect", "--brief")
            monitors = []
            for line in output.splitlines():
                if "connected" in line:
                    identifier = line.split()[0]
                    monitors.append(
                        MonitorDescriptor(
                            identifier=identifier, backend_name="ddcutil"
                        )
                    )
            if not monitors:
                raise DdcError("ddcutil found no connected monitors")
            return monitors

        def capabilities(self, monitor):
            from lumen.model.capability import parse_capabilities

            output = self._run("capabilities", "--brief", monitor.identifier)
            return parse_capabilities(output)

        def get(self, monitor, code):
            from lumen.model.monitor import VcpValue

            output = self._run(
                "getvcp", "--brief", monitor.identifier, f"0x{code:02X}"
            )
            value, maximum = 0, 0
            for part in output.replace("=", " ").split():
                try:
                    number = int(part)
                except ValueError:
                    continue
                if maximum == 0:
                    value = number
                else:
                    maximum = number
                    break
            return VcpValue(
                code=code, value=value, maximum=maximum, supported=True
            )

        def set(self, monitor, code, value):
            self._run("setvcp", monitor.identifier, f"0x{code:02X}", str(value))
            confirmed = self.get(monitor, code)
            if confirmed.value != value:
                raise DdcError(
                    f"monitor did not accept 0x{code:02X}={value} "
                    f"(read back {confirmed.value})"
                )

    backend = DdcUtilBackend()
    backend.list_monitors()
    return backend


_DDC_BACKENDS = (_try_ddccontrol, _try_ddcutil)


def detect_backends() -> BackendAvailability:
    """Report what this machine offers, without raising."""
    ddc = False
    ddc_reason = ""
    for factory in _DDC_BACKENDS:
        try:
            factory()
        except Exception as exc:
            ddc_reason = str(exc) or factory.__name__
        else:
            ddc = True
            ddc_reason = factory.__name__
            break

    gamma = False
    gamma_reason = ""
    try:
        from lumen.backends.gamma.xrandr import XRandRGammaBackend

        backend = XRandRGammaBackend()
        backend.list_outputs()
    except Exception as exc:
        gamma_reason = str(exc) or "no gamma backend"
    else:
        gamma = True
        gamma_reason = "xrandr"

    return BackendAvailability(
        ddc=ddc,
        gamma=gamma,
        ddc_reason=ddc_reason,
        gamma_reason=gamma_reason,
    )


def make_ddc_backend() -> DdcBackend:
    """The first DDC backend that works."""
    failures: list[str] = []
    for factory in _DDC_BACKENDS:
        try:
            return factory()
        except Exception as exc:
            failures.append(f"{factory.__name__}: {exc}")
    raise DdcError(
        "no DDC/CI backend could be started:\n" + "\n".join(failures)
    )


def make_gamma_backend() -> GammaBackend | None:
    """A gamma backend, or None when the platform has none."""
    try:
        from lumen.backends.gamma.xrandr import XRandRGammaBackend

        return XRandRGammaBackend()
    except Exception:
        return None
