from __future__ import annotations

from pathlib import Path

from pi_piper.audio.capture import AlsaCapture
from pi_piper.config import InputConfig


def test_stop_closes_capture_stdout_pipe(
    tmp_path: Path, monkeypatch
) -> None:
    fake_arecord = tmp_path / "arecord"
    fake_arecord.write_text("#!/bin/sh\ncat /dev/zero\n", encoding="utf-8")
    fake_arecord.chmod(0o755)
    monkeypatch.setattr("pi_piper.audio.capture.shutil.which", lambda _name: str(fake_arecord))

    capture = AlsaCapture(InputConfig())
    pcm = capture.start()
    assert not pcm.closed

    capture.stop()

    assert pcm.closed
    assert not capture.is_running()
    capture.stop()  # idempotent after the process and pipe are released
