from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import struct
import tempfile
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .ecdc import parse_header


SEGMENT_RE = re.compile(r"^segment-(\d{12})\.ecdc$")


def _protobuf_varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _protobuf_uint(field: int, value: int) -> bytes:
    if value < 0:
        raise ValueError("protobuf unsigned integers cannot be negative")
    return _protobuf_varint(field << 3) + _protobuf_varint(value)


def _protobuf_bool(field: int, value: bool) -> bytes:
    return _protobuf_uint(field, int(value))


def _protobuf_fixed32(field: int, value: int) -> bytes:
    return _protobuf_varint((field << 3) | 5) + struct.pack("<I", value)


def _protobuf_bytes(field: int, value: bytes) -> bytes:
    return _protobuf_varint((field << 3) | 2) + _protobuf_varint(len(value)) + value


def _protobuf_string(field: int, value: str) -> bytes:
    return _protobuf_bytes(field, value.encode("utf-8"))


def _unix_millis(value: str) -> int:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("protobuf manifest timestamps must include a timezone")
    return int(timestamp.timestamp() * 1000)


def crc32c(payload: bytes) -> int:
    """Return the Castagnoli CRC-32 checksum used by the protobuf manifest."""
    checksum = 0xFFFFFFFF
    for byte in payload:
        checksum ^= byte
        for _ in range(8):
            checksum = (checksum >> 1) ^ (0x82F63B78 if checksum & 1 else 0)
    return checksum ^ 0xFFFFFFFF


def manifest_protobuf(
    document: dict[str, Any], segment_metadata: list[dict[str, Any]]
) -> bytes:
    """Encode the compact v2 schema from the same snapshot as the JSON file."""
    sample_rate = document["init"]["sample_rate"]
    segment_duration_samples = round(document["target_duration"] * sample_rate)
    encoded = bytearray(
        b"".join(
            (
                _protobuf_uint(1, 2),
                _protobuf_uint(2, document["media_sequence"]),
                _protobuf_uint(3, segment_duration_samples),
            )
        )
    )
    if "title" in document:
        encoded.extend(_protobuf_string(4, document["title"]))

    groups: list[list[dict[str, Any]]] = []
    for segment in segment_metadata:
        if not groups or groups[-1][-1]["epoch"] != segment["epoch"]:
            groups.append([])
        groups[-1].append(segment)

    for group_segments in groups:
        group = bytearray(
            _protobuf_uint(1, group_segments[0]["epoch_start_unix_ms"])
        )
        if group_segments[0]["discontinuity"]:
            group.extend(_protobuf_bool(2, True))
        for segment in group_segments:
            item = b"".join(
                (
                    _protobuf_uint(1, segment["byte_length"]),
                    _protobuf_fixed32(2, segment["_crc32c"]),
                )
            )
            group.extend(_protobuf_bytes(3, item))
        encoded.extend(_protobuf_bytes(5, bytes(group)))
    return bytes(encoded)


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
        self.protobuf_path = self.path.with_suffix(".pb")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.segments: list[dict[str, Any]] = []
        self.epoch_start_by_id: dict[str, int] = {}
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
                    epoch = segment["epoch"]
                    epoch_start = segment.get("epoch_start_unix_ms")
                    if epoch_start is None:
                        epoch_start = self.epoch_start_by_id.get(epoch)
                    if epoch_start is None:
                        epoch_start = _unix_millis(segment["program_date_time"]) - round(
                            segment["pts_samples"] * 1000 / self.config.sample_rate
                        )
                    segment["epoch_start_unix_ms"] = int(epoch_start)
                    segment["_crc32c"] = crc32c(
                        (self.output_dir / segment["uri"]).read_bytes()
                    )
                    self.epoch_start_by_id[epoch] = int(epoch_start)
                    valid.append(segment)
            if any(
                int(segment["sample_count"]) != self.config.samples_per_segment
                for segment in valid
            ):
                # A legacy short tail cannot coexist with the fixed-duration
                # protobuf window; resume sequence numbers but start a fresh window.
                return
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
        epoch_start_unix_ms: int | None = None,
    ) -> dict[str, Any]:
        header = parse_header(payload)
        if (
            header.model != self.config.model
            or header.audio_length != sample_count
            or header.codebooks != self.config.codebooks
            or header.language_model
        ):
            raise ValueError("encoded segment header does not match stream configuration")
        if sample_count != self.config.samples_per_segment:
            raise ValueError("segment sample_count must match the fixed segment duration")

        if epoch_start_unix_ms is None:
            epoch_start_unix_ms = self.epoch_start_by_id.get(epoch)
        if epoch_start_unix_ms is None:
            epoch_start_unix_ms = _unix_millis(program_date_time) - round(
                pts_samples * 1000 / self.config.sample_rate
            )
        self.epoch_start_by_id[epoch] = int(epoch_start_unix_ms)

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
            "epoch_start_unix_ms": int(epoch_start_unix_ms),
            "discontinuity": discontinuity,
            "byte_length": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "_crc32c": crc32c(payload),
        }
        self.next_sequence += 1
        self.segments.append(segment)
        if len(self.segments) > self.config.window_segments:
            removed = self.segments[: -self.config.window_segments]
            self.segments = self.segments[-self.config.window_segments :]
            self.discontinuity_sequence += sum(bool(item["discontinuity"]) for item in removed)
        live_epochs = {item["epoch"] for item in self.segments}
        self.epoch_start_by_id = {
            key: value
            for key, value in self.epoch_start_by_id.items()
            if key in live_epochs
        }
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
            "segments": [
                {key: value for key, value in item.items() if not key.startswith("_")}
                for item in self.segments
            ],
        }
        if self.config.title is not None:
            document["title"] = self.config.title
        return document

    def write_manifest(self) -> None:
        document = self.document()
        encoded_json = json.dumps(
            document, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        encoded_protobuf = manifest_protobuf(document, self.segments)
        atomic_write(self.protobuf_path, encoded_protobuf, self.config.fsync)
        atomic_write(self.path, encoded_json, self.config.fsync)

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
