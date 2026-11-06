"""User-facing exception types."""

from __future__ import annotations


class MusicCutError(RuntimeError):
    """Base class for errors that should be shown without a traceback."""


class InputAudioError(MusicCutError):
    """The input file is missing, unsupported or unreadable."""


class ModelError(MusicCutError):
    """The separation model could not be downloaded or loaded."""


class SeparationCancelled(MusicCutError):
    """The user cancelled the current job."""
