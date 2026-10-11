# Codex candidate: one context, one model queue, three state owners

## Problem

Build a live, local activity-notes demo on the GX10 in about 16 hours. Its scarce resource is one shared vision-language model, not storage or HTTP throughput. The same evidence must support changing music, break overlays, a reviewed chat-created skill, and recall without each feature building its own memory or inference loop. Honor Python plus web and tray, Xorg `:1`, two-layer notes, transient screenshots, all four privacy controls, and swappable OpenAI-compatible inference. The supplied grounding is the authority: the 2.9-second vision measurement used a **black screen**, real desktop quality remains unproven, and no computer-control framework is validated here. This package is Phase B only; it makes no GX10 changes.

Workflow checklist: [x] Ground (supplied); [x] Sketch (this candidate); [ ] Agree/synthesis (orchestrator); [ ] Implement (later); [ ] Scrap/re-ground if implementation disproves the shape. The requested ownership and timing tables are capacity examples, not team assignments: D21 says one person currently, and P8 leaves actual build orchestration to Codex.

## Usage (caller's view)

Write these callers first. `sketch/usage.py` preserves them; the other sketches derive from these operations. A skill never reads SQLite or calls mpv. Recall never receives a screenshot. Both receive the same immutable, cited `Context`.

```python
# Skill tick: context includes interpretations, coverage, and stable episode IDs.
context = await portal.context(ContextQuery(window=last_hour, limit=40))
decision = await intelligence.decide(context, await skills.due(now), now)
receipts = await skills.apply(decision, context.token)

# Recall: the exact same query and evidence rules.
context = await portal.context(ContextQuery(window=last_hour, text=question))
answer = await intelligence.recall(question, context)
if await portal.is_current(context.token):
    show_answer(answer)

# Creation does not imply approval; enable the revision actually reviewed.
draft = await skills.save_draft(await intelligence.compose(user_request))
show_review(draft.source, draft.requested_actions, draft.digest)
await skills.enable(draft.skill_id, draft.digest)
```

Timeline correction is `portal.correct(note_ref, replacement_text)`; forgetting is `portal.delete(note_ref)`. The web UI translates stale-version errors into “This note changed; reload it,” rather than overwriting a concurrent edit. No caller coordinates SQL, revision invalidation, or an indexing job.

## Shape

Use a modular Python monolith with a Qt desktop companion. Store current observations and evidence-linked interpretations in one SQLite database owned by `Portal`; keep skill approvals and run receipts in a separate database owned by `Skills`. The companion owns presentation and playback effects. The runtime owns only consent, lifecycle, and composition. There is no general agent framework, message broker, vector store, or distributed event log in the critical path.

The interface is deliberately deep: `Portal.context` hides retrieval, chronology, coverage, episode construction, and revision validity; `Skills.apply` hides grants, freshness, deduplication, competing actions, and execution receipts. `Intelligence` hides admission, prompts, provider JSON, and validation. Exposing a generic “send prompt” method would move all that knowledge into every skill. These boundaries follow `boundary-discipline`, `single-source-of-truth`, and `minimize-reader-load`.

### Module map and state ownership

| Module | Knowledge and exclusive writes | Public boundary | Possible capacity lane |
|---|---|---|---|
| `domain.py` | Immutable vocabulary, identities, enums, action payloads; no mutable state | Values in the sketches | Joint contract, initially lane A |
| `portal.py` | `memory.sqlite`: observations, sample coverage, segments, evidence edges, FTS; correction/deletion semantics | `context`, `detail`, correction/deletion; capture/rollup write methods | A: memory and inference |
| `intelligence.py` | One inference queue, backend config, prompts and response validation; no second memory | `observe`, `rollup`, `decide`, `recall`, `compose` | A |
| `skills.py` | Managed YAML files, approved hashes, enabled state, due times, cooldowns, run receipts in `skills.sqlite` | Draft/review/enable; `due`, `apply` | B: skill behavior |
| `desktop.py` | X11 metadata/capture, local track manifest selection, companion IPC, Qt overlay/tray, mpv control | `CaptureSource`, `ActionBackend` | B initially; separable from skill policy |
| `runtime.py` + static web assets | Core lifecycle and persisted privacy settings; thin HTTP mapping, no business-state mirror | Status, consent, exclusions; HTTP commands to owners | C: web and integration |

Only the owning module imports its storage or transport library. Enforce this with a small AST import-boundary check before shared-contract merges: no `sqlite3` outside the two owners, no model SDK outside intelligence, no Qt/X11/mpv outside desktop, no direct private imports. Python visibility alone is insufficient. All action names, payload schemas, generation prompts, and UI permissions derive from one action registry; a contract check requires it to cover exactly the `Action` union and `ActionName` enum. Do not maintain a second handwritten action list in JavaScript.

Although the two databases contain domain-specific tables, they do not both own memory or consent. They need no cross-database transaction: an action carries an evidence token checked immediately before dispatch. Deletion changes the memory epoch; stale work fails closed. File updates and approvals are reconciled by content hash, not a distributed transaction. This applies `separate-before-serializing-shared-state` at the state boundaries while keeping the single memory owner's transactions local.

### Data structures and dominant reads

* **Observation:** capture UUID, time, app class, short visible-facts text, origin (`model`/`user`), version. “Raw” means the model's screenshot observation, not ground truth and not retained pixels. It must not contain inferred motivation.
* **Segment:** stable ID/version, half-open interval, activity enum, summary, explicitly labeled inferred intent, source observation versions, origin, and normalized episode ID. Model output is a `SegmentDraft`; Portal assigns identities and joins compatible adjacent segments. One rollup can return multiple segments when an activity changes inside its interval.
* **Coverage:** successful sample times and explicit gaps, independent of inferred text. A sample supports at most the next 15 seconds, bounded by the next sample, privacy gap, and query end. A missing interval greater than 30 seconds breaks continuity. Never extend focus using a timer alone. Observed duration is a sampled estimate, not a timesheet.
* **Episode:** derived maximal adjacent segments of the same activity with continuous coverage. Its identity comes from the first supporting observation; it stays stable as the episode grows. A meeting end requires meeting evidence followed by fresh non-meeting evidence, not silence or a privacy gap. Focus duration sums supported intervals; no keyboard logging is required.
* **Context:** immutable segments, evidence token, derived focus and meeting transitions, observed duration, gaps, freshness, and truncation cursor. `EvidenceToken` includes a memory epoch and segment versions. Destructive edits rotate the epoch; appends alone do not invalidate unrelated snapshots.

Private SQL indexes are chosen now: observations `(captured_at, id)`, segments `(start, end, id)`, evidence edges `(observation_id, segment_id)` plus reverse lookup, and FTS5 over segment summary/intent. Timeline scans use time indexes; recall intersects time bounds with FTS, ranks, then restores chronological order; correction/deletion finds all dependents through the reverse index. Focus and meeting facts are calculated over the full requested time slice before search filtering or page limits. No semantic vector index is required to answer the demo's time-bounded questions.

Portal has one read interface, not a skill cache plus a recall store. A normal tick asks for the last hour and up to 40 segments; maximum page size is 200. The timeline sets `include_observations=True` to obtain a bounded raw-observation page too, including observations still awaiting rollup; skills and recall leave it false. Recall parses explicit time ranges at the web boundary; an unparseable range uses the last hour and displays that assumption. Long/day queries page through the same interface. Text prompts have an 8K input-token budget within the target 32K context. When all evidence cannot fit, select a bounded chronological sample, mark it truncated, and say the answer is partial; duration totals still come from coverage over the whole requested range. Raw details are fetched by evidence ref, not routinely repeated in every prompt.

### Runtime picture and model-call budget

```text
GNOME Xorg :1
  │ ephemeral guarded capture
  ▼
Python core (one asyncio loop, singleton flock)
  capture opportunities ──► Intelligence's single admission queue
  Portal ◄── observation / segment commits ◄── one local model server
  Portal.context ──► joint skill decision ──► Skills.apply
  localhost web ───► recall / draft / edit / pause commands
  Skills.apply ──► private ordered Unix socket ──► Qt companion
                                                   ├─ tray + full-screen overlay
                                                   └─ mpv IPC → local tracks
```

Processes: existing Ollama **or** vLLM (never both large models); one core serving HTTP/static files on a configured loopback port; one Qt companion with GUI event loop; one mpv child owned by the companion. Starting via SSH must inherit the supplied display and Xauthority. Use a per-user singleton lock for the real capture loop; fixture-only development does not acquire it. Qt uses native text/widgets for the overlay, avoiding a second embedded browser dependency. The overlay is a reusable command with Escape, a dismiss button, and a hard timeout. Tray sends the same pause endpoint as the web app and renders core status; it owns no enable flag.

| Work | Cadence | Model calls/minute | Prompt/output bound |
|---|---|---:|---|
| Observe screenshot | Every 15 s when admitted and permitted | 4 maximum | 1280-px JPEG; about 950 image tokens in the black-frame test; output ≤240 tokens |
| Roll up notes | Every 120 s; early after app/window activity transition, minimum 30 s apart | 0.5 steady; 2 maximum | At most 32 fresh/overlap observations; output ≤700 tokens |
| Evaluate all due skills together | 30-s coalescing window, only with fresh interpreted context | Up to 2 total, independent of skill count | ≤8K input, ≤600 output tokens |
| Recall or skill composition | User request, admitted at at most 2/minute | 0 idle, up to 2 | ≤8K input, ≤900 output tokens |
| Correction/delete/edit/pause, action execution | User command / validated proposal | 0 | Deterministic code |

Budget: **6.5 calls/minute** with ongoing captures and skill checks at steady state; **8** during frequent activity transitions; **10** including interactive requests. A text repair occupies a later admission slot within these ceilings, not an extra immediate retry. At the observed 2.9-second black-screen latency, 6.5 calls imply roughly 19 model-seconds/minute; that is an illustrative lower-confidence estimate, not a performance claim for longer text output or real images. Capture's measured old 2.28-second subprocess cost is also not a measurement of this proposed Xlib adapter.

One active request, at most eight pending text jobs, and one replaceable **capture opportunity**. Pixels are acquired only after admission, never queued. Coalesce rollups and skill ticks, expire captures after their tick, and reject excess interactive jobs with a visible busy state. Interactive jobs get priority but at most two consecutive slots before the oldest background job; age background jobs to avoid starvation. Request timeout is 40 seconds; failures create coverage gaps and stale status, never a catch-up storm. Pause bypasses this queue.

Fresh skill decisions require an interpretation no older than 150 seconds and a current permitted capture no older than 30 seconds. Early rollup is driven by eligible app/window identity change, with identity used ephemerally; it is not another model call. Same-window changes may take up to the normal two-minute rollup. An editor-to-reader transition in the demo should cause an early rollup followed by the next joint skill tick; that latency must be measured, not promised. Unchanged-screen suppression is deferred: periodic samples are cheap enough initially and preserve honest continuity.

### Skill file format and complete examples

UTF-8 `.skill.yaml`, safely parsed with a closed versioned schema. The file is the goal/trigger/grant specification; the registry separately stores which **digest** was reviewed and enabled. Unknown fields/action names, excessively long content, unsupported versions, and unregistered hook names are errors. `priority` is bounded 0–100, intervals 30–3600 seconds, and cooldowns 0–86400. Every action is independently checked against the approved grant. No shell, arbitrary filesystem, or arbitrary network action exists in the initial vocabulary.

```yaml
version: 1
id: music
title: Music for the moment
goal: >-
  Infer a fitting mood from the current activity segments and inferred intent.
  Use focus for sustained debugging, calm for reading, upbeat for light varied
  work, and break for a real break. Choose a mood, never a track or file path.
trigger:
  every_seconds: 30
  when: >-
    Fresh notes support a fitting mood for the current activity.
    Prefer continuity when the evidence is ambiguous. Do nothing during gaps.
  once_per: mood_change
  minimum_focus_seconds: 0
allowed_actions:
  - music.set_mood
priority: 10
cooldown_seconds: 60
hook: null
```

```yaml
version: 1
id: break-reminder
title: Take a short break
goal: >-
  After a sustained focus streak, suggest a brief stretch with a full-screen
  overlay and request break music for up to three minutes. Be concise and kind.
trigger:
  every_seconds: 30
  when: >-
    The portal reports a current focus streak meeting minimum_focus_seconds.
    Remind once for that focus streak. Do not infer focus across gaps.
  once_per: focus_streak
  minimum_focus_seconds: 1500
allowed_actions:
  - overlay.show
  - music.set_mood
priority: 90
cooldown_seconds: 1200
hook: null
```

```yaml
version: 1
id: stand-after-meetings
title: Stand up after meetings
goal: >-
  Help me move between meetings and my next task. Show an overlay saying
  "Meeting finished — time to stand up" and invite a short stretch.
trigger:
  every_seconds: 30
  when: >-
    The portal reports a meeting-ended transition supported by a meeting
    segment followed by a fresh non-meeting segment. Never treat a missing
    capture, lock, pause, or excluded app as a finished meeting.
  once_per: meeting_end
  minimum_focus_seconds: 0
allowed_actions:
  - overlay.show
priority: 60
cooldown_seconds: 60
hook: null
```

These are actual complete proposed files, including the file produced for the chat demo. No `enabled: true` can be smuggled in by the generator. Draft saving returns the exact text, hash, and requested permissions. Review shows those together; enabling posts the hash. Editing an enabled file suspends that revision until reviewed again. A draft revision cannot silently replace the live approved specification.

The model receives all due goals/triggers plus one context and returns typed proposals. Natural-language conditions remain model interpreted. Hard constraints—valid episode, duration threshold, cooldown, grants, and repeat scope—are enforced by code. The validated structured `minimum_focus_seconds` field is authoritative; zero means no focus prerequisite. The break file's prose refers to the field rather than duplicating its value. `once_per: meeting_end` must name an eligible transition in the context. `focus_streak` must name the current streak. `activity_episode` supports other user-authored reminders tied to an observed episode. `mood_change` compares the proposed mood with the effective mood and respects a minimum dwell. A skill cannot mint a novel episode ID to bypass deduplication.

Optional hooks use `hook: {name: "registered_python_hook"}` and the `run_trusted_hook` signature. Only developer-installed, reviewed Python hooks may be registered; they replace that skill's model decision and return the same typed proposal. They are trusted code, not sandboxed plugins. Chat creation emits declarative files only in this build. This satisfies the optional exact-logic hook without pretending arbitrary Python is constrained by a YAML permission list.

### Actions, conflicts, and retries

`SetMood(mood, hold_seconds)` and `ShowOverlay(title, body, dismiss_label, timeout_seconds)` are the initial action union. Break's higher priority can hold break music for at most 180 seconds, enforced by Skills; after expiry normal music selection resumes at the next tick. Skills owns the desired mood and hold expiry in its run journal, derives the effective mood from acknowledgments, and rejects lower-priority changes during an active hold. One joint batch chooses at most one winning mood and one winning overlay by priority, then skill ID; a lower-priority proposal is marked skipped and may be reconsidered later. Do not overlay an existing reminder or churn music every capture. Render overlay fields as plain text. Skill text and notes are untrusted data in prompts; permission validation is deterministic outside the model.

The local manifest lists track IDs, relative paths under the approved library root, mood tags, license, and source URL. The desktop owner validates paths and chooses a deterministic next track tagged with the requested mood, avoiding the current track when possible. Missing tracks return a failure receipt and a visible library warning. An agent should later obtain the already-authorized 20–30 licensed tracks, recording provenance; this architecture runner does not download them. There is no streaming service or per-track inference.

Run identity combines reviewed skill digest, repeat scope/episode, and effect index. Mood transitions also include a persisted transition counter so returning to an earlier mood is valid. Reserve each effect in the skill journal before IPC, then record acknowledgment. The companion deduplicates run IDs during its lifetime; setting the same mood is also naturally idempotent. A crash after delivery but before acknowledgment leaves an `unknown` receipt; **never automatically replay an unknown overlay**. Restart begins paused. We accept a missed reminder in exchange for avoiding duplicate interruptions; there is no exactly-once promise across process crashes. A two-action break plan may partially succeed, and each effect has its own receipt.

Framework substitution happens only at `ActionBackend.execute/quiesce`. A framework adapter must implement the same local named actions, acknowledgments, pause barrier, and bounds. It gets no model prompts, skill permissions, or memory access. Give trycua at most one hour of later validation on real Xorg; failure means use the native backend immediately. General screen-clicking and arbitrary application launching are outside this demo's required action set.

### Privacy, correction, and failure boundaries

Runtime owns one monotonic consent generation. The tray and web app request changes from it. Pause advances the generation first, stops new captures and autonomous effects, drops queued inference work, invalidates old responses, and sends the companion a quiescence barrier to dismiss overlays and stop automated music. Only the barrier acknowledgment completes the paused status. Existing inference may finish locally, but cannot write a note or trigger an action. Recall and draft editing remain available while paused; enabling skills while paused does not run them.

The X11 capture adapter checks lock/DPMS and fails closed if status is unknown. On one Xlib connection it briefly grabs the server, enumerates mapped windows and checks app class/title exclusions, captures through the same connection, and releases the grab in `finally` before resizing or inference. Any mapped excluded window conservatively suppresses the entire capture, even if occluded. This trades recall coverage for a smaller exclusion mechanism and avoids trusting only the foreground app. Before and after checks reject lock transitions and black frames. No excluded window title enters notes or logs. An exclusion update advances the consent generation, discarding stale frames/results. This is an application privacy control, not protection against a hostile X server.

Screenshots exist only in RAM: frame, resized JPEG, and request encoding are released on success, failure, timeout, or cancellation. There are no screenshot files to clean up after a crash. Local model servers must run without request/image logging, and model buffers live only for the request; verify that configuration before the privacy demo. Releasing Python buffers does not claim cryptographic erasure or removal from swap. Metadata logs contain durations/status/IDs, never prompts, screenshots, window titles, or note text.

Portal edits use optimistic versions. Observation edits remove dependent interpretations and FTS entries in the same transaction and queue fresh rollup. Segment edits retain corrected text as user-authored, set old inferred classification to unknown, and are never overwritten automatically. The UI can request an explicit fresh interpretation. Segment deletion is labeled **“Delete segment and its source notes”**; it removes its observations and any other segments depending on them. Observation deletion removes all dependent segments. This prevents a future rollup from resurrecting deleted material. New live captures are new evidence; deleting history is not an app exclusion.

An edit/delete rotates the memory epoch before readers can obtain a new context. Pending rollups compare source versions at commit, pending recall checks its token before display, and actions check immediately before dispatch. The UI drops cached answers on epoch changes; chat answers are session-only, not saved transcripts. Run journals retain IDs/statuses plus nontext arbitration state such as desired mood/expiry, not note text, proposal reasons, or overlay copy. Core-queued decisions become invalid on deletion. Effects already dispatched may complete; forgetting a note does not undo a delivered effect. Pause provides the stronger desktop barrier when needed. SQLite uses `secure_delete`, current-only note content, no app-managed backups, and a WAL checkpoint after outstanding readers finish. Retention defaults to the current event's notes until explicit deletion; add configurable automatic retention after the demo rather than inventing a hidden purge policy.

If the model fails, show stale status and stop autonomous actions after freshness expires. If the desktop companion disconnects, stop admitting new autonomous effects and mark actions unavailable; capture/recall may continue if the privacy gate is valid. If either owner encounters a conflicting version, discard and retry from fresh context on the next normal tick. Do not store a second copy to make failures disappear.

HTTP binds loopback, validates Origin and a per-launch token for mutations, and serves its own assets locally. Companion IPC is a user-only Unix socket. Model credentials are read from external configuration, never skills or prompts. A remote backend is not enabled by default; local/offline demo behavior must remain explicit.

### Red-flag screen

| Flag | Design response |
|---|---|
| Shallow wrappers / pass-throughs | No repository/service/controller stack. Portal and Skills each complete an operation; desktop adapter adds real platform conversion and pause semantics. |
| Temporal decomposition | No load/validate/save packages. File validation belongs to Skills; evidence validation belongs to Portal; provider parsing belongs to Intelligence. |
| Information leakage | Context and typed actions cross boundaries; SQL, OpenAI JSON, X11 IDs, and Qt objects stay private. |
| Split ownership | Memory, skill approval/journal, and desktop effects each have one owner; UI stores only disposable views. |
| Two ways to do a task | Recall and every skill use Portal; model calls use Intelligence; effects use Skills then ActionBackend. Hooks follow the same proposal path. |
| Importable internals | Import-boundary check fails the local check suite, rather than relying on underscore conventions. |
| Hand-synced lists | Action registry generates prompt/UI schemas; exhaustive union/enum coverage check catches drift. |

### Build order for the next roughly 16 hours

All tests below are planned acceptance gates, not claims of execution. Only one integration owner runs against the real screen; other work uses recorded text fixtures and fake adapters. With one human directing agents, run the same independent work lanes asynchronously; with fewer coding agents, preserve the order and trim polish first.

| Hours | Parallel-ready work and integration gate | What it is tested against |
|---|---|---|
| 0–1 | Freeze caller examples, YAML/action schema and fixture contexts. A builds storage skeleton; B validates desktop dependency availability and local track library; C builds static timeline/chat shell. | Three shared fixtures: focus→reading, meeting→work, and lock/exclusion gap. No model needed. |
| 1–3 | A implements Portal and serialized inference; B validates actual Xlib capture/metadata, Qt tray/overlay, and mpv; C uses fake Portal JSON for timeline, edit/delete, pause UI. Framework spike ends within one hour. | In-memory/temp SQLite queries and destructive invalidation; actual unlocked screenshot and structured vision result; Escape and timeout on the real overlay; visible GNOME tray host. |
| 3–5 | Integrate one complete capture→observation→rollup→timeline path. B develops named effects with receipts; C wires the real memory endpoints. | Live desktop produces useful observations without intent claims; segments cite actual source IDs; pause during inference cannot publish; mapped excluded window suppresses capture. |
| 5–8 | Joint skill decision plus arbitration and digest approvals; finish tagged local tracks. C adds permission review panel and status. | Focus/reading fixture changes mood; real mpv plays the selected local tag; break beats music; duplicate episode delivers one overlay; failed effect has a truthful receipt. |
| 8–10 | Chat composition and exact-hash enable; recall on the same Portal. | Exact stand-after-meetings request yields valid disabled YAML; edited-after-review hash is refused; meeting→work triggers once; lock does not end a meeting; recall cites notes and admits gaps. |
| 10–12 | Integrate privacy and edit/delete under live inference load; restart/reconnect paths. | Delete while rollup/recall is running rejects stale results; restart does not replay unknown effects; pause acknowledgment means no queued desktop action remains; no screenshot files/log payloads. |
| 12–14 | Full live rehearsal, actual call latency and queue occupancy measurements. Consider backend swap only if Qwen3.8 is already vision-verified. | Editor→reading music change on GX10; real meeting-ended reminder; overlay Escape; last-hour recall; offline network disabled while demo continues. |
| 14–16 | Fix failing acceptance gates, rehearse and document launch sequence. Leave visual polish last. | One end-to-end presenter script, plus singleton capture lock, tray pause and web fallback, and clean restart into paused state. |

For a short live break demo, create a separate reviewed copy with a 60-second focus threshold and label it “Demo: one-minute break”; retain the real 1500-second default. Feed actual activity, not a synthetic live-looking timeline. Prepare prerecorded fixtures for development only and label any fallback demonstration honestly.

## Synthesis decision

## Tradeoffs accepted

* We accept up to two-minute same-window interpretation lag in exchange for sharing one model predictably across all features.
* We accept sampled durations and explicit gaps in exchange for avoiding keylogging and unsupported precision.
* We accept conservative suppression of any mapped excluded window in exchange for a small, auditable capture policy.
* We accept a Qt companion and mpv process in exchange for a real reusable full-screen overlay and local playback independent of a browser tab.
* We accept lost reminders after an ambiguous crash in exchange for no surprise duplicate overlays.
* We accept trusted installed hooks and declarative-only chat creation in exchange for avoiding an unbuilt Python sandbox.
* We accept partial, labeled answers for oversized recall ranges in exchange for one memory interface and bounded model context.

## Alternatives considered

* **Independent agent loop per skill:** each skill retrieves, infers, and acts. It hides individual skill logic but exposes model scheduling, inconsistent context, conflicting effects, and deletion races to every caller. Joint decisions plus a central effect owner hide more complexity behind fewer public operations.
* **Event-sourced stream with projections and worker processes:** immutable capture events feed rollup, retrieval, and skill subscribers. It hides scheduling and replay but exposes offsets, eventual consistency, correction tombstones, and image retention rules to clients. That machinery solves a scale problem absent on one desktop and makes “delete this note” much harder to explain.
* **Turnkey computer-use agent as the application core:** it hides desktop mechanics and planning, but every skill inherits its screenshot lifecycle, action semantics, and unsupported real-Xorg assumptions. Keep the useful replaceable desktop boundary without handing it memory ownership or model scheduling.

## Open questions and risks

* Can the unlocked-screen smoke test deliver accurate observations and useful segments within this provisional call budget? If not, reduce capture to every 30 seconds before shrinking privacy or review behavior.
* Does the stock GNOME session already expose the tray, and do the native Qt/Xlib dependencies work on this aarch64 image? The tray is a required gate, not silently replaced by a browser button; an unavailable host needs a concrete session-compatible resolution before rehearsal.
* Will real meeting observations distinguish a finished meeting from brief tab switches? Require supporting consecutive non-meeting observations during rollup before publishing the transition, and use a clear real meeting-to-editor demo transition.
* Can the Xlib capture critical section stay short enough not to disturb input? Measure its duration in the first desktop spike; do not keep the server grabbed for resize, network, or inference.
* Is Qwen3.8 vision actually ready and better on this workload? Until proven with the same fixtures, the demo stays on the grounded Ollama Qwen3.6 backend.
* Do users want permanent note retention or a default expiry after the event? The current proposal retains only text until explicit deletion and makes that visible in settings.

## Next implementation step

Implement Portal against the three shared text fixtures and the caller examples, proving correction/delete invalidation before attaching the first live capture.
