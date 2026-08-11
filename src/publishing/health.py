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
from datetime import datetime, timedelta, timezone

from ..accounts.models import PageConfig
from ..core.errors import PublishError
from ..core.meta_api import LONG_LIVED_TOKEN_DAYS, REQUIRED_PERMISSIONS
from ..core.settings import Settings

#: A token with fewer days than this left does not start a go-live.
DEFAULT_WARN_DAYS = 10

#: Per-page env variable holding the date the token was generated, as ISO
#: ``YYYY-MM-DD``. See :func:`_expiry_from_declared_date` for why it exists.
ISSUED_AT_SUFFIX = "_TOKEN_ISSUED_AT"


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
    expiry_source: str = ""
    days_left: int | None = None
    permissions: str = Verdict.UNKNOWN
    permissions_source: str = ""
    granted_scopes: tuple[str, ...] = ()
    account: str = Verdict.UNREACHABLE
    account_type: str = ""
    username: str = ""
    limit: str = "—"
    upload_method: str = ""
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Things worth printing that are not problems. A note never changes the
    #: exit code — the moment it could, it would be a warning.
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.warnings

    def as_dict(self) -> dict:
        return {"page_id": self.page_id, "credentials": self.credentials,
                "token": self.token, "expiry": self.expiry,
                "expiry_source": self.expiry_source,
                "days_left": self.days_left, "permissions": self.permissions,
                "permissions_source": self.permissions_source,
                "account": self.account, "account_type": self.account_type,
                "username": self.username, "limit": self.limit,
                "failures": list(self.failures), "warnings": list(self.warnings),
                "notes": list(self.notes)}


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


def _classify_expiry(when: datetime, warn_days: int, health: PageHealth,
                     source: str) -> None:
    health.expiry_source = source
    health.days_left = (when - datetime.now(timezone.utc)).days
    if health.days_left < 0:
        health.expiry = Verdict.EXPIRED
        health.failures.append(
            f"token scaduto il {when.date().isoformat()} ({source})")
    elif health.days_left <= warn_days:
        health.expiry = Verdict.EXPIRING
        health.warnings.append(
            f"il token scade fra {health.days_left} giorni "
            f"({when.date().isoformat()}, {source}): rinnovalo prima del go-live")
    else:
        health.expiry = Verdict.OK


def _expiry_from(data: dict, warn_days: int, health: PageHealth) -> bool:
    """Expiry as ``debug_token`` reports it. False if it did not report one."""
    expires = data.get("expires_at")
    if expires in (None, 0):
        return False
    _classify_expiry(datetime.fromtimestamp(int(expires), tz=timezone.utc),
                     warn_days, health, "letta da debug_token")
    return True


def _expiry_from_declared_date(page: PageConfig, warn_days: int,
                               health: PageHealth) -> bool:
    """Expiry computed from a date the operator wrote down. False if absent.

    This exists because for the Instagram Login flavour the expiry is not
    readable at all. ``debug_token`` is a Facebook Graph endpoint; handed a
    valid Meta app access token and an Instagram user token it answers
    ``(#2) Service temporarily unavailable`` every time, while
    ``client_credentials`` on the same app returns 200 — so the app and the
    secret are fine and the token type is what it will not introspect.
    Verified against a real account on 2026-08-11.

    That left a gate nobody could pass: the preflight blocks the first go-live
    on any credential warning, and "expiry unknown" was a warning that no
    action could clear. Lowering the gate would have thrown away the very
    protection it exists for — a token with days left starting five accounts.

    So the remaining source is the operator: the App Dashboard states the date
    a long-lived token was generated, and its life is a documented 60 days. The
    value is *declared*, not verified, and every report says which of the two
    it is. A declared date is worth having; a declared date presented as a
    measurement would not be.
    """
    raw = os.environ.get(f"{page.env_prefix()}{ISSUED_AT_SUFFIX}", "").strip()
    if not raw:
        return False
    try:
        issued = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        health.warnings.append(
            f"{page.env_prefix()}{ISSUED_AT_SUFFIX}={raw!r} non è una data "
            f"ISO (YYYY-MM-DD): la ignoro")
        return False
    _classify_expiry(issued + timedelta(days=LONG_LIVED_TOKEN_DAYS), warn_days,
                     health, "dichiarata, non verificata")
    return True


def _permissions_from(data: dict, health: PageHealth) -> bool:
    """Permissions as ``debug_token`` lists them. False if it listed none."""
    scopes = tuple(data.get("scopes") or ())
    health.granted_scopes = scopes
    if not scopes:
        return False
    missing = [p for p in REQUIRED_PERMISSIONS if p not in scopes]
    if missing:
        health.permissions = Verdict.MISSING
        health.permissions_source = "elenco degli scope"
        health.failures.append(f"permessi mancanti: {', '.join(missing)}")
    else:
        health.permissions = Verdict.OK
        health.permissions_source = "elenco degli scope"
    return True


def _permissions_by_probe(target, health: PageHealth) -> None:
    """Ask the publishing node itself, when the scope list is out of reach.

    ``content_publishing_limit`` hangs off the same node as ``/media`` and
    creates nothing. It is not the scope list and is not reported as one — but
    a token that reaches the publishing node is evidence of a different order
    from "we could not check". If it refuses, that *is* a failure: the publish
    call would refuse too.
    """
    try:
        target.client.get_publishing_limit(target.ig_user_id)
    except PublishError as e:
        health.permissions = Verdict.MISSING
        health.permissions_source = "sonda sul nodo di pubblicazione"
        health.failures.append(
            f"il nodo di pubblicazione rifiuta questo token: {e}")
        return
    health.permissions = Verdict.OK
    health.permissions_source = "sonda: il nodo di pubblicazione risponde"


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

    # -- 2. expiry and scopes ------------------------------------------------
    # Preferred source first, fallbacks after, and the report always says which
    # one answered. Instagram Login usually falls all the way through.
    app_id, app_secret = app_credentials()
    got_expiry = got_scopes = False
    debug_failure = ""
    if app_id and app_secret:
        try:
            data = (target.client.debug_token(app_id, app_secret) or {}).get("data", {})
            if not data.get("is_valid", True):
                health.token = Verdict.INVALID
                health.failures.append("debug_token: is_valid=false")
            got_expiry = _expiry_from(data, warn_days, health)
            got_scopes = _permissions_from(data, health)
        except PublishError as e:
            # Held, not raised yet. Whether this is a problem depends on
            # whether anything else answers the questions it was asked — and
            # for Instagram Login it never answers, so reporting it as a
            # warning after a fallback has succeeded would leave a gate nobody
            # can pass while telling them nothing they can act on.
            debug_failure = str(e)

    if not got_expiry and not _expiry_from_declared_date(page, warn_days, health):
        health.expiry = Verdict.UNKNOWN
        health.warnings.append(
            f"scadenza non determinabile: Instagram Login non la espone in "
            f"lettura. Dichiara la data di generazione del token in "
            f"{page.env_prefix()}{ISSUED_AT_SUFFIX} (YYYY-MM-DD) e verrà "
            f"calcolata sui {LONG_LIVED_TOKEN_DAYS} giorni documentati")
    if not got_scopes:
        _permissions_by_probe(target, health)

    if debug_failure:
        answered = health.expiry != Verdict.UNKNOWN and health.permissions != Verdict.UNKNOWN
        message = (f"debug_token non disponibile ({debug_failure}): con "
                   f"Instagram Login questo endpoint non introspeziona il "
                   f"token")
        if answered:
            health.notes.append(message + " — scadenza e permessi ricavati "
                                          "altrimenti, vedi le fonti qui sotto")
        else:
            health.warnings.append(message)

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
