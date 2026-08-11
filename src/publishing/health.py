"""What is actually true about a page's credentials, stated one axis at a time.

The old health check collapsed everything into two counters and a coloured
table, so "I could not reach Meta" and "Meta says this token is dead" came out
looking the same. They are not the same, and the difference decides whether the
right move is to wait or to regenerate a token.

Five independent axes, each with its own verdict:

``token``        VALID / INVALID / UNVERIFIABLE
``expiry``       OK / EXPIRING / EXPIRED / UNKNOWN
``permissions``  OK / MISSING / UNKNOWN
``account``      OK / INCOMPATIBLE / UNREACHABLE
``limit``        informational only

UNKNOWN is not a synonym for OK. Expiry and permissions can only be read
through ``debug_token``, which needs the app id and secret (see
``core.meta_api``); without them both come back UNKNOWN, and UNKNOWN is a
warning — which the first go-live treats as blocking, because a token whose
remaining life nobody can state is not a token to start five accounts on.

Nothing here publishes, and no token or secret is returned, printed or logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..accounts.models import PageConfig
from ..core.errors import PublishError
from ..core.meta_api import REQUIRED_PERMISSIONS
from ..core.settings import Settings

#: A token with fewer days than this left does not start a go-live.
DEFAULT_WARN_DAYS = 10


class Verdict:
    OK = "ok"
    VALID = "valido"
    INVALID = "non valido"
    UNVERIFIABLE = "non verificabile"
    EXPIRING = "in scadenza"
    EXPIRED = "scaduto"
    UNKNOWN = "sconosciuta"
    MISSING = "permessi mancanti"
    INCOMPATIBLE = "account incompatibile"
    UNREACHABLE = "account irraggiungibile"
    ABSENT = "credenziali assenti"


@dataclass
class PageHealth:
    page_id: str
    credentials: str = Verdict.ABSENT
    token: str = Verdict.UNVERIFIABLE
    expiry: str = Verdict.UNKNOWN
    days_left: int | None = None
    permissions: str = Verdict.UNKNOWN
    granted_scopes: tuple[str, ...] = ()
    account: str = Verdict.UNREACHABLE
    account_type: str = ""
    username: str = ""
    limit: str = "—"
    upload_method: str = ""
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.warnings

    def as_dict(self) -> dict:
        return {"page_id": self.page_id, "credentials": self.credentials,
                "token": self.token, "expiry": self.expiry,
                "days_left": self.days_left, "permissions": self.permissions,
                "account": self.account, "account_type": self.account_type,
                "username": self.username, "limit": self.limit,
                "failures": list(self.failures), "warnings": list(self.warnings)}


@dataclass
class HealthReport:
    pages: list[PageHealth] = field(default_factory=list)

    @property
    def failures(self) -> int:
        return sum(len(p.failures) for p in self.pages)

    @property
    def warnings(self) -> int:
        return sum(len(p.warnings) for p in self.pages)

    @property
    def exit_code(self) -> int:
        """0 everything green, 1 at least one failure, 2 only warnings."""
        if self.failures:
            return 1
        return 2 if self.warnings else 0

    @property
    def green(self) -> bool:
        """What the canary means by "the health check is green"."""
        return self.exit_code == 0


def app_credentials() -> tuple[str, str]:
    """The only two app-level variables, and the only place they are read."""
    return (os.environ.get("META_APP_ID") or "",
            os.environ.get("META_APP_SECRET") or "")


def _expiry_from(data: dict, warn_days: int, health: PageHealth) -> None:
    expires = data.get("expires_at")
    if expires in (None, 0):
        # A never-expiring token exists (page tokens), but for the Instagram
        # long-lived token it means the field was not returned. Say so.
        health.expiry = Verdict.UNKNOWN
        health.warnings.append(
            "scadenza non riportata da debug_token: non posso dire quanto vive")
        return
    when = datetime.fromtimestamp(int(expires), tz=timezone.utc)
    health.days_left = (when - datetime.now(timezone.utc)).days
    if health.days_left < 0:
        health.expiry = Verdict.EXPIRED
        health.failures.append(f"token scaduto il {when.date().isoformat()}")
    elif health.days_left <= warn_days:
        health.expiry = Verdict.EXPIRING
        health.warnings.append(
            f"il token scade fra {health.days_left} giorni "
            f"({when.date().isoformat()}): rinnovalo prima del go-live")
    else:
        health.expiry = Verdict.OK


def _permissions_from(data: dict, health: PageHealth) -> None:
    scopes = tuple(data.get("scopes") or ())
    health.granted_scopes = scopes
    if not scopes:
        health.permissions = Verdict.UNKNOWN
        health.warnings.append("debug_token non ha riportato gli scope")
        return
    missing = [p for p in REQUIRED_PERMISSIONS if p not in scopes]
    if missing:
        health.permissions = Verdict.MISSING
        health.failures.append(f"permessi mancanti: {', '.join(missing)}")
    else:
        health.permissions = Verdict.OK


def check_page(settings: Settings, page: PageConfig, *,
               warn_days: int = DEFAULT_WARN_DAYS,
               target=None, factory=None) -> PageHealth:
    """One page, no publishing. ``target``/``factory`` exist for the tests."""
    health = PageHealth(page_id=page.page_id,
                        upload_method=page.publishing.upload_method)

    if target is None:
        if factory is None:
            from .publisher import build_publish_target
            factory = build_publish_target
        target = factory(settings, page)
    if target is None:
        health.failures.append(
            f"credenziali assenti: servono {page.env_prefix()}_IG_USER_ID e "
            f"{page.env_prefix()}_ACCESS_TOKEN")
        return health
    health.credentials = Verdict.OK

    # -- 1. is this token alive? (token only, always available) -------------
    try:
        me = target.client.verify_token() or {}
        health.token = Verdict.VALID
        health.username = str(me.get("username") or "")
        health.account_type = str(me.get("account_type") or "").lower()
    except PublishError as e:
        # An auth error is Meta answering "no". Anything else is us failing to
        # ask, and the two must not be reported as the same thing.
        if str(e.code) in ("190", "102", "10", "200", "803") or not e.retryable:
            health.token = Verdict.INVALID
            health.failures.append(f"token rifiutato da Meta: {e}")
        else:
            health.token = Verdict.UNVERIFIABLE
            health.failures.append(f"token non verificabile: {e}")
        return health

    # -- 2. expiry and scopes (need the app credentials) --------------------
    app_id, app_secret = app_credentials()
    if not (app_id and app_secret):
        health.warnings.append(
            "scadenza e permessi non verificabili senza META_APP_ID e "
            "META_APP_SECRET: sono facoltativi per pubblicare, necessari per "
            "leggere debug_token")
    else:
        try:
            data = (target.client.debug_token(app_id, app_secret) or {}).get("data", {})
            if not data.get("is_valid", True):
                health.token = Verdict.INVALID
                health.failures.append("debug_token: is_valid=false")
            _expiry_from(data, warn_days, health)
            _permissions_from(data, health)
        except PublishError as e:
            health.warnings.append(f"debug_token non disponibile: {e}")

    # -- 3. the account itself ----------------------------------------------
    try:
        info = target.client.get_account_info(target.ig_user_id) or {}
        health.username = str(info.get("username") or health.username)
        health.account_type = str(info.get("account_type")
                                  or health.account_type).lower()
        health.account = Verdict.OK
    except PublishError as e:
        health.account = Verdict.UNREACHABLE
        health.failures.append(f"account irraggiungibile: {e}")

    if health.account_type == "personal":
        health.account = Verdict.INCOMPATIBLE
        health.failures.append(
            "account personale: la pubblicazione via API non è supportata")
    elif health.account_type and health.account_type != "business":
        health.account = Verdict.INCOMPATIBLE
        health.warnings.append(
            f"tipo account {health.account_type!r}: le specifiche richiedono "
            f"un account professional business")
    elif not health.account_type:
        health.warnings.append("tipo account non riportato")

    # -- 4. informational ----------------------------------------------------
    try:
        raw = target.client.get_publishing_limit(target.ig_user_id) or {}
        rows = raw.get("data")
        quota = rows[0] if isinstance(rows, list) and rows else raw
        health.limit = str(quota.get("quota_usage", quota.get("config", "?")))
    except PublishError:
        health.limit = "n/d"

    if page.publishing.upload_method == "hosted_url":
        health.warnings.append(
            "upload_method=hosted_url richiede hosting pubblico: il progetto "
            "usa resumable proprio per non averne bisogno")

    return health


def check_pages(settings: Settings, pages: list[PageConfig], *,
                warn_days: int = DEFAULT_WARN_DAYS,
                factory=None) -> HealthReport:
    return HealthReport(pages=[check_page(settings, p, warn_days=warn_days,
                                          factory=factory) for p in pages])
