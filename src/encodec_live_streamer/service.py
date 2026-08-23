from __future__ import annotations

import logging
import signal
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .config import Config
from .encoder import EncodecEncoder
from .ffmpeg import BYTES_PER_SAMPLE_FRAME, FfmpegInput
from .manifest import ManifestStore


LOG = logging.getLogger(__name__)


class Encoder(Protocol):
    def encode(self, pcm_f32le: bytes) -> bytes: ...


class LiveService:
    def __init__(self, config: Config, encoder: Encoder | None = None) -> None:
        self.config = config.validate()
        if not self.config.segment_is_hq_aligned:
            LOG.warning(
                "segment_duration %.6g is not aligned to the HQ EnCodec 0.99s stride; "
                "use 1.98, 2.97, 3.96, or another 0.99s multiple to reduce boundary artifacts",
                self.config.segment_duration,
            )
        self.encoder = encoder
        self.store = ManifestStore(config)
        self.stopping = threading.Event()
        self._input: FfmpegInput | None = None

    def request_stop(self, *_args: object) -> None:
        self.stopping.set()
        if self._input is not None:
            self._input.stop()

    def run(self) -> None:
        if self.encoder is None:
            self.encoder = EncodecEncoder(self.config.bandwidth_kbps, self.config.device)
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, self.request_stop)

        first_attempt = True
        while not self.stopping.is_set():
            source = FfmpegInput(self.config)
            self._input = source
            source.start()
            epoch = str(uuid.uuid4())
            pts_samples = 0
            anchor = datetime.now(timezone.utc)
            first_segment = True
            published = 0
            try:
                for pcm in source.chunks(self.config.samples_per_segment):
                    if self.stopping.is_set():
                        break
                    sample_count = len(pcm) // BYTES_PER_SAMPLE_FRAME
                    if not sample_count:
                        continue
                    assert self.encoder is not None
                    payload = self.encoder.encode(pcm)
                    timestamp = anchor + timedelta(seconds=pts_samples / 48_000)
                    item = self.store.publish_segment(
                        payload,
                        sample_count=sample_count,
                        pts_samples=pts_samples,
                        program_date_time=timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        epoch=epoch,
                        discontinuity=first_segment,
                    )
                    LOG.info(
                        "published sequence=%s duration=%.3fs bytes=%s",
                        item["sequence"],
                        item["duration"],
                        item["byte_length"],
                    )
                    first_segment = False
                    first_attempt = False
                    published += 1
                    pts_samples += sample_count
            finally:
                source.stop()
                exit_code = source.wait()
                self._input = None
            if self.stopping.is_set():
                break
            if not self.config.restart_ffmpeg:
                if exit_code != 0:
                    raise RuntimeError(f"FFmpeg exited with status {exit_code}")
                break
            LOG.warning(
                "FFmpeg ended with status %s after %s segments; restarting in %.1fs",
                exit_code,
                published,
                self.config.restart_delay,
            )
            if self.stopping.wait(self.config.restart_delay):
                break
            # Prevent a permanently bad command from becoming an invisible tight loop.
            if first_attempt and self.config.restart_delay == 0:
                time.sleep(0.1)
