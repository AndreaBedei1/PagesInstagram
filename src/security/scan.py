"""Scan the repository for credentials that must never be committed.

Backs ``python -m src.cli security-check``. It looks only at files **tracked by
git** (plus, optionally, the working tree) — untracked local files such as
``.env`` are the user's business and are already ignored by ``.gitignore``.

Detection is pattern based and deliberately conservative about placeholders:
``EAAG...``, ``<TOKEN>``, ``change-me`` and empty assignments are documentation,
not secrets. Anything that looks like a real Meta token, app secret or long
opaque credential is reported and makes the command exit non-zero.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Files/directories never scanned (binary or generated).
SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".mp4", ".mov", ".mp3",
    ".wav", ".m4a", ".ogg", ".flac", ".safetensors", ".ckpt", ".pt", ".pth",
    ".sqlite", ".zip", ".gz", ".7z", ".pdf", ".woff", ".woff2", ".ttf", ".otf",
})
SKIP_DIRS = ("generated/", "logs/", ".git/", ".venv/", "__pycache__/",
             ".pytest_cache/")

#: Values that are obviously documentation, not credentials.
PLACEHOLDER_RE = re.compile(
    r"^(?:$|\.{3}|x{3,}|<[^>]*>|change[-_ ]?me|your[-_ ]?\w+|placeholder|"
    r"esempio|example|todo|dummy|fake|\*+|1789x+|1789y+)",
    re.IGNORECASE,
)

#: Anything matched here is a *candidate*; placeholders are filtered afterwards.
#: ``(rule_name, pattern, capture_group)`` — group 0 means "the whole match".
_RULES: list[tuple[str, re.Pattern[str], int]] = [
    # Meta / Facebook user or page access token.
    ("meta_access_token", re.compile(r"\bEAA[A-Za-z0-9]{24,}\b"), 0),
    # Instagram Login long-lived token.
    ("instagram_token", re.compile(r"\bIGQ[A-Za-z0-9_\-]{24,}\b"), 0),
    # Assignments of sensitive names to a non-placeholder value.
    ("credential_assignment", re.compile(
        r"(?i)\b((?:META|ICE)_[A-Z0-9_]*(?:ACCESS_TOKEN|APP_SECRET|CLIENT_SECRET"
        r"|IG_USER_ID)|access_token|app_secret|client_secret|api_key|apikey"
        r"|secret_key)\s*[:=]\s*[\"']?([^\s\"',;#]{8,})"), 2),
    # 32-hex app secret sitting on its own line.
    ("app_secret_hex", re.compile(
        r"(?i)\bapp[_-]?secret\b[^\n]{0,20}?\b([0-9a-f]{32})\b"), 1),
    # A token in a URL query string. This is how credentials leak into logs,
    # reports and bug trackers without anyone writing them down on purpose.
    ("token_in_query_string", re.compile(
        r"(?i)[?&](access_token|input_token|client_secret|app_secret)"
        r"=([^\s&\"'#]{8,})"), 2),
    # A serialised Authorization header — "Authorization: OAuth …" is exactly
    # what the resumable upload sends, and exactly what a debug dump captures.
    ("authorization_header", re.compile(
        r"(?i)\bauthorization\b\s*[:=]\s*[\"']?\s*(?:OAuth|Bearer|Basic)\s+"
        r"([^\s\"',;#]{8,})"), 1),
    # A bare bearer/OAuth token anywhere else.
    ("bearer_token", re.compile(
        r"(?i)\b(?:Bearer|OAuth)\s+([A-Za-z0-9_\-\.]{24,})\b"), 1),
    # Long-lived token exchange responses.
    ("token_json_field", re.compile(
        r"(?i)\"(access_token|refresh_token|client_secret)\"\s*:\s*\"([^\"]{8,})\""),
     2),
]

#: Paths that must never be tracked, whatever they contain. A committed runtime
#: database or log is a credential leak waiting to happen even when today's copy
#: is clean, and a committed checkpoint is gigabytes of licensed weights.
FORBIDDEN_TRACKED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(^|/)\.env($|\.)(?!example)", "file .env"),
    (r"(^|/)secrets/", "directory secrets/"),
    (r"\.(safetensors|ckpt|pt|pth)$", "file di modello"),
    (r"(^|/)database/.*\.(sqlite|db)($|-)", "database runtime"),
    (r"(^|/)logs/.*\.(log|jsonl|json)$", "log runtime"),
    (r"(^|/)reports/.*\.(json|html)$", "report runtime"),
    (r"(^|/)\.cache/", "cache runtime"),
)

_FORBIDDEN_TRACKED_RE = tuple(
    (re.compile(p, re.IGNORECASE), label)
    for p, label in FORBIDDEN_TRACKED_PATTERNS)

#: Names whose *value* is not secret even though the key looks sensitive.
_SAFE_KEYS = {"ice_dashboard_token"}


@dataclass
class SecretFinding:
    path: str
    line: int
    rule: str
    excerpt: str            # already redacted — the raw value is never stored

    def as_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "rule": self.rule,
                "excerpt": self.excerpt}


@dataclass
class SecretScanResult:
    scanned_files: int = 0
    findings: list[SecretFinding] = field(default_factory=list)
    gitignore_ok: bool = True
    gitignore_issues: list[str] = field(default_factory=list)
    tracked_env_files: list[str] = field(default_factory=list)
    forbidden_tracked: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (not self.findings and self.gitignore_ok
                and not self.tracked_env_files
                and not self.forbidden_tracked)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "scanned_files": self.scanned_files,
            "findings": [f.as_dict() for f in self.findings],
            "gitignore_ok": self.gitignore_ok,
            "gitignore_issues": self.gitignore_issues,
            "tracked_env_files": self.tracked_env_files,
            "forbidden_tracked": self.forbidden_tracked,
        }


def _redact(value: str) -> str:
    """Keep only enough of a match to locate it; never echo a credential."""
    value = value.strip()
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-2:]} ({len(value)} char)"


def scan_text(text: str, path: str = "<memory>") -> list[SecretFinding]:
    """Scan one blob of text. Exposed for unit tests."""
    findings: list[SecretFinding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        # A comment can still leak a real token, so the high-confidence token
        # patterns keep running; the generic "name = value" heuristic does not,
        # because prose like "# token (60 days)" is not an assignment.
        comment = stripped.startswith("#")
        for rule, pattern, group in _RULES:
            if comment and rule == "credential_assignment":
                continue
            for m in pattern.finditer(line):
                value = m.group(group) if group else m.group(0)
                if not value or PLACEHOLDER_RE.match(value):
                    continue
                if rule == "credential_assignment":
                    key = (m.group(1) or "").lower()
                    if key in _SAFE_KEYS:
                        continue
                    # An .env.example style empty assignment is fine.
                    if value.startswith(("${", "$(")):
                        continue
                    # Ignore obviously non-secret literals used in docs/tests.
                    if value.lower() in {"none", "null", "true", "false", "test",
                                         "token", "secret", "mock-token",
                                         "test-token"}:
                        continue
                findings.append(SecretFinding(
                    path=path, line=lineno, rule=rule, excerpt=_redact(value)))
    return findings


def tracked_files(root: str | Path) -> list[str]:
    """Repo-relative paths of every file tracked by git (empty if not a repo)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=str(root), capture_output=True,
            text=True, timeout=60, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [p for p in out.split("\0") if p]


def _check_gitignore(root: Path, result: SecretScanResult) -> None:
    gi = root / ".gitignore"
    if not gi.exists():
        result.gitignore_ok = False
        result.gitignore_issues.append(".gitignore assente")
        return
    body = gi.read_text(encoding="utf-8", errors="replace")
    lines = {ln.strip() for ln in body.splitlines()}
    required = {
        ".env": "il file .env deve essere escluso da Git",
        "secrets/": "la directory secrets/ deve essere esclusa da Git",
        "*.safetensors": "i checkpoint dei modelli non vanno committati",
        ".cache/": "la cache dell'audit delle fonti non va committata",
    }
    for needle, message in required.items():
        if needle not in lines:
            result.gitignore_ok = False
            result.gitignore_issues.append(f"{message} (manca '{needle}')")


def scan_repository(root: str | Path, *, include_untracked: bool = False,
                    max_bytes: int = 2 * 1024 * 1024) -> SecretScanResult:
    """Scan tracked files (and optionally the whole working tree) for secrets."""
    root = Path(root).resolve()
    result = SecretScanResult()
    _check_gitignore(root, result)

    paths = tracked_files(root)
    if include_untracked or not paths:
        paths = [
            str(p.relative_to(root)).replace("\\", "/")
            for p in root.rglob("*") if p.is_file()
        ]

    for rel in paths:
        rel = rel.replace("\\", "/")
        if any(rel.startswith(d) or f"/{d}" in rel for d in SKIP_DIRS):
            continue
        if Path(rel).suffix.lower() in SKIP_SUFFIXES:
            continue
        # A tracked .env (any variant except the example) is itself a failure.
        name = Path(rel).name
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            result.tracked_env_files.append(rel)
        # …and so is a tracked runtime artefact, whatever it happens to contain
        # today: a database, a log, a report or a cache is a credential leak
        # waiting for the next run, and a checkpoint is licensed weights.
        for pattern, label in _FORBIDDEN_TRACKED_RE:
            if pattern.search(rel):
                result.forbidden_tracked.append({"path": rel, "reason": label})
                break
        full = root / rel
        try:
            if not full.is_file() or full.stat().st_size > max_bytes:
                continue
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        result.scanned_files += 1
        result.findings.extend(scan_text(text, rel))
    return result
