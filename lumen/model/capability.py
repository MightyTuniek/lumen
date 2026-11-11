"""Parsing of MCCS / DDC-CI capability strings.

A monitor's capability string looks like:

    cmds(01 02 03 07 0C E3 F3)
    vcp(04 10 12 14(05 06 08 0B) 16 18 1A ... E0(00 04 05) ...)
    mccs_ver(2.2) mswhql(1) asset_eep(40)

The `vcp(...)` section lists the supported VCP codes. A code may carry a
parenthesised list of its supported values, which is how enumerated
controls (colour presets, input sources) declare their options.
"""

from __future__ import annotations

import re
from typing import Iterable

from .monitor import Capabilities
from .vcp_registry import Safety, VcpSpec, registry_for

_SECTION_START = re.compile(r"(\w+)\s*\(")


def _parse_codes(text: str) -> tuple[set[int], dict[int, tuple[int, ...]]]:
    """Parse a code list, honouring nested parentheses.

    A capability section looks like `10 12 14(05 06 08 0B) 16 18 1A`, so a
    code may carry a parenthesised list of its own values. Splitting on
    whitespace alone would break those values apart, so the text is
    scanned position by position.
    """
    codes: set[int] = set()
    values: dict[int, tuple[int, ...]] = {}

    length = len(text)
    index = 0
    while index < length:
        if text[index].isspace():
            index += 1
            continue

        # Read the code itself.
        start = index
        while index < length and not text[index].isspace() and text[index] != "(":
            index += 1
        head = text[start:index].strip()
        if not head:
            index += 1
            continue
        try:
            code = int(head, 16)
        except ValueError:
            continue
        codes.add(code)

        # Look past the whitespace for an opening parenthesis.
        probe = index
        while probe < length and text[probe].isspace():
            probe += 1
        if probe >= length or text[probe] != "(":
            continue

        # Find the matching closing parenthesis, counting nesting.
        depth = 0
        cursor = probe
        while cursor < length:
            if text[cursor] == "(":
                depth += 1
            elif text[cursor] == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if cursor >= length:
            break

        enumerated: list[int] = []
        for part in text[probe + 1 : cursor].split():
            try:
                enumerated.append(int(part, 16))
            except ValueError:
                continue
        if enumerated:
            values[code] = tuple(enumerated)
        index = cursor + 1

    return codes, values


def _sections(raw: str) -> list[tuple[str, str]]:
    """Split a capability string into its named sections.

    The `vcp(...)` section contains nested parentheses, so the sections are
    found by scanning rather than by matching a flat regular expression.
    """
    sections: list[tuple[str, str]] = []
    index = 0
    length = len(raw)
    while index < length:
        match = _SECTION_START.search(raw, index)
        if match is None:
            break
        name = match.group(1)
        depth = 1
        cursor = match.end()
        while cursor < length and depth:
            if raw[cursor] == "(":
                depth += 1
            elif raw[cursor] == ")":
                depth -= 1
            cursor += 1
        if depth == 0:
            sections.append((name, raw[match.end() : cursor - 1]))
            index = cursor
        else:
            break
    return sections


def parse_capabilities(raw: str) -> Capabilities:
    """Parse an MCCS capability string into a `Capabilities`."""
    codes: set[int] = set()
    values: dict[int, tuple[int, ...]] = {}
    commands: set[int] = set()
    mccs_version: str | None = None

    for name, body in _sections(raw or ""):
        name = name.lower()
        if name == "vcp":
            section_codes, section_values = _parse_codes(body)
            codes |= section_codes
            values.update(section_values)
        elif name == "cmds":
            section_codes, _ = _parse_codes(body)
            commands |= section_codes
        elif name == "mccs_ver":
            mccs_version = body.strip() or None

    return Capabilities(
        vcp_codes=frozenset(codes),
        vcp_values=values,
        commands=frozenset(commands),
        mccs_version=mccs_version,
        raw=raw or "",
    )


def capabilities_from_vcp_list(codes: Iterable[int]) -> Capabilities:
    """Build capabilities for a backend that can only report which codes exist."""
    return Capabilities(vcp_codes=frozenset(int(code) for code in codes))


def supported_specs(capabilities: Capabilities) -> list[VcpSpec]:
    """Registry specs for the codes a monitor advertises.

    Dangerous codes are excluded: they can blank the screen, power the
    monitor off or destroy the user's settings, so they never appear in
    the normal control list.
    """
    specs = registry_for(capabilities.vcp_codes)
    return [spec for spec in specs if spec.safety is not Safety.DANGEROUS]
