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
| `reads` | no | Daybook lists this skill may read besides the screen. The only value is `upcoming`; see [Reading the Upcoming list](#reading-the-upcoming-list). Shown to the user as a permission |
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
| `<signal> is <value> for <duration>` | when the value has been `<value>` continuously for at least `<duration>`. Explicit pause, lock, private, or away interruptions restart the streak; missing readings more than 5 minutes apart also restart it | the streak's first tick: once per streak |
| `every <duration>` | at most once per wall-clock bucket, and only on ticks, so only while the user is present | the bucket |

A short completed fullscreen app overlay preserves a duration rule's original occurrence key when the readings on either side remain within five minutes and have the same known value. Dismissing a reminder therefore does not restart that reminder's timer or allow a second firing for the same streak. An overlay lasting more than five minutes breaks continuity. Streak duration, including `$<signal>.for`, remains elapsed time between the first and latest matching readings.

**Durations:** a non-negative integer followed by `s`, `m`, or `h`, for example `30s`, `2m`, `50m`, `2h`. Zero (`0s`) is allowed only where a parameter says so (`min_dwell`); trigger durations and cooldowns must be greater than zero.

**Arguments:**

- literals (string, number, boolean)
- `$<signal>`: the signal's current value
- `$<signal>.for`: the current streak length, rendered like "52 min"
- `$upcoming.next` (text parameters of a skill with `reads: [upcoming]`): the soonest Upcoming item, rendered like "CSE 130 Assignment 3 (due in 2 h 10 min)", or "nothing due"

**Steps** run in order. An ordinary failing step is recorded, and the rule continues with its next step. When capture is paused, the skill is disabled, or its approved file changes, eligibility denial stops subsequent steps and hooks. Desktop effects re-check immediately before each helper starts, including all stages of opening a new tab. A helper already started may finish; Daybook suppresses later effects rather than undoing them. The claimed occurrence is not retried.

### Actions

The action catalog in [ARCHITECTURE.md §6.7](ARCHITECTURE.md#67-act-actions-music-overlay-and-desktop) is the only list of actions and their parameters; it isn't repeated here. In a running system, `daybook skill check` and the skill editor show the live catalog.

For short, casual reminders, use `overlay.show` with `layout: bubble` and conversational `body` text; use `layout: fullscreen` for a reminder that covers the screen. Omitting layout retains fullscreen behavior. Bubble rectangles are masked from capture in RAM; fullscreen pauses capture. The catalog supplies the valid layout values and defaults.

To act on something only after warning about it, show the warning first, then `wait`, then act: for example `overlay.show` (a bubble), `wait: { for: 5s }`, `desktop.close_tab: { title: "*Instagram*" }`. `desktop.close_tab` refuses unless the same skill's warning was actually shown, and a pause or disable during the `wait` stops the close. The built-in `instagram-nudge` skill shows the pattern.

Desktop navigation uses explicit actions and permissions. For example:

```yaml
do:
  - desktop.launch: {app: "Visual Studio Code"}
  - desktop.focus: {app: "Visual Studio Code", title: "*Budget*"}
  - desktop.open_tab: {url: "https://example.com", app: "Firefox"}
  - desktop.switch_tab: {tab: "2", app: "Firefox"}
allow: [desktop.launch, desktop.focus, desktop.open_tab, desktop.switch_tab]
```

Launch accepts an installed application name or desktop file ID. A unique final word of an installed display name (such as Code or Chrome), or its class name (such as VS Code), also resolves; duplicate matches are refused. Focus also accepts a case-insensitive WM_CLASS glob and an optional window-title glob. Duplicate installed names or multiple non-active matching windows return an ambiguity error; a focus title selector can choose the window. Tab actions default to the active browser; an explicit app selects a running browser window. To target one of several browser windows, focus its title first, then omit the tab action's app.

Tab navigation supports Firefox/LibreWolf and Chrome/Chromium/Brave/Edge using standard Linux shortcuts. `next` and `previous` follow the tab strip; quoted `"1"` through `"8"` select positions, and `last` selects the final tab. Hidden tab titles are not searchable. New-tab URLs must be HTTP/HTTPS; `desktop.open_url` retains file and mailto support. No action closes or quits a whole application.

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
- `reads` isn't a list of known names, a signal is named like a `reads` entry (such as `upcoming`), or `$upcoming.next` appears without `reads: [upcoming]` or in a non-text parameter
- a hook step appears in a skill that isn't a built-in (any user or chat-made skill), or has no matching function in `<id>.hook.py`

An invalid file is still saved, and shows as **invalid** in the UI so it can be fixed.

One limit spans skills, so it's checked when a skill is approved or enabled rather than when one file is parsed: at most 12 signals across all active skills.

## Approval

- **Approved and enabled are separate.** A skill runs only when it's enabled *and* `sha256(skill file + hook file)` is approved. Disabling a skill keeps its approval, so turning it back on needs no new review.
- **Edits reset approval.** Any edit, whether by hand, in the UI editor, or by the generator, returns the skill to **needs review**.
- **Stale approvals are refused.** "Approve & enable" posts the hash the user reviewed. If the file changed since, the server refuses and shows the new text.
- **Built-in exception.** Skills in `skills/builtin/` count as approved, because they're part of the repo and go through normal PRs. Built-ins other than `music` start disabled.

## Chat requests and capability limits

Chat can draft reminders from sampled screen notes: visible apps, windows, text,
numeric values and UI elements, with supported inferences about the current task.
Presence or absence can be evidence when the relevant area is sufficiently
visible. Missing or ambiguous evidence produces an unknown signal reading;
it does not make the capability unavailable.

A warning before an action is a best-effort reminder while its visible condition
is observed. Sampling cannot guarantee catching a quick action or block it.
Drafts preserve the requested activity, conditions, and named application/window targets.
Daybook uses only the action catalog. It cannot hear calls, detect voice tone,
read hidden app state, use private app APIs or keyboard hooks, intercept actions,
or know feelings that have not been expressed.

If an essential requested input or action is fundamentally unavailable, chat
explains the limitation instead of saving a skill. `draft_skill` raises
`UnsupportedSkill` with that explanation, and Chat returns a plain response.
Supported drafts keep the usual review and exact-hash approval flow. Skill drafts
use low reasoning effort to plan combined conditions; other model calls keep
their existing effort settings.

## Reading the Upcoming list

Daybook keeps an **Upcoming list** of deadlines and events: it scans notes that name a date (a calendar event, an assignment page, an email) and adds what it finds, and the user can add items in chat ("remember CSE 130 Assignment 3 is due Friday at 11:59 PM"). A skill opts in with `reads: [upcoming]`:

- On every tick, the skill's questions may use the list as current facts. Each item comes with its due time already computed by code, for example "CSE 130 Assignment 3 (due in 2 h 10 min)", so a question can say "while something on the Upcoming list is due within 3 hours" and the model only compares, never does date math.
- Its text arguments may use `$upcoming.next`.
- Skills without `reads` see exactly what they saw before: the screen.

```markdown
---
id: deadline-nudge
name: Back to work before a deadline
reads: [upcoming]
signals:
  drifting:
    ask: >
      Is the user watching videos, scrolling social media, gaming, or idly browsing
      rather than working, while something on the Upcoming list is due within 12 hours?
    type: bool
rules:
  - when: drifting is true for 2m
    cooldown: 10m
    do:
      - overlay.show:
          title: Deadline coming up
          body: $upcoming.next. Time to get back to it?
          dismiss: Back to it
          layout: bubble
allow: [overlay.show]
---
When something on my Upcoming list is due soon and I drift off to videos or social media, nudge me back to work.
```

This is the built-in `deadline-nudge` (disabled by default). Chat drafts see recent notes and the Upcoming list, so "pull me back to this assignment if I slack off" can name the assignment in the question and reminder, and can add `desktop.focus` to bring its window forward.

## Writing good signals

- Ask about the **present**: "Is the user in a video call right now?", not "Did the user have a meeting today?"
- Make each question answerable from screen notes: apps, windows, and what's visible.
- Prefer `bool` or a short enum, with a description for each value.
- Let the trigger handle time ("is true for 50m"). Don't put time in the question.
- A rule tests one value of one signal. Independent rules do not combine as AND.
  Put combined requirements, exclusions and numeric comparisons in one present
  boolean question, then trigger that condition becoming true or persisting.
- Use `updates` only for an action intentionally refreshed on every known reading.
  It does not filter values or compare a number to a threshold; reminders need a
  transition, a held condition, or a timer.

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

While this fullscreen break overlay is up, capture is paused for it (`OVERLAY`). With no new observations there's no tick, so the music skill can't replace the break music. After the overlay is dismissed, the next tick sets the mood again.

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


Active signal questions also guide which visible facts the screenshot observer preserves. They are context for evidence selection, not extra model calls or instructions to decide a signal. The observer retains ordinary activity plus relevant short text, numbers, controls, or status; it only reports absence when the relevant area is sufficiently visible. Unreadable, clipped, or missing evidence remains uncertain for the tick.

Approving a new or edited skill refreshes evidence through the existing journal-epoch resume behavior. Disabling or re-enabling an approved skill changes the observer's questions on a still screen at the normal ten-second observation limit. Queued observations read the latest approved questions at admission; changes during an in-flight observation refresh after it completes. Temporary skill-file reconciliation failures do not silently replace active questions with an empty list.
