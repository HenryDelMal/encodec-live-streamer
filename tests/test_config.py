from pathlib import Path
import tempfile
import unittest

from encodec_live_streamer.config import Config
from encodec_live_streamer.ffmpeg import command


class ConfigTests(unittest.TestCase):
    def test_default_is_48khz_stereo_and_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(input="x", output_dir=Path(directory)).validate()
            self.assertEqual(config.segment_duration, 3.96)
            self.assertEqual(config.sample_rate, 48_000)
            self.assertEqual(config.channels, 2)
            self.assertEqual(config.codebooks, 8)
            self.assertTrue(config.segment_is_aligned)

    def test_24khz_3kbps_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                input="x",
                output_dir=Path(directory),
                samplerate=24,
                bandwidth_kbps=3,
            ).validate()
            self.assertEqual(config.model, "encodec_24khz")
            self.assertEqual(config.sample_rate, 24_000)
            self.assertEqual(config.channels, 1)
            self.assertEqual(config.codebooks, 4)
            self.assertEqual(config.bytes_per_sample_frame, 4)
            self.assertTrue(config.segment_is_aligned)

    def test_two_second_segments_are_not_48khz_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                input="x",
                output_dir=Path(directory),
                segment_duration=2.0,
            ).validate()
            self.assertFalse(config.segment_is_aligned)

    def test_ffmpeg_command_orders_input_options_before_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                input="hw:0",
                input_format="alsa",
                input_options=("-thread_queue_size", "1024"),
                output_dir=Path(directory),
            ).validate()
            argv = command(config)
            self.assertLess(argv.index("-thread_queue_size"), argv.index("-i"))
            self.assertEqual(argv[argv.index("-f") + 1], "alsa")
            self.assertEqual(argv[argv.index("-ac") + 1], "2")
            self.assertEqual(argv[argv.index("-ar") + 1], "48000")
            self.assertEqual(argv[-2:], ["f32le", "pipe:1"])

    def test_24khz_ffmpeg_command_is_mono(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                input="x", output_dir=Path(directory), samplerate=24, bandwidth_kbps=3
            ).validate()
            argv = command(config)
            self.assertEqual(argv[argv.index("-ac") + 1], "1")
            self.assertEqual(argv[argv.index("-ar") + 1], "24000")

    def test_rejects_invalid_bandwidth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "bandwidth"):
                Config(input="x", output_dir=Path(directory), bandwidth_kbps=5).validate()

    def test_24khz_accepts_1_5_kbps_but_48khz_rejects_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Config(
                input="x", output_dir=Path(directory), samplerate=24, bandwidth_kbps=1.5
            ).validate()
            with self.assertRaisesRegex(ValueError, "bandwidth"):
                Config(
                    input="x", output_dir=Path(directory), samplerate=48, bandwidth_kbps=1.5
                ).validate()

    def test_rejects_invalid_samplerate_and_threads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "samplerate"):
                Config(input="x", output_dir=Path(directory), samplerate=32).validate()
            with self.assertRaisesRegex(ValueError, "threads"):
                Config(input="x", output_dir=Path(directory), threads=0).validate()

    def test_toml_reports_missing_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.toml"
            path.write_text("[stream]\nbandwidth_kbps = 12\n")
            with self.assertRaisesRegex(ValueError, "input, output_dir"):
                Config.from_toml(path)
