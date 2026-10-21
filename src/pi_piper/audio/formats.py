"""ALSA sample-format names, mapped for playback tools and WAV headers.

Config values are hand-edited, so both spellings of each format are
accepted ("S16_LE" and "S16LE"); matching is case-insensitive.
"""

from __future__ import annotations

import struct

_ALSA_BITS = {
    "S8": 8,
    "U8": 8,
    "S16LE": 16,
    "S16BE": 16,
    "S24LE": 24,
    "S24BE": 24,
    "S243LE": 24,
    "S243BE": 24,
    "S32LE": 32,
    "S32BE": 32,
}

#: PulseAudio/PipeWire sample-format names for `paplay --format=`. ALSA's
#: S24_LE is 24-bit in a 32-bit container (Pulse "s24-32le"); ALSA's packed
#: 3-byte S24_3LE is Pulse "s24le".
_PAPLAY_NAMES = {
    "S8": "s8",
    "U8": "u8",
    "S16LE": "s16le",
    "S16BE": "s16be",
    "S24LE": "s24-32le",
    "S24BE": "s24-32be",
    "S243LE": "s24le",
    "S243BE": "s24be",
    "S32LE": "s32le",
    "S32BE": "s32be",
}


#: Canonical spellings for error messages.
_KNOWN_FORMATS = (
    "S8, U8, S16_LE, S16_BE, S24_LE, S24_BE, S24_3LE, S24_3BE, S32_LE, S32_BE"
)


def _normalize(fmt: str) -> str:
    return fmt.strip().upper().replace("_", "")


def alsa_format_bits(fmt: str) -> int:
    """Bits per sample of an ALSA format name (e.g. "S16_LE" -> 16)."""
    try:
        return _ALSA_BITS[_normalize(fmt)]
    except KeyError:
        raise ValueError(
            f"unsupported ALSA format {fmt!r}; known: {_KNOWN_FORMATS}"
        ) from None


def is_s16le(fmt: str) -> bool:
    """True when fmt is S16_LE, the only layout a PCM WAV header describes here."""
    return _normalize(fmt) == "S16LE"


def alsa_format_to_paplay(fmt: str) -> str:
    """Map an ALSA format name to the paplay/PulseAudio format name."""
    try:
        return _PAPLAY_NAMES[_normalize(fmt)]
    except KeyError:
        raise ValueError(
            f"unsupported ALSA format {fmt!r}; known: {_KNOWN_FORMATS}"
        ) from None


def wav_header(rate: int, channels: int, bits: int = 16) -> bytes:
    """Streaming WAV header: RIFF/data sizes are 0xFFFFFFFF (unknown length),
    the accepted hack for endless WAV streams."""
    byte_rate = rate * channels * bits // 8
    block_align = channels * bits // 8
    return (
        b"RIFF"
        + struct.pack("<I", 0xFFFFFFFF)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack("<H", 1)
        + struct.pack("<H", channels)
        + struct.pack("<I", rate)
        + struct.pack("<I", byte_rate)
        + struct.pack("<H", block_align)
        + struct.pack("<H", bits)
        + b"data"
        + struct.pack("<I", 0xFFFFFFFF)
    )
