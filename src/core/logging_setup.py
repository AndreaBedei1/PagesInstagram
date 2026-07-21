"""Logging with rotation and mandatory secret redaction.

Tokens must NEVER reach a log file. Any code that holds a secret registers its
value with :func:`register_secret`; the redacting filter then masks that value
(and anything that looks like a bearer token / access_token) in every record.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:  # rich console handler is optional/pretty; fall back to stderr if absent
    from rich.logging import RichHandler

    _HAVE_RICH = True
except Exception:  # pragma: no cover
    _HAVE_RICH = False

_SECRETS: set[str] = set()

# Patterns that always get masked even if the exact value was never registered.
_TOKEN_PATTERNS = [
    re.compile(r"(access_token=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(client_secret=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(app_secret=)[^&\s\"']+", re.IGNORECASE),
]

_MASK = "***REDACTED***"


def register_secret(value: str | None) -> None:
    """Register a secret string so it is masked wherever it appears in logs."""
    if value and len(value) >= 6:
        _SECRETS.add(value)


def redact(text: str) -> str:
    """Return ``text`` with all known secrets and token patterns masked."""
    if not text:
        return text
    for secret in _SECRETS:
        if secret in text:
            text = text.replace(secret, _MASK)
    for pat in _TOKEN_PATTERNS:
        text = pat.sub(rf"\1{_MASK}", text)
    return text


class RedactingFilter(logging.Filter):
    """A logging filter that redacts secrets from the fully-formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = redact(record.getMessage())
            record.args = ()  # already interpolated into msg above
        except Exception:  # never let logging blow up the pipeline
            pass
        return True


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    *,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
    console: bool = True,
) -> logging.Logger:
    """Configure the root logger with a rotating file handler + console.

    Idempotent: repeated calls replace handlers rather than stacking them.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Clear our own handlers to stay idempotent (keep it simple & predictable).
    for h in list(root.handlers):
        root.removeHandler(h)

    redactor = RedactingFilter()

    file_handler = RotatingFileHandler(
        log_dir / "engine.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    file_handler.addFilter(redactor)
    root.addHandler(file_handler)

    if console:
        if _HAVE_RICH:
            console_handler: logging.Handler = RichHandler(
                rich_tracebacks=True, show_path=False, markup=False
            )
        else:  # pragma: no cover
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        console_handler.addFilter(redactor)
        root.addHandler(console_handler)

    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
