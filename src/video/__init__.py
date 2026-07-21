"""Video assembly (FFmpeg): still image + Ken Burns + baked-in licensed music."""

from .ffmpeg import resolve_ffmpeg, probe_media, FFProbeInfo
from .builder import VideoBuilder, VideoResult

__all__ = ["resolve_ffmpeg", "probe_media", "FFProbeInfo", "VideoBuilder", "VideoResult"]
