"""Global settings loaded from ``config/settings.yaml`` + environment (.env).

Secrets (tokens, app secret) are NEVER part of Settings — they are read on
demand from the environment by the publishing layer. Only non-secret operational
config lives here.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .enums import Mode
from .errors import ConfigError
from .meta_api import (DEFAULT_GRAPH_API_VERSION, IMAGE_POST_SIZE,
                       valid_version)
from .paths import Paths


class ComfyUISettings(BaseModel):
    url: str = "http://127.0.0.1:8188"
    launch_command: str | None = None  # e.g. path to start_comfyui.bat
    auto_start: bool = True
    startup_timeout_seconds: int = 180
    poll_interval_seconds: float = 1.0
    output_dir: str | None = None  # ComfyUI's own output dir (to fetch images)
    # Local model. Default = official Stability SDXL Base 1.0 single-file
    # checkpoint (see docs/LOCAL_MODEL_SETUP.md). Overridable without code
    # changes via ICE_COMFYUI_CHECKPOINT / ICE_COMFYUI_MODEL_FAMILY.
    model_family: str = "sdxl"            # sdxl | sd15
    checkpoint: str = "sd_xl_base_1.0.safetensors"
    default_workflow: str = "sdxl_background.json"
    #: Explicit legacy fallback kept for development on small GPUs.
    legacy_checkpoint: str = "DreamShaper_8_pruned.safetensors"
    legacy_workflow: str = "sd15_background.json"
    steps: int | None = None              # None => family default
    cfg: float | None = None
    sampler_name: str | None = None
    scheduler: str | None = None
    max_retries: int = 3
    request_timeout_seconds: int = 300
    # Fallback (deterministic gradient) policy per mode. In production a ComfyUI
    # failure must NOT silently degrade quality — the job goes to NEEDS_REVIEW.
    allow_fallback_in_dry_run: bool = True
    allow_fallback_in_test: bool = True
    allow_fallback_in_production: bool = False


class RenderingSettings(BaseModel):
    # The size the renderer draws and the size the audit demands are the same
    # number by construction. Two copies would let the pipeline produce stills
    # the publisher then refuses — the drift only shows up mid-publication.
    post_size: tuple[int, int] = IMAGE_POST_SIZE  # 4:5
    story_size: tuple[int, int] = (1080, 1920)  # 9:16
    safe_margin_ratio: float = 0.08  # fraction of the shorter side
    story_top_safe_ratio: float = 0.14  # Instagram UI reserves top/bottom
    story_bottom_safe_ratio: float = 0.18
    min_font_px: int = 34
    max_lines: int = 8


class VideoSettings(BaseModel):
    reel_duration_seconds: float = 8.0    # main content (Meta documents 3 s – 15 min)
    story_duration_seconds: float = 8.0
    feed_duration_seconds: float = 8.0    # legacy 4:5 (hosted_url only)
    fps: int = 30
    ken_burns: bool = True
    ken_burns_zoom: float = 1.04          # very gentle motion
    audio_bitrate: str = "128k"
    video_crf: int = 20
    ffmpeg_path: str | None = None  # if None, use imageio-ffmpeg bundled binary
    #: Emit a silent AAC track when the page has no music, so every published
    #: file carries an audio stream without ever using third-party music.
    silent_audio_when_no_music: bool = True


class MusicSettings(BaseModel):
    enabled: bool = True
    default_volume_db: float = -18.0
    fade_in_seconds: float = 1.5
    fade_out_seconds: float = 2.0
    avoid_reuse_last_n: int = 8


class PublishingSettings(BaseModel):
    # Single source of truth: src/core/meta_api.py (see docs/META_RESUMABLE_UPLOAD.md).
    graph_api_version: str = DEFAULT_GRAPH_API_VERSION
    api_flavor: str = "instagram_login"  # instagram_login | facebook_login
    # Direct upload is the DEFAULT and needs no public hosting — with the
    # facebook_login flavor. Meta does not implement resumable upload for
    # instagram_login; check_upload_method() refuses that pairing.
    upload_method: str = "resumable"     # resumable | hosted_url
    # Which hosting the hosted_url method uses. `cloudflare_quick_tunnel`
    # needs no account, no card and no persistent host: it serves the one
    # file from localhost behind a tunnel that lives for the length of a
    # single publication. `public_base_url` is the classic case, and the
    # only one that needs ICE_PUBLIC_MEDIA_BASE_URL.
    hosted_url_provider: str = "public_base_url"
    feed_media_type: str = "IMAGE"        # IMAGE (4:5 still) | REELS
    story_media_type: str = "STORIES"
    share_reel_to_feed: bool = True
    # Resumable upload tuning
    upload_timeout_seconds: int = 600
    upload_chunk_size: int = 8 * 1024 * 1024   # streaming chunk (no full-file in RAM)
    resumable_max_retries: int = 5
    # hosted_url provider (legacy/optional): only used when upload_method=hosted_url
    public_media_base_url: str = ""
    status_poll_interval_seconds: float = 5.0
    status_poll_max_seconds: float = 300.0
    max_retries: int = 5
    backoff_base_seconds: float = 30.0
    backoff_max_seconds: float = 1800.0


class ContentGateSettings(BaseModel):
    """How strict the editorial gate is (see src/content/editorial.py).

    ``development_dataset`` lets the whole pipeline run on content that has not
    been reviewed yet — previews, renders, dry-runs. It never enables real
    publication: :meth:`Settings.require_production_ready` forces the gate on
    whenever the run mode is ``production``, whatever this says.

    ``production_dataset`` applies the gate everywhere, which is what a
    pre-production rehearsal should use to see exactly how many days are
    genuinely publishable today.
    """

    dataset_mode: str = "development_dataset"  # development_dataset | production_dataset


class QualitySettings(BaseModel):
    min_score: float = 0.75
    min_contrast_ratio: float = 4.5  # WCAG AA large text
    max_repair_attempts: int = 5
    ocr_enabled: bool = False  # optional final check only, never the primary one


class DatabaseSettings(BaseModel):
    path: str = "database/content.sqlite"
    busy_timeout_ms: int = 5000


class LoggingSettings(BaseModel):
    level: str = "INFO"
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 5


class DashboardSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class Settings(BaseModel):
    mode: Mode = Mode.DRY_RUN
    timezone: str = "Europe/Rome"
    comfyui: ComfyUISettings = Field(default_factory=ComfyUISettings)
    rendering: RenderingSettings = Field(default_factory=RenderingSettings)
    video: VideoSettings = Field(default_factory=VideoSettings)
    music: MusicSettings = Field(default_factory=MusicSettings)
    publishing: PublishingSettings = Field(default_factory=PublishingSettings)
    content: ContentGateSettings = Field(default_factory=ContentGateSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)

    # Non-persisted, resolved at load time.
    _paths: Paths | None = None

    model_config = {"arbitrary_types_allowed": True}

    @property
    def paths(self) -> Paths:
        if self._paths is None:
            self._paths = Paths.create()
        return self._paths

    def db_path(self) -> Path:
        p = Path(self.database.path)
        return p if p.is_absolute() else self.paths.root / p

    def require_production_ready(self) -> bool:
        """Whether only reviewed content may be selected.

        Always true in production: no configuration file can let un-reviewed
        content reach a real account.
        """
        if self.mode == Mode.PRODUCTION:
            return True
        return self.content.dataset_mode == "production_dataset"

    def comfyui_fallback_allowed(self) -> bool:
        """Whether the deterministic background fallback is allowed in the current mode."""
        if self.mode == Mode.PRODUCTION:
            return self.comfyui.allow_fallback_in_production
        if self.mode == Mode.TEST:
            return self.comfyui.allow_fallback_in_test
        return self.comfyui.allow_fallback_in_dry_run


def _apply_env_overrides(data: dict) -> dict:
    """Overlay a small set of environment variables onto the config dict.

    Only operational (non-secret) values are handled here.
    """
    env = os.environ
    if env.get("ICE_MODE"):
        data["mode"] = env["ICE_MODE"]
    if env.get("ICE_TIMEZONE"):
        data["timezone"] = env["ICE_TIMEZONE"]

    comfy = data.setdefault("comfyui", {})
    if env.get("ICE_COMFYUI_URL"):
        comfy["url"] = env["ICE_COMFYUI_URL"]
    if env.get("ICE_COMFYUI_LAUNCH_BAT"):
        comfy["launch_command"] = env["ICE_COMFYUI_LAUNCH_BAT"]
    if env.get("ICE_COMFYUI_OUTPUT_DIR"):
        comfy["output_dir"] = env["ICE_COMFYUI_OUTPUT_DIR"]
    if env.get("ICE_COMFYUI_CHECKPOINT"):
        comfy["checkpoint"] = env["ICE_COMFYUI_CHECKPOINT"]
    if env.get("ICE_COMFYUI_WORKFLOW"):
        comfy["default_workflow"] = env["ICE_COMFYUI_WORKFLOW"]
    if env.get("ICE_COMFYUI_MODEL_FAMILY"):
        comfy["model_family"] = env["ICE_COMFYUI_MODEL_FAMILY"]

    video = data.setdefault("video", {})
    if env.get("ICE_FFMPEG_PATH"):
        video["ffmpeg_path"] = env["ICE_FFMPEG_PATH"]

    content = data.setdefault("content", {})
    if env.get("ICE_DATASET_MODE"):
        wanted = env["ICE_DATASET_MODE"].strip()
        if wanted not in ("development_dataset", "production_dataset"):
            raise ConfigError(
                f"ICE_DATASET_MODE={wanted!r} non valido "
                f"(atteso development_dataset o production_dataset)")
        content["dataset_mode"] = wanted

    pub = data.setdefault("publishing", {})
    if env.get("ICE_PUBLIC_MEDIA_BASE_URL"):
        pub["public_media_base_url"] = env["ICE_PUBLIC_MEDIA_BASE_URL"]
    if env.get("META_GRAPH_API_VERSION"):
        version = env["META_GRAPH_API_VERSION"].strip()
        if not valid_version(version):
            raise ConfigError(
                f"META_GRAPH_API_VERSION={version!r} non è una versione Graph API "
                f"valida (atteso il formato 'vNN.N', per esempio "
                f"{DEFAULT_GRAPH_API_VERSION!r})")
        pub["graph_api_version"] = version

    dash = data.setdefault("dashboard", {})
    if env.get("ICE_DASHBOARD_HOST"):
        dash["host"] = env["ICE_DASHBOARD_HOST"]
    if env.get("ICE_DASHBOARD_PORT"):
        dash["port"] = int(env["ICE_DASHBOARD_PORT"])
    return data


def load_settings(paths: Paths | None = None, *, load_dotenv: bool = True) -> Settings:
    """Load settings from YAML + environment. Missing YAML → sensible defaults."""
    paths = paths or Paths.create()

    if load_dotenv:
        try:
            from dotenv import load_dotenv as _ld

            _ld(paths.root / ".env")
        except Exception:
            pass  # dotenv is optional at runtime

    cfg_file = paths.config / "settings.yaml"
    data: dict = {}
    if cfg_file.exists():
        try:
            data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid config/settings.yaml: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("config/settings.yaml must be a mapping at the top level.")

    data = _apply_env_overrides(data)
    try:
        settings = Settings(**data)
    except Exception as e:
        raise ConfigError(f"Invalid settings: {e}") from e
    settings._paths = paths
    return settings
