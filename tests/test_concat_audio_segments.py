"""
Run with: python3 tests/test_concat_audio_segments.py

Tests _concat_audio_segments, the only pure, parser-like logic in main.py: it stitches
Steam game-recording audio-track chunk files together in order before handing the result to
ffmpeg. A sorting mistake here produces scrambled or silent audio with no error at all, so this
is the one piece of logic worth a standalone test (no live Anki/Steam Deck needed to run it).
"""
import os
import sys
import types
import tempfile
import unittest

# main.py does `import decky` at module level — that module only exists inside the real Decky
# Loader runtime. Stub the couple of attributes main.py actually touches so it can be imported
# here to test pure logic in isolation.
if "decky" not in sys.modules:
    fake_decky = types.ModuleType("decky")
    fake_decky.DECKY_USER_HOME = tempfile.gettempdir()

    class _FakeLogger:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    fake_decky.logger = _FakeLogger()
    sys.modules["decky"] = fake_decky

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main  # noqa: E402


class ConcatAudioSegmentsTest(unittest.TestCase):
    def test_concatenates_init_then_chunks_in_numeric_order_across_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            bg_dir = os.path.join(tmp, "bg_1_20260101_000000")
            fg_dir = os.path.join(tmp, "fg_1_20260101_000000")
            os.makedirs(bg_dir)
            os.makedirs(fg_dir)

            def write(path, data):
                with open(path, "wb") as f:
                    f.write(data)

            write(os.path.join(bg_dir, "init-stream1.m4s"), b"BG_INIT")
            # Deliberately out of numeric order, and includes a two-digit index, to catch a
            # naive alphabetical sort (which would wrongly place chunk 10 before chunk 2).
            write(os.path.join(bg_dir, "chunk-stream1-1.m4s"), b"BG_1")
            write(os.path.join(bg_dir, "chunk-stream1-10.m4s"), b"BG_10")
            write(os.path.join(bg_dir, "chunk-stream1-2.m4s"), b"BG_2")

            write(os.path.join(fg_dir, "init-stream1.m4s"), b"FG_INIT")
            write(os.path.join(fg_dir, "chunk-stream1-1.m4s"), b"FG_1")

            out_path = os.path.join(tmp, "out.m4s")
            segment_dirs = main._recording_segment_dirs(tmp)
            main._concat_audio_segments(segment_dirs, out_path)

            with open(out_path, "rb") as f:
                result = f.read()

        # bg (pre-trigger buffer) must come before fg (post-trigger continuation); within each
        # segment, init first, then chunks in numeric order: 1, 2, 10.
        expected = b"BG_INIT" b"BG_1" b"BG_2" b"BG_10" b"FG_INIT" b"FG_1"
        self.assertEqual(result, expected)

    def test_raises_when_no_segment_has_an_audio_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            seg_dir = os.path.join(tmp, "fg_1_20260101_000000")
            os.makedirs(seg_dir)
            # Only a video track (stream 0), no init-stream1.m4s.
            with open(os.path.join(seg_dir, "init-stream0.m4s"), "wb") as f:
                f.write(b"VIDEO_INIT")

            out_path = os.path.join(tmp, "out.m4s")
            segment_dirs = main._recording_segment_dirs(tmp)
            with self.assertRaises(Exception):
                main._concat_audio_segments(segment_dirs, out_path)


if __name__ == "__main__":
    unittest.main()
