"""When Meta stops accepting the credentials, and what the engine does about it.

On 2026-08-14 and again on 2026-08-15 the worker did exactly what it was told:
it woke up on time, opened a tunnel, and asked Meta to publish. Meta answered

    Graph API error [200]: Cannot call API for app <id> on behalf of user <id>

because API access for the app had been blocked — nothing to do with the token,
the media or the schedule. The engine treated it like any other failure: the job
went to ``FAILED``, and the next morning it burned the next one. Two posts were
lost before anybody noticed, and nothing in the system said why.

Both halves of that were wrong.

A credentials failure is not a failure *of the job*. The media is fine, the
caption is fine, the slot is fine; the only thing missing is permission to
speak. So the job goes back to ``MEDIA_READY`` and stays publishable: when the
credentials work again it can still go out, subject to its own missed-slot
policy — which is the right place for that decision, not here.

And a credentials failure is not a per-job event but a per-page state. Once Meta
has refused the token there is no reason to believe the next attempt will differ,
so the page is marked blocked and publication attempts stop. The engine re-probes
on a slow clock and clears the mark by itself the moment the token is accepted
again, so a block that resolves on Meta's side needs no operator here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..core.errors import PublishError
from ..core.logging_setup import get_logger
from .arming import ensure_runtime_flags

log = get_logger("publishing.credentials")

_KEY = "credentials:blocked:"

#: Graph error codes that mean "not allowed to speak", as opposed to "this
#: request was wrong". Meta documents them under Login and Authorization:
#:
#:   190  invalid/expired access token
#:   200  permission denied — also the app-level "API access blocked."
#:   10   permission denied (permission not granted or removed)
#:   102  session invalid
#:   104  request not signed / missing token
#:   463  the token has expired
#:   467  the token is invalid
#:
#: Nothing about the media, the schedule or the tunnel appears here on purpose:
#: those are real job failures and must keep failing loudly per job.
AUTH_ERROR_CODES: frozenset[str] = frozenset(
    {"190", "200", "10", "102", "104", "463", "467"})

#: How long to wait before asking Meta whether the credentials work again. Slow
#: on purpose: a blocked app is resolved by a person on a dashboard, in minutes
#: at best, and hammering the endpoint is how an app gets blocked in the first
#: place.
DEFAULT_RECHECK_MINUTES = 30


def is_auth_error(e: BaseException) -> bool:
    """Is this Meta refusing the credentials, rather than the request?"""
    code = str(getattr(e, "code", "") or "")
    if code in AUTH_ERROR_CODES:
        return True
    # The client raises with the code when Meta sends one. When it cannot (a
    # malformed error body), the message is all there is.
    text = str(e).lower()
    return ("api access blocked" in text
            or "cannot call api for app" in text
            or "error validating access token" in text)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(db, page_id: str) -> dict | None:
    ensure_runtime_flags(db)
    row = db.conn.execute("SELECT value FROM runtime_flags WHERE key=?",
                          (_KEY + page_id,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def _write(db, page_id: str, payload: dict) -> None:
    ensure_runtime_flags(db)
    db.conn.execute(
        "INSERT INTO runtime_flags(key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (_KEY + page_id, json.dumps(payload, ensure_ascii=False), _now()))
    db.conn.commit()


def block_state(db, page_id: str) -> dict | None:
    """What we know about this page's credentials, or None if all is well."""
    return _read(db, page_id)


def record_block(db, page_id: str, message: str) -> dict:
    """Remember that Meta refused, keeping the first refusal's timestamp.

    The first timestamp is the one worth keeping: "blocked since the 14th" is
    an actionable sentence, "blocked since a minute ago" is not.
    """
    existing = _read(db, page_id) or {}
    state = {
        "page_id": page_id,
        "since": existing.get("since") or _now(),
        "last_seen": _now(),
        "attempts": int(existing.get("attempts") or 0) + 1,
        "message": message[:500],
        "last_probe": existing.get("last_probe"),
    }
    _write(db, page_id, state)
    if not existing:
        log.error("credenziali rifiutate da Meta per %s: %s — le pubblicazioni "
                  "sono sospese e i job restano ripubblicabili", page_id, message)
    return state


def clear_block(db, page_id: str) -> None:
    ensure_runtime_flags(db)
    db.conn.execute("DELETE FROM runtime_flags WHERE key=?", (_KEY + page_id,))
    db.conn.commit()
    log.info("credenziali di nuovo accettate per %s: pubblicazioni riprese",
             page_id)


def mark_probe(db, page_id: str) -> None:
    state = _read(db, page_id)
    if state:
        state["last_probe"] = _now()
        _write(db, page_id, state)


def due_for_probe(state: dict | None, *,
                  recheck_minutes: int = DEFAULT_RECHECK_MINUTES,
                  now: datetime | None = None) -> bool:
    """Has enough time passed to ask Meta again?"""
    if not state:
        return False
    now = now or datetime.now(timezone.utc)
    stamp = state.get("last_probe") or state.get("since")
    if not stamp:
        return True
    try:
        last = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return True
    return now - last >= timedelta(minutes=recheck_minutes)


def probe(target) -> tuple[bool, str]:
    """One cheap read to ask "do these credentials work now?".

    Deliberately not a publication and not the full health check: this runs on
    a timer while a page is blocked, so it has to be the smallest question that
    still has the right answer.
    """
    try:
        target.client.get_account_info(target.ig_user_id)
    except PublishError as e:
        return (False, str(e)) if is_auth_error(e) else (True, f"non conclusivo: {e}")
    except Exception as e:  # noqa: BLE001 — a probe must never crash a tick
        return True, f"non conclusivo: {e}"
    return True, "credenziali accettate"


def describe(state: dict | None) -> str:
    if not state:
        return "credenziali accettate"
    return (f"credenziali rifiutate da Meta dal {state.get('since', '?')} "
            f"({state.get('attempts', 0)} tentativi): {state.get('message', '')}")
