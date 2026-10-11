# Skill file format

A skill is a text file that asks the model questions about what the user is doing, and reacts to the answers with allowed actions. This document is the contract that the parser (`daybook.skills.parse_skill`), the chat generator, the review UI, and every skill author share. For how skills run, see [ARCHITECTURE.md §2](ARCHITECTURE.md#2-the-core-idea) and [§6.6](ARCHITECTURE.md#66-skills-format-approval-triggers-and-generator).

Changing this format is a shared-contract change (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

## Files

- `<id>.skill.md`: YAML front matter, followed by a Markdown body. The body is the goal, written in plain words for the user and the model.
- `<id>.hook.py` (optional, built-in skills only): async functions that rules can call as steps. Hooks are unrestricted Python, so they're allowed only in `skills/builtin/`, where they go through the repo's PR review. `.hook.py` files anywhere else are never loaded.
- Built-in skills live in the repo's `skills/builtin/`. User and chat-made skills live in `$DAYBOOK_HOME/skills/`.

## Front matter

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | `[a-z0-9-]{1,40}`. Must match the file name |
| `name` | yes | Short display name |
| `signals` | yes (at least 1) | Questions the tick answers on this skill's behalf. At most 4 per skill, and at most 12 across all active skills |
| `rules` | yes (at least 1) | When to act, and what to do |
| `allow` | yes | The actions this skill may use. Shown to the user as its permissions |
| `created_by` | no | `builtin`, `user`, or `chat`. Set by the app |
| `request` | no | For chat-made skills, the message it was generated from |

### Signals

```yaml
signals:
  <name>:
    ask: <one question about the user's situation right now, answerable from the notes>
    type: bool | music.mood | {enum: [a, b, c]} | {number: [min, max]}
    describe: {a: "...", b: "..."}   # enum only, optional: one line per value, shown to the model
```

- A signal is stored as `<skill id>.<name>`, so two skills can both ask `focused` without colliding.
- `music.mood` is a **catalog type**. Its values and descriptions come from `~/Music/daybook/library.toml`.
- An answer outside the type is stored as *unknown*. So is "can't tell".

### Rules

```yaml
rules:
  - when: <trigger>
    cooldown: <duration>        # optional: minimum time between firings of this rule
    do:
      - <action>: { <param>: <arg>, ... }
      - hook: <function name>   # optional, built-in skills only
```

**Triggers.** There are exactly four forms. Anything else is a parse error.

| Form | Fires | Occurrence key (it fires at most once per key) |
|---|---|---|
| `<signal> updates` | on every tick where the signal has a known value | that tick |
| `<signal> becomes <value>` | when the value changes to `<value>`. Needs a previous known reading within 5 minutes, so app startup is never an edge | that tick |
| `<signal> is <value> for <duration>` | when the value has been `<value>` continuously for at least `<duration>`. A gap of more than 5 minutes (lock, pause, away) breaks the streak | the streak's first tick: once per streak |
| `every <duration>` | at most once per wall-clock bucket, and only on ticks, so only while the user is present | the bucket |

**Durations:** `30s`, `10m`, `2h`.

**Arguments:**

- literals (string, number, boolean)
- `$<signal>`: the signal's current value
- `$<signal>.for`: the current streak length, rendered like "52 min"

**Steps** run in order. A failing step is recorded, and the rule continues with its next step.

### Actions

The action catalog in [ARCHITECTURE.md §6.7](ARCHITECTURE.md#67-act-actions-music-overlay-and-desktop) is the only list. Today it has:

- `music.play_mood {mood, min_dwell="60s"}`, `music.stop`
- `overlay.show {title, body, dismiss="OK", minutes=1, style=calm|alert}`, `overlay.hide`
- `notify.send {title, body}`
- `desktop.open_url {url}`, `desktop.launch {app}`, `desktop.focus {app}`

Every action used in `do` must appear in `allow`. Parameter values are checked against the catalog when the file is parsed; for example, `mood` must be a mood in the library.

## Validation

`parse_skill` rejects a file, listing every error with its location (for example `rules[0].when`), if any of these holds:

- front matter is missing or isn't valid YAML, or a required field is missing
- `id` doesn't match the file name or the pattern
- there are unknown fields
- a trigger isn't one of the four forms
- a trigger or argument refers to a signal that isn't declared
- an action is used in `do` but missing from `allow`
- an `allow` entry isn't in the catalog
- an argument doesn't fit its parameter's type
- the skill has more than 4 signals
- a hook step appears in a skill that isn't a built-in (any user or chat-made skill), or has no matching function in `<id>.hook.py`

An invalid file is still saved, and shows as **invalid** in the UI so it can be fixed.

One limit spans skills, so it's checked when a skill is approved or enabled rather than when one file is parsed: at most 12 signals across all active skills.

## Approval

- **Approved and enabled are separate.** A skill runs only when it's enabled *and* `sha256(skill file + hook file)` is approved. Disabling a skill keeps its approval, so turning it back on needs no new review.
- **Edits reset approval.** Any edit, whether by hand, in the UI editor, or by the generator, returns the skill to **needs review**.
- **Stale approvals are refused.** "Approve & enable" posts the hash the user reviewed. If the file changed since, the server refuses and shows the new text.
- **Built-in exception.** A skill in `skills/builtin/` counts as approved while its files are identical to `origin/main`, because changes there reach `main` only through a reviewed PR. A built-in that differs, even if committed on a feature branch, needs review like any other skill. Built-ins other than `music` start disabled.

## Writing good signals

- Ask about the **present**: "Is the user in a video call right now?", not "Did the user have a meeting today?"
- Make each question answerable from screen notes: apps, windows, and what's visible.
- Prefer `bool` or a short enum, with a description for each value.
- Let the trigger handle time ("is true for 50m"). Don't put time in the question.

## Built-in skills

### `music`

```markdown
---
id: music
name: Mood music
signals:
  mood:
    ask: Which kind of music fits what the user is doing right now?
    type: music.mood
rules:
  - when: mood updates
    do:
      - music.play_mood: { mood: $mood }
allow: [music.play_mood]
---
Play local music that matches what I'm doing, and change it when my activity changes.
Focus music while I code or debug, calm music while I read, break music when I'm not working.
```

`mood updates` fires on every tick. `play_mood` does nothing if that mood is already playing, and won't change this skill's own choice again within the 60 s `min_dwell`, so the music doesn't flip back and forth. The dwell counts only this skill's changes, so after a break the music skill can switch back right away.

### `break`

```markdown
---
id: break
name: Break reminder
signals:
  focused:
    ask: >
      Is the user doing sustained, focused work (coding, debugging, writing, studying)
      rather than browsing, chatting, watching media, or idling?
    type: bool
rules:
  - when: focused is true for 50m
    do:
      - overlay.show:
          title: Time for a break
          body: You've been focused for $focused.for. Stand up, look away from the screen, get some water.
          dismiss: I'm back
          minutes: 5
      - music.play_mood: { mood: break, min_dwell: 0s }
allow: [overlay.show, music.play_mood]
---
After a long stretch of focused work, cover the screen with a break reminder and switch to break music.
```

While the overlay is up, capture is paused for it (`OVERLAY`). With no new observations there's no tick, so the music skill can't replace the break music. After the overlay is dismissed, the next tick sets the mood again.

### `break-demo`

`break-demo` is identical to `break` except:

- `id: break-demo`
- `name: Break reminder (demo, 2 min)`
- `when: focused is true for 2m`

It's used for the live demo, so the real skill keeps its 50 minutes and nothing is edited on stage. Enable either `break` or `break-demo`, not both.

## Example: a skill made from chat

Request: *"remind me to stand up every time I finish a meeting"*

```markdown
---
id: stand-after-meeting
name: Stand up after meetings
created_by: chat
request: remind me to stand up every time I finish a meeting
signals:
  in_meeting:
    ask: >
      Is the user currently in a video call or online meeting
      (Zoom, Google Meet, Microsoft Teams, Jitsi, a Discord or Slack call, or similar)?
    type: bool
rules:
  - when: in_meeting becomes false
    cooldown: 10m
    do:
      - overlay.show:
          title: Meeting's over
          body: Stand up and stretch for a minute before the next thing.
          dismiss: Done
          minutes: 1
allow: [overlay.show]
---
Every time I finish a meeting, remind me to stand up.
```

## Music library

`~/Music/daybook/library.toml` is the only list of moods. Each mood has a folder of tracks, and every track records its license and source:

```toml
[moods.focus]
describe = "concentrated work: coding, debugging, writing"
[moods.calm]
describe = "reading, research, slow browsing"
[moods.energetic]
describe = "light admin: email, tidying files, quick chores"
[moods.break]
describe = "not working: videos, social media, idle, on a break"

[[tracks]]
file = "focus/kevin-macleod-cipher.mp3"
title = "Cipher"
artist = "Kevin MacLeod"
license = "CC BY 4.0"
source = "https://incompetech.com/"
```

`daybook music check` fails if a folder and a mood disagree, or if a track is missing its license or source.
