"""The command line interface.

The CLI shares the engine with the graphical application, so there is one
implementation of "set brightness" rather than two. It is also what the
login service uses, and the first thing to run when something is not
working: `lumenctl doctor` reports which backends were found and why.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from lumen.backends.base import DdcError
from lumen.backends.registry import (
    detect_backends,
    make_ddc_backend,
    make_gamma_backend,
)
from lumen.engine.apply import ProfileApplier
from lumen.model.profile import (
    load_profile,
    profile_directory,
    profile_matches_monitor,
    profile_slug,
)


def _parse_code(text: str) -> int:
    return int(text, 16)


def _applier():
    backend = make_ddc_backend()
    monitors = backend.list_monitors()
    if not monitors:
        raise DdcError("no monitors were found")
    monitor = monitors[0]
    if hasattr(backend, "describe"):
        monitor = backend.describe(monitor)
    capabilities = backend.capabilities(monitor)
    gamma = make_gamma_backend()
    output = None
    if gamma is not None:
        outputs = gamma.list_outputs()
        output = outputs[0] if outputs else None
    return ProfileApplier(backend, monitor, capabilities, gamma, output), monitor


def _cmd_detect(_args) -> int:
    backend = make_ddc_backend()
    monitors = backend.list_monitors()
    if not monitors:
        print("No monitors were found.")
        return 1
    for monitor in monitors:
        if hasattr(backend, "describe"):
            monitor = backend.describe(monitor)
        capabilities = backend.capabilities(monitor)
        print(f"{monitor.identifier}  {monitor.model or 'unknown model'}")
        print(f"  connection: {monitor.connection or 'unknown'}")
        print(f"  MCCS version: {capabilities.mccs_version or 'unknown'}")
        codes = sorted(capabilities.vcp_codes)
        print(f"  {len(codes)} VCP codes supported")
        for code in codes:
            print(f"    0x{code:02X}")
    return 0


def _cmd_get(args) -> int:
    backend = make_ddc_backend()
    monitor = backend.list_monitors()[0]
    value = backend.get(monitor, _parse_code(args.code))
    print(f"0x{value.code:02X} = {value.value} (maximum {value.maximum})")
    return 0


def _cmd_set(args) -> int:
    backend = make_ddc_backend()
    monitor = backend.list_monitors()[0]
    code = _parse_code(args.code)
    reading = backend.get(monitor, code)
    if reading.maximum and not (0 <= args.value <= reading.maximum):
        print(
            f"0x{code:02X} must be between 0 and {reading.maximum}, "
            f"not {args.value}",
            file=sys.stderr,
        )
        return 1
    backend.set(monitor, code, args.value)
    print(f"0x{code:02X} = {args.value}")
    return 0


def _cmd_profiles(args) -> int:
    directory = profile_directory()
    if args.action == "list":
        paths = sorted(directory.glob("*.json")) if directory.exists() else []
        if not paths:
            print("No profiles yet. Run the calibration wizard to create one.")
            return 0
        for path in paths:
            profile = load_profile(path)
            print(f"{profile.name}  ({path.name})")
        return 0

    path = directory / f"{profile_slug(args.name)}.json"

    if args.action == "show":
        if not path.exists():
            print(f"No profile called {args.name!r}.", file=sys.stderr)
            return 1
        print(json.dumps(load_profile(path).to_dict(), indent=2))
        return 0

    if args.action == "rm":
        if not path.exists():
            print(f"No profile called {args.name!r}.", file=sys.stderr)
            return 1
        path.unlink()
        print(f"Removed {args.name}.")
        return 0

    if args.action == "apply":
        if not path.exists():
            print(f"No profile called {args.name!r}.", file=sys.stderr)
            return 1
        profile = load_profile(path)
        applier, monitor = _applier()
        if not profile_matches_monitor(profile, monitor):
            print(
                f"Warning: this profile was made for a different monitor "
                f"({profile.monitor.model or 'unknown'}).",
                file=sys.stderr,
            )
        notes = applier.apply(profile)
        for note in notes:
            print(note)
        print(f"Applied {profile.name}.")
        return 0

    return 1


def _cmd_capture(args) -> int:
    applier, _monitor = _applier()
    snapshot = applier.capture()
    target = Path(args.output) if args.output else Path("lumen-snapshot.json")
    payload = {
        "ddc": {f"{code:02X}": value for code, value in sorted(snapshot.ddc.items())},
        "output": snapshot.output,
        "gamma": None,
    }
    if snapshot.gamma is not None:
        payload["gamma"] = [list(channel) for channel in snapshot.gamma]
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {target}.")
    return 0


def _cmd_restore(args) -> int:
    from lumen.engine.apply import Snapshot

    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    ddc = {
        int(code, 16): int(value) for code, value in payload.get("ddc", {}).items()
    }
    raw_gamma = payload.get("gamma")
    gamma = None
    if raw_gamma:
        gamma = tuple(
            tuple(int(value) for value in channel) for channel in raw_gamma
        )
    applier, _monitor = _applier()
    applier.restore(Snapshot(ddc=ddc, gamma=gamma, output=payload.get("output")))
    print(f"Restored {args.path}.")
    return 0


def _cmd_doctor(_args) -> int:
    availability = detect_backends()
    print("DDC/CI backend:")
    print(f"  available: {availability.ddc}")
    print(f"  detail:    {availability.ddc_reason}")
    print("Software gamma:")
    print(f"  available: {availability.gamma}")
    print(f"  detail:    {availability.gamma_reason}")
    print("Session:")
    print(f"  type:      {os.environ.get('XDG_SESSION_TYPE', 'unknown')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lumenctl", description="Guided display calibration"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("detect", help="list monitors and their capabilities").set_defaults(
        func=_cmd_detect
    )
    sub.add_parser("doctor", help="report which backends are available").set_defaults(
        func=_cmd_doctor
    )

    get_parser = sub.add_parser("get", help="read a VCP value")
    get_parser.add_argument("code", help="the VCP code, for example 0x10")
    get_parser.set_defaults(func=_cmd_get)

    set_parser = sub.add_parser("set", help="write a VCP value")
    set_parser.add_argument("code", help="the VCP code, for example 0x10")
    set_parser.add_argument("value", type=int, help="the new value")
    set_parser.set_defaults(func=_cmd_set)

    profiles = sub.add_parser("profiles", help="manage saved profiles")
    profiles.add_argument("action", choices=["list", "show", "apply", "rm"])
    profiles.add_argument("name", nargs="?", default="")
    profiles.set_defaults(func=_cmd_profiles)

    capture = sub.add_parser("capture", help="record the current display state")
    capture.add_argument("--output", default="", help="where to write the snapshot")
    capture.set_defaults(func=_cmd_capture)

    restore = sub.add_parser("restore", help="put a captured state back")
    restore.add_argument("path", help="the snapshot file")
    restore.set_defaults(func=_cmd_restore)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_error:
        # argparse exits with 0 for --help and 2 for a bad command.
        return int(exit_error.code or 0)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except DdcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - a last resort
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
