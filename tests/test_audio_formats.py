"""ALSA format names drive paplay flags and WAV headers; wrong mappings
mean garbage audio, so both directions are pinned here."""

from __future__ import annotations

import io
import struct

import pytest

from pi_piper.audio.formats import alsa_format_bits, alsa_format_to_paplay, wav_header
from pi_piper.config import Config
from pi_piper.sinks.airplay import AirPlaySink
from pi_piper.sinks.base import SinkError
from pi_piper.sinks.sonos import SonosSink


@pytest.mark.parametrize(
    ("fmt", "bits"),
    [
        ("S16_LE", 16),
        ("S16LE", 16),  # compact spelling is accepted
        ("s16_le", 16),  # case-insensitive
        ("S8", 8),
        ("U8", 8),
        ("S24_LE", 24),
        ("S24_3LE", 24),
        ("S32_LE", 32),
    ],
)
def test_alsa_format_bits(fmt: str, bits: int) -> None:
    assert alsa_format_bits(fmt) == bits


def test_unknown_format_is_rejected_with_known_list() -> None:
    with pytest.raises(ValueError, match="S16_LE"):
        alsa_format_bits("FLOAT32_LE")


@pytest.mark.parametrize(
    ("fmt", "paplay_name"),
    [
        ("S16_LE", "s16le"),
        ("S24_LE", "s24le"),
        ("S24_3LE", "s24_3le"),
        ("S32_LE", "s32le"),
        ("U8", "u8"),
    ],
)
def test_alsa_format_to_paplay(fmt: str, paplay_name: str) -> None:
    assert alsa_format_to_paplay(fmt) == paplay_name


def test_wav_header_fields() -> None:
    h = wav_header(48000, 2, 16)
    assert len(h) == 44
    assert h[0:4] == b"RIFF" and h[8:12] == b"WAVE" and h[36:40] == b"data"
    assert struct.unpack("<H", h[22:24])[0] == 2  # channels
    assert struct.unpack("<I", h[24:28])[0] == 48000  # sample rate
    assert struct.unpack("<I", h[28:32])[0] == 48000 * 2 * 2  # byte rate
    assert struct.unpack("<H", h[32:34])[0] == 4  # block align
    assert struct.unpack("<H", h[34:36])[0] == 16  # bits
    # streaming stream: unknown RIFF/data sizes
    assert struct.unpack("<I", h[4:8])[0] == 0xFFFFFFFF
    assert struct.unpack("<I", h[40:44])[0] == 0xFFFFFFFF


def test_sonos_rejects_non_16bit_before_touching_network() -> None:
    cfg = Config()
    cfg.input.format = "S32_LE"
    with pytest.raises(SinkError, match="16-bit"):
        SonosSink(cfg).start(io.BytesIO(b""))


def test_airplay_rejects_non_16bit_before_spawning_threads(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("PI_PIPER_STATE_DIR", str(tmp_path))
    cfg = Config()
    cfg.input.format = "S24_LE"
    with pytest.raises(SinkError, match="16-bit"):
        AirPlaySink(cfg).start(io.BytesIO(b""))
