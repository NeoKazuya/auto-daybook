// Table of Contents: today at a glance. Each segment is a chapter line that opens
// in the Daily Book.
import { api } from '../api.js';
import { h, ribbon, panel, stat, emptyState, errorState, skeletons, clock, duration, longDay, dayStamp } from '../ui.js';

const page = { route: 'contents', label: 'Table of Contents', icon: 'contents', render };
export default page;

const GAP_LABEL = { paused: 'Paused', locked: 'Screen locked', away: 'Away', withheld: 'Private window', overlay: 'Overlay on screen' };

async function render(root) {
  const body = h('div', { style: 'display:contents' }, skeletons(4));
  root.append(
    ribbon(page, [h('span.pill', 'TODAY', h('b', dayStamp()))]),
    h('div',
      h('div.label', 'Daybook for'),
      h('h1', { style: 'margin:0.2rem 0 0;font-size:2.6rem;font-weight:500;letter-spacing:-0.02em' }, longDay())),
    body);

  async function refresh() {
    try {
      const [tl, kinds, sk, status] = await Promise.all([api.timeline(), api.totals('kind'), api.skills(), api.status()]);
      body.replaceChildren(...view(tl, kinds, sk, status));
    } catch (err) {
      body.replaceChildren(errorState(err, refresh));
    }
  }
  await refresh();
  return { onEvent: refresh };
}

function view(tl, kinds, skills, status) {
  const active = kinds.reduce((t, k) => t + k.active, 0);
  const activeSkills = skills.filter((s) => s.status === 'active').length;
  const review = skills.filter((s) => s.status === 'needs_review').length;

  const stats = h('div.stats',
    stat('clock', 'Active today', duration(active)),
    stat('layers', 'Chapters', `${tl.segments.length}`),
    stat('quill', 'Skills', review ? `${activeSkills} active · ${review} to review` : `${activeSkills} active`),
    stat('cpu', 'Model', status.model.reachable ? `${status.model.calls_per_min} calls/min` : 'Offline'));

  if (!tl.segments.length) {
    return [stats, emptyState('contents', 'No notes yet',
      status.capture === 'running'
        ? 'Daybook is watching. The first chapter appears a few seconds after you start working.'
        : 'Capture is paused. Resume it from Auto-Daybook or the tray to start taking notes.')];
  }

  // Interleave segments and capture gaps by start time.
  const lines = [
    ...tl.segments.map((s) => ({ at: s.started_at, seg: s })),
    ...tl.spans.filter((g) => g.state !== 'running').map((g) => ({ at: g.start, gap: g })),
  ].sort((a, b) => new Date(a.at) - new Date(b.at));

  const toc = h('div.toc', lines.map(({ seg, gap }) => gap
    ? h('div.toc-line.gap',
        h('span.toc-time', clock(gap.start)),
        h('span.toc-title', `${GAP_LABEL[gap.state] ?? gap.state} — no notes`),
        h('span.toc-leader'),
        h('span.toc-dur', duration((new Date(gap.end) - new Date(gap.start)) / 1000)))
    : h(`a.toc-line${seg.open ? '.open' : ''}`, { href: `#/daily?seg=${seg.id}` },
        h('span.toc-time', clock(seg.started_at)),
        h('span.toc-title', seg.title),
        h('span.toc-leader'),
        h('span.toc-dur', duration(seg.active)))));

  const max = Math.max(...kinds.map((k) => k.active), 1);
  const bars = h('div.bars', kinds.map((k) =>
    h('div.bar-row',
      h('span.bar-key', k.key),
      h('div.bar-track', h('div.bar-fill', { style: `width:${(k.active / max) * 100}%` })),
      h('span.bar-val', duration(k.active)))));

  return [
    h('div.grid-2',
      panel('Chapters of the day', 'contents', h('span.counter', `${tl.segments.length} entries`), toc),
      h('div', { style: 'display:flex;flex-direction:column;gap:1.5rem' },
        panel('Time by kind', 'layers', null, bars),
        panel('Now writing', 'quill', null, nowCard(tl.segments.find((s) => s.open), status)))),
    stats,
  ];
}

function nowCard(open, status) {
  if (status.capture !== 'running') {
    return h('p.muted', { style: 'margin:0' }, 'Capture is paused. Nothing is being written.');
  }
  if (!open) return h('p.muted', { style: 'margin:0' }, 'Between chapters. A new one starts on the next change.');
  return h('a', { href: `#/daily?seg=${open.id}`, style: 'display:flex;flex-direction:column;gap:0.35rem' },
    h('div.row-title', open.title),
    h('div.seg-intent', h('span.inferred', 'inferred'), open.intent),
    h('div', { style: 'display:flex;gap:0.5rem;margin-top:0.35rem' },
      h('span.tag', open.kind), h('span.tag.quiet', `${duration(open.active)} so far`)));
}
