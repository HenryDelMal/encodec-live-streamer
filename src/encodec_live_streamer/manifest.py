from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import tempfile
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .ecdc import parse_header


SEGMENT_RE = re.compile(r"^segment-(\d{12})\.ecdc$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def atomic_write(path: pathlib.Path, payload: bytes, fsync: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            # mkstemp starts at 0600. Published manifest/segments must be readable
            # by an nginx worker running under a different unprivileged account.
            os.fchmod(handle.fileno(), 0o644)
            handle.write(payload)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(temporary, path)
        if fsync:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class ManifestStore:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.output_dir = config.output_dir
        self.path = self.output_dir / config.manifest_name
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.segments: list[dict[str, Any]] = []
        self.discontinuity_sequence = 0
        self.next_sequence = self._next_sequence_on_disk()
        self._load_compatible_manifest()

    @property
    def init(self) -> dict[str, Any]:
        return {
            "container": "ecdc",
            "container_version": 0,
            "model": self.config.model,
            "sample_rate": self.config.sample_rate,
            "channels": self.config.channels,
            "bits_per_codebook": 10,
            "bandwidth_kbps": self.config.bandwidth_kbps,
            "codebooks": self.config.codebooks,
            "language_model": False,
            "self_initializing_segments": True,
        }

    def _next_sequence_on_disk(self) -> int:
        values = [
            int(match.group(1))
            for item in self.output_dir.iterdir()
            if (match := SEGMENT_RE.match(item.name))
        ]
        return max(values, default=-1) + 1

    def _load_compatible_manifest(self) -> None:
        try:
            old = json.loads(self.path.read_text())
            if old.get("format") != "encodec-live-v1" or old.get("init") != self.init:
                return
            valid = []
            for segment in old.get("segments", []):
                if (self.output_dir / segment["uri"]).is_file():
                    valid.append(segment)
            self.segments = valid[-self.config.window_segments :]
            self.discontinuity_sequence = int(old.get("discontinuity_sequence", 0))
            if valid:
                self.next_sequence = max(self.next_sequence, int(valid[-1]["sequence"]) + 1)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return

    def publish_segment(
        self,
        payload: bytes,
        *,
        sample_count: int,
        pts_samples: int,
        program_date_time: str,
        epoch: str,
        discontinuity: bool,
    ) -> dict[str, Any]:
        header = parse_header(payload)
        if (
            header.model != self.config.model
            or header.audio_length != sample_count
            or header.codebooks != self.config.codebooks
            or header.language_model
        ):
            raise ValueError("encoded segment header does not match stream configuration")

        sequence = self.next_sequence
        uri = f"segment-{sequence:012d}.ecdc"
        atomic_write(self.output_dir / uri, payload, self.config.fsync)
        segment = {
            "sequence": sequence,
            "uri": uri,
            "duration": sample_count / self.config.sample_rate,
            "sample_count": sample_count,
            "pts_samples": pts_samples,
            "program_date_time": program_date_time,
            "epoch": epoch,
            "discontinuity": discontinuity,
            "byte_length": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        self.next_sequence += 1
        self.segments.append(segment)
        if len(self.segments) > self.config.window_segments:
            removed = self.segments[: -self.config.window_segments]
            self.segments = self.segments[-self.config.window_segments :]
            self.discontinuity_sequence += sum(bool(item["discontinuity"]) for item in removed)
        self.write_manifest()
        self.cleanup()
        return segment

    def document(self) -> dict[str, Any]:
        media_sequence = self.segments[0]["sequence"] if self.segments else self.next_sequence
        document = {
            "format": "encodec-live-v1",
            "version": 1,
            "updated_at": utc_now(),
            "media_sequence": media_sequence,
            "discontinuity_sequence": self.discontinuity_sequence,
            "target_duration": self.config.segment_duration,
            "independent_segments": True,
            "init": self.init,
            "segments": self.segments,
        }
        if self.config.title is not None:
            document["title"] = self.config.title
        return document

    def write_manifest(self) -> None:
        encoded = (json.dumps(self.document(), indent=2, sort_keys=True) + "\n").encode()
        atomic_write(self.path, encoded, self.config.fsync)

    def cleanup(self) -> None:
        if not self.segments:
            return
        keep_from = max(0, int(self.segments[0]["sequence"]) - self.config.stale_grace_segments)
        for item in self.output_dir.iterdir():
            match = SEGMENT_RE.match(item.name)
            if match and int(match.group(1)) < keep_from:
                try:
                    item.unlink()
                except FileNotFoundError:
                    pass
