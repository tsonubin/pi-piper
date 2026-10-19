from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SYSTEM_CONFIG = Path("/etc/pi-piper/config.yaml")
DEFAULT_USER_CONFIG = Path.home() / ".config" / "pi-piper" / "config.yaml"
DEFAULT_STATE_DIR = Path("/var/lib/pi-piper")


@dataclass
class InputConfig:
    device: str = "default"
    rate: int = 44100
    channels: int = 2
    format: str = "S16_LE"
    period_size: int = 1024


@dataclass
class BluetoothConfig:
    adapter: str = "hci0"
    target_mac: str | None = None
    auto_reconnect: bool = True


@dataclass
class AirPlayConfig:
    target_id: str | None = None
    target_name: str | None = None
    credentials: dict[str, str] = field(default_factory=dict)


@dataclass
class SonosConfig:
    target_uid: str | None = None
    target_name: str | None = None
    stream_port: int = 8973
    stream_codec: str = "wav"


@dataclass
class Config:
    backend: str = "bluetooth"
    log_level: str = "INFO"
    input: InputConfig = field(default_factory=InputConfig)
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    airplay: AirPlayConfig = field(default_factory=AirPlayConfig)
    sonos: SonosConfig = field(default_factory=SonosConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or _resolve_config_path()
        if not path.exists():
            return cls()
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Config:
        cfg = cls()
        if "backend" in data:
            cfg.backend = data["backend"]
        if "log_level" in data:
            cfg.log_level = data["log_level"]
        if "input" in data:
            cfg.input = InputConfig(**{**asdict(cfg.input), **(data["input"] or {})})
        if "bluetooth" in data:
            cfg.bluetooth = BluetoothConfig(
                **{**asdict(cfg.bluetooth), **(data["bluetooth"] or {})}
            )
        if "airplay" in data:
            cfg.airplay = AirPlayConfig(**{**asdict(cfg.airplay), **(data["airplay"] or {})})
        if "sonos" in data:
            cfg.sonos = SonosConfig(**{**asdict(cfg.sonos), **(data["sonos"] or {})})
        return cfg

    def save(self, path: Path | None = None) -> Path:
        path = path or _resolve_config_path(prefer_writable=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(asdict(self), fh, sort_keys=False)
        return path


def _resolve_config_path(prefer_writable: bool = False) -> Path:
    env = os.environ.get("PI_PIPER_CONFIG")
    if env:
        return Path(env)
    # An existing user config overrides the system one (XDG-style
    # precedence). Reads and writes must agree: mutating CLI commands save
    # without root, so a save that lands in the user file while loads keep
    # preferring /etc would silently discard every change.
    if DEFAULT_USER_CONFIG.exists():
        return DEFAULT_USER_CONFIG
    if DEFAULT_SYSTEM_CONFIG.exists() and not prefer_writable:
        return DEFAULT_SYSTEM_CONFIG
    if prefer_writable and os.access("/etc/pi-piper", os.W_OK):
        return DEFAULT_SYSTEM_CONFIG
    return DEFAULT_USER_CONFIG


def state_dir() -> Path:
    env = os.environ.get("PI_PIPER_STATE_DIR")
    if env:
        p = Path(env)
    elif os.access(DEFAULT_STATE_DIR.parent, os.W_OK):
        p = DEFAULT_STATE_DIR
    else:
        p = Path.home() / ".local" / "state" / "pi-piper"
    p.mkdir(parents=True, exist_ok=True)
    return p
