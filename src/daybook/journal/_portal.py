"""Short read snapshots and the single notes-to-prompt renderer."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from ._types import (
    CaptureSpan,
    Hit,
    Observation,
    Segment,
    SignalId,
    SignalReading,
    Situation,
    TimeRange,
    Total,
)

if TYPE_CHECKING:
    from ._store import Journal


class Portal:
    def __init__(self, journal: Journal) -> None:
        self._journal = journal

    def now(self) -> Situation:
        from ._store import moment

        j = self._journal
        with j._lock:
            j._check_open()
            state = j._db.execute(
                "SELECT state FROM capture_spans ORDER BY id DESC LIMIT 1"
            ).fetchone()
            segment = j._db.execute("SELECT id FROM segments WHERE open=1").fetchone()
            observation = j._db.execute(
                "SELECT * FROM observations ORDER BY started_at DESC, id DESC LIMIT 1"
            ).fetchone()
            rows = j._db.execute("""SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY skill, name ORDER BY at DESC, tick DESC) AS number
                FROM readings) WHERE number=1 ORDER BY skill, name""").fetchall()
            readings = tuple(
                SignalReading(
                    SignalId(row["skill"], row["name"]),
                    json.loads(row["value"]),
                    moment(row["at"]),
                    row["tick"],
                )
                for row in rows
            )
            return Situation(
                state[0] if state else "running",
                j._segment(segment[0]) if segment else None,
                j._row_observation(observation) if observation else None,
                readings,
            )

    def observations(
        self, range: TimeRange, unassigned_only: bool = False
    ) -> list[Observation]:
        from ._store import bounds

        start, end = bounds(range)
        if start == end:
            return []
        j = self._journal
        with j._lock:
            j._check_open()
            query = "SELECT * FROM observations WHERE started_at<? AND (last_seen_at>? OR started_at>=?)"
            if unassigned_only:
                query += " AND segment IS NULL"
            rows = j._db.execute(
                query + " ORDER BY started_at, id", (end, start, start)
            ).fetchall()
            return [j._row_observation(row) for row in rows]

    def segments(self, range: TimeRange) -> list[Segment]:
        j = self._journal
        with j._lock:
            ids = list(
                dict.fromkeys(
                    o.segment for o in self.observations(range) if o.segment is not None
                )
            )
            return [j._segment(id) for id in ids]

    def capture_spans(self, range: TimeRange) -> list[CaptureSpan]:
        from ._store import bounds, moment

        start, end = bounds(range)
        if start == end:
            return []
        j = self._journal
        with j._lock:
            j._check_open()
            rows = j._db.execute(
                "SELECT * FROM capture_spans WHERE start<? AND (end IS NULL OR (end>start AND end>?)) ORDER BY start, id",
                (end, start),
            ).fetchall()
            return [
                CaptureSpan(
                    row["state"],
                    moment(row["start"]),
                    moment(row["end"]) if row["end"] else None,
                )
                for row in rows
            ]

    def readings(self, signal: SignalId, range: TimeRange) -> list[SignalReading]:
        from ._store import bounds, moment

        start, end = bounds(range)
        j = self._journal
        with j._lock:
            j._check_open()
            rows = j._db.execute(
                "SELECT * FROM readings WHERE skill=? AND name=? AND at>=? AND at<? ORDER BY at, tick",
                (signal.skill, signal.name, start, end),
            ).fetchall()
            return [
                SignalReading(
                    signal, json.loads(row["value"]), moment(row["at"]), row["tick"]
                )
                for row in rows
            ]

    def search(self, text: str, range: TimeRange | None = None) -> list[Hit]:
        words = re.findall(r"\w+", text, flags=re.UNICODE)
        if not words:
            return []
        query = " AND ".join('"' + word + '"' for word in words)
        j = self._journal
        with j._lock:
            j._check_open()
            allowed = None
            if range is not None:
                notes = self.observations(range)
                allowed = {o.id for o in notes} | {
                    o.segment for o in notes if o.segment is not None
                }
            rows = j._db.execute(
                "SELECT id, kind, segment, snippet(note_search, 3, '', '', '…', 32) AS text FROM note_search WHERE note_search MATCH ? ORDER BY rank, rowid",
                (query,),
            ).fetchall()
            return [
                Hit(row["id"], row["kind"], row["text"], row["segment"])
                for row in rows
                if allowed is None or row["id"] in allowed
            ]

    def totals(
        self, range: TimeRange, by: Literal["kind", "title", "app"] = "kind"
    ) -> list[Total]:
        if by not in {"kind", "title", "app"}:
            raise ValueError("totals group must be kind, title, or app")
        j = self._journal
        with j._lock:
            sums: dict[str, float] = {}
            segments: dict[str, Segment] = {}
            for note in self.observations(range):
                seconds = max(
                    0.0,
                    (
                        min(note.last_seen_at, range.end)
                        - max(note.started_at, range.start)
                    ).total_seconds(),
                )
                if seconds == 0:
                    continue
                segment = None
                if note.segment:
                    if note.segment not in segments:
                        segments[note.segment] = j._segment(note.segment)
                    segment = segments[note.segment]
                label = (
                    note.window.app
                    if by == "app"
                    else (segment.title if segment else "Unassigned")
                    if by == "title"
                    else segment.kind
                    if segment
                    else "other"
                )
                sums[label] = sums.get(label, 0) + seconds
            return [
                Total(label, seconds)
                for label, seconds in sorted(
                    sums.items(), key=lambda item: (-item[1], item[0])
                )
            ]

    def context(
        self,
        range: TimeRange,
        budget_tokens: int,
        detail: Literal["segments", "observations"] = "segments",
    ) -> str:
        from ._store import bounds

        bounds(range)
        if detail not in {"segments", "observations"}:
            raise ValueError("context detail must be segments or observations")
        if budget_tokens <= 0:
            return ""
        j = self._journal
        with j._lock:
            # Entries carry time, line kind, and parent; whole lines are removed.
            entries: list[tuple[datetime, str, str | None, str]] = []
            for segment in self.segments(range):
                notes = [
                    o
                    for o in segment.observations
                    if o.started_at < range.end
                    and (o.last_seen_at > range.start or o.started_at >= range.start)
                ]
                start = min(max(o.started_at, range.start) for o in notes)
                end = max(min(o.last_seen_at, range.end) for o in notes)
                active = sum(
                    max(
                        0.0,
                        (
                            min(o.last_seen_at, range.end)
                            - max(o.started_at, range.start)
                        ).total_seconds(),
                    )
                    for o in notes
                )
                line = f"{self._time(start)}–{self._time(end)} [{segment.id}] {segment.title} ({segment.kind}; {active:.0f}s active). Intent ({segment.authored}): {segment.intent}"
                entries.append((start, "segment", segment.id, line))
                if detail == "observations":
                    for note in notes:
                        line = f"  {self._time(note.started_at)} [{note.id}] {note.window.app} · {note.window.title}: {note.seen}"
                        if note.visible_text:
                            line += f" Text: {note.visible_text}"
                        entries.append((start, "observation", segment.id, line))
            if detail == "observations":
                for note in self.observations(range, unassigned_only=True):
                    entries.append(
                        (
                            note.started_at,
                            "observation",
                            None,
                            f"  {self._time(note.started_at)} [{note.id}] {note.window.app} · {note.window.title}: {note.seen} {note.visible_text}",
                        )
                    )
            for span in self.capture_spans(range):
                start, end = (
                    max(span.start, range.start),
                    min(span.end or range.end, range.end),
                )
                entries.append(
                    (
                        start,
                        "span",
                        None,
                        f"{self._time(start)}–{self._time(end)} {span.state}",
                    )
                )
            entries.sort(key=lambda item: item[0])
            limit = int(budget_tokens * 3.5)
            while len("\n".join(entry[3] for entry in entries)) > limit:
                target = next(
                    (i for i, entry in enumerate(entries) if entry[1] == "observation"),
                    None,
                )
                if target is None:
                    target = next(
                        (i for i, entry in enumerate(entries) if entry[1] == "segment"),
                        0,
                    )
                entries.pop(target)
            return "\n".join(entry[3] for entry in entries)

    @staticmethod
    def _time(at: datetime) -> str:
        return at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
