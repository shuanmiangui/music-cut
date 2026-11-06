"""Command-line interface for Music Cut."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .config import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_BIT_DEPTH,
    DEFAULT_QUALITY,
    DEFAULT_SAMPLE_RATE,
    MODEL_LABEL,
    OUTPUT_ROOT,
    QUALITY_PRESETS,
    SUPPORTED_EXTENSIONS,
    bit_depth_label,
)
from .errors import MusicCutError, SeparationCancelled
from .progress import ProgressUpdate
from .separator import StemSeparator

STAGE_NAMES = {
    "loading": "准备",
    "separating": "分离",
    "writing": "导出",
    "done": "完成",
}


def _format_seconds(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


class ConsoleReporter:
    def __init__(self) -> None:
        self.tty = bool(getattr(sys.stderr, "isatty", lambda: False)())
        self.started = time.monotonic()
        self.stage = ""
        self.last_percent = -1
        self.last_draw = 0.0

    def _clear(self) -> None:
        if self.tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

    def _bar(self, fraction: float, width: int = 24) -> str:
        fraction = max(0.0, min(1.0, fraction))
        filled = int(round(width * fraction))
        return "█" * filled + "░" * (width - filled)

    def __call__(self, update: ProgressUpdate) -> None:
        now = time.monotonic()
        percent = int(round(update.fraction * 100))
        changed_stage = update.stage != self.stage
        milestone = percent // 10 > self.last_percent // 10
        if not changed_stage and not milestone and now - self.last_draw < 0.15:
            if not (self.tty and update.stage == "separating"):
                return
        if changed_stage:
            self._clear()
            self.stage = update.stage
            self.last_percent = -1
            self.last_draw = now

        if update.stage == "done":
            return
        if self.tty:
            elapsed = now - self.started
            eta = ""
            if 0.05 < update.fraction < 0.99 and update.stage == "separating":
                remaining = elapsed * (1 - update.fraction) / update.fraction
                if remaining >= 1:
                    eta = f" · 剩余约 {_format_seconds(remaining)}"
            message = update.message
            if len(message) > 48:
                message = message[:47] + "…"
            line = (
                f"[{STAGE_NAMES[update.stage]}] "
                f"{self._bar(update.fraction)} {percent:3d}%  "
                f"{message}{eta}"
            )
            self._clear()
            sys.stderr.write(line)
            sys.stderr.flush()
            self.last_percent = percent
            self.last_draw = now
        else:
            if changed_stage or percent // 10 > self.last_percent // 10:
                print(
                    f"[{STAGE_NAMES[update.stage]}] {percent:3d}% {update.message}",
                    file=sys.stderr,
                )
                self.last_percent = percent
                self.last_draw = now

    def finish(self, *, success: bool = True) -> None:
        self._clear()
        elapsed = time.monotonic() - self.started
        label = "完成" if success else "已停止"
        print(f"{label} · 耗时 {_format_seconds(elapsed)}", file=sys.stderr)


def _configure_logging(verbose: bool) -> Path:
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "musiccut.log"
    handler = RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(handler)
    if verbose:
        console = logging.StreamHandler()
        console.setLevel(logging.DEBUG)
        console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        root.addHandler(console)
    return log_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="musiccut",
        description="Music Cut：把 MP3 / WAV 拆分为人声、鼓、贝斯、钢琴、其他乐器五条无损 WAV 分轨。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  musiccut song.mp3\n"
            "  musiccut song.mp3 -o ~/Movies/项目/音频\n"
            "  musiccut a.mp3 b.wav --quality max --sample-rate 48000\n"
            "  musiccut song.wav --keep-guitar\n\n"
            "输出：<输出目录>/<歌曲名>_stems/01_vocals.wav … 05_other.wav\n"
            "模型：Demucs v4 HTDemucs 6s（MIT），本地运行，首次使用需下载约 55 MB 权重。"
        ),
    )
    parser.add_argument("inputs", nargs="*", help="一个或多个 .mp3 / .wav 文件")
    parser.add_argument(
        "-o",
        "--out-dir",
        default=str(OUTPUT_ROOT),
        help=f"输出根目录（默认：{OUTPUT_ROOT}）",
    )
    parser.add_argument(
        "-q",
        "--quality",
        choices=list(QUALITY_PRESETS),
        default=DEFAULT_QUALITY,
        help=(
            "音质预设：fast（快）、high（推荐）、max（最慢）；"
            f"默认 {DEFAULT_QUALITY}"
        ),
    )
    parser.add_argument(
        "--shifts",
        type=int,
        default=None,
        help="覆盖预设：测试时随机偏移次数，越多越稳但越慢（1-10）",
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=None,
        help="覆盖预设：分段重叠比例（0-1，推荐 0.25-0.75）",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help=f"输出采样率，默认 {DEFAULT_SAMPLE_RATE} Hz（视频常用 48000）",
    )
    parser.add_argument(
        "--bit-depth",
        choices=["24", "32f"],
        default=DEFAULT_BIT_DEPTH,
        help="WAV 位深：24 = 24-bit PCM（兼容性最好），32f = 32-bit float（不过载）",
    )
    parser.add_argument(
        "--keep-guitar",
        action="store_true",
        help="额外保留独立吉他轨（默认吉他合并进 other）",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "mps", "cuda", "cpu"],
        default="auto",
        help="计算设备：auto 会优先使用 Apple GPU / CUDA / CPU",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="在终端显示调试日志"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {APP_VERSION}"
    )
    return parser


def _print_header(args: argparse.Namespace, device_label: str, log_path: Path) -> None:
    preset = QUALITY_PRESETS[args.quality]
    shifts = preset.shifts if args.shifts is None else args.shifts
    overlap = preset.overlap if args.overlap is None else args.overlap
    quality_name = preset.label if args.shifts is None and args.overlap is None else "自定义"
    print()
    print(f"{APP_NAME} {APP_VERSION} · 本地五轨分离")
    print(f"模型  {MODEL_LABEL}  ·  {device_label}")
    print(
        f"音质  {quality_name}（shifts={shifts}, overlap={overlap:.2f}）"
        f"  ·  导出 {args.sample_rate} Hz / {bit_depth_label(args.bit_depth)}"
    )
    print(f"输出  {Path(args.out_dir).expanduser()}")
    print(f"日志  {log_path}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.inputs:
        parser.print_help()
        return 2

    log_path = _configure_logging(args.verbose)
    cancel_event = threading.Event()

    valid_inputs: list[Path] = []
    for raw in args.inputs:
        path = Path(raw).expanduser()
        if not path.exists():
            print(f"跳过：找不到文件 {path}", file=sys.stderr)
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            print(f"跳过：只支持 MP3 / WAV，收到 {path.name}", file=sys.stderr)
            continue
        valid_inputs.append(path)
    if not valid_inputs:
        print("没有可处理的 MP3 / WAV 文件。", file=sys.stderr)
        return 2

    try:
        separator = StemSeparator(device=args.device, cancel_event=cancel_event)
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except MusicCutError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    _print_header(args, separator.device_label, log_path)
    failures = 0
    successes = []
    total = len(valid_inputs)

    for index, source in enumerate(valid_inputs, start=1):
        print(f"[{index}/{total}] {source.name}", flush=True)
        reporter = ConsoleReporter()
        try:
            result = separator.separate_file(
                source,
                output_root=args.out_dir,
                quality=args.quality,
                shifts=args.shifts,
                overlap=args.overlap,
                sample_rate=args.sample_rate,
                bit_depth=args.bit_depth,
                keep_guitar=args.keep_guitar,
                progress=reporter,
            )
            reporter.finish()
            successes.append(result)
            print(f"  分轨  {result.output_dir}")
            for stem in result.stems:
                print(
                    f"    {stem.label:<4}  {stem.path.name:<16} "
                    f"{_format_size(stem.size_bytes)}  "
                    f"峰值 {stem.peak_dbfs:5.1f} dBFS"
                )
            if result.peak_guard_gain_db < 0:
                print(
                    f"  防削波  所有分轨统一降低 "
                    f"{abs(result.peak_guard_gain_db):.2f} dB（相对比例不变）"
                )
            for warning in result.warnings:
                print(f"  提示  {warning}")
            print()
        except SeparationCancelled:
            reporter.finish(success=False)
            print("\n已取消。", file=sys.stderr)
            return 130
        except KeyboardInterrupt:
            cancel_event.set()
            reporter.finish(success=False)
            print("\n已取消。", file=sys.stderr)
            return 130
        except MusicCutError as exc:
            reporter.finish(success=False)
            failures += 1
            print(f"失败：{exc}\n", file=sys.stderr)
            logging.getLogger(__name__).exception("Separation failed for %s", source)
        except Exception as exc:
            reporter.finish(success=False)
            failures += 1
            print(f"失败：发生未预期错误：{exc}", file=sys.stderr)
            print(f"详细日志：{log_path}", file=sys.stderr)
            logging.getLogger(__name__).exception("Unexpected failure for %s", source)
            print()

    if successes and not failures:
        print(
            f"全部完成：{len(successes)} 首 / "
            f"{sum(len(result.stems) for result in successes)} 个分轨"
        )
        if len(successes) == 1:
            print(f"打开输出文件夹：open \"{successes[0].output_dir}\"")
        else:
            print(f"输出根目录：{Path(args.out_dir).expanduser().resolve()}")
        return 0
    if successes:
        print(f"完成 {len(successes)} 首，失败 {failures} 首。", file=sys.stderr)
        return 1
    print("没有成功导出分轨。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
