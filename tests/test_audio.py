from __future__ import annotations

import math
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from musiccut.audio_io import (
    expected_output_frames,
    fit_frames,
    load_audio,
    peak_dbfs,
    resample_audio,
    rms_dbfs,
    write_wav_atomic,
)


class AudioIOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="musiccut-tests-")
        cls.root = Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def _sine(self, seconds=0.5, sr=44100, channels=2, freq=440.0):
        t = np.arange(int(seconds * sr), dtype=np.float32) / sr
        mono = 0.4 * np.sin(2 * math.pi * freq * t)
        return np.column_stack([mono] * channels)

    def test_load_stereo_wav(self):
        path = self.root / "stereo.wav"
        sf.write(path, self._sine(sr=48000), 48000, subtype="PCM_24")
        audio = load_audio(path)
        self.assertEqual(audio.samples.shape, (2, 24000))
        self.assertEqual(audio.sample_rate, 48000)
        self.assertEqual(audio.source_channels, 2)
        self.assertFalse(audio.warnings)

    def test_mono_is_duplicated(self):
        path = self.root / "mono.wav"
        sf.write(path, self._sine(channels=1), 44100, subtype="PCM_16")
        audio = load_audio(path)
        self.assertEqual(audio.samples.shape[0], 2)
        np.testing.assert_allclose(audio.samples[0], audio.samples[1])
        self.assertEqual(audio.source_channels, 1)
        self.assertTrue(any("单声道" in item for item in audio.warnings))

    def test_mp3_decoding(self):
        import imageio_ffmpeg

        wav_path = self.root / "mp3-source.wav"
        mp3_path = self.root / "mp3-source.mp3"
        sf.write(wav_path, self._sine(seconds=1.0), 44100, subtype="PCM_16")
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(wav_path),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "320k",
                str(mp3_path),
            ],
            check=True,
        )
        audio = load_audio(mp3_path)
        self.assertEqual(audio.sample_rate, 44100)
        self.assertEqual(audio.samples.shape[0], 2)
        self.assertGreater(audio.samples.shape[1], 40000)

    def test_ffmpeg_fallback(self):
        path = self.root / "fallback.wav"
        sf.write(path, self._sine(seconds=0.3), 44100, subtype="PCM_16")
        audio = load_audio(path, force_ffmpeg=True)
        self.assertEqual(audio.backend, "ffmpeg")
        self.assertEqual(audio.samples.shape, (2, 13230))

    def test_resample_and_fit(self):
        samples = self._sine(seconds=0.5, sr=44100).T
        out = resample_audio(samples, 44100, 48000)
        self.assertEqual(out.shape[0], 2)
        self.assertAlmostEqual(out.shape[1], 24000, delta=2)
        fitted = fit_frames(out, 24000)
        self.assertEqual(fitted.shape, (2, 24000))
        trimmed = fit_frames(out, 1000)
        self.assertEqual(trimmed.shape, (2, 1000))
        padded = fit_frames(out[:, :100], 500)
        self.assertEqual(padded.shape, (2, 500))

    def test_expected_frames(self):
        self.assertEqual(expected_output_frames(44100, 44100, 48000), 48000)
        self.assertEqual(expected_output_frames(48000, 48000, 48000), 48000)

    def test_write_pcm24_and_float(self):
        samples = np.array([[0.5, -0.25], [0.1, -0.5]], dtype=np.float32)
        path24 = self.root / "out24.wav"
        write_wav_atomic(path24, samples, 48000, bit_depth="24")
        info = sf.info(path24)
        self.assertEqual(info.subtype, "PCM_24")
        self.assertEqual(info.frames, 2)
        read24, _ = sf.read(path24, dtype="float32", always_2d=True)
        self.assertLessEqual(float(np.abs(read24).max()), 0.51)

        hot = np.array([[1.7, -1.5], [0.2, -0.2]], dtype=np.float32)
        path32 = self.root / "out32.wav"
        write_wav_atomic(path32, hot, 48000, bit_depth="32f")
        info32 = sf.info(path32)
        self.assertEqual(info32.subtype, "FLOAT")
        read32, _ = sf.read(path32, dtype="float32", always_2d=True)
        self.assertGreater(float(read32.max()), 1.0)
        self.assertLess(float(read32.min()), -1.0)
        self.assertEqual(list(path24.parent.glob(".*.tmp.wav")), [])

    def test_level_helpers(self):
        silence = np.zeros((2, 10), dtype=np.float32)
        self.assertEqual(peak_dbfs(silence), -999.0)
        self.assertEqual(rms_dbfs(silence), -999.0)
        full = np.ones((2, 10), dtype=np.float32)
        self.assertAlmostEqual(peak_dbfs(full), 0.0, places=6)
        self.assertAlmostEqual(rms_dbfs(full), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
