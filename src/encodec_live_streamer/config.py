from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

try:
    import tomllib
except ImportError:  # Python 3.9 and 3.10
    import tomli as tomllib


SUPPORTED_BANDWIDTHS = {3.0: 2, 6.0: 4, 12.0: 8, 24.0: 16}
HQ_SEGMENT_STRIDE_SAMPLES = 47_520


@dataclasses.dataclass(frozen=True)
class Config:
    input: str
    output_dir: pathlib.Path
    input_format: str | None = None
    input_options: tuple[str, ...] = ()
    ffmpeg: str = "ffmpeg"
    bandwidth_kbps: float = 12.0
    segment_duration: float = 3.96
    window_segments: int = 8
    stale_grace_segments: int = 2
    device: str = "cpu"
    restart_ffmpeg: bool = True
    restart_delay: float = 2.0
    manifest_name: str = "stream.json"
    fsync: bool = True

    @property
    def samples_per_segment(self) -> int:
        return round(self.segment_duration * 48_000)

    @property
    def codebooks(self) -> int:
        return SUPPORTED_BANDWIDTHS[self.bandwidth_kbps]

    @property
    def segment_is_hq_aligned(self) -> bool:
        return self.samples_per_segment % HQ_SEGMENT_STRIDE_SAMPLES == 0

    def validate(self) -> Config:
        if not self.input:
            raise ValueError("input must not be empty")
        if self.bandwidth_kbps not in SUPPORTED_BANDWIDTHS:
            raise ValueError("bandwidth_kbps must be one of 3, 6, 12, or 24")
        if not 0.25 <= self.segment_duration <= 30:
            raise ValueError("segment_duration must be between 0.25 and 30 seconds")
        if self.samples_per_segment <= 0:
            raise ValueError("segment_duration is too small")
        if self.window_segments < 2:
            raise ValueError("window_segments must be at least 2")
        if self.stale_grace_segments < 0:
            raise ValueError("stale_grace_segments cannot be negative")
        if self.restart_delay < 0:
            raise ValueError("restart_delay cannot be negative")
        if pathlib.Path(self.manifest_name).name != self.manifest_name:
            raise ValueError("manifest_name must be a file name, not a path")
        return self

    @classmethod
    def from_toml(cls, path: str | pathlib.Path) -> Config:
        source = pathlib.Path(path)
        with source.open("rb") as handle:
            raw = tomllib.load(handle)
        table: dict[str, Any] = raw.get("stream", raw)
        allowed = {field.name for field in dataclasses.fields(cls)}
        unknown = set(table) - allowed
        if unknown:
            raise ValueError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
        values = dict(table)
        missing = {"input", "output_dir"} - set(values)
        if missing:
            raise ValueError(f"missing configuration keys: {', '.join(sorted(missing))}")
        values["output_dir"] = pathlib.Path(values["output_dir"])
        if "input_options" in values:
            if not isinstance(values["input_options"], list):
                raise ValueError("input_options must be a TOML array of strings")
            values["input_options"] = tuple(str(item) for item in values["input_options"])
        return cls(**values).validate()
