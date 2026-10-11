"""One owner of prompts, OpenAI transport, validation, and model admission.

One active request, bounded text jobs, one replaceable capture opportunity (not
an image queue). Every public method waits for its own lane before doing work.
No SDK or response dictionaries cross this interface. Credentials come from
configuration outside skills and are never placed in prompts or logs.
"""

from dataclasses import dataclass
from datetime import datetime
from .domain import Answer, Context, Decision, Observation, SegmentDraft, SkillSpec
from .desktop import CapturePermit, CaptureSource


@dataclass(frozen=True)
class DraftText:
    source: str


@dataclass(frozen=True)
class CapturedObservation:
    note: Observation
    permit: CapturePermit


class Intelligence:
    async def observe(self, source: CaptureSource) -> CapturedObservation | None:
        """Acquire image only when admitted; None means capture safely skipped."""
        # TODO: source obtains consent epoch + lock/app/window guard before capture.
        # TODO: memory-only JPEG; recheck gate before request and before returning note.
        # TODO: parse visible facts only; release JPEG/base64/response buffers in finally.
        # TODO: no retry retaining images; next capture uses a fresh screen.
        raise NotImplementedError

    async def rollup(self, observations: tuple[Observation, ...]) -> tuple[SegmentDraft, ...]:
        """Interpret bounded evidence into activity, intent, and explicit source refs."""
        # TODO: activity episodes are normalized by Portal at commit, not invented by model.
        raise NotImplementedError

    async def decide(
        self, context: Context, skills: tuple[SkillSpec, ...], now: datetime,
    ) -> Decision:
        """One joint text call for all due declarative skills; an empty decision is valid."""
        # TODO: data-delimit notes against prompt injection; emit only advertised actions.
        # TODO: check episode references and schema; permissions are enforced by Skills.
        # TODO: bad structured output fails closed; at most one deferred text repair job.
        raise NotImplementedError

    async def recall(self, question: str, context: Context) -> Answer:
        """Answer only from supplied segments; citations must resolve inside context."""
        # TODO: retain gaps/truncation in wording; never claim sampled duration is exact.
        # TODO: model receives no raw SQL, screenshot, or ability to dispatch actions.
        raise NotImplementedError

    async def compose(self, request: str) -> DraftText:
        """Generate a disabled declarative skill, using the shared action schema."""
        # TODO: source has no enabled field; hooks rejected on this generation path.
        raise NotImplementedError
