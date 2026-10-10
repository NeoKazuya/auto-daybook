# Review triage

Read this before step 5 of the agent loop in [CONTRIBUTING.md](../../CONTRIBUTING.md).

Codex reviews the PR; you decide what to do about each finding. Codex saw a diff and a short description. You have the task, the surrounding code, and what was already tried. Use that context. Filter and decide, don't just collect.

Adapted from PStack's `interrogate` skill (lead judgment framework and rubric). See [Credits](#credits).

## Sort every finding

| Bucket | Meaning here | What to do |
|---|---|---|
| **Act on** | A real problem given this project's goals: it breaks the demo or a main flow, loses or corrupts data, leaks a secret, or breaks another person's code | Fix it in this PR |
| **Consider** | A legitimate point, but not clearly worth fixing now | Reply with the tradeoff. If it should happen later, add it to the Tracker |
| **Noted** | Technically valid, but not actionable at this stage | Reply in one line |
| **Dismissed** | Wrong, nitpicky, or missing context | Reply with the reason |

Reply to each finding with its bucket and a one-line reason, then resolve the thread. The dismissed replies matter: they let a person see what was rejected and overrule it.

If more than five findings land in **Act on**, you're probably not filtering hard enough.

## Filters

**Nitpick gravity.** Reviewers fill the space. When they don't find anything serious, they inflate nits. If every finding is a nit or a style preference, the code is probably fine. Say so.

**Hypothetical vs. actual.** "What if this is null?" is only a finding if a caller can actually pass null. Trace the call site. If upstream validation or the type system prevents it, dismiss the finding.

**Premature abstraction.** Suggestions to extract a helper, add an interface, or generalize only matter if this code will need to change in a second way. If not, simple inline code wins.

**"I would have done it differently."** A preference is not a bug unless the reviewer shows a concrete problem with the current approach.

**Missing context.** Watch for findings that show the reviewer didn't have the full picture. Examples: comments on code this PR didn't write or modify, flagging a pattern the rest of the codebase already uses, or a suggestion that conflicts with a constraint you know about.

## When the reviewer is right

Don't dismiss a finding just because it's uncomfortable. Signs it deserves attention:

- It names a concrete execution path, not a hypothetical.
- It shows a gap in your understanding of the code.
- You read it and think "...yeah, actually."

Be especially careful before dismissing security or correctness findings, even if they come from a single comment.

When you do act on a finding, check whether it is a symptom or the root cause. A guard clause that hides a broken contract, or a retry that masks a bug, is not a fix. Fix the layer the problem actually comes from.

## Credits

Adapted from [PStack](https://github.com/cursor/plugins/tree/e43c7ee26e0038c6c1fa8380dd34ce86ff94cb2a/pstack/skills/interrogate) (`interrogate`: `references/lead-judgment.md`, `references/rubric.md`), MIT License, Copyright (c) 2026 Lauren Tan. See [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md).
