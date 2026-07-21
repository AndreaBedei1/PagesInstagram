"""Resolve per-page Instagram credentials from the environment ONLY.

Env keys (see .env.example), with ICE_<PAGE_ID_UPPER>_ prefix:
    ICE_MOTIVATIONAL_IT_IG_USER_ID
    ICE_MOTIVATIONAL_IT_ACCESS_TOKEN     (falls back to META_ACCESS_TOKEN)

Tokens are registered with the log redactor so they can never leak into logs,
and are never written to YAML or the database.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..accounts.models import PageConfig
from ..core.logging_setup import register_secret


@dataclass
class PageCredentials:
    ig_user_id: str
    access_token: str

    def __repr__(self) -> str:  # never print the token
        return f"PageCredentials(ig_user_id={self.ig_user_id!r}, access_token='***')"


def resolve_credentials(page: PageConfig) -> PageCredentials | None:
    prefix = page.env_prefix()
    uid = os.environ.get(f"{prefix}_IG_USER_ID")
    token = (os.environ.get(f"{prefix}_ACCESS_TOKEN")
             or os.environ.get("META_ACCESS_TOKEN"))
    if uid and token:
        register_secret(token)
        return PageCredentials(ig_user_id=uid, access_token=token)
    return None
