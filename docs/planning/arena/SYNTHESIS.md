# Arena synthesis record

PStack `arena` Phases C–F for the architecture. The task given to every runner is in [TASK.md](TASK.md).

## Runners

| Runner | Engine / model family | Output |
|---|---|---|
| `opus` | Claude Code, Claude Opus | [candidate-opus/](candidate-opus/) |
| `codex` | Codex CLI, OpenAI GPT (Codex default model) | [candidate-codex/](candidate-codex/) |

Launched 2026-10-10 16:52 PT.

## Rubric

Written before reading any candidate. Runners didn't see it. Score each criterion 1–5.

1. **Parallel build by 2–3 people with agents.** Few shared contracts, each with one owner. Modules can be built and tested independently (fakes or replays) on one shared box, without needing the real screen or the real model for most work.
2. **Demo reliability.** The music skill, break overlay, chat-created skill, and recall work end to end with any OpenAI-compatible vision model. The design degrades sensibly on a locked screen, a slow or swapped model, a missing music tag, or a bad generated skill file.
3. **Model budget.** One shared model at about 3 s per image call. The design states its cadence and calls per minute and stays within them. Simple filtering (change detection, exclusions, timestamps) is done in code, not by the model.
4. **Interface depth and ownership.** One owner per piece of state (notes, skills, settings). Small portal and action interfaces that hide real work. No design red flags (split ownership, two ways to do one task, hand-synced lists, importable internals).
5. **Extensibility the product promises.** A new skill is a text file (plus an optional hook) with no core changes. Allowed actions are whitelisted per skill. A computer-control framework could replace the in-house actions behind the same interface.
6. **Privacy built into the structure.** Pause, exclusions, screenshot deletion, and note editing are enforced at the right boundary, not by convention.

## Cross-judge

_Pending: a judge from a different model family scores both candidates against the rubric._

## Pick

_Pending._

## Grafts

_Pending._

## Verification

_Pending._
