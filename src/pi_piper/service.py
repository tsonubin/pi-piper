from __future__ import annotations

import logging
import signal
import sys
import threading
import time

from pi_piper.audio import AlsaCapture
from pi_piper.config import Config, ConfigError
from pi_piper.sinks import build_sink
from pi_piper.sinks.base import SinkError

log = logging.getLogger(__name__)


def _install_signal_handlers(stop: threading.Event) -> None:
    def _handler(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def run(cfg: Config) -> int:
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Signals set an Event the loop polls: raising from a handler can fire
    # anywhere, including inside the cleanup finally, and leak the capture
    # process or leave the remote zone playing.
    stop = threading.Event()
    _install_signal_handlers(stop)

    capture = AlsaCapture(cfg.input)
    try:
        sink = build_sink(cfg)
    except SinkError as e:
        log.error("cannot build sink: %s", e)
        return 1

    backoff = 1.0
    try:
        while not stop.is_set():
            try:
                pcm = capture.start()
                sink.start(pcm)
                backoff = 1.0
                while not stop.is_set() and capture.is_running() and sink.is_alive():
                    time.sleep(0.5)
                if stop.is_set():
                    break
                log.warning(
                    "pipeline stopped (capture=%s sink=%s); restarting",
                    capture.is_running(),
                    sink.is_alive(),
                )
            except SinkError as e:
                log.error("sink error: %s", e)
            except FileNotFoundError as e:
                log.error("missing dependency: %s", e)
                return 2
            except OSError as e:
                # e.g. EADDRINUSE on the Sonos stream port, paplay spawn
                # failures: retryable, unlike a missing binary.
                log.error("i/o error: %s", e)
            finally:
                try:
                    sink.stop()
                except Exception as e:
                    log.warning("sink stop failed: %s", e)
                capture.stop()
            stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)
    finally:
        try:
            sink.stop()
        except Exception:
            pass
        capture.stop()
    return 0


def main() -> None:
    try:
        cfg = Config.load()
    except ConfigError as e:
        # logging is configured inside run(); a broken config exits before
        # that, so report directly.
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(run(cfg))


if __name__ == "__main__":
    main()
