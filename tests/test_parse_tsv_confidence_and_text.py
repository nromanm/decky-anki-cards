"""
Run with: python3 tests/test_parse_tsv_confidence_and_text.py

Tests _parse_tsv_confidence_and_text, which turns Tesseract's per-word TSV output into the
"Example" field's text: groups words into lines, inserts a blank line between paragraphs/blocks,
drops low-confidence words, and sums confidence so the best-reading color channel wins. Any
regression here silently produces squished or garbled OCR text with no error at all.
"""
import os
import sys
import types
import tempfile
import unittest

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


def _write_tsv(rows):
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    lines = [header]
    for block, par, line, conf, text in rows:
        lines.append(f"5\t1\t{block}\t{par}\t{line}\t1\t0\t0\t10\t10\t{conf}\t{text}")
    fd, path = tempfile.mkstemp(suffix=".tsv")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


class ParseTsvConfidenceAndTextTest(unittest.TestCase):
    def test_joins_words_on_a_line_and_blank_lines_between_paragraphs(self):
        path = _write_tsv([
            (1, 1, 1, 90, "Hello"), (1, 1, 1, 90, "world"),
            (1, 1, 2, 90, "second"), (1, 1, 2, 90, "line"),
            (2, 1, 1, 90, "New"), (2, 1, 1, 90, "paragraph"),
        ])
        try:
            text, confidence = main._parse_tsv_confidence_and_text(path)
        finally:
            os.remove(path)
        self.assertEqual(text, "Hello world\nsecond line\n\nNew paragraph")
        self.assertEqual(confidence, 90 * 6)

    def test_drops_words_below_confidence_threshold(self):
        below = main._OCR_MIN_WORD_CONFIDENCE - 1
        path = _write_tsv([(1, 1, 1, 90, "keep"), (1, 1, 1, below, "drop")])
        try:
            text, confidence = main._parse_tsv_confidence_and_text(path)
        finally:
            os.remove(path)
        self.assertEqual(text, "keep")
        self.assertEqual(confidence, 90)

    def test_no_words_above_threshold_returns_empty(self):
        below = main._OCR_MIN_WORD_CONFIDENCE - 1
        path = _write_tsv([(1, 1, 1, below, "nope")])
        try:
            text, confidence = main._parse_tsv_confidence_and_text(path)
        finally:
            os.remove(path)
        self.assertEqual((text, confidence), ("", 0.0))


if __name__ == "__main__":
    unittest.main()
