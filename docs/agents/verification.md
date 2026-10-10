# Verification

Read this before step 3 of the agent loop in [CONTRIBUTING.md](../../CONTRIBUTING.md).

The goal is to prove the change works and that it doesn't break something somewhere else. A writeup that sounds right is not proof. It reads as convincing whether or not it's true.

Adapted from PStack's `blast-radius`, `tdd`, and `create-verification-skill` skills. See [Credits](#credits).

## Find the fact the change is safe because of

Listing the callers isn't the job, because search finds those in a second. The job is finding the breakage search won't show.

Most changes that look risky are safe because of one or two facts. For example: "this endpoint is only called by the settings page", or "this column is nullable, so old rows still load". Find those facts. If they hold, most of the risky cases are cleared at once. Spend your time here, not on a long list of maybes.

Look where search stops:

- the JSON an API returns, and every client that reads it
- a database column or stored format
- env vars and config
- another service or script reading the same data
- timing: async callbacks, teardown, retries, two requests at once
- the source of a library you call, at the version pinned in the lockfile

## How sure are you

For each fact the change depends on, get it as far down this list as is cheap, and say where it stopped.

1. **You said so.** Worthless on its own.
2. **You pointed at the line.** A real `file:line`, or the library's own source.
3. **You showed the bad case can't happen.** You walked the failure step by step, and it doesn't reach.
4. **You ran it.** A script or test that calls the real code and fails loudly if you're wrong.
5. **You reproduced it in the running app.**

Level 4 is usually one small script that imports the code the app ships and calls the exact function you're worried about. For a change to a main user flow, aim for level 5. Keep one dev server running in your worktree and reuse it, rather than restarting it for every check.

## Standards for proof

- Use the real user path, not internal setters or test-only endpoints.
- Capture the action and the state it produced, not just the final screen.
- Check side effects (rows written, files created, requests sent) along with what's visible.
- Mock only where a boundary to an external system already exists.
- When a dry-run or test mode is the safe option, confirm what it actually skips by watching files, network, and data. Don't trust the name.
- Clean up only the processes and scratch data you started. Keep the evidence.

## Bug fixes

When a bug has a clear, cheap test path:

1. Write the smallest focused test that would have caught the bug. It should encode the intended behavior, not mirror the current code.
2. Run it before fixing. Confirm it fails, and that it fails for the reason you expect. If it passes, or fails for some other reason, fix the test first.
3. Make the smallest change that fixes the bug.
4. Run the test again and confirm it passes.

If a test would be impractical (heavy setup, brittle mocks, slow end-to-end infrastructure, state that only exists in production), use the closest executable check instead: a script, a `curl` command, browser automation, or a log assertion.

Prefer no new test over a bad test. A bad test mostly tests mocks, encodes implementation details, depends on timing, or would be deleted right after proving the fix.

- Don't change tests to match a wrong implementation.
- Don't weaken existing assertions unless the expected behavior really changed and the PR says why.

## When to stop

Stop once the changed behavior and the main risk next to it have evidence. The aim is a result you can defend, not the longest test log.

## What goes in the PR

The **Proof** section of the PR body holds:

- **Safety fact:** the fact the change depends on, the level it reached (1–5), and the evidence: a command and its output, a screenshot, or a log line.
- **Bug fix:** the test or check that failed before, the failure it gave, and the run that passed after. If you couldn't show a failure before the fix, say why and what you used instead.
- **Not checked:** anything nearby you didn't exercise. If the safety fact never got past level 1, write "unproven".

## Credits

Adapted from [PStack](https://github.com/cursor/plugins/tree/e43c7ee26e0038c6c1fa8380dd34ce86ff94cb2a/pstack) (`blast-radius`, `tdd`, `create-verification-skill`), MIT License, Copyright (c) 2026 Lauren Tan. See [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md).
