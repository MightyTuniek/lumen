"""Cancellable ArgyllCMS processes and instrument discovery.

A PTY is required: Argyll reads prompt responses from its terminal and
otherwise buffers progress. No shell commands or arbitrary prompt replies
are used. The caller owns all user interaction.
"""

from __future__ import annotations

import codecs
import os
import re
import select
import shutil
import signal
import subprocess
import time
import tty
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from lumen.model.colorimeter import Instrument, Progress, Reading
from lumen.engine.cancellation import Cancellation, Cancelled

TOOLS = ("spotread", "dispcal", "targen", "dispread", "colprof", "profcheck", "dispwin")
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


class ArgyllError(RuntimeError):
    pass



@dataclass(frozen=True)
class ToolAvailability:
    paths: dict[str, str]
    missing: tuple[str, ...]

    @property
    def available(self):
        return not self.missing

    @property
    def description(self):
        return "ArgyllCMS tools available" if self.available else "Install ArgyllCMS; missing: " + ", ".join(self.missing)


def availability() -> ToolAvailability:
    paths = {name: path for name in TOOLS if (path := shutil.which(name))}
    return ToolAvailability(paths, tuple(name for name in TOOLS if name not in paths))


def parse_instruments(output: str) -> list[Instrument]:
    instruments = []
    # Argyll usage lists communication ports below -c, before other options.
    section = re.search(r"(?ms)^\s*-c\s[^\n]*\n(.*?)(?=^\s*-\w|\Z)", output)
    if section:
        for match in re.finditer(r"(?m)^\s*(\d+)\s*=\s*['\"](.+?)['\"]\s*$", section[1]):
            name = match[2]
            if "usb" in name.lower() or not re.search(r"/dev/(tty|cu\.)", name):
                # USB bus/port enumeration is transient. Retain the instrument
                # description; identical devices remain an explicit UI choice.
                identity = re.sub(r"^usb-[^:()]*:\s*", "", name, flags=re.I).strip()
                instruments.append(Instrument(identity, name, int(match[1])))
    return instruments


def parse_display_types(output: str) -> list[tuple[str, str]]:
    section = re.search(r"(?ms)^\s*-y\s[^\n]*\n(.*?)(?=^\s*-\w|\Z)", output)
    if not section:
        return []
    return [(m[1], m[2].strip().rstrip(",")) for m in re.finditer(
        r"(?m)(?:^|\s)([A-Za-z0-9])\s*=\s*(.*?)(?=\s*,?\s+[A-Za-z0-9]\s*=|$)", section[1])]


def parse_displays(output: str, outputs: list[str]) -> dict[str, int]:
    matches = {}
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)\s*=\s*['\"](.+)", line)
        if not match:
            continue
        found = [name for name in outputs if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", match[2])]
        if len(found) == 1 and not re.search(r"USB|serial|/dev/", line, re.I):
            matches.setdefault(found[0], []).append(int(match[1]))
    return {name: values[0] for name, values in matches.items() if len(values) == 1}


class ProcessRunner:
    def __init__(self, token: Cancellation | None = None, on_event=None, on_prompt=None):
        self.token = token or Cancellation()
        self.on_event = on_event or (lambda event: None)
        self.on_prompt = on_prompt

    def run(self, args: list[str], *, cwd: Path | None = None, timeout: float = 120,
            check: bool = True, interactive: bool = True, auto_read: bool = False,
            log: Path | None = None) -> str:
        self.token.check()
        master, slave = os.openpty()
        tty.setraw(slave)
        env = dict(os.environ, LC_ALL="C", LANG="C", ARGYLL_EXCLUDE_SERIAL_SCAN="/dev/ttyS0,/dev/ttyS1,/dev/ttyS2,/dev/ttyS3")
        transcript = ""
        pending = ""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        deadline = time.monotonic() + timeout
        process = None
        stream = None
        try:
            stream = log.open("a", encoding="utf-8") if log else None
            process = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave,
                                       cwd=cwd, env=env, start_new_session=True)
            os.close(slave)
            slave = None
            while True:
                self.token.check()
                if time.monotonic() > deadline:
                    raise ArgyllError(f"{Path(args[0]).name} timed out")
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        raw = os.read(master, 8192)
                    except OSError:
                        raw = b""
                    if not raw:
                        break
                    chunk = decoder.decode(raw).replace("\r", "\n")
                    if stream and stream.tell() < 4 * 1024 * 1024:
                        stream.write(chunk[:4 * 1024 * 1024 - stream.tell()])
                        stream.flush()
                    transcript = (transcript + chunk)[-262144:]
                    pending = (pending + chunk)[-8192:]
                    for line in chunk.splitlines():
                        if line.strip():
                            progress = re.search(r"(\d+)\s*(?:of|/)\s*(\d+)", line)
                            fraction = min(1.0, int(progress[1]) / int(progress[2])) if progress and int(progress[2]) else None
                            self.on_event(Progress(Path(args[0]).name, line.strip()[:500], fraction))
                    # Only act when a complete terminal prompt has arrived. Keep
                    # preceding lines so calibration instructions remain visible.
                    prompt = re.search(r"(?is)(?:press|hit)[^\n]{0,220}(?:continue|reading|start|exit|abort)[^\n]*", pending)
                    if interactive and prompt:
                        message = pending.strip()
                        if auto_read and "take a reading" in prompt[0].lower():
                            answer = " "
                        elif re.search(r"calibrat|place|position|instrument|sensor|diffuser", message, re.I):
                            if self.on_prompt is None:
                                raise ArgyllError("Instrument needs attention: " + message)
                            answer = self.on_prompt(message, self.token)
                            self.token.check()
                            if answer is False or answer is None:
                                raise Cancelled("Instrument prompt cancelled")
                            answer = " "
                        else:
                            raise ArgyllError("Unrecognized ArgyllCMS prompt: " + message)
                        os.write(master, answer.encode())
                        pending = ""
                    elif interactive and re.search(r"(?i)(?:press|hit)[^\n]{0,220}(?:key|return)[^\n]*:", pending):
                        raise ArgyllError("Unrecognized ArgyllCMS prompt: " + pending.strip())
                elif process.poll() is not None:
                    break
            process.wait(timeout=5)
            if check and process.returncode:
                raise ArgyllError(f"{Path(args[0]).name} failed ({process.returncode}):\n{transcript[-4000:]}")
            return transcript
        except OSError as error:
            raise ArgyllError(str(error)) from error
        finally:
            if process is not None:
                # Kill the group even if the parent exited: no surviving child
                # may write to the display after rollback.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if stream:
                stream.close()
            os.close(master)
            if slave is not None:
                os.close(slave)


class ArgyllBackend:
    def __init__(self, runner: ProcessRunner | None = None, paths=None):
        self.runner = runner or ProcessRunner()
        self.tools = paths if paths is not None else availability().paths
        self._calibrated = False

    def run(self, tool: str, *args: str, **kwargs):
        if tool not in self.tools:
            raise ArgyllError(f"Install ArgyllCMS: {tool} is missing")
        return self.runner.run([self.tools[tool], *args], **kwargs)

    def discover(self, outputs: list[str]):
        text = self.run("dispcal", "-?", check=False, interactive=False, timeout=30)
        section = re.search(r"(?ms)^\s*-d\s[^\n]*\n(.*?)(?=^\s*-\w|\Z)", text)
        self.display_listing = section[1].strip() if section else ""
        self.display_numbers = [int(m[1]) for m in re.finditer(r"(?m)^\s*(\d+)\s*=", self.display_listing)]
        return parse_instruments(text), parse_displays(text, outputs)

    def display_types(self, instrument: Instrument):
        text = self.run("spotread", f"-c{instrument.port}", "-?", check=False,
                        interactive=False, timeout=30)
        return parse_display_types(text)

    @staticmethod
    def instrument_args(instrument: Instrument, display_type: str = "", correction: str | None = None):
        args = [f"-c{instrument.port}"]
        if display_type:
            if not re.fullmatch(r"[A-Za-z0-9]", display_type):
                raise ArgyllError("Select a display type offered by the instrument")
            args.append(f"-y{display_type}")
        if correction:
            path = Path(correction)
            if path.suffix.lower() not in (".ccss", ".ccmx") or not path.is_file():
                raise ArgyllError("Choose a readable .ccss or .ccmx correction file")
            args.extend(["-X", str(path.resolve())])
        return args

    def measure(self, instrument: Instrument, display_type="", correction=None) -> Reading:
        args = ["-e", "-O", *self.instrument_args(instrument, display_type, correction)]
        if self._calibrated:
            args.append("-N")
        for _ in range(2):
            text = self.run("spotread", *args, auto_read=True, timeout=180)
            self._calibrated = True
            matches = list(re.finditer(r"XYZ\s*[:=]\s*(" + NUMBER + r")\s+(" + NUMBER + r")\s+(" + NUMBER + r")", text))
            if matches:
                return Reading(*(float(value) for value in matches[-1].groups()))
            # Some instruments use -O for calibration only on first invocation.
            if "-N" not in args:
                args.append("-N")
        raise ArgyllError("Instrument returned no XYZ measurement")
