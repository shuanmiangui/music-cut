"""Download and verify the local Demucs checkpoint."""

from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .config import (
    MODEL_BAG_YAML,
    MODEL_DIR,
    MODEL_WEIGHTS_FILENAME,
    MODEL_WEIGHTS_SHA256,
    MODEL_WEIGHTS_SIZE,
    MODEL_WEIGHTS_URL,
)
from .errors import ModelError

DownloadProgress = Callable[[float, str], None]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            block = file.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def model_weights_path(model_dir: Path = MODEL_DIR) -> Path:
    return model_dir / MODEL_WEIGHTS_FILENAME


def model_ready(model_dir: Path = MODEL_DIR, *, verify: bool = True) -> bool:
    path = model_weights_path(model_dir)
    if not path.is_file() or path.stat().st_size != MODEL_WEIGHTS_SIZE:
        return False
    if not verify:
        return True
    try:
        return sha256_file(path) == MODEL_WEIGHTS_SHA256
    except OSError:
        return False


def _write_model_yaml(model_dir: Path) -> None:
    yaml_path = model_dir / "htdemucs_6s.yaml"
    try:
        if not yaml_path.exists() or yaml_path.read_text() != MODEL_BAG_YAML:
            yaml_path.write_text(MODEL_BAG_YAML)
    except OSError as exc:
        raise ModelError(f"无法写入模型配置目录 {model_dir}：{exc}") from exc


def _download(
    target: Path,
    *,
    progress: DownloadProgress | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    if partial.exists():
        try:
            partial.unlink()
        except OSError:
            pass

    request = urllib.request.Request(
        MODEL_WEIGHTS_URL,
        headers={"User-Agent": "MusicCut/1.0 (local music separation)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header else MODEL_WEIGHTS_SIZE
            received = 0
            with partial.open("wb") as file:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    file.write(block)
                    received += len(block)
                    if progress is not None:
                        frac = min(1.0, received / max(total, 1))
                        progress(
                            frac,
                            f"下载分离模型 {received / 1e6:.1f} / {total / 1e6:.1f} MB",
                        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass
        raise ModelError(
            "下载 Demucs 模型失败。请检查网络后重试，或重新运行 setup.command。\n"
            f"下载地址：{MODEL_WEIGHTS_URL}\n错误：{exc}"
        ) from exc

    if partial.stat().st_size != MODEL_WEIGHTS_SIZE:
        size = partial.stat().st_size
        partial.unlink(missing_ok=True)
        raise ModelError(
            f"模型下载不完整（{size} 字节，应为 {MODEL_WEIGHTS_SIZE} 字节），请重试。"
        )
    digest = sha256_file(partial)
    if digest != MODEL_WEIGHTS_SHA256:
        partial.unlink(missing_ok=True)
        raise ModelError(
            "模型校验失败，文件可能已损坏。请删除 models/demucs 后重新运行 setup.command。"
        )
    os.replace(partial, target)


def ensure_model(
    model_dir: Path = MODEL_DIR,
    *,
    progress: DownloadProgress | None = None,
    verify: bool = True,
) -> Path:
    """Ensure the official 6-source checkpoint is present and intact.

    The checkpoint is loaded through Demucs' local repository mechanism, so the
    app never has to contact Hugging Face at runtime.
    """
    model_dir = Path(model_dir)
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ModelError(f"无法创建模型目录 {model_dir}：{exc}") from exc
    _write_model_yaml(model_dir)

    target = model_weights_path(model_dir)
    if model_ready(model_dir, verify=verify):
        if progress is not None:
            progress(1.0, "分离模型已就绪")
        return target

    if target.exists():
        # Invalid or partially copied weights: replace them rather than risk loading
        # a corrupted checkpoint.
        try:
            target.unlink()
        except OSError as exc:
            raise ModelError(
                f"模型文件损坏且无法删除：{target}\n{exc}\n请手动删除后重试。"
            ) from exc

    if progress is not None:
        progress(0.0, "首次运行：下载分离模型（约 55 MB）…")
    _download(target, progress=progress)
    if progress is not None:
        progress(1.0, "分离模型下载完成")
    return target


if __name__ == "__main__":
    import sys

    def _print_progress(fraction: float, message: str) -> None:
        suffix = "\033[K" if sys.stdout.isatty() else ""
        sys.stdout.write(f"\r{int(fraction * 100):3d}%  {message}{suffix}")
        sys.stdout.flush()

    try:
        path = ensure_model(progress=_print_progress)
    except ModelError as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"\n模型就绪：{path}")
