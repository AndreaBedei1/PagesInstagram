"""Per-page arming: production mode alone must not start publishing.

``ICE_MODE=production`` is one variable, and one variable is one mistake away
from five accounts posting at once. Arming is a second, persistent, per-page
switch that has to be turned on deliberately, page by page, after that page has
been through a controlled canary.

Every page starts disarmed. Nothing in the normal run path arms anything; the
only writer is ``scripts/arm_page.ps1`` (and its ``--all`` sibling), and the
publisher refuses a page that is not armed regardless of mode.

State lives in the database rather than in a file, so it survives a checkout,
cannot be committed by accident, and is visible to ``status``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..core.enums import Mode

#: Stored in the ``settings`` key-value table as ``armed:<page_id>``.
KEY_PREFIX = "armed:"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ArmingState:
    page_id: str
    armed: bool
    armed_at: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {"page_id": self.page_id, "armed": self.armed,
                "armed_at": self.armed_at, "note": self.note}


def ensure_runtime_flags(db) -> None:
    """The one small key-value table used for switches that outlive a process."""
    db.conn.execute(
        "CREATE TABLE IF NOT EXISTS runtime_flags ("
        " key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
    db.conn.commit()


#: Kept as the old private name so nothing in this module had to change.
_ensure_table = ensure_runtime_flags


def get_state(db, page_id: str) -> ArmingState:
    _ensure_table(db)
    row = db.conn.execute(
        "SELECT value, updated_at FROM runtime_flags WHERE key=?",
        (KEY_PREFIX + page_id,)).fetchone()
    if row is None:
        return ArmingState(page_id=page_id, armed=False,
                           note="mai armata")
    value, updated = row[0], row[1]
    return ArmingState(page_id=page_id, armed=value == "armed",
                       armed_at=updated,
                       note="armata" if value == "armed" else "disarmata")


def is_armed(db, page_id: str) -> bool:
    return get_state(db, page_id).armed


def set_armed(db, page_id: str, armed: bool) -> ArmingState:
    _ensure_table(db)
    db.conn.execute(
        "INSERT INTO runtime_flags(key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (KEY_PREFIX + page_id, "armed" if armed else "disarmed", _now()))
    db.conn.commit()
    return get_state(db, page_id)


def all_states(db, page_ids: list[str]) -> list[ArmingState]:
    return [get_state(db, p) for p in page_ids]


def may_publish(db, page_id: str, mode: Mode) -> tuple[bool, str]:
    """The final word before a real publish call.

    Dry-run never publishes whatever the flag says; production publishes only
    for a page somebody armed on purpose.
    """
    if mode == Mode.DRY_RUN:
        return False, "ICE_MODE=dry_run: nessuna pubblicazione reale"
    state = get_state(db, page_id)
    if not state.armed:
        return False, (f"la pagina {page_id!r} non è armata: esegui "
                       f"scripts/arm_page.ps1 {page_id} dopo il canary")
    return True, f"pagina armata il {state.armed_at}"
