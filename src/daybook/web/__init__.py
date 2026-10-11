"""FastAPI composition. The public HTTP contract is daybook.web.wire."""

from __future__ import annotations

import asyncio
import html
import secrets
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from daybook.act import MusicPlayer, Overlay
from daybook.chat import Chat
from daybook.events import Bus
from daybook.journal import Journal
from daybook.mind import Mind
from daybook.model import ModelGateway
from daybook.sense import Sense
from daybook.skills import SkillBook

from . import wire as w


@dataclass(frozen=True)
class Services:
    journal: Journal
    sense: Sense
    mind: Mind
    skills: SkillBook
    chat: Chat
    gateway: ModelGateway
    music: MusicPlayer
    overlay: Overlay
    bus: Bus


def create_app(
    *, mock: bool = False, token: str, origin: str, services: Services | None = None
) -> FastAPI:
    """Bind to loopback. Mutations require both token and exact configured Origin."""
    if not mock:
        raise NotImplementedError(
            "Real services are implemented after the shared skeleton; use --mock-api"
        )
    from ._mock import MockAPI

    bus = Bus()
    backend = MockAPI(bus)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(backend.script())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="Daybook",
        version="0.1.0",
        lifespan=lifespan,
        responses={
            403: {"model": w.ErrorResponse},
            404: {"model": w.ErrorResponse},
            409: {"model": w.ErrorResponse},
            422: {"model": w.ErrorResponse},
        },
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        locations = [
            ".".join(str(part) for part in error["loc"]) for error in exc.errors()
        ]
        return JSONResponse(
            w.ErrorResponse(
                detail="invalid_request: " + ", ".join(locations)
            ).model_dump(),
            status_code=422,
        )

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") != origin or not secrets.compare_digest(
                request.headers.get("x-daybook-token", ""), token
            ):
                return JSONResponse(
                    {"detail": "invalid_token_or_origin"}, status_code=403
                )
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        path = Path(__file__).parent / "static/index.html"
        page = (
            path.read_text()
            if path.exists()
            else '<!doctype html><html><head><title>Daybook · API</title></head><body><p>Daybook mock API is running.</p><a href="/docs">API reference</a></body></html>'
        )
        meta = f'<meta name="daybook-token" content="{html.escape(token, quote=True)}">'
        return (
            page.replace("</head>", meta + "</head>")
            if "</head>" in page
            else meta + page
        )

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/api/timeline", response_model=w.TimelineResponse)
    async def timeline(
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
    ):
        if any(t is not None and t.tzinfo is None for t in (start, end)) or (
            start is not None and end is not None and start >= end
        ):
            raise HTTPException(422, "invalid_time_range")
        return backend.get_timeline(start, end)

    @app.get("/api/now", response_model=w.NowResponse)
    async def now():
        return backend.now()

    @app.patch("/api/segments/{id}", response_model=w.Segment)
    async def edit_segment(id: str, body: w.SegmentPatch):
        return backend.edit_segment(id, body)

    @app.delete("/api/segments/{id}", response_model=w.MutationResponse)
    async def forget_segment(id: str):
        return backend.forget_segment(id)

    @app.patch("/api/observations/{id}", response_model=w.Observation)
    async def edit_observation(id: str, body: w.ObservationPatch):
        return backend.edit_observation(id, body)

    @app.delete("/api/observations/{id}", response_model=w.MutationResponse)
    async def forget_observation(id: str):
        return backend.forget_observation(id)

    @app.post("/api/forget", response_model=w.MutationResponse)
    async def forget(body: w.TimeWindow):
        return backend.forget(body)

    @app.post("/api/forget-all", response_model=w.MutationResponse)
    async def forget_all():
        return backend.forget(None)

    @app.post("/api/chat", response_model=w.ChatResponse)
    async def chat(body: w.ChatRequest):
        return backend.chat(body.message)

    @app.get("/api/skills", response_model=w.SkillsResponse)
    async def skills():
        return backend.skills()

    @app.post("/api/skills/parse", response_model=w.SkillParseResponse)
    async def parse(body: w.SkillParseRequest):
        return backend.parse(body.id, body.text)

    @app.put("/api/skills/{id}", response_model=w.SkillResponse)
    async def save(id: str, body: w.SkillPut):
        return backend.save(id, body.text)

    @app.post("/api/skills/{id}/approve", response_model=w.SkillResponse)
    async def approve(id: str, body: w.ApprovalRequest):
        return backend.approve(id, body.hash)

    @app.post("/api/skills/{id}/enable", response_model=w.SkillResponse)
    async def enable(id: str):
        return backend.enable(id, True)

    @app.post("/api/skills/{id}/disable", response_model=w.SkillResponse)
    async def disable(id: str):
        return backend.enable(id, False)

    @app.delete("/api/skills/{id}", response_model=w.MutationResponse)
    async def delete(id: str):
        return backend.delete_skill(id)

    @app.get("/api/privacy", response_model=w.PrivacyResponse)
    async def privacy():
        return backend.privacy

    @app.put("/api/privacy", response_model=w.PrivacyResponse)
    async def set_privacy(body: w.PrivacyPut):
        return backend.set_privacy(body)

    @app.post("/api/overlay/{id}/dismiss", response_model=w.MutationResponse)
    async def dismiss(id: str):
        return backend.dismiss(id)

    @app.get("/api/status", response_model=w.StatusResponse)
    async def status():
        backend.status.shell_connected = backend.shells > 0
        return backend.status

    @app.post("/api/tick", response_model=w.TickResponse)
    async def tick():
        return backend.tick()

    @app.get(
        "/api/events",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "Named SSE events; see x-sse-events for payload schemas",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
    )
    async def stream(request: Request, client: Literal["ui", "shell"] = "ui"):
        async def generate():
            if client == "shell":
                backend.shells += 1
            try:
                with bus.subscribe() as queue:
                    yield ": connected\n\n"
                    while not await request.is_disconnected():
                        try:
                            event = await asyncio.wait_for(queue.get(), timeout=15)
                        except TimeoutError:
                            yield ": heartbeat\n\n"
                            continue
                        name, data = w.serialize_event(event)
                        yield f"event: {name}\ndata: {data}\n\n"
            finally:
                if client == "shell":
                    backend.shells -= 1

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        schema["x-sse-events"] = {
            name: model.model_json_schema() for name, model in w.EVENT_MODELS.items()
        }
        return schema

    app.openapi = openapi
    return app
