"""Typed exceptions used across the engine."""
from __future__ import annotations


class EngineError(Exception):
    """Base class for all engine errors."""


class ConfigError(EngineError):
    """Invalid or missing configuration."""


class MigrationError(EngineError):
    """Database migration failure."""


class ContentError(EngineError):
    """Content selection / validation problem."""


class ComfyUIError(EngineError):
    """ComfyUI connectivity or generation failure."""


class RenderError(EngineError):
    """Typography / image rendering failure."""


class QualityRejected(EngineError):
    """Media failed quality validation and could not be auto-repaired."""


class MusicError(EngineError):
    """Music selection / processing failure."""


class VideoError(EngineError):
    """Video assembly (ffmpeg) failure."""


class PublishError(EngineError):
    """Publishing failure. ``retryable`` distinguishes transient from terminal."""

    def __init__(self, message: str, *, retryable: bool = False, code: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code
