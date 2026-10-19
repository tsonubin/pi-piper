from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import struct
import threading
from pathlib import Path
from typing import IO

from pi_piper.config import Config, state_dir
from pi_piper.sinks.base import AudioSink, SinkError

log = logging.getLogger(__name__)


def _wav_header(rate: int, channels: int, bits: int = 16) -> bytes:
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


async def _scan_airplay(timeout: float = 5.0) -> list[dict[str, str]]:
    try:
        import pyatv
    except ImportError as e:
        raise SinkError("pyatv not installed") from e
    loop = asyncio.get_running_loop()
    atvs = await pyatv.scan(loop, timeout=timeout)
    out = []
    for atv in atvs:
        out.append(
            {
                "id": atv.identifier or "",
                "name": atv.name,
                "address": str(atv.address),
                "model": str(atv.device_info.model) if atv.device_info else "",
            }
        )
    return out


def scan_airplay(timeout: float = 5.0) -> list[dict[str, str]]:
    return asyncio.run(_scan_airplay(timeout))


class AirPlaySink(AudioSink):
    name = "airplay"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._writer_thread: threading.Thread | None = None
        self._fifo_path: Path | None = None
        self._stop = threading.Event()
        self._stream_done = threading.Event()
        self._airplay_future: concurrent.futures.Future[None] | None = None

    def _make_fifo(self) -> Path:
        fifo_dir = state_dir() / "airplay"
        fifo_dir.mkdir(parents=True, exist_ok=True)
        path = fifo_dir / f"stream-{os.getpid()}.wav"
        if path.exists():
            path.unlink()
        os.mkfifo(str(path))
        return path

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            loop.close()

    async def _start_async(self, fifo: Path) -> None:
        try:
            import pyatv
        except ImportError as e:
            raise SinkError("pyatv not installed") from e
        target_id = self.cfg.airplay.target_id
        target_name = self.cfg.airplay.target_name
        if not target_id and not target_name:
            raise SinkError(
                "no airplay target configured; run `pi-piper airplay scan` then "
                "`pi-piper airplay use <id>`"
            )
        loop = asyncio.get_running_loop()
        atvs = await pyatv.scan(loop, timeout=5.0)
        match = None
        for atv in atvs:
            if target_id and atv.identifier == target_id:
                match = atv
                break
            if target_name and atv.name == target_name:
                match = atv
                break
        if match is None:
            raise SinkError(f"airplay target not found (id={target_id} name={target_name})")
        for proto, creds in self.cfg.airplay.credentials.items():
            try:
                proto_enum = getattr(pyatv.const.Protocol, proto.capitalize())
                match.set_credentials(proto_enum, creds)
            except Exception as e:
                log.warning("could not apply credentials for %s: %s", proto, e)
        atv = await pyatv.connect(match, loop)
        try:
            log.info("streaming to airplay device %s", match.name)
            await atv.stream.stream_file(str(fifo))
        finally:
            atv.close()

    def start(self, pcm_stream: IO[bytes]) -> None:
        if self._loop_thread is not None:
            raise SinkError("sink already started")
        self._fifo_path = self._make_fifo()
        header = _wav_header(self.cfg.input.rate, self.cfg.input.channels)
        self._stop.clear()

        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()
        while self._loop is None:
            threading.Event().wait(0.01)

        async def _runner() -> None:
            try:
                await self._start_async(self._fifo_path)
            finally:
                self._stream_done.set()

        self._stream_done.clear()
        fut = asyncio.run_coroutine_threadsafe(_runner(), self._loop)

        def _writer() -> None:
            assert self._fifo_path is not None
            with open(self._fifo_path, "wb") as f:
                f.write(header)
                f.flush()
                while not self._stop.is_set():
                    chunk = pcm_stream.read(4096)
                    if not chunk:
                        break
                    try:
                        f.write(chunk)
                        f.flush()
                    except BrokenPipeError:
                        break

        self._writer_thread = threading.Thread(
            target=_writer, daemon=True, name="pi-piper-airplay-writer"
        )
        self._writer_thread.start()
        fut.add_done_callback(self._stream_finished)
        self._airplay_future = fut

    def _stream_finished(self, fut: concurrent.futures.Future[None]) -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            log.error("airplay stream failed: %s", exc)
        else:
            log.info("airplay stream ended")

    def stop(self) -> None:
        self._stop.set()
        if self._loop is not None:
            fut = self._airplay_future
            if fut is not None and not fut.done():
                # Cancel the stream task and wait for the coroutine to unwind
                # so its `finally: atv.close()` runs before the loop stops.
                self._loop.call_soon_threadsafe(fut.cancel)
                self._stream_done.wait(timeout=2)
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5)
        if self._fifo_path is not None and self._fifo_path.exists():
            try:
                self._fifo_path.unlink()
            except OSError:
                pass
        self._loop = None
        self._loop_thread = None
        self._writer_thread = None
        self._airplay_future = None
        self._fifo_path = None

    def is_alive(self) -> bool:
        if self._loop_thread is None or not self._loop_thread.is_alive():
            return False
        fut = self._airplay_future
        if fut is not None and fut.done():
            # Stream ended or failed: the pipeline is dead even though the
            # event loop thread is still parked in run_forever().
            return False
        return True
