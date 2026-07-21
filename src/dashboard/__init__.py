"""Local review dashboard (FastAPI). Localhost-only, token-protected."""

from .app import create_app, run_dashboard

__all__ = ["create_app", "run_dashboard"]
