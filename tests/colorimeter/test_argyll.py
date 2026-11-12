import sys
import threading
import time
from pathlib import Path

import pytest

from lumen.backends.colorimeter.argyll import ArgyllBackend, ArgyllError, Cancellation, Cancelled, ProcessRunner, parse_displays, parse_display_types, parse_instruments
from lumen.model.colorimeter import Instrument

HELP = ''' -d n Choose the display
    1 = 'Monitor 1, Output DP-2 at 0, 0'
    2 = 'Monitor 2, Output HDMI-1 at 1920, 0'
 -c listno Choose instrument
    1 = 'usb-1: X-Rite i1Display Pro'
    2 = '/dev/ttyS0'
 -y type Display type
    l = LCD White LED
    o = OLED
 -t temp White point
'''


def test_discovery_matches_names_not_order():
    assert parse_displays(HELP, ['HDMI-1', 'DP-2']) == {'DP-2': 1, 'HDMI-1': 2}
    assert [i.name for i in parse_instruments(HELP)] == ['usb-1: X-Rite i1Display Pro']
    assert parse_display_types(HELP) == [('l', 'LCD White LED'), ('o', 'OLED')]
    assert parse_displays(HELP + "    3 = 'Output DP-2'\n", ['DP-2']) == {}


def test_runner_handles_fragmented_prompt_without_newline():
    events, prompts = [], []
    script = "import os,time;os.write(1,b'Place instrument on screen.\\nHit any key to con');time.sleep(.05);os.write(1,b'tinue:');os.read(0,1);print('Patch 2 of 4')"
    runner = ProcessRunner(on_event=events.append, on_prompt=lambda text, token: prompts.append(text) or True)
    text = runner.run([sys.executable, '-u', '-c', script], timeout=2)
    assert len(prompts) == 1
    assert 'Patch 2 of 4' in text
    assert any(e.fraction == 0.5 for e in events)


def test_runner_does_not_answer_unknown_prompts():
    script = "print('Hit any key to erase: ',end='',flush=True);input()"
    with pytest.raises(ArgyllError, match='Unrecognized'):
        ProcessRunner().run([sys.executable, '-u', '-c', script], timeout=2)


def test_runner_timeout_and_cancel_stop_process(tmp_path):
    marker = tmp_path / 'late-write'
    script = "import time,pathlib;time.sleep(.5);pathlib.Path(" + repr(str(marker)) + ").touch()"
    with pytest.raises(ArgyllError, match='timed out'):
        ProcessRunner().run([sys.executable, '-c', script], timeout=.1)
    token = Cancellation()
    timer = threading.Timer(.1, token.cancel)
    timer.start()
    with pytest.raises(Cancelled):
        ProcessRunner(token).run([sys.executable, '-c', script], timeout=2)
    timer.join()
    time.sleep(.6)
    assert not marker.exists()


def test_runner_reports_exit_error_and_keeps_bounded_output():
    with pytest.raises(ArgyllError, match='permission denied'):
        ProcessRunner().run([sys.executable, '-c', "import sys;print('permission denied');sys.exit(2)"])
    result = ProcessRunner().run([sys.executable, '-c', "print('x'*300000)"], interactive=False)
    assert len(result) <= 262144


def test_measurement_requires_real_xyz():
    class Runner:
        def __init__(self):
            self.calls = []
        def run(self, args, **kwargs):
            self.calls.append(args)
            return 'Result is XYZ: 95.05 100.0 108.9'
    runner = Runner()
    backend = ArgyllBackend(runner, {'spotread': '/fake/spotread'})
    instrument = Instrument('id', 'i1', 2)
    assert backend.measure(instrument).y == 100
    backend.measure(instrument)
    assert '-N' not in runner.calls[0] and '-N' in runner.calls[1]
    assert '-c2' in runner.calls[0]


def test_correction_is_validated_before_measurement(tmp_path):
    with pytest.raises(ArgyllError):
        ArgyllBackend.instrument_args(Instrument('id', 'i1', 1), correction=str(tmp_path / 'missing.ccss'))
