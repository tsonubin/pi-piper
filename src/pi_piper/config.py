from __future__ import annotations

import logging
import os
import stat
import tempfile
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

DEFAULT_SYSTEM_CONFIG = Path("/etc/pi-piper/config.yaml")
DEFAULT_USER_CONFIG = Path.home() / ".config" / "pi-piper" / "config.yaml"
DEFAULT_STATE_DIR = Path("/var/lib/pi-piper")


class ConfigError(RuntimeError):
    """The config file exists but cannot be parsed."""


@dataclass
class InputConfig:
    device: str = "default"
    rate: int = 44100
    channels: int = 2
    format: str = "S16_LE"
    period_size: int = 1024


@dataclass
class BluetoothConfig:
    target_mac: str | None = None


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
            try:
                data = yaml.safe_load(fh) or {}
            except yaml.YAMLError as e:
                raise ConfigError(f"{path}: invalid YAML: {e}") from e
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Config:
        cfg = cls()
        if not isinstance(data, dict):
            log.warning("config root must be a mapping; using defaults")
            return cfg
        for key, value in data.items():
            if key == "backend":
                cfg.backend = value if isinstance(value, str) else str(value)
            elif key == "log_level":
                if isinstance(value, str):
                    cfg.log_level = value
                else:
                    log.warning(
                        "config log_level: expected a string, got %r; using default %r",
                        value,
                        cfg.log_level,
                    )
            elif key in ("input", "bluetooth", "airplay", "sonos"):
                _apply_section(getattr(cfg, key), value, key)
            else:
                log.warning("ignoring unknown config key %r", key)
        return cfg

    def save(self, path: Path | None = None) -> Path:
        path = path or _resolve_config_path(prefer_writable=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        # Atomic replace: write to a sibling temp file, then os.replace() it
        # over the target. A crash mid-write must never leave a truncated
        # config (the next load would then fail, possibly with credentials
        # half-written). Same directory keeps the rename on one filesystem.
        # Each writer owns its temporary file. A shared .tmp name lets one
        # concurrent save unlink or replace another save's in-flight data.
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
        tmp = Path(tmp_name)
        try:
            # mkstemp creates the file exclusively with mode 0600.
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump(asdict(self), fh, sort_keys=False)
            if created:
                # AirPlay credentials end up in this file: keep user configs
                # private. Files under /etc stay world-readable because the
                # service user has to read them.
                mode = 0o644 if path.is_relative_to(Path("/etc")) else 0o600
            else:
                mode = stat.S_IMODE(path.stat().st_mode)
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return path


def _apply_section(section: Any, data: Any, label: str) -> None:
    if data is None:
        return
    if not isinstance(data, dict):
        log.warning("ignoring config section %r: expected a mapping", label)
        return
    known = {f.name for f in fields(section)}
    for key, value in data.items():
        if key not in known:
            log.warning("ignoring unknown config key %s.%s", label, key)
            continue
        setattr(section, key, _coerce(section, key, value, label))


def _coerce(section: Any, key: str, value: Any, label: str) -> Any:
    """Coerce hand-edited YAML scalars to the field's declared type.

    A wrong-typed value used to reach runtime as-is and crash the daemon
    mid-run (an int target_mac kills the subprocess spawn with TypeError,
    which escapes the retry loop and crash-loops the systemd unit). Instead
    coerce what is sane and fall back to the default with a warning, so the
    appliance still starts.
    """
    f = next(f for f in fields(section) if f.name == key)
    ftype = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", "")
    default = f.default if f.default is not MISSING else f.default_factory()

    def _fallback(reason: str) -> Any:
        log.warning(
            "config %s.%s: %s, got %r; using default %r", label, key, reason, value, default
        )
        return default

    if ftype.startswith("dict"):
        if isinstance(value, dict):
            return value
        return _fallback("expected a mapping")
    if ftype == "int":
        if isinstance(value, bool):
            return _fallback("expected a number")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                pass
        return _fallback("expected a number")
    if ftype.startswith("str"):
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return _fallback("expected a string")
        if isinstance(value, int | float):
            return str(value)
        return _fallback("expected a string")
    return value


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
