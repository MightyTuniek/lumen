from types import SimpleNamespace

import pytest

from lumen.backends.colorimeter.colord import ColordBackend, IccError
from .conftest import icc_bytes


class Device:
    def __init__(self):
        self.default = 'old'
        self.profiles = ['old']
        self.inhibited = False
        self.fail = False
    def GetProfileForQualifiers(self, qualifiers):
        return self.default
    def ProfilingInhibit(self):
        self.inhibited = True
    def ProfilingUninhibit(self):
        self.inhibited = False
    def AddProfile(self, relation, profile):
        self.profiles.append(profile)
    def MakeProfileDefault(self, profile):
        if self.fail and profile != 'old':
            raise RuntimeError('permission denied')
        self.default = profile
    def RemoveProfile(self, profile):
        self.profiles.remove(profile)


def backend():
    device = Device()
    b = ColordBackend.__new__(ColordBackend)
    b.inhibited = set()
    b._dbus = SimpleNamespace(types=SimpleNamespace(UnixFd=lambda fd: fd))
    def missing(*args):
        raise RuntimeError('missing')
    b.manager = SimpleNamespace(GetDevicesByKind=lambda _: ['device'], FindProfileById=missing,
                                CreateProfileWithFd=lambda *args: 'new')
    b.device = lambda path: device
    b.properties = lambda *args: {'Metadata': {'XRANDR_name': 'DP-1', 'OutputEdidMd5': 'abc'}}
    return b, device


def test_colord_identity_and_inhibition():
    b, device = backend()
    assert b.find_device('DP-1', 'abc') == 'device'
    assert b.find_device('DP-1', 'different') is None
    assert b.find_device('DP-2') is None
    b.inhibit('device')
    assert device.inhibited
    b.uninhibit('device')
    assert not device.inhibited


def test_colord_installs_persistent_profile_and_verifies_default(tmp_path):
    b, device = backend()
    path = tmp_path / 'display.icc'
    path.write_bytes(icc_bytes())
    assert b.install('device', path) == 'new'
    assert device.default == 'new'
    b.restore('device', 'old', 'new')
    assert device.default == 'old' and device.profiles == ['old']


def test_colord_assignment_failure_preserves_previous_default(tmp_path):
    b, device = backend()
    device.fail = True
    path = tmp_path / 'display.icc'
    path.write_bytes(icc_bytes())
    with pytest.raises(IccError, match='permission denied'):
        b.install('device', path)
    assert device.default == 'old' and device.profiles == ['old']
