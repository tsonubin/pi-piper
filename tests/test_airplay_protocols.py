"""AirPlay credential protocol names are user input; they must resolve to
pyatv Protocol members in any case (README documents `credentials raop`)."""

from __future__ import annotations

import pytest
from pyatv.const import Protocol

from pi_piper.sinks.airplay import _protocol_enum
from pi_piper.sinks.base import SinkError


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("raop", Protocol.RAOP),
        ("RAOP", Protocol.RAOP),
        ("airplay", Protocol.AirPlay),
        ("AirPlay", Protocol.AirPlay),
        ("mrp", Protocol.MRP),
        ("companion", Protocol.Companion),
    ],
)
def test_protocol_names_resolve_case_insensitively(given: str, expected: Protocol) -> None:
    assert _protocol_enum(given) is expected


def test_unknown_protocol_names_the_valid_ones() -> None:
    with pytest.raises(SinkError) as exc:
        _protocol_enum("hdmi")
    msg = str(exc.value)
    assert "raop" in msg and "airplay" in msg
