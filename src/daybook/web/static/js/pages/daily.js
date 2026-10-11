// Daily Book: the timeline. Segment cards expand to the observations they were built
// from; capture gaps are shown honestly; everything is editable and deletable inline.
import { api } from '../api.js';
import { h, icon, btn, busy, ribbon, panel, emptyState, errorState, skeletons, confirmDialog, toast, clock, duration, dayStamp, folio } from '../ui.js';

const page = { route: 'daily', label: 'Daily Book', icon: 'calendar', render };
export default page;

const GAP = {
  paused: ['pause', 'Paused', 'Capture was paused. Nothing was recorded.'],
  locked: ['lock', 'Locked', 'The screen was locked.'],
  away: ['moon', 'Away', 'No keyboard or mouse input, so no notes.'],
  withheld: ['eyeoff', 'Private', 'An excluded window was on screen, so capture was withheld.'],
  overlay: ['bolt', 'Overlay', 'A Daybook overlay covered the screen.'],
};

async function render(root, params) {
  const expanded = new Set(params.get('seg') ? [params.get('seg')] : []);
  // A recall citation carries the answer's time window, which may not be today.
  const from = params.get('from');
  const to = params.get('to');
  const day = from ? new Date(from) : new Date();
  const isToday = day.toDateString() === new Date().toDateString();
  const editing = new Set();
  const list = h('div', skeletons(4));
  const count = h('span.counter', '…');

  root.append(
    ribbon(page, [
      h('span.pill', 'LEAF', h('b', dayStamp(day))),
      h('button.pill', { type: 'button', onclick: (e) => busy(e.currentTarget, async () => {
        const r = await api.tick();
        toast(r?.recorded ? 'Tick recorded. New notes appear in a few seconds.' : 'Nothing new to note right now. Daybook may be paused, have ticked moments ago, or have no new screen notes.');
      }) },
        icon('bolt'), 'Tick now'),
    ]),
    panel(isToday ? "Today's entries" : `Entries for ${dayStamp(day)}`, 'calendar', count, list));

  let data = null;
  let latestRefresh = null;

  async function refresh() {
    const request = {};
    latestRefresh = request;
    try {
      const next = await api.timeline(from, to);
      if (latestRefresh !== request) return;
      data = next;
      if (!editing.size) draw();
    } catch (err) {
      if (latestRefresh !== request) return;
      if (editing.size) toast("Couldn't refresh timeline: " + err.message, 'error');
      else list.replaceChildren(errorState(err, refresh));
    }
  }

  function draw() {
    count.textContent = `${data.segments.length} segments`;
    const gaps = data.capture_spans.filter((g) => GAP[g.state]);
    if (!data.segments.length && !gaps.length) {
      list.replaceChildren(emptyState('calendar', 'No notes yet',
        'Work as usual. Daybook writes a note a few seconds after the screen changes, and groups them into segments here.'));
      return;
    }
    const items = [
      ...data.segments.map((s, i) => ({ at: s.started_at, el: () => segmentEntry(s, i + 1) })),
      ...gaps.map((g) => ({ at: g.start, el: () => gapEntry(g) })),
    ].sort((a, b) => new Date(a.at) - new Date(b.at));
    list.replaceChildren(h('div.leaf', items.map((x) => x.el())));
  }

  function gapEntry(g) {
    const [ic, name, text] = GAP[g.state];
    return h('div.entry.gap',
      h('span.stamp', clock(g.start)),
      h('div.gap-entry', icon(ic), h('b', name), `${clock(g.start)}–${g.end ? clock(g.end) : 'now'}`, h('span', '· ' + text)));
  }

  function segmentEntry(s, n) {
    const isOpen = expanded.has(s.id);
    const chev = icon('chevron');
    chev.classList.add('chev');
    const card = h(`div.row.seg${s.open ? '.open' : ''}${isOpen ? '.expanded' : ''}`, { id: `seg-${s.id}` },
      h('div.seg-head', { onclick: () => toggle(s.id), role: 'button', tabindex: 0, 'aria-expanded': String(isOpen),
                          onkeydown: (e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), toggle(s.id)) },
        h('div.row-text',
          h('div', { style: 'display:flex;align-items:center;gap:0.5rem' }, chev, h('span.row-title', s.title)),
          h('div.seg-intent', h('span.inferred', 'inferred'), s.intent),
          h('div.seg-meta',
            h('span.tag', s.kind),
            s.authored === 'user' && h('span.tag.quiet', 'edited by you'),
            s.open && h('span.tag.ember', 'writing now'))),
        h('div.seg-time',
          h('span.seg-active', duration(s.active)),
          h('span.seg-range', `${clock(s.started_at)}–${s.open || !s.ended_at ? 'now' : clock(s.ended_at)}`),
          h('span.folio', folio(n)))),
      editing.has(s.id) ? segmentForm(s) : null,
      isOpen ? h('div.seg-body',
        s.observations.map(observation),
        h('div.seg-actions',
          btn('Edit segment', { icon: 'edit', onclick: () => beginEdit(s.id) }),
          btn('Delete segment', { icon: 'trash', kind: 'danger', onclick: () => removeSegment(s) }))) : null);
    return h(`div.entry${s.open ? '.open' : ''}`, h('span.stamp', clock(s.started_at)), card);
  }

  function observation(o) {
    const editingThis = editing.has(o.id);
    return h('div.obs',
      h('span.obs-time', clock(o.started_at)),
      h('div',
        h('div.obs-window', `${o.window.app} · ${o.window.title}`),
        editingThis
          ? observationForm(o)
          : [h('div.obs-seen', o.seen), o.visible_text && h('div.obs-text', o.visible_text)]),
      editingThis ? h('span') : h('div.obs-actions',
        btn('', { icon: 'edit', kind: 'ghost', title: 'Edit this note', onclick: () => beginEdit(o.id) }),
        btn('', { icon: 'trash', kind: 'ghost', title: 'Delete this note', onclick: () => removeObservation(o) })));
  }

  function canLeaveEditor() {
    if (!editing.size) return true;
    toast('Save or cancel your current edit first.', 'error');
    return false;
  }

  function beginEdit(id) {
    if (!canLeaveEditor()) return;
    editing.add(id);
    draw();
  }

  async function cancelEdit(id) {
    editing.delete(id);
    list.replaceChildren(skeletons(4));
    await refresh();
  }

  async function saveEdit(id, controls, change, message) {
    for (const control of controls) control.disabled = true;
    try {
      const saved = await change();
      // The confirmed PATCH is our fallback if the following GET is unavailable.
      const current = data.segments.flatMap(s => [s, ...s.observations]).find(item => item.id === id);
      if (current) Object.assign(current, saved);
      if (message) toast(message);
      await refresh();
      editing.delete(id);
      draw();
    } finally {
      for (const control of controls) control.disabled = false;
    }
  }

  function segmentForm(s) {
    const title = h('input.input', { value: s.title, 'aria-label': 'Title' });
    const intent = h('input.input', { value: s.intent, 'aria-label': 'Intent' });
    const cancel = btn('Cancel', { kind: 'ghost', onclick: () => cancelEdit(s.id) });
    const save = btn('Save', { kind: 'primary', onclick: () => busy(save, () =>
      saveEdit(s.id, [title, intent, cancel],
        () => api.editSegment(s.id, { title: title.value.trim(), intent: intent.value.trim() }),
        'Saved. Daybook won’t overwrite your wording.')) });
    return h('div.edit-form',
      h('label.label', 'Title'), title,
      h('label.label', 'Intent'), intent,
      h('div.row-btns', cancel, save));
  }

  function observationForm(o) {
    const seen = h('textarea.textarea', { style: 'min-height:5rem;font-family:var(--serif);font-size:1rem', 'aria-label': 'What was on screen' }, o.seen);
    const text = h('textarea.textarea', { style: 'min-height:4rem', maxlength: 300, 'aria-label': 'Captured on-screen text' }, o.visible_text);
    const cancel = btn('Cancel', { kind: 'ghost', onclick: () => cancelEdit(o.id) });
    const save = btn('Save', { kind: 'primary', onclick: () => busy(save, () =>
      saveEdit(o.id, [seen, text, cancel],
        () => api.editObservation(o.id, { seen: seen.value.trim(), visible_text: text.value.trim() }))) });
    return h('div', { style: 'display:flex;flex-direction:column;gap:0.5rem;margin-top:0.4rem' },
      h('label.label', 'What was on screen'), seen,
      h('label.label', 'Captured text (up to 300 characters)'), text,
      h('div.row-btns', { style: 'display:flex;gap:0.5rem;justify-content:flex-end' },
        cancel, save));
  }

  async function removeSegment(s) {
    if (!canLeaveEditor()) return;
    const ok = await confirmDialog({
      title: 'Delete segment and its source notes',
      body: `“${s.title}” and its ${s.observations.length} notes will be removed from the timeline and from search. This can't be undone.`,
      confirm: 'Delete segment', danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteSegment(s.id);
      toast('Segment and its notes deleted.');
    } catch (err) {
      toast(`Nothing was deleted: ${err.message}`, 'error');
    }
    await refresh();
  }

  async function removeObservation(o) {
    if (!canLeaveEditor()) return;
    const ok = await confirmDialog({
      title: 'Delete this note',
      body: 'The note is removed from the timeline and from search. If it was part of a summary Daybook wrote, that summary is cleared too.',
      confirm: 'Delete note', danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteObservation(o.id);
      toast('Note deleted.');
    } catch (err) {
      toast(`Nothing was deleted: ${err.message}`, 'error');
    }
    await refresh();
  }

  function toggle(id) {
    if (!canLeaveEditor()) return;
    expanded.has(id) ? expanded.delete(id) : expanded.add(id);
    draw();
  }

  await refresh();

  // A citation from the Sticky Board or Table of Contents lands here: scroll and highlight.
  const target = params.get('seg') && document.getElementById(`seg-${params.get('seg')}`);
  if (target) {
    target.scrollIntoView({ block: 'center' });
    target.classList.add('flash');
  } else if (params.get('seg')) {
    toast('That segment is no longer in the timeline. It may have been deleted.');
  }

  return { onEvent: () => { if (!editing.size) refresh(); } };
}
