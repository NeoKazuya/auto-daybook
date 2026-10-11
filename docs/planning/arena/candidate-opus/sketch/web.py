"""daybook.web — FastAPI on 127.0.0.1:<port> (default 8765) + a no-build static UI (index.html, app.js ES modules).

This file is the frontend/backend contract. Routes call one method each on the owning module; no route
holds logic. Response bodies are built from domain types by small `to_json` functions here (the only
place wire shapes exist).

Pages (one HTML page with tabs; titled "Daybook · <tab>", which the default exclusion matches):
    Timeline   segments (expand -> observations), capture spans (paused/locked/private), edit + delete + "forget last 15 min"
    Chat       recall answers with links into the timeline; skill drafts render as a review card
    Skills     list with status; review card = goal, signals as questions, rules in words, permissions, raw file;
               editor with live parse errors; Enable (posts the reviewed hash) / Disable / Delete
    Privacy    pause toggle, exclusions editor, "screenshots kept: none" indicator, model + budget footer
    /overlay/<id>   the overlay page the shell renders full-screen (title, body, countdown, dismiss button)

Routes:
    GET    /api/timeline?from=&to=               -> {segments: [...], spans: [...]}            portal.segments / capture_spans
    GET    /api/now                              -> Situation                                    portal.now
    PATCH  /api/segments/{id}   {title?, intent?, kind?}                                         journal.edit_segment
    DELETE /api/segments/{id}                                                                    journal.forget_segment
    PATCH  /api/observations/{id} {seen}                                                         journal.edit_observation
    DELETE /api/observations/{id}                                                                journal.forget_observation
    POST   /api/forget          {from, to}                                                       journal.forget_range
    POST   /api/chat            {message}        -> {kind: answer|draft|text, ...}               chat.respond
    GET    /api/skills                           -> [SkillEntry]                                 book.entries
    PUT    /api/skills/{id}     {text, hook?}    -> SkillEntry                                   book.write
    POST   /api/skills/{id}/approve {hash}       -> SkillEntry | 409 stale                       book.approve
    POST   /api/skills/{id}/disable                                                              book.disable
    DELETE /api/skills/{id}                                                                      book.delete
    POST   /api/skills/parse    {text}           -> {errors: [...]}                              parse_skill (live editor check)
    GET    /api/privacy  /  PUT /api/privacy {paused, exclusions}                                sense.privacy / set_privacy
    POST   /api/overlay/{id}/dismiss                                                             overlay.hide
    GET    /api/status                           -> {model: GatewayStatus, music, overlay, capture}
    GET    /api/events  (SSE; ?client=shell|ui)  -> every bus event as {type, data}
    POST   /api/tick                                                                             mind.tick_now (demo/debug button)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WebDeps:
    """Everything the routes call. Built in app.py."""

    journal: object
    sense: object
    mind: object
    book: object
    chat: object
    overlay: object
    music: object
    gateway: object
    bus: object


def create_app(deps: WebDeps) -> object:
    """Returns the FastAPI app with the routes above and /static mounted."""
    raise NotImplementedError
