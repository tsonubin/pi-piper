from __future__ import annotations

from pi_piper.config import Config
from pi_piper.sinks.base import AudioSink, SinkError


def build_sink(cfg: Config) -> AudioSink:
    backend = cfg.backend.lower()
    if backend == "bluetooth":
        from pi_piper.sinks.bluetooth import BluetoothSink
        return BluetoothSink(cfg)
    if backend == "airplay":
        from pi_piper.sinks.airplay import AirPlaySink
        return AirPlaySink(cfg)
    if backend == "sonos":
        from pi_piper.sinks.sonos import SonosSink
        return SonosSink(cfg)
    raise SinkError(f"unknown backend: {cfg.backend!r}")


__all__ = ["AudioSink", "SinkError", "build_sink"]
