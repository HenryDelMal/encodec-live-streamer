from pathlib import Path
import tempfile
import unittest

from encodec_live_streamer.config import Config
from encodec_live_streamer.encoder import native_command
from encodec_live_streamer.ffmpeg import command


class ConfigTests(unittest.TestCase):
    def test_default_is_48khz_stereo_and_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(input="x", output_dir=Path(directory)).validate()
            self.assertEqual(config.segment_duration, 3.96)
            self.assertEqual(config.sample_rate, 48_000)
            self.assertEqual(config.channels, 2)
            self.assertEqual(config.codebooks, 8)
            self.assertEqual(config.beam_size, 1)
            self.assertEqual(config.refinement_passes, 0)
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

    def test_rejects_invalid_encoder_tuning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "samplerate"):
                Config(input="x", output_dir=Path(directory), samplerate=32).validate()
            with self.assertRaisesRegex(ValueError, "threads"):
                Config(input="x", output_dir=Path(directory), threads=0).validate()
            for value in (0, 65, True):
                with self.assertRaisesRegex(ValueError, "beam_size"):
                    Config(input="x", output_dir=Path(directory), beam_size=value).validate()
            for value in (-1, 65, True):
                with self.assertRaisesRegex(ValueError, "refinement_passes"):
                    Config(
                        input="x",
                        output_dir=Path(directory),
                        refinement_passes=value,
                    ).validate()

    def test_native_command_includes_beam_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                input="x",
                output_dir=Path(directory),
                beam_size=4,
                refinement_passes=2,
            ).validate()
            argv = native_command(config)
            self.assertEqual(argv[argv.index("--beam-size") + 1], "4")
            self.assertEqual(argv[argv.index("--refinement-passes") + 1], "2")

    def test_toml_reports_missing_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.toml"
            path.write_text("[stream]\nbandwidth_kbps = 12\n")
            with self.assertRaisesRegex(ValueError, "input, output_dir"):
                Config.from_toml(path)

    def test_optional_title_from_toml_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.toml"
            path.write_text(
                '[stream]\ninput = "x"\noutput_dir = "public"\n'
                'title = "Bio Bio Santiago"\n'
            )
            self.assertEqual(Config.from_toml(path).title, "Bio Bio Santiago")
            with self.assertRaisesRegex(ValueError, "title must not be empty"):
                Config(input="x", output_dir=Path(directory), title="  ").validate()
