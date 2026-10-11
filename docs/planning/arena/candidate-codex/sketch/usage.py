"""Caller-first specification; examples below are usage, not implementation.

1. One context interface for a skill and for recall:

    from .portal import Portal
    from .domain import ContextQuery, TimeRange

    context = await portal.context(ContextQuery(window=last_hour, limit=40))
    decision = await intelligence.decide(context, enabled_skills, now)
    receipts = await skills.apply(decision, context.token)

    context = await portal.context(ContextQuery(window=last_hour, text=question))
    answer = await intelligence.recall(question, context)
    if await portal.is_current(context.token):
        show_answer(answer)  # cited segments, coverage, and uncertainty

2. Chat creates a disabled file; review enables precisely that revision:

    draft = await intelligence.compose("Remind me to stand up after meetings")
    saved = await skills.save_draft(draft)
    show_review(saved.source, saved.requested_actions, saved.digest)
    enabled = await skills.enable(saved.skill_id, saved.digest)

3. The timeline's edit/delete path uses the owner of the underlying memory:

    context = await portal.context(ContextQuery(window=today, limit=100))
    observation = await portal.detail(context.segments[0].evidence[0])
    await portal.correct(observation.ref, "Reading documentation, not a meeting")
    await portal.delete(context.segments[0].ref)

Time ranges are explicit timezone-aware half-open intervals. The web boundary
resolves 'today' and 'last hour' using the desktop timezone. No caller receives
SQL rows, OpenAI JSON, filesystem paths to images, or an action backend handle.
The implementation must preserve these usages, rather than making callers
coordinate retrieval, permission checks, inference queues, or invalidation.
"""
