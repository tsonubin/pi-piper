from __future__ import annotations

from abc import ABC, abstractmethod
from typing import IO

from pi_piper.config import Config


class SinkError(RuntimeError):
    pass


class AudioSink(ABC):
    """One-way pipe: takes a raw PCM stream and pushes it to a remote device."""

    name: str = "sink"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @abstractmethod
    def start(self, pcm_stream: IO[bytes]) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def is_alive(self) -> bool:
        ...
