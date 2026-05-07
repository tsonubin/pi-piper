from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import IO

from pi_piper.config import Config
from pi_piper.sinks.base import AudioSink, SinkError

log = logging.getLogger(__name__)


@dataclass
class BluetoothDevice:
    mac: str
    name: str
    paired: bool = False
    connected: bool = False


class BluetoothCtl:
    """Thin wrapper over `bluetoothctl` for pairing/connection from the CLI."""

    def __init__(self, adapter: str = "hci0"):
        self.adapter = adapter
        if not shutil.which("bluetoothctl"):
            raise SinkError("bluetoothctl not found; install bluez")

    def _run(self, *args: str, timeout: float = 10.0) -> str:
        cmd = ["bluetoothctl", *args]
        log.debug("bluetoothctl: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return result.stdout

    def power_on(self) -> None:
        self._run("power", "on")
        self._run("agent", "on")
        self._run("default-agent")

    def scan(self, duration: float = 8.0) -> list[BluetoothDevice]:
        self.power_on()
        proc = subprocess.Popen(
            ["bluetoothctl", "--timeout", str(int(duration)), "scan", "on"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=duration + 5)
        return self.list_devices()

    def list_devices(self) -> list[BluetoothDevice]:
        out = self._run("devices")
        devices: list[BluetoothDevice] = []
        for line in out.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) >= 3 and parts[0] == "Device":
                devices.append(BluetoothDevice(mac=parts[1], name=parts[2]))
        for dev in devices:
            info = self._run("info", dev.mac)
            dev.paired = "Paired: yes" in info
            dev.connected = "Connected: yes" in info
        return devices

    def pair(self, mac: str, timeout: float = 60.0) -> None:
        self.power_on()
        self._run("trust", mac)
        out = self._run("pair", mac, timeout=timeout)
        if "Failed" in out and "AlreadyExists" not in out:
            raise SinkError(f"pair failed: {out.strip()}")

    def connect(self, mac: str, timeout: float = 30.0) -> None:
        out = self._run("connect", mac, timeout=timeout)
        if "Connection successful" not in out and "Already connected" not in out:
            for _ in range(int(timeout)):
                if "Connected: yes" in self._run("info", mac):
                    return
                time.sleep(1)
            raise SinkError(f"connect failed: {out.strip()}")

    def disconnect(self, mac: str) -> None:
        self._run("disconnect", mac)


def _pactl_find_bluez_sink(mac: str) -> str | None:
    if not shutil.which("pactl"):
        return None
    out = subprocess.run(
        ["pactl", "list", "short", "sinks"],
        capture_output=True, text=True, check=False,
    ).stdout
    needle = mac.replace(":", "_").lower()
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and "bluez" in fields[1].lower() and needle in fields[1].lower():
            return fields[1]
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and "bluez" in fields[1].lower():
            return fields[1]
    return None


class BluetoothSink(AudioSink):
    name = "bluetooth"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self._proc: subprocess.Popen[bytes] | None = None
        self.ctl = BluetoothCtl(cfg.bluetooth.adapter)

    def _ensure_connected(self) -> str:
        mac = self.cfg.bluetooth.target_mac
        if not mac:
            raise SinkError(
                "no bluetooth target configured; run `pi-piper bluetooth pair <MAC>`"
            )
        info = self.ctl._run("info", mac)
        if "Connected: yes" not in info:
            log.info("connecting to %s", mac)
            self.ctl.connect(mac)
        return mac

    def start(self, pcm_stream: IO[bytes]) -> None:
        if self._proc is not None:
            raise SinkError("sink already started")
        if not shutil.which("paplay"):
            raise SinkError("paplay not found; install pulseaudio-utils or pipewire-pulse")
        mac = self._ensure_connected()
        sink = None
        for _ in range(20):
            sink = _pactl_find_bluez_sink(mac)
            if sink:
                break
            time.sleep(0.5)
        if not sink:
            raise SinkError(
                f"no PulseAudio/PipeWire sink found for {mac}; "
                "is pipewire-pulse or pulseaudio running?"
            )
        log.info("routing audio to %s", sink)
        cmd = [
            "paplay",
            f"--device={sink}",
            "--raw",
            f"--rate={self.cfg.input.rate}",
            f"--channels={self.cfg.input.channels}",
            "--format=s16le",
        ]
        self._proc = subprocess.Popen(cmd, stdin=pcm_stream, bufsize=0)

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

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
