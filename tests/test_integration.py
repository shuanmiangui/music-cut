from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from musiccut.separator import StemSeparator


@unittest.skipUnless(
    os.environ.get("MUSICCUT_RUN_INTEGRATION") == "1",
    "set MUSICCUT_RUN_INTEGRATION=1 to run the model integration test",
)
class IntegrationTests(unittest.TestCase):
    def test_end_to_end_five_stems(self):
        with tempfile.TemporaryDirectory(prefix="musiccut-integration-") as temp:
            root = Path(temp)
            sr = 44100
            duration = 3.0
            t = np.arange(int(sr * duration), dtype=np.float32) / sr
            rng = np.random.default_rng(7)
            music = (
                0.12 * np.sin(2 * np.pi * 440 * t)
                + 0.10 * np.sin(2 * np.pi * 110 * t)
                + 0.05 * rng.standard_normal(t.size)
            ).astype(np.float32)
            # simple transients so the drums stem has something to learn
            for beat in np.arange(0.1, duration, 0.5):
                start = int(beat * sr)
                n = min(int(0.08 * sr), t.size - start)
                music[start : start + n] += (
                    0.35
                    * np.sin(2 * np.pi * 80 * np.arange(n) / sr)
                    * np.exp(-np.arange(n) / (0.025 * sr))
                ).astype(np.float32)
            music = np.clip(music, -0.95, 0.95)
            stereo = np.column_stack([music, music])
            source = root / "测试 mix.mp3"
            wav_source = root / "测试 mix.wav"
            sf.write(wav_source, stereo, sr, subtype="PCM_16")
            # Exercise the MP3 path through FFmpeg, no external tool needed.
            import imageio_ffmpeg
            import subprocess

            subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(wav_source),
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    str(source),
                ],
                check=True,
            )
            progress = []
            separator = StemSeparator(device="auto")
            result = separator.separate_file(
                source,
                output_root=root / "out",
                quality="fast",
                sample_rate=48000,
                bit_depth="24",
                progress=lambda update: progress.append(update),
            )
            self.assertEqual(
                [stem.stem for stem in result.stems],
                ["vocals", "drums", "bass", "piano", "other"],
            )
            self.assertEqual(len(result.stems), 5)
            self.assertGreaterEqual(len({p.stage for p in progress}), 4)
            expected_frames = int(round(44100 * duration)) * 48000 // 44100
            for stem in result.stems:
                self.assertTrue(stem.path.is_file(), stem.path)
                info = sf.info(stem.path)
                self.assertEqual(info.samplerate, 48000)
                self.assertEqual(info.channels, 2)
                self.assertEqual(info.frames, expected_frames)
                self.assertEqual(info.subtype, "PCM_24")
                data, _ = sf.read(stem.path, dtype="float32", always_2d=True)
                self.assertLessEqual(float(np.abs(data).max()), 1.0)
            metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], 1)
            self.assertEqual(metadata["model"]["name"], "htdemucs_6s")
            self.assertEqual(len(metadata["stems"]), 5)
            self.assertEqual(metadata["export"]["sample_rate"], 48000)
            self.assertTrue(metadata["export"]["lossless"])


if __name__ == "__main__":
    unittest.main()
