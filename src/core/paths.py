"""Central filesystem path resolution.

All paths are derived from the project root (the directory containing
``pyproject.toml``) so the engine works regardless of the current working
directory. Tests can inject an alternate root.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def find_project_root(start: Path | None = None) -> Path:
    """Walk upwards from ``start`` until a directory with pyproject.toml is found."""
    start = (start or Path(__file__)).resolve()
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    # Fallback: two levels up from src/core/paths.py -> project root
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    """Resolved absolute paths for every well-known folder in the repo."""

    root: Path

    @classmethod
    def create(cls, root: Path | None = None) -> "Paths":
        return cls(root=(root or find_project_root()).resolve())

    # --- top-level folders -------------------------------------------------
    @property
    def accounts(self) -> Path:
        return self.root / "accounts"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def database_dir(self) -> Path:
        return self.root / "database"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def fonts(self) -> Path:
        return self.assets / "fonts"

    @property
    def music(self) -> Path:
        return self.assets / "music"

    @property
    def overlays(self) -> Path:
        return self.assets / "overlays"

    @property
    def templates(self) -> Path:
        return self.assets / "templates"

    @property
    def comfyui(self) -> Path:
        return self.root / "comfyui"

    @property
    def comfy_workflows(self) -> Path:
        return self.comfyui / "workflows"

    @property
    def comfy_prompts(self) -> Path:
        return self.comfyui / "prompts"

    @property
    def generated(self) -> Path:
        return self.root / "generated"

    @property
    def backgrounds(self) -> Path:
        return self.generated / "backgrounds"

    @property
    def posts(self) -> Path:
        return self.generated / "posts"

    @property
    def stories(self) -> Path:
        return self.generated / "stories"

    @property
    def reels(self) -> Path:
        return self.generated / "reels"

    @property
    def failed(self) -> Path:
        return self.generated / "failed"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def datasets(self) -> Path:
        return self.root / "datasets"

    def ensure_runtime_dirs(self) -> None:
        """Create the folders the engine writes to at runtime (idempotent)."""
        for p in (
            self.database_dir,
            self.backgrounds,
            self.posts,
            self.stories,
            self.reels,
            self.failed,
            self.logs,
        ):
            p.mkdir(parents=True, exist_ok=True)
