"""
Run with: python3 tests/test_clamp_crop_fractions.py

Tests _clamp_crop_fractions, which turns the 4 crop sliders (0-100%) into the fractions fed into
ffmpeg's crop filter. A bug here either lets opposite sliders crop away the entire image (ffmpeg
then fails, or crops to nothing) or silently ignores part of what the user dragged.
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


class ClampCropFractionsTest(unittest.TestCase):
    def test_all_zero_means_no_crop(self):
        self.assertEqual(main._clamp_crop_fractions(0, 0, 0, 0), (0.0, 0.0, 0.0, 0.0))

    def test_normal_values_convert_percent_to_fraction(self):
        self.assertEqual(main._clamp_crop_fractions(10, 20, 30, 40), (0.10, 0.20, 0.30, 0.40))

    def test_single_edge_is_capped_at_90_percent(self):
        top, left, right, bottom = main._clamp_crop_fractions(150, 0, 0, 0)
        self.assertEqual((top, left, right, bottom), (0.90, 0.0, 0.0, 0.0))

    def test_opposite_edges_still_leave_at_least_10_percent_of_the_dimension(self):
        # Left maxes out at 90%; right must then be capped so left+right never reaches 100%.
        top, left, right, bottom = main._clamp_crop_fractions(0, 100, 100, 0)
        self.assertEqual(left, 0.90)
        self.assertEqual(right, 0.0)
        self.assertLess(left + right, 1.0)

    def test_negative_values_are_clamped_to_zero(self):
        self.assertEqual(main._clamp_crop_fractions(-5, -5, -5, -5), (0.0, 0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
