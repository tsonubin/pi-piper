from __future__ import annotations

import logging
import signal
import sys
import time

from pi_piper.audio import AlsaCapture
from pi_piper.config import Config
from pi_piper.sinks import build_sink
from pi_piper.sinks.base import SinkError

log = logging.getLogger(__name__)


class _ShutdownRequest(Exception):
    pass


def _install_signal_handlers() -> None:
    def _handler(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        raise _ShutdownRequest()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def run(cfg: Config) -> int:
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _install_signal_handlers()

    capture = AlsaCapture(cfg.input)
    sink = build_sink(cfg)

    backoff = 1.0
    try:
        while True:
            try:
                pcm = capture.start()
                sink.start(pcm)
                backoff = 1.0
                while capture.is_running() and sink.is_alive():
                    time.sleep(0.5)
                log.warning("pipeline stopped (capture=%s sink=%s); restarting",
                            capture.is_running(), sink.is_alive())
            except SinkError as e:
                log.error("sink error: %s", e)
            except FileNotFoundError as e:
                log.error("missing dependency: %s", e)
                return 2
            finally:
                try:
                    sink.stop()
                except Exception as e:
                    log.warning("sink stop failed: %s", e)
                capture.stop()
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
    except _ShutdownRequest:
        return 0


def main() -> None:
    cfg = Config.load()
    sys.exit(run(cfg))


if __name__ == "__main__":
    main()
