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
  current = { page, hooks: {} };
  current.hooks = (await page.render(root, params)) ?? {};
}

/* ---------- footer: capture, model, calls/min ---------- */

const CAPTURE_LABEL = {
  running: 'Recording', paused: 'Paused', locked: 'Screen locked', away: 'Away',
  withheld: 'Private window', overlay: 'Overlay up',
};

async function refreshStatus() {
  const footer = document.getElementById('footer');
  try {
    const [s, sk] = await Promise.all([api.status(), api.skills()]);
    status = { ...s, needs_review: sk.filter((x) => x.status === 'needs_review').length };
    ribbon.captureState = s.capture;
    document.querySelectorAll('.state-pip').forEach((el) => { el.dataset.state = s.capture; });
    const m = s.model;
    footer.replaceChildren(
      h('span.item', h(`span.dot${s.capture === 'running' ? '.live' : ''}`), CAPTURE_LABEL[s.capture] ?? s.capture),
      h('span.sep'),
      h('span.item', icon('cpu'), m.name, h(`span.dot${m.reachable ? '.ok' : '.bad'}`), m.reachable ? 'reachable' : 'offline'),
      h('span.sep'),
      h('span.item', icon('bolt'), `${m.calls_per_min} calls/min`),
      ...(s.deletion_pending ? [h('span.sep'), h('span.item', icon('trash'), 'Deletion pending')] : []),
    );
    if (current) renderNav(current.page);
  } catch (err) {
    footer.replaceChildren(h('span.item', h('span.dot.bad'), `Status unavailable — ${err.message}`));
  }
}

/* ---------- boot ---------- */

applyPrefs();
hydrateIcons();
if (DEMO) {
  document.getElementById('sidebar-foot').append(
    h('div', 'LOCAL ONLY · 127.0.0.1'),
    h('span.demo-flag', { title: 'No server token: showing built-in example data' }, 'DEMO DATA'));
} else {
  document.getElementById('sidebar-foot').append(h('div', 'LOCAL ONLY · 127.0.0.1'));
}

window.addEventListener('hashchange', show);
await refreshStatus();
await show();
setInterval(refreshStatus, 5000);

// Live updates: pages that care re-read their data; the footer follows capture state.
let pending = null;
subscribe((event) => {
  if (event.type === 'CaptureStateChanged' || event.type === 'SkillChanged') refreshStatus();
  clearTimeout(pending);
  pending = setTimeout(() => current?.hooks.onEvent?.(event), 250);
});
