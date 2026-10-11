// Auto-Daybook: the machine room. Privacy controls (pause, exclusions, forgetting),
// proof that no screenshots are stored, and the model's live status (ARCHITECTURE §7, §8).
import { api } from '../api.js';
import { h, icon, btn, busy, ribbon, panel, errorState, skeletons, confirmDialog, toast } from '../ui.js';

const page = { route: 'settings', label: 'Auto-Daybook', icon: 'archive', render };
export default page;

async function render(root) {
  const left = h('div', { style: 'display:flex;flex-direction:column;gap:1.5rem' }, skeletons(3));
  const right = h('div', { style: 'display:flex;flex-direction:column;gap:1.5rem' }, skeletons(2));
  root.append(
    ribbon(page, [h('span.pill', icon('shield'), 'LOCAL ONLY ·', h('b', '127.0.0.1'))]),
    h('div.grid-2', left, right));

  let privacy, status;

  async function refresh() {
    try {
      [privacy, status] = await Promise.all([api.privacy(), api.status()]);
      draw();
    } catch (err) {
      left.replaceChildren(errorState(err, refresh));
      right.replaceChildren();
    }
  }

  async function save(patch, message) {
    privacy = await api.setPrivacy({ ...privacy, ...patch });
    if (message) toast(message);
    draw();
  }

  function draw() {
    left.replaceChildren(capturePanel(), exclusionsPanel(), forgetPanel());
    right.replaceChildren(screenshotsPanel(), modelPanel());
  }

  function capturePanel() {
    const sw = h('input', { type: 'checkbox', checked: !privacy.paused, 'aria-label': 'Capture on' });
    sw.addEventListener('change', async () => {
      sw.disabled = true;
      try {
        await save({ paused: !sw.checked }, sw.checked ? 'Capture resumed.' : 'Capture paused. Nothing is recorded until you resume.');
      } catch (err) {
        toast(err.message, 'error');
        sw.checked = !sw.checked;
      } finally {
        sw.disabled = false;
      }
    });
    return panel('Capture', 'eyeoff', h(`span.tag${privacy.paused ? '.quiet' : '.ember'}`, privacy.paused ? 'Paused' : 'Recording'),
      h('div', { style: 'display:flex;align-items:center;justify-content:space-between;gap:1rem' },
        h('div',
          h('div.row-title', privacy.paused ? 'Daybook is paused' : 'Daybook is taking notes'),
          h('p.muted', { style: 'margin:0.25rem 0 0' }, privacy.paused
            ? 'No screen is read and no notes are written. Music keeps playing.'
            : 'It reads the screen every few seconds and keeps only short text notes.')),
        h('label.switch', sw, h('span'))));
  }

  function exclusionsPanel() {
    const match = h('select', { 'aria-label': 'Match on' }, h('option', { value: 'app' }, 'App'), h('option', { value: 'title' }, 'Title has'));
    const value = h('input.input', { placeholder: 'e.g. KeePassXC or “Private Browsing”', 'aria-label': 'Exclusion' });
    const add = btn('Add', { icon: 'plus', kind: 'primary' });
    add.type = 'submit';
    const form = h('form.excl-form', { onsubmit: (e) => {
      e.preventDefault();
      const v = value.value.trim();
      if (!v) return;
      busy(add, () => save({ exclusions: [...privacy.exclusions, { match: match.value, value: v }] }, `Excluded ${v}.`));
    } }, match, value, add);

    return panel('Never capture', 'eyeoff', h('span.counter', `${privacy.exclusions.length} rules`),
      h('p.muted', { style: 'margin:0' }, 'While any of these is visible, capture is withheld. Their titles are never stored or logged.'),
      h('div.rows', privacy.exclusions.map((x, i) =>
        h('div.row', { style: 'padding:0.6rem 1rem' },
          h('div.row-main', h('span.tag.quiet', x.match === 'app' ? 'app' : 'title has'), h('span', { style: 'font-size:1.05rem' }, x.value)),
          btn('', { icon: 'x', kind: 'ghost', title: `Stop excluding ${x.value}`, onclick: (e) => busy(e.currentTarget, () =>
            save({ exclusions: privacy.exclusions.filter((_, j) => j !== i) }, `${x.value} is no longer excluded.`)) })))),
      form);
  }

  function forgetPanel() {
    const forget15 = btn('Forget last 15 minutes', { icon: 'rewind', onclick: async (e) => {
      const b = e.currentTarget;
      const ok = await confirmDialog({ title: 'Forget the last 15 minutes?', body: 'Notes, segments and readings from the last 15 minutes are deleted from the timeline, search and the database files.', confirm: 'Forget 15 minutes', danger: true });
      if (!ok) return;
      const to = new Date();
      await busy(b, () => api.forget(new Date(to - 15 * 60_000).toISOString(), to.toISOString()));
      toast('The last 15 minutes are forgotten.');
      refresh();
    } });
    const forgetAll = btn('Forget everything', { icon: 'trash', kind: 'danger', onclick: async (e) => {
      const b = e.currentTarget;
      const ok = await confirmDialog({ title: 'Forget everything?', body: 'Every note, segment and reading is deleted. Skills and settings stay. This can’t be undone.', confirm: 'Forget everything', danger: true });
      if (!ok) return;
      await busy(b, () => api.forgetAll());
      toast('Everything is forgotten.');
      refresh();
    } });
    return panel('Forget', 'trash', status.deletion_pending ? h('span.tag.ember', 'Deletion pending') : null,
      h('p.muted', { style: 'margin:0' }, status.deletion_pending
        ? 'Deletion is waiting for a reader to finish, then the old pages are wiped from the database files.'
        : 'Deleting removes the text from the timeline, from search, and from the database files.'),
      h('div', { style: 'display:flex;gap:0.6rem;flex-wrap:wrap' }, forget15, forgetAll));
  }

  function screenshotsPanel() {
    const stored = privacy.screenshots_stored ?? 0;
    return panel('Screenshots', 'shield', null,
      h('div.big-ok', icon('shield'), `Screenshots stored: ${stored === 0 ? 'none' : stored}`),
      h('p.muted', { style: 'margin:0' }, 'Frames exist only in memory while the model reads them. Only text notes are kept.'),
      privacy.keep_frames && h('div.notice', 'Debug setting keep_frames is ON: frames are being written to disk.'));
  }

  function modelPanel() {
    const m = status.model;
    const tick = btn('Tick now', { icon: 'bolt', onclick: (e) => busy(e.currentTarget, async () => {
      await api.tick();
      toast('Tick requested. New notes and skill reactions follow in a few seconds.');
    }) });
    return panel('Model', 'cpu', h(`span.tag${m.reachable ? '' : '.danger'}`, m.reachable ? 'Reachable' : 'Offline'),
      h('dl.kv',
        h('dt', 'Model'), h('dd.mono', m.name),
        h('dt', 'Latency'), h('dd', m.latency_ms ? `${(m.latency_ms / 1000).toFixed(1)} s` : '—'),
        h('dt', 'Calls/min'), h('dd', `${m.calls_per_min}`),
        h('dt', 'Queue'), h('dd', m.queue ? `${m.queue} waiting` : 'empty'),
        h('dt', 'Music'), h('dd', status.now_playing ? `${status.now_playing.mood} · ${status.now_playing.title}` : 'stopped')),
      !m.reachable && h('div.notice', 'The model is unreachable. Capture shows gaps until it’s back; nothing is retried in a storm.'),
      h('div', h('div.label', { style: 'margin-bottom:0.5rem' }, 'Demo helper'), tick));
  }

  await refresh();
  return { onEvent: (e) => { if (e.type === 'CaptureStateChanged' || e.type === 'Forgotten') refresh(); } };
}
