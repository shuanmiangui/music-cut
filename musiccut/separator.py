"""Five-stem separation built on Demucs v4 (HTDemucs 6-source)."""

from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .audio_io import (
    AudioData,
    expected_output_frames,
    fit_frames,
    load_audio,
    peak_dbfs,
    resample_audio,
    rms_dbfs,
    write_wav_atomic,
)
from .config import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_QUALITY,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SEED,
    FIVE_STEMS,
    MAX_DURATION_SECONDS,
    MIN_DURATION_SECONDS,
    MODEL_DIR,
    MODEL_LABEL,
    MODEL_LICENSE,
    MODEL_NAME,
    MODEL_SAMPLE_RATE,
    MODEL_SOURCES,
    MODEL_WEIGHTS_FILENAME,
    MODEL_WEIGHTS_SHA256,
    OUTPUT_ROOT,
    PEAK_CEILING,
    PEAK_CEILING_DBFS,
    QUALITY_PRESETS,
    SIX_STEMS,
    STEM_LABELS,
    bit_depth_label,
    stem_filename,
)
from .errors import InputAudioError, ModelError, MusicCutError, SeparationCancelled
from .model_store import ensure_model
from .progress import ProgressCallback, report

logger = logging.getLogger(__name__)

DEVICE_LABELS = {
    "mps": "Apple GPU（MPS）",
    "cuda": "NVIDIA GPU（CUDA）",
    "cpu": "CPU",
}


@dataclass
class StemFile:
    stem: str
    label: str
    path: Path
    peak_dbfs: float
    rms_dbfs: float
    frames: int
    channels: int
    size_bytes: int


@dataclass
class SeparationResult:
    source_path: Path
    output_dir: Path
    metadata_path: Path
    stems: list[StemFile]
    sample_rate: int
    bit_depth: str
    duration: float
    device: str
    device_label: str
    shifts: int
    overlap: float
    elapsed_seconds: float
    peak_guard_gain_db: float
    warnings: list[str] = field(default_factory=list)


def _resolve_device(requested: str) -> str:
    import torch

    requested = (requested or "auto").lower()
    if requested == "auto":
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise MusicCutError("当前环境没有可用的 CUDA 显卡，请改用 --device cpu。")
        return "cuda"
    if requested == "mps":
        if not (
            getattr(torch.backends, "mps", None)
            and torch.backends.mps.is_available()
        ):
            raise MusicCutError("当前环境没有可用的 Apple MPS GPU，请改用 --device cpu。")
        return "mps"
    if requested == "cpu":
        return "cpu"
    raise MusicCutError(f"未知设备：{requested}（可选 auto / mps / cuda / cpu）")


def _stems_to_numpy(stems: dict[str, Any]) -> dict[str, np.ndarray]:
    output: dict[str, np.ndarray] = {}
    for name, tensor in stems.items():
        array = tensor.detach().cpu().float().numpy()
        if array.ndim == 1:
            array = array[None, :]
        if array.ndim != 2:
            raise MusicCutError(f"模型输出的“{name}”维度异常：{array.shape}")
        output[name] = np.ascontiguousarray(array, dtype=np.float32)
    return output


def _safe_folder_name(name: str) -> str:
    name = re.sub(r"[/\\:\x00-\x1f\x7f]", "_", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "audio"
    return name[:100]


def _make_output_dir(root: Path, source: Path) -> Path:
    root = root.expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MusicCutError(f"无法创建输出目录 {root}：{exc}") from exc
    if not root.is_dir():
        raise MusicCutError(f"输出位置不是文件夹：{root}")
    if not os.access(root, os.W_OK):
        raise MusicCutError(f"输出目录不可写：{root}")

    base = f"{_safe_folder_name(source.stem)}_stems"
    candidate = root / base
    counter = 2
    while True:
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = root / f"{base}_{counter}"
            counter += 1
        except OSError as exc:
            raise MusicCutError(f"无法创建输出文件夹 {candidate}：{exc}") from exc


def apply_common_peak_guard(
    stems: dict[str, np.ndarray], bit_depth: str
) -> tuple[dict[str, np.ndarray], float]:
    """Prevent integer-PCM clipping without changing inter-stem balance.

    The separator can legitimately produce a stem whose sample peak exceeds 0 dBFS
    while the stems still sum to a normal-level mix. Clamping that stem would
    distort it, and normalizing stems independently would destroy the balance.
    Instead, if any stem exceeds the ceiling, one common gain is applied to all.
    Float output is left untouched so it can preserve values above 1.0.
    """
    if bit_depth != "24" or not stems:
        return stems, 0.0
    max_peak = max(
        (float(np.max(np.abs(array))) for array in stems.values()), default=0.0
    )
    if max_peak <= PEAK_CEILING or max_peak <= 0.0:
        return stems, 0.0
    gain = PEAK_CEILING / max_peak
    gain_db = 20.0 * math.log10(gain)
    return (
        {
            name: np.ascontiguousarray(array * gain, dtype=np.float32)
            for name, array in stems.items()
        },
        gain_db,
    )


class StemSeparator:
    """Keep one loaded model and reuse it for multiple tracks."""

    def __init__(
        self,
        *,
        model_dir: Path = MODEL_DIR,
        device: str = "auto",
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.device = _resolve_device(device)
        self.cancel_event = cancel_event or threading.Event()
        self._separator = None
        self._segment_seconds = 7.8
        if self.device == "cpu":
            # M4 has 4 performance + 6 efficiency cores. Oversubscribing all 10
            # threads is usually slower than a moderate pool for conv/STFT work.
            try:
                import torch

                torch.set_num_threads(max(1, min(8, os.cpu_count() or 4)))
            except Exception:
                pass

    @property
    def device_label(self) -> str:
        return DEVICE_LABELS.get(self.device, self.device.upper())

    def set_cancel_event(self, event: threading.Event) -> None:
        self.cancel_event = event

    def _ensure_loaded(
        self,
        *,
        shifts: int,
        overlap: float,
        progress: ProgressCallback | None,
        callback,
    ) -> None:
        if self._separator is not None:
            self._separator.update_parameter(
                device=self.device,
                shifts=shifts,
                overlap=overlap,
                split=True,
                jobs=0,
                progress=False,
                callback=callback,
            )
            return

        def download_progress(fraction: float, message: str) -> None:
            report(progress, "loading", 0.02 + 0.04 * fraction, message)

        ensure_model(self.model_dir, progress=download_progress)
        from demucs.api import Separator

        report(progress, "loading", 0.06, "正在加载 Demucs 模型…")
        try:
            separator = Separator(
                model=MODEL_NAME,
                repo=self.model_dir,
                device=self.device,
                shifts=shifts,
                overlap=overlap,
                split=True,
                jobs=0,
                progress=False,
                callback=callback,
            )
        except Exception as exc:
            raise ModelError(
                "Demucs 模型加载失败。请确认 models/demucs 文件完整，"
                "或重新运行 setup.command。\n"
                f"错误：{exc}"
            ) from exc

        model = separator.model
        segment = getattr(model, "segment", None)
        if segment is None and hasattr(model, "models") and model.models:
            segment = max(
                (getattr(sub, "segment", 7.8) for sub in model.models),
                default=7.8,
            )
        try:
            self._segment_seconds = float(segment) if segment else 7.8
        except (TypeError, ValueError):
            self._segment_seconds = 7.8
        self._separator = separator
        report(progress, "loading", 0.08, "模型已加载")

    def _make_demucs_callback(
        self,
        *,
        shifts: int,
        progress: ProgressCallback | None,
    ):
        last_fraction = 0.0
        segment_samples = max(1, int(self._segment_seconds * MODEL_SAMPLE_RATE))

        def callback(data: dict[str, Any]) -> None:
            nonlocal last_fraction
            if self.cancel_event.is_set():
                raise SeparationCancelled("已取消")
            if not isinstance(data, dict):
                return
            shift_idx = int(data.get("shift_idx", 0) or 0)
            offset = int(data.get("segment_offset", 0) or 0)
            audio_length = max(1, int(data.get("audio_length", 1) or 1))
            state = data.get("state", "end")
            if state == "end":
                within = min(1.0, (offset + segment_samples) / audio_length)
            else:
                within = min(1.0, offset / audio_length)
            fraction = (shift_idx + within) / max(1, shifts)
            fraction = max(last_fraction, min(1.0, fraction))
            last_fraction = fraction
            report(
                progress,
                "separating",
                0.10 + 0.72 * fraction,
                f"第 {shift_idx + 1}/{shifts} 遍 · {fraction * 100:.0f}%",
            )

        return callback

    def _run_demucs(
        self,
        audio: AudioData,
        *,
        shifts: int,
        overlap: float,
        progress: ProgressCallback | None,
    ) -> dict[str, np.ndarray]:
        import torch

        callback = self._make_demucs_callback(shifts=shifts, progress=progress)
        self._ensure_loaded(
            shifts=shifts, overlap=overlap, progress=progress, callback=callback
        )
        self._separator.update_parameter(
            device=self.device,
            shifts=shifts,
            overlap=overlap,
            split=True,
            jobs=0,
            progress=False,
            callback=callback,
        )
        random.seed(DEFAULT_SEED)
        np.random.seed(DEFAULT_SEED)
        torch.manual_seed(DEFAULT_SEED)
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            try:
                torch.mps.manual_seed(DEFAULT_SEED)
            except Exception:
                pass

        tensor = torch.from_numpy(audio.samples)
        _, stems = self._separator.separate_tensor(tensor, sr=audio.sample_rate)
        return _stems_to_numpy(stems)

    def separate_file(
        self,
        input_path: str | Path,
        *,
        output_root: str | Path = OUTPUT_ROOT,
        quality: str = DEFAULT_QUALITY,
        shifts: int | None = None,
        overlap: float | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        bit_depth: str = "24",
        keep_guitar: bool = False,
        progress: ProgressCallback | None = None,
    ) -> SeparationResult:
        started = time.monotonic()
        source = Path(input_path).expanduser()
        preset = QUALITY_PRESETS.get(quality)
        if preset is None:
            raise MusicCutError(f"未知音质预设：{quality}")
        shifts = preset.shifts if shifts is None else int(shifts)
        overlap = preset.overlap if overlap is None else float(overlap)
        if not 1 <= shifts <= 10:
            raise MusicCutError("shifts 必须在 1 到 10 之间。")
        if not 0.0 <= overlap < 1.0:
            raise MusicCutError("overlap 必须在 0（含）到 1（不含）之间。")
        if bit_depth not in {"24", "32f"}:
            raise MusicCutError("位深只支持 24 或 32f。")
        try:
            sample_rate = int(sample_rate)
        except (TypeError, ValueError) as exc:
            raise MusicCutError("采样率必须是整数。") from exc
        if not 8000 <= sample_rate <= 192000:
            raise MusicCutError("采样率必须在 8000 到 192000 Hz 之间。")

        if self.cancel_event.is_set():
            raise SeparationCancelled("已取消")
        report(progress, "loading", 0.01, "读取音频…")
        audio = load_audio(source)
        if audio.duration < MIN_DURATION_SECONDS:
            raise InputAudioError(
                f"音频太短（{audio.duration:.2f} 秒），请使用至少 "
                f"{MIN_DURATION_SECONDS:.2f} 秒的音频。"
            )
        if audio.duration > MAX_DURATION_SECONDS:
            raise InputAudioError(
                f"音频太长（{audio.duration / 60:.1f} 分钟），"
                "为保证稳定性和内存安全，当前上限为 60 分钟。"
            )
        report(progress, "loading", 0.04, "准备输出文件夹…")
        output_dir = _make_output_dir(Path(output_root), source)

        written_files: list[Path] = []
        try:
            report(
                progress,
                "loading",
                0.05,
                f"使用 {MODEL_LABEL} · {self.device_label}",
            )

            def inference() -> dict[str, np.ndarray]:
                return self._run_demucs(
                    audio, shifts=shifts, overlap=overlap, progress=progress
                )

            try:
                stems = inference()
            except SeparationCancelled:
                raise
            except Exception as exc:
                # Some PyTorch/MPS combinations hit unsupported ops or transient
                # memory errors. A CPU retry is slow but much better than failing a job.
                if self.device == "mps" and not self.cancel_event.is_set():
                    logger.warning("MPS separation failed (%s); retrying on CPU", exc)
                    self.device = "cpu"
                    self._separator = None
                    try:
                        import torch

                        torch.set_num_threads(max(1, min(8, os.cpu_count() or 4)))
                    except Exception:
                        pass
                    report(
                        progress,
                        "loading",
                        0.08,
                        "GPU 分离失败，自动改用 CPU 重试（会更慢）…",
                    )
                    stems = inference()
                else:
                    raise

            if self.cancel_event.is_set():
                raise SeparationCancelled("已取消")

            missing = [name for name in MODEL_SOURCES if name not in stems]
            if missing:
                raise MusicCutError(f"模型输出缺少分轨：{', '.join(missing)}")

            if keep_guitar:
                ordered = list(SIX_STEMS)
            else:
                ordered = list(FIVE_STEMS)
                stems["other"] = stems["other"] + stems["guitar"]
                stems.pop("guitar", None)

            target_frames = expected_output_frames(
                audio.frames, audio.sample_rate, sample_rate
            )
            report(progress, "writing", 0.84, "重采样并整理分轨…")
            processed: dict[str, np.ndarray] = {}
            for index, name in enumerate(ordered):
                if self.cancel_event.is_set():
                    raise SeparationCancelled("已取消")
                array = stems[name]
                array = resample_audio(
                    array, MODEL_SAMPLE_RATE, sample_rate, quality="VHQ"
                )
                array = fit_frames(array, target_frames)
                processed[name] = np.ascontiguousarray(array, dtype=np.float32)
                report(
                    progress,
                    "writing",
                    0.84 + 0.10 * (index + 1) / len(ordered),
                    f"整理分轨 · {STEM_LABELS.get(name, name)} {index + 1}/{len(ordered)}",
                )

            processed, peak_guard_gain_db = apply_common_peak_guard(
                processed, bit_depth
            )
            if peak_guard_gain_db < 0.0:
                logger.info(
                    "Applied global peak guard %.2f dB to keep 24-bit stems unclipped",
                    peak_guard_gain_db,
                )

            report(progress, "writing", 0.95, "写入无损 WAV…")
            stem_records: list[StemFile] = []
            for index, name in enumerate(ordered):
                if self.cancel_event.is_set():
                    raise SeparationCancelled("已取消")
                filename = stem_filename(name, index, len(ordered))
                path = output_dir / filename
                written_files.append(path)
                size = write_wav_atomic(
                    path,
                    processed[name],
                    sample_rate,
                    bit_depth=bit_depth,
                )
                array = processed[name]
                stem_records.append(
                    StemFile(
                        stem=name,
                        label=STEM_LABELS.get(name, name),
                        path=path,
                        peak_dbfs=peak_dbfs(array),
                        rms_dbfs=rms_dbfs(array),
                        frames=int(array.shape[1]),
                        channels=int(array.shape[0]),
                        size_bytes=size,
                    )
                )
                report(
                    progress,
                    "writing",
                    0.95 + 0.035 * (index + 1) / len(ordered),
                    f"写入 WAV · {index + 1}/{len(ordered)}",
                )

            elapsed = time.monotonic() - started
            metadata_path = output_dir / "separation.json"
            metadata = self._build_metadata(
                source=source,
                audio=audio,
                output_dir=output_dir,
                stems=stem_records,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                shifts=shifts,
                overlap=overlap,
                keep_guitar=keep_guitar,
                elapsed=elapsed,
                peak_guard_gain_db=peak_guard_gain_db,
            )
            try:
                metadata_path.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False)
                    + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                raise MusicCutError(f"无法写入分轨信息文件：{exc}") from exc
            written_files.append(metadata_path)

            report(progress, "done", 1.0, "分轨完成")
            return SeparationResult(
                source_path=source,
                output_dir=output_dir,
                metadata_path=metadata_path,
                stems=stem_records,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                duration=target_frames / float(sample_rate),
                device=self.device,
                device_label=self.device_label,
                shifts=shifts,
                overlap=overlap,
                elapsed_seconds=time.monotonic() - started,
                peak_guard_gain_db=peak_guard_gain_db,
                warnings=list(audio.warnings),
            )
        except BaseException:
            for path in written_files:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                if output_dir.exists() and not any(output_dir.iterdir()):
                    output_dir.rmdir()
            except OSError:
                pass
            raise

    def _build_metadata(
        self,
        *,
        source: Path,
        audio: AudioData,
        output_dir: Path,
        stems: list[StemFile],
        sample_rate: int,
        bit_depth: str,
        shifts: int,
        overlap: float,
        keep_guitar: bool,
        elapsed: float,
        peak_guard_gain_db: float,
    ) -> dict[str, Any]:
        subtype = "FLOAT" if bit_depth == "32f" else "PCM_24"
        return {
            "schema_version": 1,
            "app": {"name": APP_NAME, "version": APP_VERSION},
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": {
                "path": str(source.resolve()),
                "filename": source.name,
                "duration_seconds": round(audio.duration, 6),
                "sample_rate": audio.sample_rate,
                "channels": audio.source_channels,
                "decode_backend": audio.backend,
                "warnings": audio.warnings,
            },
            "model": {
                "name": MODEL_NAME,
                "label": MODEL_LABEL,
                "repository": "https://github.com/adefossez/demucs",
                "license": MODEL_LICENSE,
                "weights_file": MODEL_WEIGHTS_FILENAME,
                "weights_sha256": MODEL_WEIGHTS_SHA256,
                "separation_sample_rate": MODEL_SAMPLE_RATE,
                "experimental_piano": True,
            },
            "separation": {
                "device": self.device,
                "device_label": self.device_label,
                "shifts": shifts,
                "overlap": overlap,
                "seed": DEFAULT_SEED,
                "elapsed_seconds": round(elapsed, 3),
                "keep_guitar_separate": keep_guitar,
            },
            "export": {
                "format": "WAV",
                "lossless": True,
                "sample_rate": sample_rate,
                "bit_depth": bit_depth_label(bit_depth),
                "subtype": subtype,
                "channels": 2,
                "frames": stems[0].frames if stems else 0,
                "duration_seconds": round(stems[0].frames / sample_rate, 6)
                if stems
                else 0.0,
                "peak_guard": {
                    "applied": peak_guard_gain_db < 0.0,
                    "gain_db": round(peak_guard_gain_db, 4),
                    "ceiling_dbfs": PEAK_CEILING_DBFS,
                    "description": (
                        "24-bit PCM only: one common gain was applied to all stems "
                        "to prevent clipping. Relative balance is unchanged."
                    ),
                },
            },
            "stems": [
                {
                    "stem": item.stem,
                    "label": item.label,
                    "file": item.path.name,
                    "peak_dbfs": round(item.peak_dbfs, 3),
                    "rms_dbfs": round(item.rms_dbfs, 3),
                    "channels": item.channels,
                    "frames": item.frames,
                    "sample_rate": sample_rate,
                    "bit_depth": bit_depth_label(bit_depth),
                    "size_bytes": item.size_bytes,
                }
                for item in stems
            ],
            "notes": [
                "所有分轨使用同一增益，相对音量保持不变；相加可重建接近原曲的混音。",
                "钢琴分轨来自 Demucs 官方实验性 6-source 模型，密集编曲中可能有串音或残留。",
                "默认将 guitar 合并进 other；需要单独吉他轨时使用 --keep-guitar。",
                "没有做降噪、EQ、压缩或单轨响度归一化，以免改变音色。",
            ],
        }
