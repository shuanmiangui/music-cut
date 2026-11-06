from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from musiccut.config import PEAK_CEILING
from musiccut.separator import (
    _make_output_dir,
    _safe_folder_name,
    apply_common_peak_guard,
)


class OutputPathTests(unittest.TestCase):
    def test_safe_folder_name(self):
        self.assertEqual(_safe_folder_name("normal name"), "normal name")
        self.assertEqual(_safe_folder_name("a/b:c"), "a_b_c")
        self.assertEqual(_safe_folder_name("   "), "audio")
        self.assertLessEqual(len(_safe_folder_name("x" * 500)), 100)

    def test_unique_output_dirs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "new root"
            source = Path("/tmp/My Song.mp3")
            first = _make_output_dir(root, source)
            second = _make_output_dir(root, source)
            self.assertEqual(first.name, "My Song_stems")
            self.assertEqual(second.name, "My Song_stems_2")
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())


class PeakGuardTests(unittest.TestCase):
    def test_common_gain_preserves_balance(self):
        stems = {
            "a": np.array([[1.4, 0.7]], dtype=np.float32),
            "b": np.array([[0.35, 0.1]], dtype=np.float32),
        }
        guarded, gain_db = apply_common_peak_guard(stems, "24")
        self.assertLess(gain_db, 0)
        self.assertLessEqual(float(np.abs(guarded["a"]).max()), PEAK_CEILING + 1e-6)
        ratio_before = stems["b"][0, 0] / stems["a"][0, 0]
        ratio_after = guarded["b"][0, 0] / guarded["a"][0, 0]
        self.assertAlmostEqual(float(ratio_before), float(ratio_after), places=6)

    def test_float_is_untouched(self):
        stems = {"a": np.array([[1.4]], dtype=np.float32)}
        guarded, gain_db = apply_common_peak_guard(stems, "32f")
        self.assertEqual(gain_db, 0.0)
        self.assertAlmostEqual(float(guarded["a"][0, 0]), 1.4, places=5)

    def test_quiet_mix_is_untouched(self):
        stems = {"a": np.array([[0.5]], dtype=np.float32)}
        guarded, gain_db = apply_common_peak_guard(stems, "24")
        self.assertEqual(gain_db, 0.0)
        self.assertEqual(float(guarded["a"][0, 0]), 0.5)


if __name__ == "__main__":
    unittest.main()
