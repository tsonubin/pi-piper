from __future__ import annotations

import http.server
import logging
import socket
import socketserver
import threading
from collections import deque
from typing import IO

from pi_piper.audio.formats import alsa_format_bits, is_s16le, wav_header
from pi_piper.config import Config
from pi_piper.sinks.base import AudioSink, SinkError

log = logging.getLogger(__name__)

def discover_sonos() -> list[dict[str, str]]:
    try:
        import soco
    except ImportError as e:
        raise SinkError("soco not installed") from e
    devices = soco.discover(timeout=5) or set()
    return [
        {"uid": d.uid, "name": d.player_name, "ip": d.ip_address}
        for d in devices
    ]


class _Broadcaster:
    """Reads from a single PCM source and fans bytes out to N HTTP listeners.

    Each subscriber's buffer is guarded by that subscriber's condition (the
    same lock the pump uses to append and the handler uses to drain), so a
    chunk can't be torn by a concurrent join or dropped by a racing clear().
    A slow listener's backlog is capped at MAX_CHUNKS with drop-oldest: for a
    live line-in, skipping stale audio beats unbounded lag or memory growth
    on a memory-tight Pi.
    """

    #: Per-subscriber backlog cap. At the default 44.1kHz stereo S16 input a
    #: 4096-byte chunk is ~23ms, so 256 chunks is ~6s — far beyond the ~1s
    #: Sonos buffering, so drops only ever hit a genuinely stalled client.
    MAX_CHUNKS = 256

    def __init__(self, stream: IO[bytes], chunk: int = 4096):
        self._stream = stream
        self._chunk = chunk
        self._lock = threading.Lock()
        self._subs: dict[int, tuple[threading.Condition, deque[bytes]]] = {}
        self._next_id = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._pump, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def subscribe(self) -> tuple[int, threading.Condition, deque[bytes]]:
        cond = threading.Condition()
        buf: deque[bytes] = deque()
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subs[sub_id] = (cond, buf)
        return sub_id, cond, buf

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subs.pop(sub_id, None)

    def _pump(self) -> None:
        while not self._stop.is_set():
            data = self._stream.read(self._chunk)
            if not data:
                break
            with self._lock:
                subs = list(self._subs.values())
            for cond, buf in subs:
                with cond:
                    if len(buf) >= self.MAX_CHUNKS:
                        buf.popleft()
                    buf.append(data)
                    cond.notify()
        # Wake handler threads so they notice stop() and exit even when the
        # capture stream never reaches EOF.
        with self._lock:
            subs = list(self._subs.values())
        for cond, _buf in subs:
            with cond:
                cond.notify_all()


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _make_handler(
    broadcaster: _Broadcaster, header: bytes
) -> type[http.server.BaseHTTPRequestHandler]:

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
            log.debug("http: " + fmt, *args)

        def do_GET(self) -> None:  # noqa: N802
            if not self.path.startswith("/stream"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(header)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            sub_id, cond, buf = broadcaster.subscribe()
            try:
                while True:
                    with cond:
                        while not buf and not broadcaster.stopped:
                            cond.wait(timeout=1.0)
                        if not buf:
                            return  # broadcaster stopped and backlog drained
                        chunk = b"".join(buf)
                        buf.clear()
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
            finally:
                broadcaster.unsubscribe(sub_id)

    return Handler


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


class SonosSink(AudioSink):
    name = "sonos"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self._server: _ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._broadcaster: _Broadcaster | None = None
        self._zone = None

    def _resolve_zone(self):
        try:
            import soco
        except ImportError as e:
            raise SinkError("soco not installed") from e
        uid = self.cfg.sonos.target_uid
        name = self.cfg.sonos.target_name
        if not uid and not name:
            raise SinkError(
                "no sonos target configured; run `pi-piper sonos scan` then "
                "`pi-piper sonos use <uid>`"
            )
        zones = soco.discover(timeout=5) or set()
        for z in zones:
            if uid and z.uid == uid:
                return z
            if name and z.player_name == name:
                return z
        raise SinkError(f"sonos zone not found (uid={uid} name={name})")

    def start(self, pcm_stream: IO[bytes]) -> None:
        if self._server is not None:
            raise SinkError("sink already started")
        try:
            bits = alsa_format_bits(self.cfg.input.format)
        except ValueError as e:
            raise SinkError(str(e)) from e
        if not is_s16le(self.cfg.input.format):
            raise SinkError(
                "sonos backend serves 16-bit little-endian WAV; set input.format to S16_LE "
                f"(got {self.cfg.input.format!r})"
            )
        zone = self._resolve_zone()
        header = wav_header(self.cfg.input.rate, self.cfg.input.channels, bits)
        self._broadcaster = _Broadcaster(pcm_stream)
        self._broadcaster.start()
        port = self.cfg.sonos.stream_port
        handler = _make_handler(self._broadcaster, header)
        self._server = _ThreadingHTTPServer(("0.0.0.0", port), handler)
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="pi-piper-http"
        )
        self._server_thread.start()
        url = f"http://{_local_ip()}:{port}/stream.wav"
        log.info("serving stream at %s; telling Sonos to play it", url)
        zone.play_uri(url, title="pi-piper line-in")
        self._zone = zone

    def stop(self) -> None:
        if self._zone is not None:
            try:
                self._zone.stop()
            except Exception as e:
                log.warning("sonos stop failed: %s", e)
            self._zone = None
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._broadcaster is not None:
            self._broadcaster.stop()
            self._broadcaster = None

    def is_alive(self) -> bool:
        return (
            self._server is not None
            and self._server_thread is not None
            and self._server_thread.is_alive()
        )
