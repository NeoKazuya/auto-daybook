"""Only skills writes the approval and firing tables; connections are short-lived."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from daybook.journal import Outcome


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS skill_state (
                  skill TEXT PRIMARY KEY, enabled INTEGER NOT NULL,
                  approved_hash TEXT, at TEXT NOT NULL, approved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS firings (
                  skill TEXT NOT NULL, rule INTEGER NOT NULL, key TEXT NOT NULL,
                  at TEXT NOT NULL, outcomes TEXT NOT NULL DEFAULT '[]',
                  PRIMARY KEY (skill, rule, key)
                );
                CREATE INDEX IF NOT EXISTS firing_cooldown ON firings(skill, rule, at);
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self, skill: str) -> dict[str, object] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM skill_state WHERE skill=?", (skill,)
            ).fetchone()
            return dict(row) if row else None

    def set_state(
        self,
        skill: str,
        *,
        enabled: bool,
        approved_hash: str | None,
        approved_at: str | None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO skill_state VALUES(?,?,?,?,?) ON CONFLICT(skill) DO UPDATE SET enabled=excluded.enabled, approved_hash=excluded.approved_hash, at=excluded.at, approved_at=excluded.approved_at",
                (
                    skill,
                    enabled,
                    approved_hash,
                    datetime.now(timezone.utc).isoformat(),
                    approved_at,
                ),
            )

    def clear(self, skill: str, *, state: bool = False) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM firings WHERE skill=?", (skill,))
            if state:
                db.execute("DELETE FROM skill_state WHERE skill=?", (skill,))

    def claim(
        self, skill: str, rule: int, key: str, at: datetime, cooldown: float
    ) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute(
                "SELECT at FROM firings WHERE skill=? AND rule=? ORDER BY at DESC LIMIT 1",
                (skill, rule),
            ).fetchone()
            if (
                latest
                and (at - datetime.fromisoformat(latest[0])).total_seconds() < cooldown
            ):
                return False
            return (
                db.execute(
                    "INSERT OR IGNORE INTO firings(skill,rule,key,at) VALUES(?,?,?,?)",
                    (skill, rule, key, at.isoformat()),
                ).rowcount
                == 1
            )

    def finish(self, skill: str, rule: int, key: str, outcomes: list[Outcome]) -> None:
        payload = json.dumps(
            [{"action": o.action, "ok": o.ok, "detail": o.detail} for o in outcomes]
        )
        with self.connect() as db:
            db.execute(
                "UPDATE firings SET outcomes=? WHERE skill=? AND rule=? AND key=?",
                (payload, skill, rule, key),
            )
