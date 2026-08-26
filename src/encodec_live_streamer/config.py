from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

try:
    import tomllib
except ImportError:  # Python 3.9 and 3.10
    import tomli as tomllib


MODEL_PROFILES = {
    24: {
        "model": "encodec_24khz",
        "sample_rate": 24_000,
        "channels": 1,
        "frame_rate": 75,
        "alignment_samples": 320,
        "bandwidths": {1.5: 2, 3.0: 4, 6.0: 8, 12.0: 16, 24.0: 32},
    },
    48: {
        "model": "encodec_48khz",
        "sample_rate": 48_000,
        "channels": 2,
        "frame_rate": 150,
        "alignment_samples": 47_520,
        "bandwidths": {3.0: 2, 6.0: 4, 12.0: 8, 24.0: 16},
    },
}


@dataclasses.dataclass(frozen=True)
class Config:
    input: str
    output_dir: pathlib.Path
    title: str | None = None
    input_format: str | None = None
    input_options: tuple[str, ...] = ()
    output_options: tuple[str, ...] = ()
    ffmpeg: str = "ffmpeg"
    native_encoder: str = "encodec-live-native"
    model_dir: pathlib.Path = pathlib.Path("/opt/encodec-live/models")
    samplerate: int = 48
    bandwidth_kbps: float = 12.0
    segment_duration: float = 3.96
    window_segments: int = 8
    stale_grace_segments: int = 2
    threads: int = 1
    restart_ffmpeg: bool = True
    restart_delay: float = 2.0
    manifest_name: str = "stream.json"
    fsync: bool = True

    @property
    def profile(self) -> dict[str, Any]:
        return MODEL_PROFILES[self.samplerate]

    @property
    def model(self) -> str:
        return str(self.profile["model"])

    @property
    def sample_rate(self) -> int:
        return int(self.profile["sample_rate"])

    @property
    def channels(self) -> int:
        return int(self.profile["channels"])

    @property
    def frame_rate(self) -> int:
        return int(self.profile["frame_rate"])

    @property
    def model_path(self) -> pathlib.Path:
        return self.model_dir / f"{self.model}-combined-f32.bin"

    @property
    def bytes_per_sample_frame(self) -> int:
        return self.channels * 4  # interleaved float32

    @property
    def samples_per_segment(self) -> int:
        return round(self.segment_duration * self.sample_rate)

    @property
    def codebooks(self) -> int:
        bandwidths: dict[float, int] = self.profile["bandwidths"]
        return bandwidths[self.bandwidth_kbps]

    @property
    def segment_is_aligned(self) -> bool:
        return self.samples_per_segment % int(self.profile["alignment_samples"]) == 0

    @property
    def alignment_description(self) -> str:
        if self.samplerate == 48:
            return "the 48 kHz model's 0.99-second stride"
        return "the 24 kHz model's 320-sample (1/75-second) codec frame"

    def validate(self) -> Config:
        if not self.input:
            raise ValueError("input must not be empty")
        if self.title is not None:
            if not isinstance(self.title, str):
                raise ValueError("title must be a string")
            if not self.title.strip():
                raise ValueError("title must not be empty")
        if isinstance(self.samplerate, bool) or not isinstance(self.samplerate, int):
            raise ValueError("samplerate must be the integer 24 or 48")
        if self.samplerate not in MODEL_PROFILES:
            raise ValueError("samplerate must be 24 or 48")
        bandwidths: dict[float, int] = self.profile["bandwidths"]
        if self.bandwidth_kbps not in bandwidths:
            supported = ", ".join(f"{value:g}" for value in bandwidths)
            raise ValueError(
                f"bandwidth_kbps for samplerate={self.samplerate} must be one of {supported}"
            )
        if not 0.25 <= self.segment_duration <= 30:
            raise ValueError("segment_duration must be between 0.25 and 30 seconds")
        if self.samples_per_segment <= 0:
            raise ValueError("segment_duration is too small")
        if self.window_segments < 2:
            raise ValueError("window_segments must be at least 2")
        if self.stale_grace_segments < 0:
            raise ValueError("stale_grace_segments cannot be negative")
        if (
            isinstance(self.threads, bool)
            or not isinstance(self.threads, int)
            or self.threads < 1
        ):
            raise ValueError("threads must be an integer of at least 1")
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
            raise ValueError(
                f"unknown configuration keys: {', '.join(sorted(unknown))}"
            )

        values = dict(table)
        missing = {"input", "output_dir"} - set(values)
        if missing:
            raise ValueError(
                f"missing configuration keys: {', '.join(sorted(missing))}"
            )

        values["output_dir"] = pathlib.Path(values["output_dir"])
        if "model_dir" in values:
            values["model_dir"] = pathlib.Path(values["model_dir"])

        for name in ("input_options", "output_options"):
            if name in values:
                if not isinstance(values[name], list):
                    raise ValueError(f"{name} must be a TOML array of strings")
                values[name] = tuple(str(item) for item in values[name])

        return cls(**values).validate()
