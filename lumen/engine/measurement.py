"""CGATS interchange and independent verification chart construction."""

from __future__ import annotations

import math
import shlex
from pathlib import Path

from lumen.model.colorimeter import SampledCurves


def read_cgats(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    metadata, fields, rows = {}, [], []
    state = "header"
    for line in path.read_text(encoding="utf-8").splitlines():
        tokens = shlex.split(line, comments=True)
        if not tokens:
            continue
        if tokens[0] == "BEGIN_DATA_FORMAT":
            state = "fields"
        elif tokens[0] == "END_DATA_FORMAT":
            state = "header"
        elif tokens[0] == "BEGIN_DATA":
            state = "data"
        elif tokens[0] == "END_DATA":
            break
        elif state == "fields":
            fields.extend(tokens)
        elif state == "data":
            if len(tokens) != len(fields):
                raise ValueError("Invalid CGATS row length")
            rows.append(dict(zip(fields, tokens)))
        elif len(tokens) >= 2:
            metadata[tokens[0]] = tokens[1]
    if not rows or len(rows) != int(metadata.get("NUMBER_OF_SETS", "-1")):
        raise ValueError("Missing or truncated CGATS data")
    if len(fields) != int(metadata.get("NUMBER_OF_FIELDS", "-1")):
        raise ValueError("Invalid CGATS fields")
    return metadata, rows


def load_calibration(path: Path) -> SampledCurves:
    metadata, rows = read_cgats(path)
    if metadata.get("DEVICE_CLASS") != "DISPLAY" or metadata.get("COLOR_REP") != "RGB":
        raise ValueError("Expected an RGB display calibration")
    if metadata.get("VIDEO_LUT_CALIBRATION_POSSIBLE") == "NO":
        raise ValueError("ArgyllCMS could not calibrate the video LUT")
    if metadata.get("TV_OUTPUT_ENCODING", "NO") != "NO":
        raise ValueError("Video-range calibration is unsupported")
    knots = [float(row["RGB_I"]) for row in rows]
    if any(not math.isfinite(v) for v in knots) or abs(knots[0]) > 1e-6 or abs(knots[-1] - 1) > 1e-6:
        raise ValueError("Calibration must cover the full input range")
    if any(abs(value - i / (len(knots) - 1)) > 1e-5 for i, value in enumerate(knots)):
        raise ValueError("Calibration input samples must be evenly spaced")
    return SampledCurves(*(tuple(float(row[key]) for row in rows) for key in ("RGB_R", "RGB_G", "RGB_B")))


def verification_patches() -> list[tuple[float, float, float]]:
    patches = [(i / 20,)*3 for i in range(21)]
    patches += [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1)]
    def radical(index, base):
        value, fraction = 0.0, 1.0
        while index:
            fraction /= base
            index, digit = divmod(index, base)
            value += digit * fraction
        return value
    patches += [tuple(0.05 + 0.9 * radical(i, base) for base in (2, 3, 5)) for i in range(1, 25)]
    return patches


def write_verification_chart(path: Path) -> None:
    patches = verification_patches()
    header = ('CTI1\nDESCRIPTOR "Lumen independent verification"\nCOLOR_REP "RGB"\n'
              'NUMBER_OF_FIELDS 4\nBEGIN_DATA_FORMAT\nSAMPLE_ID RGB_R RGB_G RGB_B\n'
              'END_DATA_FORMAT\nNUMBER_OF_SETS 51\nBEGIN_DATA\n')
    rows = [f"{i} " + " ".join(f"{value * 100:.8f}" for value in patch)
            for i, patch in enumerate(patches, 1)]
    path.write_text(header + "\n".join(rows) + "\nEND_DATA\n", encoding="utf-8")


def validate_icc(data: bytes) -> None:
    """Validate generated RGB display ICC structure and its table vcgt."""
    import struct
    if (len(data) < 132 or data[36:40] != b"acsp" or data[12:16] != b"mntr"
            or data[16:20] != b"RGB " or struct.unpack_from(">I", data)[0] != len(data)):
        raise ValueError("Generated file is not an RGB display ICC profile")
    count = struct.unpack_from(">I", data, 128)[0]
    if count > (len(data) - 132) // 12:
        raise ValueError("Truncated ICC tag table")
    vcgt = None
    for index in range(count):
        tag, offset, size = struct.unpack_from(">4sII", data, 132 + 12 * index)
        if offset < 132 + count * 12 or offset + size > len(data):
            raise ValueError("ICC tag points outside the profile")
        if tag == b"vcgt":
            vcgt = data[offset:offset + size]
    if vcgt is None or len(vcgt) < 18 or vcgt[:4] != b"vcgt":
        raise ValueError("Generated ICC profile has no calibration tag")
    kind = struct.unpack_from(">I", vcgt, 8)[0]
    if kind != 0:
        raise ValueError("Generated ICC calibration must contain sampled curves")
    channels, entries, width = struct.unpack_from(">HHH", vcgt, 12)
    if channels != 3 or entries < 2 or width not in (1, 2) or len(vcgt) < 18 + channels * entries * width:
        raise ValueError("Truncated ICC calibration curves")
