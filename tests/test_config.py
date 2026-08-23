from pathlib import Path
import tempfile
import unittest

from encodec_live_streamer.config import Config
from encodec_live_streamer.ffmpeg import command


class ConfigTests(unittest.TestCase):
    def test_default_segment_duration_is_hq_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(input="x", output_dir=Path(directory)).validate()
            self.assertEqual(config.segment_duration, 3.96)
            self.assertTrue(config.segment_is_hq_aligned)

    def test_two_second_segments_are_not_hq_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                input="x",
                output_dir=Path(directory),
                segment_duration=2.0,
            ).validate()
            self.assertFalse(config.segment_is_hq_aligned)

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
            self.assertEqual(argv[-2:], ["f32le", "pipe:1"])

    def test_rejects_invalid_bandwidth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "bandwidth"):
                Config(input="x", output_dir=Path(directory), bandwidth_kbps=5).validate()

    def test_toml_reports_missing_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.toml"
            path.write_text("[stream]\nbandwidth_kbps = 12\n")
            with self.assertRaisesRegex(ValueError, "input, output_dir"):
                Config.from_toml(path)
