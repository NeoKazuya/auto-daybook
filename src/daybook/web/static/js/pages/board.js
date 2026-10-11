// Sticky Board: chat. Each exchange is pinned as a note. Recall answers cite timeline
// segments; a skill request pins a draft that links to its review card.
import { api, onNotesChanged } from '../api.js';
import { h, icon, btn, ribbon, panel, emptyState, clock } from '../ui.js';

const page = { route: 'board', label: 'Sticky Board', icon: 'sticky', render };
export default page;

// Kept for the session so switching tabs doesn't lose the board.
const notes = [];
let notesRevision = 0;
let redraw = () => {};
onNotesChanged(() => {
  notesRevision++;
  // Pending responses may still be skill drafts; fence their answers on arrival.
  for (let i = notes.length - 1; i >= 0; i--) {
    delete notes[i].titles;
    if (!notes[i].pending && notes[i].reply?.type !== 'draft') notes.splice(i, 1);
  }
  redraw();
});

const EXAMPLES = [
  'what did I do in the last hour?',
  'remind me to stand up every time I finish a meeting',
  'how long was I in meetings today?',
];

async function render(root, params) {
  const board = h('div.board');
  const input = h('input.input', {
    placeholder: 'Ask about your day, or describe a skill you want…',
    'aria-label': 'Message', autocomplete: 'off', value: params.get('q') ?? '',
  });
  const send = btn('Send', { icon: 'send', kind: 'primary' });
  send.type = 'submit';

  const composer = h('div.composer',
    h('form', { onsubmit: (e) => { e.preventDefault(); ask(input.value); } }, input, send),
    h('div.hints',
      h('span.label', 'Prefixes'),
      h('button', { type: 'button', onclick: () => prefix('/ask ') }, h('span.tag.quiet', '/ask  recall from notes')),
      h('button', { type: 'button', onclick: () => prefix('/skill ') }, h('span.tag.quiet', '/skill  draft a skill'))));

  root.append(
    ribbon(page, [h('button.pill', { type: 'button', onclick: () => { notes.length = 0; draw(); } }, icon('x'), 'Clear board')]),
    panel('Ask your Daybook', 'sticky', null, composer),
    board);

  function prefix(p) {
    input.value = p + input.value.replace(/^\/(ask|skill)\s*/, '');
    input.focus();
  }

  function draw() {
    if (!notes.length) {
      board.replaceChildren(h('div', { style: 'grid-column:1/-1' }, emptyState('sticky', 'Nothing pinned yet',
        'Ask about what you did, or describe a reminder in plain words. Answers come only from your notes.',
        h('div.hints', { style: 'justify-content:center' },
          EXAMPLES.map((x) => h('button', { type: 'button', onclick: () => ask(x) }, h('span.tag', x)))))));
      return;
    }
    board.replaceChildren(...notes.slice().reverse().map(sticky));
  }

  function sticky(n) {
    const when = clock(n.at.toISOString());
    if (n.pending) {
      return h('article.sticky.thinking', h('div.q', `› ${n.message}`), h('div.a', 'Reading your notes'),
        h('div.sticky-foot', h('span.folio', when), h('span.tag.quiet', 'model busy')));
    }
    if (n.error) {
      return h('article.sticky', h('div.q', `› ${n.message}`),
        h('div.a', { style: 'color:var(--danger)' }, n.error),
        h('div.sticky-foot', h('span.folio', when), btn('Try again', { onclick: () => { notes.splice(notes.indexOf(n), 1); ask(n.message); } })));
    }
    const r = n.reply;
    if (r.type === 'draft') {
      return h('article.sticky.draft',
        h('div.q', `› ${n.message}`),
        h('div', h('span.tag.ember', 'Skill draft · needs review')),
        h('div.a', r.summary),
        h('p.muted', { style: 'margin:0;font-size:0.98rem' }, 'Nothing runs until you approve this exact version.'),
        h('div.sticky-foot', h('span.folio', when),
          h('a.btn.ember', { href: `#/skills?id=${encodeURIComponent(r.entry.id)}` }, icon('quill'), 'Review & enable')));
    }
    return h('article.sticky',
      h('div.q', `› ${n.message}`),
      h('div.a', r.text),
      r.cites?.length ? h('div.cites', r.cites.map((id) =>
        h('a.tag.ember', { href: `#/daily?${new URLSearchParams({ seg: id, from: r.window.from, to: r.window.to })}`, title: 'Open in the Daily Book' }, icon('link'), n.titles?.[id] ?? 'Open segment'))) : null,
      h('div.sticky-foot', h('span.folio', when), r.window && h('span.tag.quiet', `${clock(r.window.from)}–${clock(r.window.to)}`)));
  }

  async function ask(message) {
    message = message.trim();
    if (!message) return;
    input.value = '';
    const n = { message, at: new Date(), pending: true };
    const revision = notesRevision;
    notes.push(n);
    draw();
    send.disabled = true;
    try {
      n.reply = await api.chat(message);
      if (n.reply.type !== 'draft' && revision !== notesRevision) return;
      // Answers cite segment IDs; look up their titles for the links.
      if (n.reply.type === 'answer' && n.reply.cites.length) {
        const tl = await api.timeline(n.reply.window.from, n.reply.window.to).catch(() => null);
        if (revision !== notesRevision) return;
        n.titles = Object.fromEntries((tl?.segments ?? []).map((seg) => [seg.id, seg.title]));
      }
    } catch (err) {
      n.error = `${err.message} Your message wasn't lost — try again.`;
    } finally {
      if (revision !== notesRevision && n.reply?.type !== 'draft') {
        const index = notes.indexOf(n);
        if (index !== -1) notes.splice(index, 1);
        delete n.titles;
      }
      n.pending = false;
      send.disabled = false;
      redraw();
    }
  }

  redraw = draw;
  draw();
  input.focus();
  return { cleanup: () => { if (redraw === draw) redraw = () => {}; } };
}
