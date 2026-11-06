"""Progress reporting shared by the CLI and GUI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

Stage = Literal["loading", "separating", "writing", "done"]


@dataclass(frozen=True)
class ProgressUpdate:
    stage: Stage
    fraction: float  # 0.0 .. 1.0
    message: str


ProgressCallback = Callable[[ProgressUpdate], None]


def report(
    callback: ProgressCallback | None,
    stage: Stage,
    fraction: float,
    message: str,
) -> None:
    if callback is None:
        return
    callback(
        ProgressUpdate(
            stage=stage,
            fraction=max(0.0, min(1.0, float(fraction))),
            message=message,
        )
    )
