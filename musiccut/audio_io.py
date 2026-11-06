"""Audio loading, resampling and atomic WAV writing."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import CACHE_DIR, SUPPORTED_EXTENSIONS
from .errors import InputAudioError


@dataclass
class AudioData:
    samples: np.ndarray  # float32, shape (2, frames)
    sample_rate: int
    source_channels: int
    backend: str
    warnings: list[str] = field(default_factory=list)

    @property
    def frames(self) -> int:
        return int(self.samples.shape[1])

    @property
    def duration(self) -> float:
        return self.frames / float(self.sample_rate)


def _temp_parent() -> str | None:
    """Prefer a temp folder inside the app (works with restrictive sandboxes)."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)
    except OSError:
        return None


def _ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if found:
            return found
        raise InputAudioError(
            "找不到 FFmpeg，无法读取这个音频文件。请重新运行 setup.command。"
        )


def _decode_with_ffmpeg(path: Path) -> tuple[np.ndarray, int, str]:
    """Decode any supported input to native-rate stereo float32 with FFmpeg."""
    ffmpeg = _ffmpeg_executable()
    tmp_dir = _temp_parent()
    with tempfile.TemporaryDirectory(prefix="musiccut-", dir=tmp_dir) as temp:
        wav_path = Path(temp) / "decoded.wav"
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "2",
            "-c:a",
            "pcm_f32le",
            "-f",
            "wav",
            str(wav_path),
        ]
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=60 * 30
            )
        except subprocess.TimeoutExpired as exc:
            raise InputAudioError("FFmpeg 解码超时，文件可能已损坏。") from exc
        except OSError as exc:
            raise InputAudioError(f"无法启动 FFmpeg：{exc}") from exc
        if proc.returncode != 0 or not wav_path.exists():
            detail = (proc.stderr or "").strip().splitlines()
            detail_text = detail[-1] if detail else "未知错误"
            raise InputAudioError(
                f"无法读取音频“{path.name}”（可能是文件损坏或没有音轨）：{detail_text}"
            )
        try:
            data, sample_rate = sf.read(
                wav_path, dtype="float32", always_2d=True
            )
        except Exception as exc:
            raise InputAudioError(f"FFmpeg 解码结果无法读取：{exc}") from exc
    return data, int(sample_rate), "ffmpeg"


def load_audio(path: str | Path, *, force_ffmpeg: bool = False) -> AudioData:
    """Load an MP3/WAV file as stereo float32 at its native sample rate.

    libsndfile (via soundfile) handles MP3 and WAV without external tools. FFmpeg is
    used as a robust fallback and for multichannel files.
    """
    path = Path(path).expanduser()
    if not path.exists():
        raise InputAudioError(f"找不到文件：{path}")
    if not path.is_file():
        raise InputAudioError(f"不是文件：{path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        allowed = " / ".join(sorted(SUPPORTED_EXTENSIONS))
        raise InputAudioError(f"只支持 {allowed} 输入，收到：{path.suffix or '无扩展名'}")

    warnings: list[str] = []
    backend = "soundfile"
    source_channels = 0
    sample_rate = 0
    data: np.ndarray | None = None

    if not force_ffmpeg:
        try:
            info = sf.info(str(path))
            source_channels = int(info.channels)
            sample_rate = int(info.samplerate)
            # Multichannel files go through FFmpeg so it can do a proper downmix.
            if source_channels > 2:
                raise ValueError("multichannel")
            data, sample_rate = sf.read(
                str(path), dtype="float32", always_2d=True
            )
            source_channels = int(data.shape[1])
        except Exception as exc:
            if source_channels > 2:
                warnings.append("多声道输入已由 FFmpeg 下混为立体声。")
            elif not isinstance(exc, ValueError):
                warnings.append("libsndfile 读取失败，已改用 FFmpeg。")
            data = None

    if data is None or force_ffmpeg:
        data, sample_rate, backend = _decode_with_ffmpeg(path)
        if source_channels == 0:
            try:
                source_channels = int(sf.info(str(path)).channels)
            except Exception:
                source_channels = int(data.shape[1])

    if data.size == 0:
        raise InputAudioError(f"“{path.name}”不包含任何音频采样。")
    if sample_rate <= 0:
        raise InputAudioError(f"“{path.name}”的采样率无效。")

    # soundfile gives (frames, channels); the rest of the app works channels-first.
    if data.ndim == 1:
        data = data[:, None]
    data = np.ascontiguousarray(data.T, dtype=np.float32)

    channels = data.shape[0]
    if channels == 1:
        data = np.repeat(data, 2, axis=0)
        if source_channels <= 1:
            warnings.append("单声道输入已复制为双声道，分离结果左右声道相同。")
    elif channels == 2:
        pass
    elif channels > 2:
        # FFmpeg should have handled this, but keep a safe fallback.
        data = data[:2]
        warnings.append("多声道输入仅保留前两个声道。")
    else:
        raise InputAudioError(f"“{path.name}”没有可用的声道。")

    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0:
        warnings.append(
            f"输入峰值 {20 * math.log10(peak):.1f} dBFS，超过 0 dBFS；"
            "分离前会按 Demucs 标准流程归一化，导出时不会削波。"
        )
    if peak < 1e-6:
        warnings.append("输入几乎为静音，导出的分轨也会是静音。")

    backend = "ffmpeg" if force_ffmpeg else backend
    return AudioData(
        samples=np.ascontiguousarray(data, dtype=np.float32),
        sample_rate=int(sample_rate),
        source_channels=int(source_channels or channels),
        backend=backend,
        warnings=warnings,
    )


def resample_audio(
    samples: np.ndarray, source_rate: int, target_rate: int, *, quality: str = "VHQ"
) -> np.ndarray:
    """High-quality, band-limited resampling of a (channels, frames) array."""
    if source_rate == target_rate:
        return np.ascontiguousarray(samples, dtype=np.float32)
    if samples.ndim != 2:
        raise ValueError("samples must have shape (channels, frames)")
    import soxr

    # soxr expects (frames, channels). VHQ uses a very high quality sinc filter.
    resampled = soxr.resample(
        samples.T, float(source_rate), float(target_rate), quality=quality
    )
    return np.ascontiguousarray(resampled.T, dtype=np.float32)


def fit_frames(samples: np.ndarray, frames: int) -> np.ndarray:
    """Trim or zero-pad the last axis to exactly ``frames`` samples."""
    if samples.ndim != 2:
        raise ValueError("samples must have shape (channels, frames)")
    current = int(samples.shape[1])
    if current == frames:
        return samples
    if current > frames:
        return np.ascontiguousarray(samples[:, :frames])
    if current == 0:
        return np.zeros((samples.shape[0], frames), dtype=np.float32)
    pad = frames - current
    return np.pad(samples, ((0, 0), (0, pad)), mode="constant")


def peak_dbfs(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak <= 0.0:
        return -999.0
    return 20.0 * math.log10(peak)


def rms_dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -999.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    if rms <= 0.0:
        return -999.0
    return 20.0 * math.log10(rms)


def expected_output_frames(
    input_frames: int, input_rate: int, output_rate: int
) -> int:
    return int(round(input_frames * float(output_rate) / float(input_rate)))


def write_wav_atomic(
    path: Path,
    samples: np.ndarray,
    sample_rate: int,
    *,
    bit_depth: str = "24",
) -> int:
    """Write a WAV via a temporary file, returning the final file size in bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if samples.ndim == 1:
        samples = samples[None, :]
    if samples.ndim != 2:
        raise ValueError("samples must have shape (channels, frames)")
    if not np.isfinite(samples).all():
        raise ValueError("refusing to write non-finite audio samples")

    subtype = "FLOAT" if bit_depth == "32f" else "PCM_24"
    if bit_depth not in {"24", "32f"}:
        raise ValueError(f"unsupported bit depth: {bit_depth}")

    temp_path = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp.wav")
    try:
        sf.write(
            str(temp_path),
            np.ascontiguousarray(samples.T, dtype=np.float32),
            int(sample_rate),
            format="WAV",
            subtype=subtype,
        )
        # Re-open and validate the header before publishing the file.
        info = sf.info(str(temp_path))
        if int(info.samplerate) != int(sample_rate):
            raise RuntimeError("written WAV has an unexpected sample rate")
        if int(info.channels) != int(samples.shape[0]):
            raise RuntimeError("written WAV has an unexpected channel count")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    return path.stat().st_size
