// Settings: monitoring (on/off, hours, which apps), display options, notes (keeping
// and forgetting), proof that no screenshots are stored, and the model's status.
// Monitoring settings go through /api/privacy, which sense owns; display options are
// per-browser preferences (prefs.js).
import { api } from '../api.js';
import { getPref, setPref } from '../prefs.js';
import { h, icon, btn, busy, ribbon, panel, errorState, skeletons, confirmDialog, toast } from '../ui.js';

const page = { route: 'settings', label: 'Settings', icon: 'gear', render };
export default page;

const DAYS = [['Mon', 1], ['Tue', 2], ['Wed', 3], ['Thu', 4], ['Fri', 5], ['Sat', 6], ['Sun', 0]];
const START_PAGES = [
  ['contents', 'Table of Contents'], ['daily', 'Daily Book'], ['board', 'Sticky Board'],
  ['skills', 'Skills'], ['tags', 'Index Tags'],
];
const RETENTION = [[null, 'Until I delete them'], [7, '7 days'], [30, '30 days'], [90, '90 days']];

async function render(root) {
  const left = h('div.stack', skeletons(3));
  const right = h('div.stack', skeletons(2));
  root.append(ribbon(page), h('div.grid-2', left, right));

  let privacy, status;
  let knownApps = [];

  async function refresh() {
    try {
      [privacy, status, knownApps] = await Promise.all([
        api.privacy(),
        api.status(),
        api.totals('app').then((rows) => rows.map((r) => r.key)).catch(() => []),
      ]);
      draw();
    } catch (err) {
      left.replaceChildren(errorState(err, refresh));
      right.replaceChildren();
    }
  }

  async function save(patch, message) {
    try {
      privacy = await api.setPrivacy({ ...privacy, ...patch });
      if (message) toast(message);
    } catch (err) {
      toast(`Couldn't save: ${err.message}`, 'error');
    }
    draw();
  }

  function draw() {
    left.replaceChildren(monitoringPanel(), hoursPanel(), appsPanel());
    right.replaceChildren(displayPanel(), notesPanel(), screenshotsPanel(), modelPanel());
  }

  /* ---------- monitoring ---------- */

  function monitoringPanel() {
    const on = !privacy.paused;
    return panel('Monitoring', 'eyeoff', h(`span.tag${on ? '.ember' : '.quiet'}`, on ? 'On' : 'Off'),
      setting(on ? 'Daybook is taking notes' : 'Monitoring is off',
        on ? 'It reads the screen every few seconds and keeps only short text notes.'
           : 'No screen is read and no notes are written until you turn it back on.',
        toggle(on, 'Monitoring', (v) => save({ paused: !v }, v ? 'Monitoring is on.' : 'Monitoring is off. Nothing is recorded until you turn it back on.'))));
  }

  function hoursPanel() {
    const sch = privacy.schedule;
    const days = h('div.days', DAYS.map(([name, n]) => {
      const pressed = sch.days.includes(n);
      return h('button.day', {
        type: 'button', 'aria-pressed': String(pressed), disabled: !sch.enabled,
        onclick: () => {
          const next = pressed ? sch.days.filter((d) => d !== n) : [...sch.days, n];
          if (!next.length) { toast('Pick at least one day, or turn off set hours.', 'error'); return; }
          save({ schedule: { ...sch, days: next } });
        },
      }, name);
    }));
    const time = (key, label) => {
      const input = h('input.input.time', { type: 'time', value: sch[key], 'aria-label': label, disabled: !sch.enabled });
      input.addEventListener('change', () => {
        if (input.value) save({ schedule: { ...sch, [key]: input.value } }, 'Monitoring hours saved.');
      });
      return input;
    };

    return panel('Monitoring hours', 'clock', null,
      setting('Only monitor during set hours',
        sch.enabled ? 'Outside these hours Daybook takes no notes, as if paused.' : 'Daybook monitors whenever it is on.',
        toggle(sch.enabled, 'Only monitor during set hours', (v) => save({ schedule: { ...sch, enabled: v } },
          v ? 'Daybook now monitors only during set hours.' : 'Daybook now monitors whenever it is on.'))),
      h(`div.hours${sch.enabled ? '' : '.off'}`,
        h('div.label', 'Days'), days,
        h('div.label', 'Hours'),
        h('div.time-row', time('start', 'Start time'), h('span.muted', 'to'), time('end', 'End time')),
        sch.enabled && sch.end <= sch.start ? h('p.muted', { style: 'margin:0' }, 'The end is before the start, so this runs overnight.') : null));
  }

  function appsPanel() {
    const only = privacy.apps_mode === 'only';
    const mode = h('div.segmented', { role: 'group', 'aria-label': 'Which apps to monitor' },
      [['all', 'All apps except blocked'], ['only', 'Only allowed apps']].map(([v, label]) =>
        h('button', { type: 'button', 'aria-pressed': String(privacy.apps_mode === v),
          onclick: () => privacy.apps_mode !== v && save({ apps_mode: v },
            v === 'only' ? 'Daybook now monitors only the allowed apps.' : 'Daybook now monitors every app except blocked ones.') }, label)));

    const allowed = only ? h('div.app-list',
      h('div.label', 'Allowed apps'),
      privacy.allowed_apps.length
        ? h('div.rows', privacy.allowed_apps.map((app) => listRow([h('span.tag.quiet', 'app'), app],
            `Stop allowing ${app}`, (b) => busy(b, () => save({ allowed_apps: privacy.allowed_apps.filter((a) => a !== app) }, `${app} is no longer monitored.`)))))
        : h('p.muted', { style: 'margin:0' }, 'No apps allowed yet, so nothing is monitored. Add one below.'),
      addForm('allowed', 'App name, e.g. Code', false, (match, value) =>
        privacy.allowed_apps.includes(value) ? toast(`${value} is already allowed.`)
          : save({ allowed_apps: [...privacy.allowed_apps, value] }, `${value} will be monitored.`))) : null;

    const blocked = h('div.app-list',
      h('div.label', 'Never monitor'),
      h('p.muted', { style: 'margin:0' }, 'While any of these is on screen, capture is withheld. Their titles are never stored.'),
      h('div.rows', privacy.exclusions.map((x, i) => listRow(
        [h('span.tag.quiet', x.match === 'app' ? 'app' : 'title has'), x.value],
        `Stop blocking ${x.value}`,
        (b) => busy(b, () => save({ exclusions: privacy.exclusions.filter((_, j) => j !== i) }, `${x.value} is no longer blocked.`))))),
      addForm('blocked', 'App name, or text in a window title', true, (match, value) =>
        save({ exclusions: [...privacy.exclusions, { match, value }] }, `${value} will never be monitored.`)));

    return panel('Apps', 'layers', h('span.counter', only ? `${privacy.allowed_apps.length} allowed` : `${privacy.exclusions.length} blocked`),
      mode, allowed, blocked);
  }

  function listRow(label, removeTitle, onRemove) {
    return h('div.row.compact',
      h('div.row-main', label),
      btn('', { icon: 'x', kind: 'ghost', title: removeTitle, onclick: (e) => onRemove(e.currentTarget) }));
  }

  function addForm(id, placeholder, withMatch, onAdd) {
    const listId = `apps-${id}`;
    const match = withMatch && h('select', { 'aria-label': 'Match on' },
      h('option', { value: 'app' }, 'App'), h('option', { value: 'title' }, 'Title has'));
    const value = h('input.input', { placeholder, 'aria-label': placeholder, list: listId });
    const add = btn('Add', { icon: 'plus', kind: 'primary' });
    add.type = 'submit';
    return h('form.excl-form', { onsubmit: (e) => {
      e.preventDefault();
      const v = value.value.trim();
      if (v) busy(add, async () => onAdd(match ? match.value : 'app', v));
    } }, match, value, add, h('datalist', { id: listId }, knownApps.map((a) => h('option', { value: a }))));
  }

  /* ---------- display (this browser) ---------- */

  function displayPanel() {
    const choice = (key, options, label) => h('div.segmented', { role: 'group', 'aria-label': label },
      options.map(([v, text]) => h('button', { type: 'button', 'aria-pressed': String(getPref(key) === v),
        onclick: () => { setPref(key, v); draw(); } }, text)));

    const start = h('select.input.select', { 'aria-label': 'Start page' },
      START_PAGES.map(([v, text]) => h('option', { value: v, selected: getPref('startPage') === v }, text)));
    start.addEventListener('change', () => { setPref('startPage', start.value); toast('Start page saved.'); });

    return panel('Display', 'contents', h('span.tag.quiet', 'This browser'),
      setting('Text size', 'Scales text and controls. The layout keeps its width.',
        choice('textSize', [['small', 'Small'], ['medium', 'Medium'], ['large', 'Large']], 'Text size')),
      setting('Clock', 'How times appear in the timeline and elsewhere.',
        choice('hour12', [[false, '24-hour'], [true, '12-hour']], 'Clock format')),
      setting('Start page', 'The page Daybook opens on.', start));
  }

  function notesPanel() {
    const keep = h('select.input.select', { 'aria-label': 'Keep notes for' },
      RETENTION.map(([v, text]) => h('option', { value: String(v), selected: privacy.retention_days === v }, text)));
    keep.addEventListener('change', () => {
      const v = keep.value === 'null' ? null : Number(keep.value);
      save({ retention_days: v }, v ? `Notes older than ${v} days will be deleted.` : 'Notes are kept until you delete them.');
    });
    const forget15 = btn('Last 15 minutes', { icon: 'rewind', onclick: async (e) => {
      const b = e.currentTarget;
      const ok = await confirmDialog({ title: 'Forget the last 15 minutes?', body: 'Notes, segments and readings from the last 15 minutes are deleted from the timeline, search and the database files.', confirm: 'Forget 15 minutes', danger: true });
      if (!ok) return;
      const to = new Date();
      await busy(b, () => api.forget(new Date(to - 15 * 60_000).toISOString(), to.toISOString()));
      toast('The last 15 minutes are forgotten.');
      refresh();
    } });
    const forgetAll = btn('Everything', { icon: 'trash', kind: 'danger', onclick: async (e) => {
      const b = e.currentTarget;
      const ok = await confirmDialog({ title: 'Forget everything?', body: 'Every note, segment and reading is deleted. Skills and settings stay. This can’t be undone.', confirm: 'Forget everything', danger: true });
      if (!ok) return;
      await busy(b, () => api.forgetAll());
      toast('Everything is forgotten.');
      refresh();
    } });
    return panel('Notes', 'calendar', status.deletion_pending ? h('span.tag.ember', 'Deletion pending') : null,
      setting('Keep notes', 'Older notes are deleted for good, the same way as Forget.', keep),
      setting('Forget now', status.deletion_pending
        ? 'Deletion is waiting for a reader to finish, then the old pages are wiped from the database files.'
        : 'Removes the text from the timeline, from search, and from the database files.',
        h('div', { style: 'display:flex;gap:0.5rem;flex-wrap:wrap' }, forget15, forgetAll)));
  }

  /* ---------- privacy proof and model ---------- */

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

function setting(title, text, control) {
  return h('div.setting', h('div.setting-text', h('h3', title), text && h('p', text)), h('div.setting-control', control));
}

function toggle(checked, label, onChange) {
  const input = h('input', { type: 'checkbox', checked, 'aria-label': label });
  input.addEventListener('change', async () => {
    input.disabled = true;
    await onChange(input.checked);
  });
  return h('label.switch', input, h('span'));
}
