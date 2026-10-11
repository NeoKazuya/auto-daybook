# Contributing

This is a hackathon repo. Several people work on it at the same time, and each person works only by talking to their coding agents. The agents do everything else: they track tasks, avoid each other's work, set up branches and worktrees, verify, request and triage review, merge, and clean up. People only answer when an agent brings them something.

`main` is the only branch. There is no staging branch, no code freeze, and no GitHub Actions. Checks run locally, and Codex review runs through its GitHub app, which doesn't use Actions minutes.

## Tracker

The pinned **Tracker** issue ([#1](https://github.com/NeoKazuya/auto-daybook/issues/1)) is the shared task list. Agents write to it only through comments, so two agents never overwrite each other:

```text
Task: <short description> (<person>)
Claimed: <task> (<person>) — branch <person>/<topic>
Done: <task> (#<pr>)
Dropped: <task> — <reason>
```

Read the current state with `gh issue view 1 --comments`. A task is open if it has a `Task:` line and no `Done:` or `Dropped:` line.

## For agents: the loop

Follow these steps for every request. Do them yourself, without waiting for a person. See [Don't block on your person](#dont-block-on-your-person) for the few cases where you stop. Talk to your person using the formats in [Talking to your person](#talking-to-your-person).

### 0. Plan the request

When your person asks for something:

- Split it into tasks small enough for one PR each, and post a `Task:` comment for each one.
- Check for overlap with other agents. Look at the open tasks and claims on the Tracker, and at `gh pr list`. If another agent has claimed the same feature or files, or the same shared contract, don't start it. Tell your person, and suggest an alternative.
- Send your person a **Plan** message, then start right away. Don't wait for approval unless something needs a decision.

### 1. Claim and set up

- Post `Claimed: <task> (<person>) — branch <person>/<topic>` on the Tracker.
- Each agent has one worktree and reuses it for every task, so dependencies are installed only once. Create it the first time:

  ```sh
  git fetch origin
  git worktree add ../auto-daybook-<person>-<agent> -b <person>/<topic> origin/main
  ```

  For later tasks, start a new branch in the same worktree:

  ```sh
  git fetch origin && git switch -c <person>/<topic> origin/main
  ```

- Never commit to `main` directly. That includes typo and copy fixes.

### 2. Build

- Stay inside the task and its area of the code.
- If the task needs a change to a shared contract, it is **shared-contract work**. It waits for review before merging (step 5). Shared contracts include types or schema used across modules, API shapes between frontend and backend, auth, secrets handling, and build or deploy config.
- For shared-contract work, read [docs/agents/design-red-flags.md](docs/agents/design-red-flags.md) first. Write the caller's usage before the types. Give each piece of state one owner. Move every caller to the new shape and delete the old one in the same PR.

### 3. Verify

Read [docs/agents/verification.md](docs/agents/verification.md). Then, before opening the PR:

- Run the local checks:

  ```sh
  scripts/check.sh   # ruff, ruff format --check, pyright, pytest, import-boundary check (see docs/ARCHITECTURE.md §10)
  ```

- Keep the local checks fast (a couple of minutes at most). Lint, typecheck, and build, plus only the tests near your change.
- Find the one or two facts the change is safe because of. Prove each as far as is cheap: run real code (level 4), or reproduce it in the running app (level 5) for changes to a main user flow. Passing type checks isn't proof.
- For a bug fix with a cheap test path, write the failing test first, and confirm it fails for the right reason before fixing.
- Rebase on `main` (`git pull --rebase origin main`) and run the checks again.
- Don't open the PR until the checks pass. If a failure comes from code you didn't touch, fix it in its own small PR first.

### 4. Open the PR and request review

```sh
gh pr create --base main --title "<short title>" --body-file <file>
gh pr comment <number> --body "@codex review"
```

Use this PR body format. Under "Proof", write only what you actually ran.

```md
Part of #1

**What changed**
-

**Why**
-

**Proof**
- Safety fact: <fact> (level <1–5>): <command + output, screenshot, or log>
- Bug fix: failed before with <failure>; passes after with <run>
- Not checked: <nearby things not exercised; write "unproven" if the safety fact stayed at level 1>

**Shared contracts touched:** none | <list, and which callers were updated>

**Screenshot** (if it's UI)
```

### 5. Merge

Most PRs merge as soon as the checks pass. Don't wait for the review:

```sh
git pull --rebase origin main    # if main moved: run the checks again, then push
gh pr merge <number> --squash --delete-branch
```

The exception is **shared-contract work**. Wait up to 20 minutes for the review, triage it (step 6), fix any "Act on" findings, then merge. If no review arrives in 20 minutes, merge anyway, note it in the PR, and triage the review when it comes.

After merging:

- Post `Done: <task> (#<pr>)` on the Tracker.
- Send your person a **Done** message.
- Move on to the next task.

### 6. Triage reviews on merged PRs

Reviews arrive after the merge, so check for them at natural breaks: before claiming each new task, and before your final Done message.

```sh
gh pr list --author @me --state merged --limit 5
gh api repos/{owner}/{repo}/pulls/<number>/comments                # inline comments
gh pr view <number> --json reviews,comments
```

Triage each review using [docs/agents/review-triage.md](docs/agents/review-triage.md). Check each finding against the code first, then sort it:

- **Act on:** a real problem for this project. It breaks the demo or a main flow, loses or corrupts data, leaks a secret, or breaks another person's code. Fix it in a small follow-up PR, which merges once the checks pass.
- **Consider / Noted / Dismissed:** reply with the bucket and a one-line reason, then resolve the thread.

Trace hypotheticals to a real call site before accepting them. Dismiss preferences and premature abstractions. Apply the **hackathon cost test** to security and hardening findings: fix them only if the fix doesn't make the team's job harder (no new machinery, process, or merge friction), since this is a small team on one trusted machine. The privacy features the demo promises still count as real problems. If more than five findings land in "Act on", or you agree with every point, go through them again. Follow-up fix PRs don't need another review unless they change a shared contract.

### 7. If main breaks after your merge

If your change broke `main`, fix it with a follow-up PR before doing anything else. Revert only if a quick fix isn't possible.

## Don't block on your person

Your person supervises asynchronously, so stay unblocked. Make a reasonable decision, carry it out, and explain it in your next message. Your person can correct course afterward. Don't ask "should I do X?" Do X, then say why.

Proceed without asking for anything reversible. That includes:

- fixing a review finding, even if it's outside your task,
- fixing a check failure caused by code you didn't touch (in its own small PR),
- resolving a rebase conflict (say which files and whose work in the Done message),
- adding a dependency,
- choosing between implementation approaches,
- splitting, reordering, or dropping your own tasks.

Stop and send a **Blocked** message only for:

- **Irreversible actions:** force-pushing `main`, deleting data that matters, deleting someone else's branch, or rotating or revoking a key.
- **Anything outside this repo:** posting to another person's or organization's repo, sending a message or email, signing up for a service, getting an API key, or spending money.
- **Conflicting claims:** another agent has claimed the same work, or another open PR changes the same shared contract. The people involved decide who does it.
- **Product direction:** the request is unclear in a way that changes what users would see. How to build it is your call. What to build is theirs.

While you wait, keep working on anything that isn't blocked.

Adapted from PStack's `principle-never-block-on-the-human`. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Talking to your person

Your person reads these messages between other work. Lead with what they need to know or decide. Use plain language: say what changed for a user of the app, not the internal file names. Keep each message short. Link the PR or Tracker comment rather than pasting its contents.

**Plan:** send when you get a request.

```text
Plan: <request in one line>
Tasks:
1. <task> — <what it changes for a user>
2. ...
Overlap: none | <agent/person> is on <task>, so I'm <doing X instead / waiting>
Starting on task 1 now.
```

**Done:** send after each merge.

```text
Done: <task> — <PR link>
What changed: <one or two lines, from a user's point of view>
Proof: <what was actually run, and the level reached, e.g. "reproduced in the running app (5)">
Review: pending | fixed <n> in <PR link>, dismissed <n> (also report reviews of earlier PRs here)
Decisions I made: <choices your person might want to reverse, e.g. added a dependency, changed someone else's code, resolved a conflict; or "none">
Not checked: <anything left unproven, or "nothing">
Next: <next task, or "all tasks done">
```

**Blocked:** send only for the cases in [Don't block on your person](#dont-block-on-your-person).

```text
Blocked: <task>
Need from you: <the decision or action, as one question>
Options:
- <option A> (recommended) — <consequence>
- <option B> — <consequence>
Why I stopped: <one or two lines>
Meanwhile: <what you're doing instead, or "idle">
```

## Secrets

- Never commit `.env` files or keys. Put the variable names in `.env.example`.
- If a key gets pushed, stop and tell a person immediately. The key has to be rotated, and deleting the commit doesn't help.

## Reference docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the system design and the full build plan. Shared contracts are defined there and in [docs/skills.md](docs/skills.md)
- [docs/setup.md](docs/setup.md): bringing the GX10 to a working state
- [docs/agents/verification.md](docs/agents/verification.md): proving a change works, bug-fix tests, what goes in the Proof section
- [docs/agents/review-triage.md](docs/agents/review-triage.md): sorting and answering review findings
- [docs/agents/design-red-flags.md](docs/agents/design-red-flags.md): shared-contract and architecture changes

These are adapted from [PStack](https://github.com/cursor/plugins/tree/e43c7ee26e0038c6c1fa8380dd34ce86ff94cb2a/pstack) (MIT). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
