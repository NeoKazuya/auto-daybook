"""Owns skill files, approvals, enablement, cooldowns, and skills.sqlite run journal.

Only this module can submit autonomous actions. A hook returns Proposal values
and receives a Context, never a desktop client. Hooks are trusted Python, not a
sandbox; chat-created skills cannot install hooks. Files use validated YAML.
"""

from dataclasses import dataclass
from datetime import datetime
from .domain import (
    ActionName, Context, Decision, Digest, EvidenceToken, Proposal, Receipt,
    SkillId, SkillSpec,
)
from .intelligence import DraftText


@dataclass(frozen=True)
class SavedDraft:
    skill_id: SkillId
    source: str
    digest: Digest
    requested_actions: frozenset[ActionName]


class Skills:
    async def save_draft(self, draft: DraftText) -> SavedDraft:
        """Validate then atomically replace a managed file; never enable on generation."""
        # TODO: safe YAML load, closed schema, text/size bounds, no traversal or hook source.
        # TODO: hash canonical full source; preserve existing approved revision until new review.
        raise NotImplementedError

    async def enable(self, skill_id: SkillId, reviewed_digest: Digest) -> SkillSpec:
        """Enable only the exact reviewed file and its exact requested action set."""
        # TODO: reread/hash; mismatch fails, rather than approving whatever is there now.
        # TODO: later external file changes suspend this skill until another review.
        raise NotImplementedError

    async def disable(self, skill_id: SkillId) -> None:
        """Invalidate queued runs immediately; journaled in-flight effects are reported."""
        raise NotImplementedError

    async def due(self, now: datetime) -> tuple[SkillSpec, ...]:
        """Immutable revisions eligible for the next coalesced decision call."""
        raise NotImplementedError

    async def apply(self, decision: Decision, token: EvidenceToken) -> tuple[Receipt, ...]:
        """Recheck evidence/consent/revision, enforce grants, arbitrate, journal, dispatch."""
        # TODO: reject proposals not from the enabled digest or eligible context episodes.
        # TODO: reload eligible context through Portal internally; verify supplied token.
        # TODO: derive action names from Action union; never trust model-declared names.
        # TODO: validate repeat scope, cooldown, current privacy epoch and source freshness.
        # TODO: enforce trigger.minimum_focus_seconds against Portal's computed streak.
        # TODO: priority break > meeting reminder > music; tie-break by stable skill ID.
        # TODO: one music setting and one overlay; ledger each effect under stable run ID.
        # TODO: no awaits between final local validation and dispatch enqueue.
        # TODO: reserve before IPC; unknown outcome after crash is not automatically replayed.
        raise NotImplementedError


def run_trusted_hook(name: str, spec: SkillSpec, context: Context) -> Proposal | None:
    """Exact-logic alternative to the model for one installed hook, same effect boundary."""
    # TODO: registered import only, not arbitrary eval/import from file; no generated hooks.
    raise NotImplementedError
