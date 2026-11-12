"""Software gamma control over X11 RandR.

The RandR gamma ramp holds 1024 entries per channel on the reference
machine, an order of magnitude finer than the monitor's own DDC
controls.

IMPORTANT: XRRSetCrtcGamma is an asynchronous X request. It is only
transmitted when the connection is flushed. A write that is not flushed
is silently discarded and the ramp keeps its previous value. Every write
here is therefore flushed, synced and then verified by reading the ramp
back. Without this the tool would appear to work sometimes and silently
fail to apply a calibration at other times.

The CRTC for an output is resolved on every access rather than cached,
because display topology changes (hotplug, clone/extend, resolution
changes) invalidate any cached mapping.

python-xlib is used rather than raw ctypes because the RandR reply
structures are version dependent: their layout depends on the protocol
version the server negotiated. Getting that wrong does not raise an
error, it reads garbage and then writes the gamma ramp to the wrong
display.
"""

from __future__ import annotations

from lumen.backends.base import GammaError

# The number of entries the gamma ramp holds on the reference machine.
DEFAULT_RAMP_SIZE = 1024


class GammaUnavailable(Exception):
    """No usable X display, or no RandR gamma support."""


class XRandRGammaBackend:
    """Controls a display's gamma ramp through libXrandr."""

    def __init__(self, display_name: str | None = None) -> None:
        try:
            from Xlib import display as xdisplay
            from Xlib.ext import randr
        except ImportError as exc:
            raise GammaUnavailable(
                "python-xlib is required for the X11 gamma backend"
            ) from exc

        self._randr = randr
        try:
            self._display = xdisplay.Display(display_name)
        except Exception as exc:
            raise GammaUnavailable(
                f"could not open the X display {display_name or 'default'}: {exc}"
            ) from exc

        if not self._display.has_extension("RANDR"):
            raise GammaUnavailable("the X server does not support RandR")

        self._crtcs: dict[str, int] = {}
        self._size = DEFAULT_RAMP_SIZE
        self._refresh_crtcs()

    # -- topology -----------------------------------------------------------

    def _refresh_crtcs(self) -> None:
        """Map output names to CRTC ids, re-reading the current topology."""
        root = self._display.screen().root
        resources = self._randr.get_screen_resources(root)

        mapping: dict[str, int] = {}
        for output_id in resources.outputs:
            try:
                info = self._display.xrandr_get_output_info(output_id, 0)
            except Exception:
                continue
            if not info.crtc:
                continue
            name = info.name.decode() if isinstance(info.name, bytes) else info.name
            mapping[name] = info.crtc

        self._crtcs = mapping

    def _crtc_for(self, output: str) -> int:
        self._refresh_crtcs()
        try:
            return self._crtcs[output]
        except KeyError:
            raise GammaError(f"no gamma-capable output named {output!r}") from None

    # -- GammaBackend protocol ---------------------------------------------

    def list_outputs(self) -> list[str]:
        self._refresh_crtcs()
        return sorted(self._crtcs)

    def describe_outputs(self):
        """Connected outputs with EDID identity (colord-compatible MD5)."""
        import hashlib
        from lumen.model.monitor import MonitorDescriptor

        root = self._display.screen().root
        resources = self._randr.get_screen_resources(root)
        atom = self._display.intern_atom("EDID")
        result = []
        for output in resources.outputs:
            info = self._display.xrandr_get_output_info(output, 0)
            if not info.crtc:
                continue
            name = info.name.decode() if isinstance(info.name, bytes) else info.name
            edid = b""
            try:
                reply = self._display.xrandr_get_output_property(output, atom, 0, 0, 256, False, False)
                edid = bytes(reply.value)
            except Exception:
                pass
            model, serial = name, ""
            if len(edid) >= 128:
                for offset in range(54, 126, 18):
                    block = edid[offset:offset + 18]
                    if block[:3] == b"\x00\x00\x00":
                        text = block[5:18].decode("ascii", "replace").strip()
                        if block[3] == 0xFC:
                            model = text
                        elif block[3] == 0xFF:
                            serial = text
            result.append(MonitorDescriptor(identifier=f"output:{name}", model=model,
                          serial=serial, edid_hash=hashlib.md5(edid).hexdigest() if edid else None,
                          connection=name, backend_name="xrandr"))
        return result

    def capture_icc(self, output: str):
        """Capture output and primary-root properties before activation."""
        from Xlib import Xatom
        root = self._display.screen().root
        resources = self._randr.get_screen_resources(root)
        atom = self._display.intern_atom("_ICC_PROFILE")
        for output_id in resources.outputs:
            info = self._display.xrandr_get_output_info(output_id, 0)
            name = info.name.decode() if isinstance(info.name, bytes) else info.name
            if name == output:
                value = self._display.xrandr_get_output_property(output_id, atom, Xatom.CARDINAL,
                                                                 0, 4 * 1024 * 1024, False, False)
                prop = root.get_full_property(atom, Xatom.CARDINAL)
                return {"output": bytes(value.value) if value.property_type else None,
                        "root": bytes(prop.value) if prop else None}
        raise GammaError(f"Output {output!r} disconnected")

    def restore_icc(self, output: str, snapshot):
        self.publish_icc(output, snapshot["output"])
        from Xlib import Xatom
        root = self._display.screen().root
        atom = self._display.intern_atom("_ICC_PROFILE")
        if snapshot["root"] is None:
            root.delete_property(atom)
        else:
            root.change_property(atom, Xatom.CARDINAL, 8, snapshot["root"])
        self._display.flush()
        self._display.sync()

    def validate_output(self, output: str):
        self._refresh_crtcs()
        crtc = self._crtc_for(output)
        if list(self._crtcs.values()).count(crtc) != 1:
            raise GammaError("Mirrored outputs share a gamma ramp; disable mirroring before calibration")

    def publish_icc(self, output: str, data: bytes | None):
        """Publish the per-output ICC atom and the primary root fallback."""
        from Xlib import Xatom

        root = self._display.screen().root
        resources = self._randr.get_screen_resources(root)
        atom = self._display.intern_atom("_ICC_PROFILE")
        found = None
        for output_id in resources.outputs:
            info = self._display.xrandr_get_output_info(output_id, 0)
            name = info.name.decode() if isinstance(info.name, bytes) else info.name
            if name == output:
                found = output_id
                break
        if found is None:
            raise GammaError(f"Output {output!r} disconnected")
        if data is None:
            self._display.xrandr_delete_output_property(found, atom)
        else:
            self._display.xrandr_change_output_property(found, atom, Xatom.CARDINAL, 0, (8, list(data)))
        primary = root.xrandr_get_output_primary().output
        if found == primary or len(self.list_outputs()) == 1:
            if data is None:
                root.delete_property(atom)
            else:
                root.change_property(atom, Xatom.CARDINAL, 8, data)
        self._display.flush()
        self._display.sync()

    def read_ramp(
        self, output: str
    ) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
        crtc = self._crtc_for(output)
        size = self._display.xrandr_get_crtc_gamma_size(crtc)
        size = int(size.size) if hasattr(size, "size") else int(size)
        if size <= 0:
            raise GammaError(f"output {output!r} reports no gamma ramp")
        ramp = self._display.xrandr_get_crtc_gamma(crtc)
        return (
            tuple(int(value) for value in ramp.red),
            tuple(int(value) for value in ramp.green),
            tuple(int(value) for value in ramp.blue),
        )

    def write_ramp(
        self,
        output: str,
        red: tuple[int, ...],
        green: tuple[int, ...],
        blue: tuple[int, ...],
    ) -> None:
        crtc = self._crtc_for(output)
        size = self._display.xrandr_get_crtc_gamma_size(crtc)
        size = int(size.size) if hasattr(size, "size") else int(size)
        if size <= 0:
            raise GammaError(f"output {output!r} reports no gamma ramp")
        if not (len(red) == len(green) == len(blue) == size):
            raise GammaError(f"ramp length mismatch: expected {size} entries per channel")

        self._display.xrandr_set_crtc_gamma(
            crtc, size, tuple(red), tuple(green), tuple(blue)
        )

        # The request is asynchronous: without this flush it never reaches
        # the server and the ramp silently keeps its old value.
        self._display.flush()
        self._display.sync()

        observed = self.read_ramp(output)
        if observed != (tuple(red), tuple(green), tuple(blue)):
            raise GammaError(
                f"the gamma ramp for {output!r} did not take the written value"
            )
