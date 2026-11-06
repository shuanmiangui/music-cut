from __future__ import annotations

import contextlib
import io
import unittest

from musiccut.cli import build_parser, main


class CLITests(unittest.TestCase):
    def test_parser_defaults(self):
        args = build_parser().parse_args(["song.mp3"])
        self.assertEqual(args.inputs, ["song.mp3"])
        self.assertEqual(args.quality, "high")
        self.assertEqual(args.sample_rate, 48000)
        self.assertEqual(args.bit_depth, "24")
        self.assertFalse(args.keep_guitar)

    def test_no_inputs_shows_help(self):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([])
        self.assertEqual(code, 2)
        self.assertIn("Music Cut", out.getvalue())

    def test_missing_input_is_reported(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["definitely-missing-file.wav"])
        self.assertEqual(code, 2)
        self.assertIn("没有可处理", err.getvalue())


if __name__ == "__main__":
    unittest.main()
