"""Contracts for the Sonos PCM fan-out: ordered delivery, bounded backlog,
and subscriber wakeup on stop()."""

from __future__ import annotations

import io
import threading
import time
from collections import deque

from pi_piper.sinks.sonos import _Broadcaster

CHUNK = 16
DEADLINE = 5.0


def _drain(cond: threading.Condition, buf: deque[bytes], n: int) -> list[bytes]:
    out: list[bytes] = []
    deadline = time.monotonic() + DEADLINE
    while len(out) < n and time.monotonic() < deadline:
        with cond:
            if buf:
                out.append(buf.popleft())
                continue
            cond.wait(timeout=0.5)
    return out


def test_fanout_delivers_all_chunks_in_order_to_every_subscriber() -> None:
    data = bytes(range(256)) * 2  # 512 bytes -> 32 chunks of 16
    b = _Broadcaster(io.BytesIO(data), chunk=CHUNK)
    subs = [b.subscribe() for _ in range(3)]
    b.start()

    for _sub_id, cond, buf in subs:
        got = b"".join(_drain(cond, buf, 32))
        assert got == data

    # each subscriber dequeues independently
    for _sub_id, cond, buf in subs:
        with cond:
            assert not buf


def test_slow_subscriber_backlog_is_capped_and_keeps_newest_audio() -> None:
    n_chunks = _Broadcaster.MAX_CHUNKS + 50
    data = b"x" * (n_chunks * CHUNK)
    b = _Broadcaster(io.BytesIO(data), chunk=CHUNK)
    _sub_id, cond, buf = b.subscribe()
    b.start()

    deadline = time.monotonic() + DEADLINE
    while time.monotonic() < deadline:
        with cond:
            if len(buf) == _Broadcaster.MAX_CHUNKS:
                break
        time.sleep(0.01)

    with cond:
        assert len(buf) == _Broadcaster.MAX_CHUNKS
        assert b"".join(buf) == data[-_Broadcaster.MAX_CHUNKS * CHUNK:]


def test_stop_wakes_waiting_subscriber() -> None:
    unblock = threading.Event()
    finished = threading.Event()

    class BlockingStream:
        def read(self, _n: int) -> bytes:
            unblock.wait()
            return b""

    b = _Broadcaster(BlockingStream(), chunk=CHUNK)
    _sub_id, cond, buf = b.subscribe()
    b.start()

    def consumer() -> None:
        while True:
            with cond:
                while not buf and not b.stopped:
                    cond.wait(timeout=1.0)
                if not buf:
                    finished.set()
                    return
                buf.clear()

    t = threading.Thread(target=consumer, daemon=True)
    t.start()
    time.sleep(0.1)  # let the consumer reach its wait

    b.stop()
    unblock.set()

    assert finished.wait(DEADLINE), "subscriber must exit after broadcaster stop"
    t.join(timeout=1.0)
