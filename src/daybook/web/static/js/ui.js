// Small DOM and formatting helpers shared by every page.
import { icon } from './icons.js';

/** h('div.cls', {attrs}, ...children). Strings become text nodes; null/false are skipped. */
export function h(spec, attrs, ...children) {
  const [tag, ...classes] = spec.split('.');
  const el = document.createElement(tag || 'div');
  if (classes.length) el.className = classes.join(' ');
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs ?? {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className += (el.className ? ' ' : '') + v;
    else if (k === 'html') el.innerHTML = v;
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export { icon };

export function btn(label, { icon: ic, kind = '', onclick, title, disabled } = {}) {
  return h(`button.btn${kind ? '.' + kind : ''}`, { type: 'button', onclick, title, disabled }, ic && icon(ic), label);
}

/** Run an async action with a busy button; errors become a toast. */
export async function busy(button, fn) {
  button.classList.add('busy');
  button.disabled = true;
  try {
    return await fn();
  } catch (err) {
    toast(err.message || 'Something went wrong.', 'error');
    throw err;
  } finally {
    button.classList.remove('busy');
    button.disabled = false;
  }
}

/* ---------- time ---------- */

export const clock = (iso) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

export function duration(seconds) {
  const m = Math.round(seconds / 60);
  if (m < 1) return '<1m';
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

export const dayStamp = (d = new Date()) =>
  d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }).toUpperCase();

export const longDay = (d = new Date()) =>
  d.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });

export const folio = (n) => `FOLIO #${String(n).padStart(2, '0')}`;

/* ---------- shared page pieces ---------- */

/** The top ribbon from the reference: capture pip / page name, tools on the right. */
export function ribbon(page, tools = []) {
  return h('div.ribbon',
    h('div.crumbs',
      h('span.state-pip', { 'data-state': ribbon.captureState ?? 'running', title: 'Capture state' }, h('i')),
      h('span.slash', '/'),
      h('span.here', icon(page.icon), page.label)),
    h('div.tools', tools));
}
ribbon.captureState = 'running';

export function panel(title, ic, tools, ...body) {
  return h('section.panel',
    h('div.panel-head', h('div.panel-title', icon(ic), title), h('div.panel-tools', tools)),
    ...body);
}

export function stat(ic, label, value) {
  return h('div.stat', h('div.stat-icon', icon(ic)), h('div', h('div.stat-label', label), h('div.stat-value', value)));
}

export function emptyState(ic, title, text, action) {
  return h('div.state', icon(ic), h('h3', title), text && h('p', text), action);
}

export function errorState(err, retry) {
  return h('div.state.error', icon('alert'), h('h3', "Couldn't load this"),
    h('p', `${err.message || err}. Your notes are safe; this is only the view.`),
    retry && btn('Try again', { onclick: retry }));
}

export function skeletons(n = 3) {
  return h('div.rows', Array.from({ length: n }, () => h('div.skeleton')));
}

/** Render loading → content | error into `host`, with retry. */
export async function load(host, fetcher, render) {
  host.replaceChildren(skeletons());
  try {
    const data = await fetcher();
    host.replaceChildren(render(data));
  } catch (err) {
    host.replaceChildren(errorState(err, () => load(host, fetcher, render)));
  }
}

/* ---------- dialog & toast ---------- */

export function confirmDialog({ title, body, confirm = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    const dlg = h('dialog',
      h('h3', title),
      h('p', body),
      h('div.row-btns',
        btn('Cancel', { kind: 'ghost', onclick: () => dlg.close('no') }),
        btn(confirm, { kind: danger ? 'danger' : 'primary', onclick: () => dlg.close('yes') })));
    dlg.addEventListener('close', () => { resolve(dlg.returnValue === 'yes'); dlg.remove(); });
    document.body.append(dlg);
    dlg.showModal();
  });
}

export function toast(text, kind = '') {
  const t = h(`div.toast${kind ? '.' + kind : ''}`, text);
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), 4500);
}
