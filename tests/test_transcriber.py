"""Unit tests for transcriber. No network, no external tools invoked."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transcriber  # noqa: E402


class TestSanitizeFilename(unittest.TestCase):
    def test_strips_path_separators(self):
        self.assertEqual(transcriber.sanitize_filename("a/b\\c"), "abc")

    def test_traversal_cannot_escape_output_dir(self):
        # A title of ".." must not resolve to the parent directory.
        self.assertEqual(transcriber.sanitize_filename(".."), "transcript")
        self.assertEqual(transcriber.sanitize_filename("../../etc/passwd"), "etcpasswd")

    def test_empty_title_gets_a_fallback(self):
        self.assertEqual(transcriber.sanitize_filename("   "), "transcript")

    def test_length_is_capped(self):
        self.assertEqual(len(transcriber.sanitize_filename("x" * 500)), 150)

    def test_control_characters_removed(self):
        self.assertEqual(transcriber.sanitize_filename("a\x00b\x1fc"), "abc")


class TestTxtPath(unittest.TestCase):
    def test_dot_in_title_is_preserved(self):
        # with_suffix() would truncate this to "Episode 1.txt", losing "5 Intro".
        base = Path("/out/Episode 1.5 Intro [abc]")
        self.assertEqual(transcriber.txt_path(base).name, "Episode 1.5 Intro [abc].txt")


class TestCookieArgs(unittest.TestCase):
    def test_absent_by_default(self):
        self.assertEqual(transcriber.cookie_args(None), [])

    def test_supported_browser_passes_through(self):
        self.assertEqual(
            transcriber.cookie_args("safari"), ["--cookies-from-browser", "safari"]
        )

    def test_unsupported_value_is_rejected(self):
        # An option-shaped value must never reach yt-dlp's argument parser.
        with self.assertRaises(transcriber.TranscriberError):
            transcriber.cookie_args("--config-location")


class TestRequireTool(unittest.TestCase):
    def test_missing_tool_raises_a_clear_error(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(transcriber.TranscriberError) as ctx:
                transcriber.require_tool("yt-dlp")
        self.assertIn("yt-dlp", str(ctx.exception))

    def test_present_tool_returns_path(self):
        with mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"):
            self.assertEqual(transcriber.require_tool("yt-dlp"), "/usr/bin/yt-dlp")


class TestRun(unittest.TestCase):
    def test_timeout_becomes_a_transcriber_error(self):
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=1)):
            with self.assertRaises(transcriber.TranscriberError) as ctx:
                transcriber.run(["/usr/bin/yt-dlp"], timeout=1)
        self.assertIn("timed out", str(ctx.exception))


class TestArgumentTerminator(unittest.TestCase):
    """A source beginning with '-' must be data, never an option."""

    def test_get_video_info_terminates_options(self):
        completed = subprocess.CompletedProcess([], 0, stdout='{"title": "t", "id": "i"}', stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
             mock.patch.object(transcriber, "run", return_value=completed) as runner:
            transcriber.get_video_info("--config-location=/tmp/evil")
        cmd = runner.call_args[0][0]
        self.assertEqual(cmd[-2], "--")
        self.assertEqual(cmd[-1], "--config-location=/tmp/evil")

    def test_download_captions_terminates_options(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
             mock.patch.object(transcriber, "run", return_value=completed) as runner:
            transcriber.download_captions("-x", Path(tmp) / "base")
        cmd = runner.call_args[0][0]
        self.assertEqual(cmd[-2], "--")
        self.assertEqual(cmd[-1], "-x")


class TestSrtConversion(unittest.TestCase):
    SRT = (
        "1\n"
        "00:00:01,000 --> 00:00:03,000\n"
        "<i>Hello</i> there\n"
        "\n"
        "2\n"
        "00:00:03,000 --> 00:00:05,000\n"
        "second line\n"
    )

    def test_timings_numbers_and_tags_are_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / "in.srt"
            out = Path(tmp) / "out.txt"
            srt.write_text(self.SRT, encoding="utf-8")
            transcriber.convert_srt_to_txt(srt, out)
            self.assertEqual(out.read_text(encoding="utf-8"), "Hello there second line")


class TestDownloadCaptions(unittest.TestCase):
    def test_writes_transcript_when_captions_exist(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "Title [abc]"

            def fake_run(cmd, timeout):
                # Stand in for yt-dlp producing a subtitle file.
                (base.parent / (base.name + ".en.srt")).write_text(
                    "1\n00:00:01,000 --> 00:00:02,000\ncaption text\n", encoding="utf-8"
                )
                return completed

            with mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
                 mock.patch.object(transcriber, "run", side_effect=fake_run):
                result = transcriber.download_captions("https://example.com/v", base)

            self.assertTrue(result)
            self.assertEqual(transcriber.txt_path(base).read_text(encoding="utf-8"),
                             "caption text")
            # The intermediate SRT is cleaned up.
            self.assertFalse((base.parent / (base.name + ".en.srt")).exists())

    def test_returns_false_when_no_captions_produced(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
             mock.patch.object(transcriber, "run", return_value=completed):
            self.assertFalse(
                transcriber.download_captions("https://example.com/v", Path(tmp) / "base")
            )


class TestTranscribe(unittest.TestCase):
    INFO = subprocess.CompletedProcess([], 0, stdout=json.dumps({"title": "T", "id": "id1"}), stderr="")

    def test_existing_transcript_short_circuits(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            (out_dir / "T [id1].txt").write_text("already here", encoding="utf-8")

            with mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
                 mock.patch.object(transcriber, "run", return_value=self.INFO), \
                 mock.patch.object(transcriber, "download_captions") as captions:
                out = transcriber.transcribe("https://example.com/v", out_dir)

            captions.assert_not_called()
            self.assertEqual(out.read_text(encoding="utf-8"), "already here")

    def test_falls_back_to_whisper_when_no_captions(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
                 mock.patch.object(transcriber, "run", return_value=self.INFO), \
                 mock.patch.object(transcriber, "download_captions", return_value=False), \
                 mock.patch.object(transcriber, "transcribe_with_whisper",
                                   return_value=True) as whisper:
                transcriber.transcribe("https://example.com/v", out_dir)
            whisper.assert_called_once()

    def test_playlist_json_is_rejected_clearly(self):
        # --dump-json emits one object per line for a playlist; that is not valid JSON.
        multi = subprocess.CompletedProcess([], 0, stdout='{"id":"a"}\n{"id":"b"}\n', stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
             mock.patch.object(transcriber, "run", return_value=multi):
            with self.assertRaises(transcriber.TranscriberError) as ctx:
                transcriber.get_video_info("https://example.com/playlist")
        self.assertIn("playlist", str(ctx.exception).lower())


class TestTranscribeLocal(unittest.TestCase):
    def test_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(transcriber.TranscriberError):
                transcriber.transcribe_local(str(Path(tmp) / "nope.mp3"), Path(tmp))


if __name__ == "__main__":
    unittest.main()
