# Task-bound smarter breaks

Start by naming what you want to work on. Daybook then offers optional desktop
notifications for extended distraction, sustained observable slowing, or a long
stretch of steady work. It does not block the screen or change the music.

## Start and stop

Run the helper on the machine running Daybook, from the project checkout:

```sh
uv run python scripts/focus_session.py start "Study chapter 3 of my biology textbook"
```

The helper shows the complete generated skill and its notification permission.
Answer `y` to approve that exact version and begin. Use `--yes` if you intentionally
want to approve the generated notification-only skill without interactive review.
Declining leaves the skill saved but inactive for review in the app.

```sh
uv run python scripts/focus_session.py status
uv run python scripts/focus_session.py stop
```

Stop before a planned break, ending work, or switching tasks. Start again with
your task when you return. Every start has a new skill version so the skill
owner clears old readings/firings on approval, including for the same task.
There is one session at a time, stored by the existing SkillBook as the user
skill `focus-session`; no separate task database or additional model loop exists.

For a different development daemon, pass its exact configured origin and token:

```sh
uv run python scripts/focus_session.py \
  --url http://127.0.0.1:8771 --token-file ~/.daybook-a-byron/token \
  start "Finish the methods section" --work-minutes 45 --off-task-minutes 7
```

`DAYBOOK_HOME` also controls the default token location. Tokens remain on
loopback requests; the helper disables proxies and refuses redirects.

## Reminders

| Current state, continuously observed | Default delay | Reminder |
| --- | --- | --- |
| Clearly unrelated activity | 5 minutes | “Back to your task?” |
| On-task work with repeated observable unproductive loops | 10 minutes | “Would a short break help?” |
| Steady work on the task | 50 minutes | “Time to recharge?” |
| Meeting, intentional rest, uncertainty | No reminder | Defer |

All delays are editable with `--off-task-minutes`, `--slowing-minutes`,
`--work-minutes`, and `--cooldown-minutes` (default 10). Values are whole minutes
from 1 to 240. These defaults are product choices, not measurements of fatigue.

One enum signal makes the three reminder conditions mutually exclusive on a
tick. The existing evaluator measures each state streak and fires once per
streak, with a per-rule cooldown. Changing state resets its timer; in particular,
the 50-minute reminder measures steady work, not total accumulated session time.
An observed slowing stretch uses the earlier 10-minute reminder instead.

The signal asks about the explicit task, not whether an app is generally
“productive.” Relevant documentation, research, reading and deliberate thinking
count as work. A still screen, little typing, ordinary errors while debugging,
or a difficult task alone do not establish slowing. The model must see repeated
unsuccessful loops relative to earlier progress; a suggestion never asserts that
the user is fatigued. Uncertainty should defer the reminder.

The capture gate and runtime remain authoritative: paused, locked, excluded and
away time is not interpreted as distraction. Missing readings and gaps longer
than five minutes break streaks. An actual call or identifiable intentional break
has no notification rule. To reliably identify an intentional break, stop the
session rather than relying only on screen inference.

## Integration and proof

The helper only uses existing `/api/skills` parse/save/approve/disable and
`/api/status` routes, with the daemon's token and exact Origin. It checks the
generated skill with the live parser before replacing a previous session, and
refuses to replace an unrelated skill named `focus-session`. The only allowed
action is `notify.send`. Existing built-in break and demo skills are unchanged;
disable their fixed reminders if you want this session to be the only break
reminder.

The mock API can demonstrate task entry, validation, exact-version approval,
replacement and stop. It cannot demonstrate actual inference or timed reminders.
Tests use the real parser and HTTP routes. GX10 inference, notification delivery
and false-positive rates need the completed capture/tick/action composition and
a real or recorded session before this feature can be called deployed.
