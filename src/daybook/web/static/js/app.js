// One page with tabs (ARCHITECTURE §8). Each tab is a hash route; the document title
// is always `Daybook · <tab>`, which capture excludes.
import { api, subscribe, DEMO } from './api.js';
import { h, icon, ribbon } from './ui.js';
import { hydrateIcons } from './icons.js';
import { applyPrefs, getPref } from './prefs.js';
import contents from './pages/contents.js';
import daily from './pages/daily.js';
import board from './pages/board.js';
import skills from './pages/skills.js';
import tags from './pages/tags.js';
import settings from './pages/settings.js';

const PAGES = [contents, daily, board, skills, tags, settings];
const byRoute = new Map(PAGES.map((p) => [p.route, p]));

const root = document.getElementById('page');
let current = null;   // { page, hooks: { onEvent?, cleanup? } }
let status = null;

function parseHash() {
  const [path, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  return { route: path || getPref('startPage') || 'contents', params: new URLSearchParams(query) };
}

function renderNav(active) {
  const nav = document.getElementById('nav');
  nav.replaceChildren(...PAGES.map((p) =>
    h('a', { href: `#/${p.route}`, 'aria-current': p === active ? 'page' : null },
      icon(p.icon), p.label,
      p.route === 'skills' && status?.needs_review ? h('span.badge', { title: 'Skills waiting for review' }, status.needs_review) : null)));
}

async function show() {
  const { route, params } = parseHash();
  const page = byRoute.get(route) ?? contents;
  current?.hooks.cleanup?.();
  document.title = `Daybook · ${page.label}`;
  renderNav(page);
  root.replaceChildren();
  window.scrollTo({ top: 0 });
  // Tabs can change while a page is still loading; only the latest navigation keeps
  // its hooks, and an older one that finishes late is cleaned up instead.
  const nav = { page, hooks: {} };
  current = nav;
  const hooks = (await page.render(root, params)) ?? {};
  if (current === nav) nav.hooks = hooks;
  else hooks.cleanup?.();
}

/* ---------- footer: capture, model, calls/min ---------- */

const CAPTURE_LABEL = {
  running: 'Recording', paused: 'Paused', locked: 'Screen locked', away: 'Away',
  withheld: 'Private window', overlay: 'Overlay up',
};

let latestStatusRefresh = null;
async function refreshStatus() {
  const request = {};
  latestStatusRefresh = request;
  const footer = document.getElementById('footer');
  try {
    const [s, sk, privacy] = await Promise.all([api.status(), api.skills(), api.privacy()]);
    if (latestStatusRefresh !== request) return;
    status = { ...s, needs_review: sk.entries.filter((x) => x.status === 'needs_review').length };
    ribbon.captureState = s.capture;
    document.querySelectorAll('.state-pip').forEach((el) => { el.dataset.state = s.capture; });
    const g = s.gateway;
    footer.replaceChildren(
      h('span.item', h(`span.dot${s.capture === 'running' ? '.live' : ''}`), CAPTURE_LABEL[s.capture] ?? s.capture),
      h('span.sep'),
      h('span.item', icon('cpu'), g.model, h(`span.dot${g.reachable ? '.ok' : '.bad'}`), g.reachable ? 'reachable' : 'offline'),
      h('span.sep'),
      h('span.item', icon('bolt'), `${g.calls_per_minute} calls/min`),
      ...(privacy.deletion_pending ? [h('span.sep'), h('span.item', icon('trash'), 'Deletion pending')] : []),
    );
    sourceFlag.hidden = !(DEMO || s.mock);
    sourceFlag.textContent = DEMO ? 'DEMO DATA' : 'MOCK API';
    if (current) renderNav(current.page);
  } catch (err) {
    if (latestStatusRefresh !== request) return;
    footer.replaceChildren(h('span.item', h('span.dot.bad'), `Status unavailable — ${err.message}`));
  }
}

/* ---------- boot ---------- */

applyPrefs();
hydrateIcons();
const sourceFlag = h('span.demo-flag', { hidden: true, title: 'Showing example data, not your notes' });
document.getElementById('sidebar-foot').append(h('div', 'LOCAL ONLY · 127.0.0.1'), sourceFlag);

window.addEventListener('hashchange', show);
await refreshStatus();
await show();
setInterval(refreshStatus, 5000);

// Live updates: events are batched for 250 ms, then the page gets the set of event
// types and payloads that arrived and re-reads what it shows. The footer follows status changes.
const STATUS_EVENTS = new Set(['CaptureStateChanged', 'SkillsChanged', 'StatusChanged', 'MusicChanged', 'Reconnected']);
let pending = null;
let batch = [];
subscribe((event) => {
  if (STATUS_EVENTS.has(event.type)) refreshStatus();
  batch.push(event);
  clearTimeout(pending);
  pending = setTimeout(() => {
    const events = batch;
    batch = [];
    current?.hooks.onEvent?.(new Set(events.map((e) => e.type)), events);
  }, 250);
});
