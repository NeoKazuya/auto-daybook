"""Only owner of memory.sqlite and its indexes; also the common read portal.

Private schema: observations, sample coverage, segments, segment_sources, FTS5.
Only this module imports sqlite3. Skills and recall cannot write through Context.
"""

from datetime import datetime
from .domain import (
    Context, ContextQuery, EvidenceToken, NoteRef, Observation, ObservationRef,
    SegmentDraft, TimeRange,
)


class Portal:
    async def context(self, query: ContextQuery) -> Context:
        """One bounded, internally consistent view, including gaps and freshness."""
        # TODO: one read transaction; intersect time and FTS, rank then order chronologically.
        # TODO: compute focus/meeting facts on full time slice, not truncated search hits.
        # TODO: derive episodes from adjacent same-kind segments with continuous coverage.
        raise NotImplementedError

    async def detail(self, ref: ObservationRef) -> Observation:
        """Return the current raw fact or report deleted/version conflict."""
        raise NotImplementedError

    async def is_current(self, token: EvidenceToken) -> bool:
        """Reject invalidated or superseded evidence; appends alone don't invalidate it."""
        raise NotImplementedError

    async def correct(self, ref: NoteRef, text: str) -> None:
        """Version-checked correction; invalidates derived context in the same transaction."""
        # TODO: observation edit: purge dependent segments, increment epoch, queue rerollup.
        # TODO: segment edit: preserve evidence, mark user origin, reset inferred activity
        # to UNKNOWN until explicitly reinterpreted; automatic rollups cannot overwrite it.
        raise NotImplementedError

    async def delete(self, ref: NoteRef) -> None:
        """Delete means forget: a segment deletion also deletes its source observations."""
        # TODO: purge dependent segments, FTS entries, queued evidence and epoch atomically.
        # TODO: source observation deletion cannot resurrect through automatic rollup.
        # TODO: secure_delete + WAL checkpoint when readers release; no retained edit history.
        raise NotImplementedError

    async def record(self, note: Observation) -> None:
        """Capture writer; dedupe capture ID. Runtime validates permit just before calling."""
        raise NotImplementedError

    async def record_gap(self, window: TimeRange) -> None:
        """Record coverage absence, without excluded titles or screenshot content."""
        raise NotImplementedError

    async def rollup_input(self, through: datetime) -> tuple[Observation, ...]:
        """Bounded unsegmented observations plus overlap required for episode continuity."""
        raise NotImplementedError

    async def commit_rollup(self, segments: tuple[SegmentDraft, ...]) -> None:
        """Compare every source version; publish all segments or reject the stale batch."""
        # TODO: validate no overlap, fabricated references, or coverage across privacy gaps.
        # TODO: deterministic IDs for the same source interval; never overwrite user edits.
        raise NotImplementedError
