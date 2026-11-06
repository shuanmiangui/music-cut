"""Shared configuration and constants."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import __version__

APP_NAME = "Music Cut"
APP_VERSION = __version__

# Resolve paths relative to the checkout / app folder, not to the current working
# directory. This keeps the app working when Finder launches the .command file.
APP_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = APP_ROOT / "models" / "demucs"
OUTPUT_ROOT = APP_ROOT / "output"
CACHE_DIR = APP_ROOT / ".cache"
LOG_DIR = APP_ROOT / "logs"

MODEL_NAME = "htdemucs_6s"
MODEL_LABEL = "Demucs v4 · HTDemucs 6s"
MODEL_REPO_URL = "https://github.com/adefossez/demucs"
MODEL_LICENSE = "MIT"
MODEL_SAMPLE_RATE = 44_100
MODEL_WEIGHTS_FILENAME = "5c90dfd2-34c22ccb.th"
MODEL_WEIGHTS_URL = (
    "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/"
    "5c90dfd2-34c22ccb.th"
)
MODEL_WEIGHTS_SHA256 = (
    "34c22ccb381c6f9fdbf324f04e1e2fe21aaaf293f5ded163a162697ff9a02ddd"
)
MODEL_WEIGHTS_SIZE = 54_996_327
MODEL_BAG_YAML = "models: ['5c90dfd2']\n"

MODEL_SOURCES = ("drums", "bass", "other", "vocals", "guitar", "piano")
FIVE_STEMS = ("vocals", "drums", "bass", "piano", "other")
SIX_STEMS = ("vocals", "drums", "bass", "piano", "guitar", "other")

STEM_LABELS = {
    "vocals": "人声",
    "drums": "鼓",
    "bass": "贝斯",
    "piano": "钢琴",
    "guitar": "吉他",
    "other": "其他乐器",
}

STEM_FILE_NUMBERS = {
    "vocals": "01",
    "drums": "02",
    "bass": "03",
    "piano": "04",
    "guitar": "05",
    "other": "05",
}

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_BIT_DEPTH = "24"
SUPPORTED_EXTENSIONS = {".mp3", ".wav"}

# A common gain of at most -0.3 dBFS is applied to every integer-PCM stem only when
# one of them would otherwise exceed full scale. This prevents clipping without
# changing the relative balance between stems.
PEAK_CEILING_DBFS = -0.3
PEAK_CEILING = 10 ** (PEAK_CEILING_DBFS / 20)

MIN_DURATION_SECONDS = 0.25
MAX_DURATION_SECONDS = 60 * 60

DEFAULT_SEED = 1234


@dataclass(frozen=True)
class QualityPreset:
    key: str
    label: str
    shifts: int
    overlap: float
    hint: str


QUALITY_PRESETS: dict[str, QualityPreset] = {
    "fast": QualityPreset(
        key="fast",
        label="快速",
        shifts=1,
        overlap=0.25,
        hint="最快，适合试听和粗剪",
    ),
    "high": QualityPreset(
        key="high",
        label="高音质",
        shifts=2,
        overlap=0.50,
        hint="推荐：质量与耗时平衡",
    ),
    "max": QualityPreset(
        key="max",
        label="极致",
        shifts=4,
        overlap=0.75,
        hint="最慢，最终交付前使用",
    ),
}
DEFAULT_QUALITY = "high"


def bit_depth_label(value: str) -> str:
    return "32-bit float" if value == "32f" else "24-bit PCM"


def stem_filename(stem: str, index: int, total: int) -> str:
    """Return a sortable, NLE-friendly filename."""
    number = STEM_FILE_NUMBERS[stem]
    if stem == "other" and total > 5:
        number = "06"
    return f"{number}_{stem}.wav"
