import struct
from pathlib import Path

import pytest

from lumen.backends.colorimeter.argyll import Cancellation


class FastCancellation(Cancellation):
    def wait(self, seconds):
        self.check()


def icc_bytes():
    tag = b'vcgt' + b'\0' * 8 + struct.pack('>HHH', 3, 2, 2) + struct.pack('>6H', 0, 65535, 0, 65535, 0, 65535)
    data = bytearray(144 + len(tag))
    struct.pack_into('>I', data, 0, len(data))
    data[12:16], data[16:20], data[36:40] = b'mntr', b'RGB ', b'acsp'
    struct.pack_into('>I', data, 128, 1)
    struct.pack_into('>4sII', data, 132, b'vcgt', 144, len(tag))
    data[144:] = tag
    return bytes(data)


CAL = '''CAL
DEVICE_CLASS "DISPLAY"
COLOR_REP "RGB"
VIDEO_LUT_CALIBRATION_POSSIBLE "YES"
NUMBER_OF_FIELDS 4
BEGIN_DATA_FORMAT
RGB_I RGB_R RGB_G RGB_B
END_DATA_FORMAT
NUMBER_OF_SETS 3
BEGIN_DATA
0 0 0 0
0.5 0.45 0.5 0.55
1 0.95 1 0.98
END_DATA
'''


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path / 'runtime'))
