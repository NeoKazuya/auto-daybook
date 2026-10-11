"""SQLite writer. No note text is copied into ticks or event payloads."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, get_args
from uuid import uuid4

from daybook.events import (
    Bus,
    CaptureStateChanged,
    Event,
    ObservationAdded,
    ObservationExtended,
    SegmentChanged,
    StatusChanged,
    TickRecorded,
)

from ._portal import Portal
from ._types import (
    ActivityKind,
    CaptureState,
    Observation,
    ObservationDraft,
    Segment,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)

PLACEHOLDER = "(summary removed after forget)"
KINDS = set(get_args(ActivityKind))
STATES = set(get_args(CaptureState))


def stamp(at: datetime) -> str:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return at.astimezone(UTC).isoformat(timespec="microseconds")


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


def bounds(range: TimeRange) -> tuple[str, str]:
    start, end = stamp(range.start), stamp(range.end)
    if start > end:
        raise ValueError("range start must not be after end")
    return start, end


SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO journal_meta VALUES ('forget_epoch', 0);
CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,
    intent TEXT NOT NULL, authored TEXT NOT NULL, open INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_segment ON segments(open) WHERE open=1;
CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY, started_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    app TEXT NOT NULL, title TEXT NOT NULL, seen TEXT NOT NULL, visible_text TEXT NOT NULL,
    segment TEXT REFERENCES segments(id) ON DELETE CASCADE,
    CHECK(last_seen_at >= started_at)
);
CREATE INDEX IF NOT EXISTS observations_time ON observations(started_at, last_seen_at);
CREATE INDEX IF NOT EXISTS observations_segment ON observations(segment, started_at);
CREATE TABLE IF NOT EXISTS ticks (
    id TEXT PRIMARY KEY, at TEXT NOT NULL, evidence_until TEXT NOT NULL UNIQUE,
    epoch INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ticks_time ON ticks(at);
CREATE TABLE IF NOT EXISTS readings (
    skill TEXT NOT NULL, name TEXT NOT NULL, at TEXT NOT NULL, tick TEXT NOT NULL,
    value TEXT NOT NULL, PRIMARY KEY(skill, name, tick)
);
CREATE INDEX IF NOT EXISTS readings_signal_time ON readings(skill, name, at);
CREATE INDEX IF NOT EXISTS readings_time ON readings(at);
CREATE TABLE IF NOT EXISTS capture_spans (
    id INTEGER PRIMARY KEY, state TEXT NOT NULL, start TEXT NOT NULL, end TEXT
);
CREATE INDEX IF NOT EXISTS capture_time ON capture_spans(start, end);
CREATE VIRTUAL TABLE IF NOT EXISTS note_search USING fts5(
    id UNINDEXED, kind UNINDEXED, segment UNINDEXED, text
);
INSERT INTO note_search(note_search, rank) VALUES('secure-delete', 1);
CREATE TRIGGER IF NOT EXISTS observation_insert AFTER INSERT ON observations BEGIN
    INSERT INTO note_search(id, kind, segment, text)
    VALUES(new.id, 'observation', new.segment, new.title || char(10) || new.seen || char(10) || new.visible_text);
END;
CREATE TRIGGER IF NOT EXISTS observation_update AFTER UPDATE OF title, seen, visible_text, segment ON observations BEGIN
    DELETE FROM note_search WHERE id=old.id AND kind='observation';
    INSERT INTO note_search(id, kind, segment, text)
    VALUES(new.id, 'observation', new.segment, new.title || char(10) || new.seen || char(10) || new.visible_text);
END;
CREATE TRIGGER IF NOT EXISTS observation_delete AFTER DELETE ON observations BEGIN
    DELETE FROM note_search WHERE id=old.id AND kind='observation';
END;
CREATE TRIGGER IF NOT EXISTS segment_insert AFTER INSERT ON segments BEGIN
    INSERT INTO note_search(id, kind, segment, text)
    VALUES(new.id, 'segment', new.id, new.title || char(10) || new.intent);
END;
CREATE TRIGGER IF NOT EXISTS segment_update AFTER UPDATE OF title, intent ON segments BEGIN
    DELETE FROM note_search WHERE id=old.id AND kind='segment';
    INSERT INTO note_search(id, kind, segment, text)
    VALUES(new.id, 'segment', new.id, new.title || char(10) || new.intent);
END;
CREATE TRIGGER IF NOT EXISTS segment_delete AFTER DELETE ON segments BEGIN
    DELETE FROM note_search WHERE id=old.id AND kind='segment';
END;
"""


class Journal:
    def __init__(self, path: Path, bus: Bus) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._bus = bus
        self._lock = threading.RLock()
        self._closed = False
        self._pending = False
        self._retry: asyncio.TimerHandle | None = None
        self._db = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False, timeout=0.2
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        self._portal = Portal(self)
        # Also clear a pending WAL from a previous process's forget on restart.
        self._checkpoint()

    @property
    def epoch(self) -> int:
        with self._lock:
            self._check_open()
            return self._epoch()

    @property
    def portal(self) -> Portal:
        return self._portal

    @property
    def deletion_pending(self) -> bool:
        with self._lock:
            if not self._closed and self._pending:
                self._checkpoint()
            return self._pending

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("journal is closed")

    def _epoch(self) -> int:
        return int(
            self._db.execute(
                "SELECT value FROM journal_meta WHERE key='forget_epoch'"
            ).fetchone()[0]
        )

    def _bump(self) -> None:
        self._db.execute(
            "UPDATE journal_meta SET value=value+1 WHERE key='forget_epoch'"
        )

    @contextmanager
    def _transaction(self, *, erase: bool = False) -> Iterator[list[Event]]:
        with self._lock:
            self._check_open()
            events: list[Event] = []
            before = self._db.total_changes
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield events
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            if self._db.total_changes != before:
                for event in events:
                    self._bus.publish(event)
                self._bus.publish(StatusChanged("journal", "notes_changed"))
            if erase or self._pending:
                self._checkpoint()

    def _stale(self, epoch: int) -> bool:
        if epoch >= self._epoch():
            return False
        logging.getLogger(__name__).info("discarded_after_forget")
        self._bus.publish(StatusChanged("journal", "discarded_after_forget"))
        return True

    def _checkpoint(self) -> None:
        try:
            busy = self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        except sqlite3.OperationalError:
            busy = 1
        pending = bool(busy)
        if pending != self._pending:
            self._pending = pending
            self._bus.publish(
                StatusChanged("journal", "deletion_pending" if pending else "ready")
            )
        if pending and self._retry is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._retry = loop.call_later(0.2, self._retry_checkpoint)

    def _retry_checkpoint(self) -> None:
        with self._lock:
            self._retry = None
            if not self._closed and self._pending:
                self._checkpoint()

    def _observation(self, id: str) -> Observation:
        row = self._db.execute(
            "SELECT * FROM observations WHERE id=?", (id,)
        ).fetchone()
        if row is None:
            raise KeyError(id)
        return self._row_observation(row)

    @staticmethod
    def _row_observation(row: sqlite3.Row) -> Observation:
        return Observation(
            row["id"],
            moment(row["started_at"]),
            moment(row["last_seen_at"]),
            WindowInfo(row["app"], row["title"]),
            row["seen"],
            row["visible_text"],
            row["segment"],
        )

    def _segment(self, id: str) -> Segment:
        row = self._db.execute("SELECT * FROM segments WHERE id=?", (id,)).fetchone()
        if row is None:
            raise KeyError(id)
        observations = tuple(
            self._row_observation(o)
            for o in self._db.execute(
                "SELECT * FROM observations WHERE segment=? ORDER BY started_at, id",
                (id,),
            ).fetchall()
        )
        return Segment(
            row["id"],
            row["title"],
            row["kind"],
            row["intent"],
            row["authored"],
            bool(row["open"]),
            observations,
        )

    def add_observation(
        self, draft: ObservationDraft, epoch: int
    ) -> Observation | None:
        at = stamp(draft.started_at)
        with self._transaction() as events:
            if self._stale(epoch):
                return None
            previous = self._db.execute(
                "SELECT * FROM observations ORDER BY started_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if previous is not None and previous["started_at"] > at:
                raise ValueError("observations must arrive in chronological order")
            if previous is not None and previous["last_seen_at"] > at:
                self._db.execute(
                    "UPDATE observations SET last_seen_at=? WHERE id=?",
                    (at, previous["id"]),
                )
                events.append(ObservationExtended(previous["id"]))
            id = uuid4().hex
            self._db.execute(
                "INSERT INTO observations VALUES(?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    id,
                    at,
                    at,
                    draft.window.app,
                    draft.window.title,
                    draft.seen,
                    draft.visible_text[:300],
                ),
            )
            events.append(ObservationAdded(id))
            return self._observation(id)

    def extend_observation(self, id: str, until: datetime) -> None:
        at = stamp(until)
        with self._transaction() as events:
            row = self._db.execute(
                "SELECT * FROM observations WHERE id=?", (id,)
            ).fetchone()
            if row is None:
                return
            successor = self._db.execute(
                "SELECT started_at FROM observations WHERE started_at>? ORDER BY started_at LIMIT 1",
                (row["started_at"],),
            ).fetchone()
            if successor is not None:
                at = min(at, successor[0])
            if at > row["last_seen_at"]:
                self._db.execute(
                    "UPDATE observations SET last_seen_at=? WHERE id=?", (at, id)
                )
                events.append(ObservationExtended(id))

    def set_capture_state(self, state: CaptureState, at: datetime) -> None:
        if state not in STATES:
            raise ValueError("unknown capture state")
        time = stamp(at)
        with self._transaction() as events:
            current = self._db.execute(
                "SELECT * FROM capture_spans ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if current is not None:
                if current["state"] == state and current["end"] is None:
                    return
                if time < current["start"]:
                    raise ValueError("capture changes must be chronological")
                self._db.execute(
                    "UPDATE capture_spans SET end=? WHERE id=?", (time, current["id"])
                )
            self._db.execute(
                "INSERT INTO capture_spans(state, start) VALUES(?, ?)", (state, time)
            )
            events.append(CaptureStateChanged(state))

    def record_tick(self, tick: Tick) -> bool:
        import json

        at, evidence = stamp(tick.at), stamp(tick.evidence_until)
        with self._transaction() as events:
            if self._stale(tick.epoch):
                return False
            if self._db.execute(
                "SELECT 1 FROM ticks WHERE evidence_until=?", (evidence,)
            ).fetchone():
                return False
            self._db.execute(
                "INSERT INTO ticks VALUES(?, ?, ?, ?)",
                (tick.id, at, evidence, tick.epoch),
            )
            ids = [
                id
                for id in dict.fromkeys(tick.observations)
                if self._db.execute(
                    "SELECT 1 FROM observations WHERE id=? AND segment IS NULL AND started_at<=?",
                    (id, evidence),
                ).fetchone()
            ]
            if ids and tick.segment is not None:
                decision = tick.segment
                if decision.kind not in KINDS:
                    raise ValueError("unknown activity kind")
                current = self._db.execute(
                    "SELECT * FROM segments WHERE open=1"
                ).fetchone()
                if isinstance(decision, Start) or current is None:
                    self._db.execute("UPDATE segments SET open=0 WHERE open=1")
                    if current is not None:
                        events.append(SegmentChanged(current["id"]))
                    id = uuid4().hex
                    self._db.execute(
                        "INSERT INTO segments VALUES(?, ?, ?, ?, 'model', 1)",
                        (id, decision.title, decision.kind, decision.intent),
                    )
                else:
                    id = current["id"]
                    if current["authored"] == "model":
                        self._db.execute(
                            "UPDATE segments SET title=?, kind=?, intent=? WHERE id=?",
                            (decision.title, decision.kind, decision.intent, id),
                        )
                for observation in ids:
                    self._db.execute(
                        "UPDATE observations SET segment=? WHERE id=?",
                        (id, observation),
                    )
                events.append(SegmentChanged(id))
            for reading in tick.readings:
                self._db.execute(
                    "INSERT OR REPLACE INTO readings VALUES(?, ?, ?, ?, ?)",
                    (
                        reading.signal.skill,
                        reading.signal.name,
                        stamp(reading.at),
                        tick.id,
                        json.dumps(reading.value, allow_nan=False),
                    ),
                )
            events.append(TickRecorded(tick.id))
            return True

    def close_open_segment(self) -> None:
        with self._transaction() as events:
            row = self._db.execute("SELECT id FROM segments WHERE open=1").fetchone()
            if row is not None:
                self._db.execute("UPDATE segments SET open=0 WHERE id=?", (row[0],))
                events.append(SegmentChanged(row[0]))

    def edit_segment(
        self,
        id: str,
        *,
        title: str | None = None,
        kind: ActivityKind | None = None,
        intent: str | None = None,
    ) -> Segment:
        if kind is not None and kind not in KINDS:
            raise ValueError("unknown activity kind")
        with self._transaction(erase=True) as events:
            old = self._segment(id)
            self._bump()
            self._db.execute(
                "UPDATE segments SET title=?, kind=?, intent=?, authored='user' WHERE id=?",
                (
                    old.title if title is None else title,
                    old.kind if kind is None else kind,
                    old.intent if intent is None else intent,
                    id,
                ),
            )
            events.append(SegmentChanged(id))
            return self._segment(id)

    def edit_observation(
        self, id: str, *, seen: str | None = None, visible_text: str | None = None
    ) -> Observation:
        with self._transaction(erase=True) as events:
            old = self._observation(id)
            self._bump()
            self._db.execute(
                "UPDATE observations SET seen=?, visible_text=? WHERE id=?",
                (
                    old.seen if seen is None else seen,
                    old.visible_text if visible_text is None else visible_text[:300],
                    id,
                ),
            )
            if old.segment:
                events.append(SegmentChanged(old.segment))
            return self._observation(id)

    def _remove(self, ids: list[str], events: list[Event]) -> None:
        affected: set[str] = set()
        for id in ids:
            row = self._db.execute(
                "SELECT segment FROM observations WHERE id=?", (id,)
            ).fetchone()
            if row is not None and row[0]:
                affected.add(row[0])
            self._db.execute("DELETE FROM observations WHERE id=?", (id,))
        for id in affected:
            if not self._db.execute(
                "SELECT 1 FROM observations WHERE segment=? LIMIT 1", (id,)
            ).fetchone():
                self._db.execute("DELETE FROM segments WHERE id=?", (id,))
            else:
                self._db.execute(
                    "UPDATE segments SET title=?, intent=?, kind='other' WHERE id=? AND authored='model'",
                    (PLACEHOLDER, PLACEHOLDER, id),
                )
            events.append(SegmentChanged(id))

    def forget_observation(self, id: str) -> None:
        with self._transaction(erase=True) as events:
            self._bump()
            self._remove([id], events)

    def forget_segment(self, id: str) -> None:
        with self._transaction(erase=True) as events:
            self._bump()
            self._db.execute("DELETE FROM segments WHERE id=?", (id,))
            events.append(SegmentChanged(id))

    def forget_range(self, range: TimeRange) -> None:
        start, end = bounds(range)
        with self._transaction(erase=True) as events:
            self._bump()
            ids = (
                [
                    row[0]
                    for row in self._db.execute(
                        "SELECT id FROM observations WHERE started_at<? AND (last_seen_at>? OR started_at>=?)",
                        (end, start, start),
                    ).fetchall()
                ]
                if start < end
                else []
            )
            self._remove(ids, events)
            self._db.execute("DELETE FROM readings WHERE at>=? AND at<?", (start, end))
            self._db.execute("DELETE FROM ticks WHERE at>=? AND at<?", (start, end))
            # Gaps are non-sensitive state history; preserve them for honest recall.

    def forget_all(self) -> None:
        with self._transaction(erase=True) as events:
            self._bump()
            ids = [
                row[0] for row in self._db.execute("SELECT id FROM segments").fetchall()
            ]
            for table in (
                "observations",
                "segments",
                "readings",
                "ticks",
                "capture_spans",
            ):
                self._db.execute(f"DELETE FROM {table}")
            events.extend(SegmentChanged(id) for id in ids)

    def trim_after(self, at: datetime) -> None:
        time = stamp(at)
        with self._transaction(erase=True) as events:
            self._bump()
            ids = [
                row[0]
                for row in self._db.execute(
                    "SELECT id FROM observations WHERE started_at>?", (time,)
                ).fetchall()
            ]
            self._remove(ids, events)
            spanning = self._db.execute(
                "SELECT id FROM observations WHERE started_at<=? AND last_seen_at>?",
                (time, time),
            ).fetchall()
            for row in spanning:
                self._db.execute(
                    "UPDATE observations SET last_seen_at=? WHERE id=?", (time, row[0])
                )
                events.append(ObservationExtended(row[0]))
            self._db.execute("DELETE FROM readings WHERE at>?", (time,))
            self._db.execute(
                "DELETE FROM ticks WHERE at>? OR evidence_until>?", (time, time)
            )
            for row in self._db.execute(
                "SELECT id FROM segments WHERE open=1"
            ).fetchall():
                events.append(SegmentChanged(row[0]))
            self._db.execute("UPDATE segments SET open=0 WHERE open=1")
            # Sense may discover AWAY retrospectively, at the last input time.
            # Remove later state history so the replacement gap starts there.
            self._db.execute("DELETE FROM capture_spans WHERE start>?", (time,))
            self._db.execute(
                "UPDATE capture_spans SET end=? WHERE start<=? AND (end IS NULL OR end>?)",
                (time, time, time),
            )

    def clear_skill_readings(self, skill: str) -> None:
        """Reset a changed skill's history without letting an old tick restore it."""
        with self._transaction(erase=True):
            self._bump()
            self._db.execute("DELETE FROM readings WHERE skill=?", (skill,))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._retry is not None:
                self._retry.cancel()
                self._retry = None
            self._checkpoint()
            self._db.close()
            self._closed = True
