"""Pure visibility and ordered privacy policy; callers provide clipped geometry."""

from fnmatch import fnmatchcase

from daybook.journal import WindowInfo

from ._types import (
    DAYBOOK_TITLE_PATTERN,
    CaptureReason,
    CaptureStatus,
    PrivacySettings,
    Rect,
    ScreenState,
)


def is_bubble(info: WindowInfo) -> bool:
    return info.app.lower() == "daybook-bubble"


def intersection(left: Rect, right: Rect) -> Rect:
    x, y = max(left.x, right.x), max(left.y, right.y)
    edge_x = min(left.x + left.width, right.x + right.width)
    edge_y = min(left.y + left.height, right.y + right.height)
    return Rect(x, y, max(0, edge_x - x), max(0, edge_y - y))


def _subtract(rect: Rect, cover: Rect) -> list[Rect]:
    overlap = intersection(rect, cover)
    if overlap.width == 0 or overlap.height == 0:
        return [rect]
    right, bottom = rect.x + rect.width, rect.y + rect.height
    pieces = [
        Rect(rect.x, rect.y, rect.width, overlap.y - rect.y),
        Rect(
            rect.x,
            overlap.y + overlap.height,
            rect.width,
            bottom - overlap.y - overlap.height,
        ),
        Rect(rect.x, overlap.y, overlap.x - rect.x, overlap.height),
        Rect(
            overlap.x + overlap.width,
            overlap.y,
            right - overlap.x - overlap.width,
            overlap.height,
        ),
    ]
    return [p for p in pieces if p.width > 0 and p.height > 0]


def visible_exclusion(
    state: ScreenState, privacy: PrivacySettings
) -> CaptureReason | None:
    reason: CaptureReason | None = None
    for i, window in enumerate(state.windows):
        app_excluded = any(
            fnmatchcase(window.info.app.lower(), pattern.lower())
            for pattern in privacy.exclude_apps
        )
        title_matches = tuple(
            pattern.lower()
            for pattern in privacy.exclude_titles
            if fnmatchcase(window.info.title.lower(), pattern.lower())
        )
        if (
            not (app_excluded or title_matches)
            or window.rect.width <= 0
            or window.rect.height <= 0
            or window.opacity <= 0
        ):
            continue
        remaining = [window.rect]
        for cover in state.windows[i + 1 :]:
            if cover.opacity < 1 or is_bubble(cover.info):
                continue
            remaining = [
                piece for rect in remaining for piece in _subtract(rect, cover.rect)
            ]
            if not remaining:
                break
        if remaining:
            if app_excluded or any(
                pattern != DAYBOOK_TITLE_PATTERN.lower() for pattern in title_matches
            ):
                return "excluded_window"
            reason = "daybook_visible"
    return reason


def gate(state: ScreenState, privacy: PrivacySettings) -> CaptureStatus:
    if state.locked is not False:
        return CaptureStatus(
            "locked", "screen_unavailable" if state.locked is None else None
        )
    if privacy.paused:
        return CaptureStatus("paused")
    if state.active is None:
        return CaptureStatus("locked", "screen_unavailable")
    if state.overlay:
        return CaptureStatus("overlay")
    if state.idle_ms >= 300_000 and not state.microphone_active:
        return CaptureStatus("away")
    reason = visible_exclusion(state, privacy)
    if reason is not None:
        return CaptureStatus("withheld", reason)
    return CaptureStatus("running")


def black(thumbnail: bytes) -> bool:
    return not thumbnail or max(thumbnail) <= 5


def changed(left: bytes, right: bytes) -> bool:
    return len(left) != len(right) or (
        sum(abs(a - b) for a, b in zip(left, right)) / max(1, len(left)) / 255 >= 0.03
    )
