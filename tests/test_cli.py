import json

import pytest

from lumen.backends.fakes import FakeDdcBackend
from lumen.cli import main
from lumen.model.monitor import MonitorDescriptor

MONITOR = MonitorDescriptor(identifier="fake:0", model="VG240Y")


class FakeBackendFactory:
    def __init__(self):
        self.backend = FakeDdcBackend(
            monitors=[MONITOR],
            capabilities={0x10: (), 0x12: ()},
            values={0x10: (40, 100), 0x12: (50, 100)},
        )

    def __call__(self):
        return self.backend


@pytest.fixture
def fake_backend(monkeypatch):
    factory = FakeBackendFactory()
    monkeypatch.setattr("lumen.cli.make_ddc_backend", factory)
    monkeypatch.setattr("lumen.cli.make_gamma_backend", lambda: None)
    return factory.backend


def test_detect_prints_the_monitor(fake_backend, capsys):
    assert main(["detect"]) == 0
    out = capsys.readouterr().out
    assert "fake:0" in out


def test_doctor_reports_backends(fake_backend, capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "DDC" in out


def test_get_prints_a_value(fake_backend, capsys):
    assert main(["get", "0x10"]) == 0
    out = capsys.readouterr().out
    assert "40" in out


def test_set_writes_a_value(fake_backend, capsys):
    assert main(["set", "0x10", "70"]) == 0
    assert fake_backend.get(MONITOR, 0x10).value == 70


def test_set_rejects_a_value_outside_the_range(fake_backend, capsys):
    assert main(["set", "0x10", "500"]) != 0


def test_an_unknown_command_returns_a_failure(capsys):
    assert main(["nonsense"]) != 0


def test_no_command_prints_help(capsys):
    assert main([]) != 0
    assert "usage" in capsys.readouterr().out.lower()


def test_capture_writes_a_snapshot(tmp_path, fake_backend, monkeypatch, capsys):
    target = tmp_path / "snapshot.json"
    assert main(["capture", "--output", str(target)]) == 0
    assert target.exists()
    data = json.loads(target.read_text())
    assert data["ddc"]["10"] == 40


def test_capture_then_restore_puts_the_values_back(tmp_path, fake_backend, capsys):
    target = tmp_path / "snapshot.json"
    assert main(["capture", "--output", str(target)]) == 0
    assert main(["set", "0x10", "70"]) == 0
    assert fake_backend.get(MONITOR, 0x10).value == 70
    assert main(["restore", str(target)]) == 0
    assert fake_backend.get(MONITOR, 0x10).value == 40


def test_profiles_list_is_empty_when_there_are_none(
    tmp_path, monkeypatch, fake_backend, capsys
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["profiles", "list"]) == 0
    assert "No profiles" in capsys.readouterr().out


def test_profiles_list_shows_a_saved_profile(
    tmp_path, monkeypatch, fake_backend, capsys
):
    from lumen.model.profile import new_profile, save_profile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_profile(new_profile("Evening", MONITOR), tmp_path / "lumen" / "profiles")
    assert main(["profiles", "list"]) == 0
    assert "Evening" in capsys.readouterr().out


def test_profiles_apply_warns_about_a_monitor_mismatch(
    tmp_path, monkeypatch, fake_backend, capsys
):
    from lumen.model.monitor import MonitorDescriptor
    from lumen.model.profile import new_profile, save_profile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    other = MonitorDescriptor(identifier="other", model="Something Else")
    save_profile(
        new_profile("Elsewhere", other), tmp_path / "lumen" / "profiles"
    )
    assert main(["profiles", "apply", "Elsewhere"]) == 0
    assert "different monitor" in capsys.readouterr().err
