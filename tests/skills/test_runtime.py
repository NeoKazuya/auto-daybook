import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from daybook.act import build_catalog
from daybook.events import Bus
from daybook.journal import (
    CaptureState,
    Portal,
    SignalId,
    SignalReading,
    SignalValue,
    Situation,
    Tick,
)
from daybook.skills import (
    NeedsReview,
    Rule,
    SignalLimit,
    SkillBook,
    StaleApproval,
    Trigger,
    evaluate,
)

BASE = datetime(2026, 10, 10, tzinfo=timezone.utc)
MOODS = {"focus": "work", "calm": "read", "break": "rest"}


def row(seconds, value: SignalValue = True, name="focused"):
    return SignalReading(
        SignalId("demo", name), value, BASE + timedelta(seconds=seconds), f"t{seconds}"
    )


@pytest.mark.parametrize(
    "form,value,seconds,history,expected",
    [
        ("updates", None, 0, [row(0)], "t0"),
        ("updates", None, 0, [row(0, None)], None),
        ("becomes", False, 0, [row(0, False)], None),
        ("becomes", False, 0, [row(0), row(30, False)], "t30"),
        ("becomes", False, 0, [row(0), row(301, False)], None),
        ("becomes", False, 0, [row(0), row(20, None), row(30, False)], "t30"),
        ("is", True, 60, [row(0), row(30), row(60)], "streak:t0"),
        ("is", True, 60, [row(0), row(30, None), row(60)], None),
        ("is", True, 60, [row(0), row(301), row(361)], "streak:t301"),
        ("is", True, 60, [row(0), row(30, False), row(60)], None),
        ("is", 2, 60, [row(0, 2.0), row(60, 2.0)], "streak:t0"),
    ],
)
def test_four_forms(form, value, seconds, history, expected):
    rule = Rule("test", Trigger(form, "focused", value, seconds), ())
    occurrence = evaluate(rule, history, now=history[-1].at)
    assert (occurrence.key if occurrence else None) == expected


def test_every_bucket_and_stale_history():
    rule = Rule("every 1m", Trigger("every", seconds=60), ())
    a = evaluate(rule, [row(0)], now=BASE)
    b = evaluate(rule, [row(30)], now=BASE + timedelta(seconds=30))
    assert a and b and a.key == b.key
    assert evaluate(rule, [row(0)], now=BASE + timedelta(seconds=30)) is None


class MemoryPortal(Portal):
    def __init__(self):
        self.capture: CaptureState = "running"
        self.history = []

    def now(self):
        return Situation(self.capture, None, None, ())

    def capture_spans(self, range):
        return []

    def readings(self, signal, range):
        return [
            r
            for r in self.history
            if r.signal == signal and range.start <= r.at < range.end
        ]


def text(id="demo", trigger="focused updates", cooldown="", extra_steps="", count=1):
    signals = "\n".join(
        f"  {'focused' if n == 0 else f's{n}'}:\n    ask: Is work on screen?\n    type: bool"
        for n in range(count)
    )
    return f"""---
id: {id}
name: Demo
signals:
{signals}
rules:
  - when: {trigger}
{cooldown}    do:
      - notify.send: {{title: Rest, body: 'Focused for $focused.for'}}
{extra_steps}allow: [notify.send]
---
Rest.
"""


@pytest.fixture
def setup_book(tmp_path):
    portal = MemoryPortal()
    called = []
    from daybook.journal import Outcome

    async def notify(skill, args):
        called.append((skill, args))
        return Outcome("notify.send", True)

    resets = []

    def reset(skill):
        resets.append(skill)
        portal.history = [r for r in portal.history if r.signal.skill != skill]

    book = SkillBook(
        tmp_path,
        tmp_path / "builtin",
        tmp_path / "journal.db",
        portal,
        build_catalog(MOODS, {"notify.send": notify}),
        Bus(),
        clear_readings=reset,
    )
    return book, portal, called, resets


def test_approval_edit_stale_hash_and_reenable(setup_book):
    book, portal, called, resets = setup_book
    saved = book.save("demo", text())
    assert saved.status == "needs_review"
    assert book.approve("demo", saved.hash).status == "active"
    assert book.disable("demo").approved
    assert book.enable("demo").status == "active"
    assert resets == ["demo"]
    edited = book.save("demo", text() + "changed")
    assert edited.status == "needs_review"
    with pytest.raises(NeedsReview):
        book.enable("demo")
    with pytest.raises(StaleApproval):
        book.approve("demo", saved.hash)
    assert book.approve("demo", edited.hash).approved
    assert resets == ["demo", "demo"]


def test_limit_and_invalid_saved(setup_book):
    book, *_ = setup_book
    for id in ("a", "b", "c"):
        entry = book.save(id, text(id=id, count=4))
        book.approve(id, entry.hash)
    entry = book.save("d", text(id="d"))
    with pytest.raises(SignalLimit):
        book.approve("d", entry.hash)
    assert len(book.active_signals()) == 12
    assert book.save("bad", "bad").status == "invalid"
    with pytest.raises(ValueError):
        book.save("../oops", "bad")


async def test_react_claim_cooldown_restart_and_pause(setup_book, tmp_path):
    book, portal, called, resets = setup_book
    saved = book.save("demo", text(cooldown="    cooldown: 2m\n"))
    book.approve("demo", saved.hash)
    at = datetime.now(timezone.utc) + timedelta(seconds=1)

    def tick(offset):
        when = at + timedelta(seconds=offset)
        reading = SignalReading(
            SignalId("demo", "focused"), True, when, f"tick{offset}"
        )
        portal.history.append(reading)
        return Tick(when, 0, when, (), None, (reading,))

    first = tick(0)
    await book.react(first)
    await book.react(first)
    await book.react(tick(30))
    assert len(called) == 1
    await book.react(tick(120))
    assert len(called) == 2
    portal.capture = "paused"
    await book.react(tick(300))
    assert len(called) == 2
    with sqlite3.connect(tmp_path / "journal.db") as db:
        assert db.execute("SELECT COUNT(*) FROM firings").fetchone()[0] == 2
    assert called[0][1]["body"] == "Focused for 0 sec"


async def test_old_tick_rejected_after_approval(setup_book):
    book, portal, called, _ = setup_book
    old = row(0)
    portal.history.append(old)
    saved = book.save("demo", text())
    book.approve("demo", saved.hash)
    await book.react(Tick(old.at, 0, old.at, (), None, (old,)))
    assert not called


async def test_failing_hook_cannot_escape_permission_and_next_step_runs(tmp_path):
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    skill = text(extra_steps="      - hook: attack\n").replace("demo", "music")
    skill = skill.replace(
        "      - notify.send: {title: Rest, body: 'Focused for $focused.for'}\n      - hook: attack",
        "      - hook: attack\n      - notify.send: {title: Rest, body: 'Focused for $focused.for'}",
    )
    (builtin / "music.skill.md").write_text(skill)
    (builtin / "music.hook.py").write_text(
        "async def attack(ctx):\n    await ctx.actions.run('desktop.launch', app='evil')\n"
    )
    portal = MemoryPortal()
    from daybook.journal import Outcome

    called = []

    async def notify(skill, args):
        called.append(args)
        return Outcome("notify.send", True)

    book = SkillBook(
        tmp_path,
        builtin,
        tmp_path / "db",
        portal,
        build_catalog(MOODS, {"notify.send": notify}),
        Bus(),
        clear_readings=lambda skill: None,
    )
    assert book.entries()[0].status == "active"
    at = datetime.now(timezone.utc) + timedelta(seconds=1)
    reading = SignalReading(SignalId("music", "focused"), True, at, "one")
    portal.history = [reading]
    await book.react(Tick(at, 0, at, (), None, (reading,)))
    assert called
    with sqlite3.connect(tmp_path / "db") as db:
        outcomes = db.execute("SELECT outcomes FROM firings").fetchone()[0]
        assert "step_failed" in outcomes


def test_user_hook_not_loaded_even_spoofed_provenance(setup_book, tmp_path):
    book, *_ = setup_book
    (tmp_path / "skills/demo.hook.py").write_text(
        "raise RuntimeError('must never load')"
    )
    entry = book.save(
        "demo",
        text(extra_steps="      - hook: attack\n").replace(
            "name: Demo", "name: Demo\ncreated_by: builtin"
        ),
    )
    assert entry.status == "invalid"
    assert any("hooks" in error.message for error in entry.errors)


def test_restoring_old_bytes_requires_review_and_cannot_exceed_cap(setup_book):
    book, *_ = setup_book
    saved = book.save("demo", text(count=4))
    book.approve("demo", saved.hash)
    book.save("demo", text(count=4) + "edited")
    for id in ("a", "b", "c"):
        other = book.save(id, text(id=id, count=4))
        book.approve(id, other.hash)
    restored = book.save("demo", saved.text)
    assert restored.status == "needs_review"
    assert len(book.active_signals()) == 12
    with pytest.raises(SignalLimit):
        book.approve("demo", restored.hash)


def test_cleanup_callback_is_required(tmp_path):
    with pytest.raises(TypeError):
        SkillBook(  # pyright: ignore[reportCallIssue]
            tmp_path,
            tmp_path / "builtin",
            tmp_path / "db",
            MemoryPortal(),
            build_catalog(MOODS),
            Bus(),
        )


async def test_exact_placeholder_in_text_argument_is_rendered_as_text(setup_book):
    book, portal, called, _ = setup_book
    saved = book.save("demo", text().replace("'Focused for $focused.for'", "$focused"))
    book.approve("demo", saved.hash)
    at = datetime.now(timezone.utc) + timedelta(seconds=1)
    reading = SignalReading(SignalId("demo", "focused"), True, at, "one")
    portal.history = [reading]
    await book.react(Tick(at, 0, at, (), None, (reading,)))
    assert called[0][1]["body"] == "True"


def test_builtin_upgrade_respects_existing_active_capacity(tmp_path):
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    file = builtin / "music.skill.md"
    file.write_text(text(id="music"))
    book = SkillBook(
        tmp_path,
        builtin,
        tmp_path / "db",
        MemoryPortal(),
        build_catalog(MOODS),
        Bus(),
        clear_readings=lambda skill: None,
    )
    assert book.entries()[0].status == "active"
    for id, count in (("a", 4), ("b", 4), ("c", 3)):
        entry = book.save(id, text(id=id, count=count))
        book.approve(id, entry.hash)
    assert len(book.active_signals()) == 12
    file.write_text(text(id="music", count=4))
    assert len(book.active_signals()) <= 12
    upgraded = next(e for e in book.entries() if e.id == "music")
    assert upgraded.approved and upgraded.status == "disabled"


def test_disappeared_user_file_cannot_restore_approval_or_bypass_limit(
    setup_book, tmp_path
):
    book, *_ = setup_book
    original = text(count=4)
    saved = book.save("demo", original)
    book.approve("demo", saved.hash)
    (tmp_path / "skills/demo.skill.md").unlink()
    assert not book.entries()
    for id in ("a", "b", "c"):
        entry = book.save(id, text(id=id, count=4))
        book.approve(id, entry.hash)
    restored = book.save("demo", original)
    assert restored.status == "needs_review"
    assert len(book.active_signals()) == 12
    with pytest.raises(SignalLimit):
        book.approve("demo", restored.hash)
