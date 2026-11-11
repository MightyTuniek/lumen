"""Guard: the portable core must never import platform-specific modules.

If model/ or engine/ gains an `import dbus`, the project can no longer be
ported to Windows or macOS without rewriting the core. This test is the
mechanical enforcement of that rule.
"""

import ast
import pathlib

import pytest

FORBIDDEN = {"gi", "dbus", "Xlib", "ctypes"}
PORTABLE_PACKAGES = ("model", "engine")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("package", PORTABLE_PACKAGES)
def test_portable_packages_do_not_import_platform_modules(package: str) -> None:
    offenders: list[str] = []
    for path in (ROOT / "lumen" / package).rglob("*.py"):
        bad = _imported_modules(path) & FORBIDDEN
        if bad:
            offenders.append(f"{path.relative_to(ROOT)} imports {sorted(bad)}")
    assert not offenders, (
        "platform imports leaked into the portable core:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("package", PORTABLE_PACKAGES)
def test_portable_packages_import_without_display_hardware(package: str) -> None:
    """The core must be importable on a machine with no display at all."""
    import importlib
    import pkgutil

    module = importlib.import_module(f"lumen.{package}")
    for info in pkgutil.iter_modules(module.__path__, f"lumen.{package}."):
        importlib.import_module(info.name)
