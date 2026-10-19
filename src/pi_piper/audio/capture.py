from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import IO

from pi_piper.config import InputConfig

log = logging.getLogger(__name__)


@dataclass
class CaptureDevice:
    card: int
    device: int
    card_name: str
    device_name: str

    @property
    def hw(self) -> str:
        return f"hw:{self.card},{self.device}"

    @property
    def plughw(self) -> str:
        return f"plughw:{self.card},{self.device}"


def list_capture_devices() -> list[CaptureDevice]:
    arecord = shutil.which("arecord")
    if not arecord:
        raise FileNotFoundError("arecord not found; install alsa-utils")
    out = subprocess.run(
        [arecord, "-l"], capture_output=True, text=True, check=False
    ).stdout
    devices: list[CaptureDevice] = []
    pattern = re.compile(
        r"card (\d+): (\S+).*?device (\d+): (\S+)", re.IGNORECASE
    )
    for line in out.splitlines():
        m = pattern.search(line)
        if m:
            devices.append(
                CaptureDevice(
                    card=int(m.group(1)),
                    card_name=m.group(2),
                    device=int(m.group(3)),
                    device_name=m.group(4),
                )
            )
    return devices


class AlsaCapture:
    """Spawn `arecord` and expose its stdout as a raw PCM stream."""

    def __init__(self, cfg: InputConfig):
        self.cfg = cfg
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> IO[bytes]:
        if self._proc is not None:
            raise RuntimeError("capture already running")
        arecord = shutil.which("arecord")
        if not arecord:
            raise FileNotFoundError("arecord not found; install alsa-utils")
        cmd = [
            arecord,
            "-q",
            "-D", self.cfg.device,
            "-c", str(self.cfg.channels),
            "-r", str(self.cfg.rate),
            "-f", self.cfg.format,
            "--period-size", str(self.cfg.period_size),
            "-t", "raw",
        ]
        log.info("starting capture: %s", " ".join(cmd))
        # stderr inherited: chatty ALSA warnings otherwise fill an undrained
        # pipe and can block arecord; inheriting also surfaces real capture
        # errors in the journal.
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
        assert self._proc.stdout is not None
        return self._proc.stdout

    def stop(self) -> None:
        if self._proc is None:
            return
        proc, self._proc = self._proc, None
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
