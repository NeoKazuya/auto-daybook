// The UI's only door to data: the HTTP routes and the SSE stream, shaped by
// src/daybook/web/wire.py (ARCHITECTURE §6.9). When the page is served without a
// token (opened from a plain static server), every call goes to the in-browser demo
// in demo.js instead, which returns the same wire shapes.

const token = document.querySelector('meta[name="daybook-token"]')?.content ?? '';
export const DEMO = !token || new URLSearchParams(location.search).has('demo');

/**
 * Server features and the remaining demo-only monitoring controls.
 */
export const supports = {
  search: true,          // GET /api/search (Portal.search)
  totals: true,          // GET /api/totals (Portal.totals)
  monitoringRules: DEMO, // schedule, apps_mode, allowed_apps, retention_days in /api/privacy
};

// Changes to existing notes invalidate derived recall, including in other tabs.
// No note contents or session token are sent over the channel.
const noteListeners = new Set();
let noteChannel = null;
try { noteChannel = new BroadcastChannel('daybook-notes'); } catch { /* unavailable */ }
export function onNotesChanged(listener) {
  noteListeners.add(listener);
  return () => noteListeners.delete(listener);
}
function notesChanged(broadcast = false) {
  for (const listener of noteListeners) listener();
  if (broadcast) noteChannel?.postMessage({ type: 'notes_changed' });
}
if (noteChannel) noteChannel.onmessage = (event) => {
  if (event.data?.type === 'notes_changed') notesChanged();
};
async function changeNotes(method, path, body) {
  const result = await call(method, path, body);
  notesChanged(true);
  return result;
}

let demo = null;
async function demoBackend() {
  demo ??= await import('./demo.js');
  return demo;
}

export class ApiError extends Error {
  constructor(status, message, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// The server answers errors with a short code in `detail`; say what it means.
const MESSAGES = {
  not_found: 'That item no longer exists. It may have been deleted.',
  invalid_request: 'Daybook rejected that request.',
  invalid_time_range: 'That time range isn’t valid.',
  invalid_token_or_origin: 'This page’s session expired. Reload the page.',
  stale_approval: 'The skill changed since you reviewed it. The new version is shown; review it again.',
  needs_review: 'This version hasn’t been reviewed yet. Approve it first.',
  active_signal_limit: 'Too many active skill questions (12 at most). Disable another skill first.',
};
const explain = (detail, status) =>
  MESSAGES[detail]
  ?? (detail?.endsWith('_not_found') ? MESSAGES.not_found : null)
  ?? (detail?.startsWith('invalid_request') ? MESSAGES.invalid_request : null)
  ?? `Request failed (${status}).`;

async function call(method, path, body) {
  if (DEMO) {
    try {
      return await (await demoBackend()).handle(method, path, body);
    } catch (err) {
      throw new ApiError(err.status ?? 500, explain(err.detail, err.status ?? 500), err.detail);
    }
  }
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') headers['X-Daybook-Token'] = token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, 'Daybook isn’t responding. Is `daybook run` still going?');
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, explain(data?.detail, res.status), data?.detail);
  return data;
}

const q = (params) => {
  const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== '')).toString();
  return s ? `?${s}` : '';
};

export const api = {
  status: () => call('GET', '/api/status'),
  now: () => call('GET', '/api/now'),
  timeline: (from, to) => call('GET', `/api/timeline${q({ from, to })}`),
  editSegment: (id, patch) => changeNotes('PATCH', `/api/segments/${id}`, patch),
  deleteSegment: (id) => changeNotes('DELETE', `/api/segments/${id}`),
  editObservation: (id, patch) => changeNotes('PATCH', `/api/observations/${id}`, patch),
  deleteObservation: (id) => changeNotes('DELETE', `/api/observations/${id}`),
  forget: (from, to) => changeNotes('POST', '/api/forget', { from, to }),
  forgetAll: () => changeNotes('POST', '/api/forget-all'),
  chat: (message) => call('POST', '/api/chat', { message }),
  skills: () => call('GET', '/api/skills'),
  saveSkill: (id, text) => call('PUT', `/api/skills/${id}`, { text }),
  parseSkill: (id, text) => call('POST', '/api/skills/parse', { id, text }),
  approveSkill: (id, hash) => call('POST', `/api/skills/${id}/approve`, { hash }),
  enableSkill: (id) => call('POST', `/api/skills/${id}/enable`),
  disableSkill: (id) => call('POST', `/api/skills/${id}/disable`),
  deleteSkill: (id) => call('DELETE', `/api/skills/${id}`),
  privacy: () => call('GET', '/api/privacy'),
  // PrivacyPut forbids unknown fields, so only send what the server accepts.
  setPrivacy: (p) => call('PUT', '/api/privacy', supports.monitoringRules ? p
    : { paused: p.paused, exclude_apps: p.exclude_apps, exclude_titles: p.exclude_titles }),
  tick: () => call('POST', '/api/tick'),
  search: async (text, from, to, limit) => (await call('GET', `/api/search${q({ q: text, from, to, limit })}`)).hits,
  totals: async (by, from, to) => (await call('GET', `/api/totals${q({ by, from, to })}`)).totals,
};

// Named SSE events from wire.EVENT_MODELS.
const EVENTS = [
  'ObservationAdded', 'ObservationExtended', 'SegmentChanged', 'TickRecorded', 'CaptureStateChanged',
  'SkillFired', 'SkillsChanged', 'OverlayShown', 'OverlayHidden', 'MusicChanged', 'StatusChanged',
];

/**
 * Subscribe to every bus event as `{ type, ...payload }`. After a reconnect it also
 * sends `{ type: 'Reconnected' }`, since events may have been missed. Returns an
 * unsubscribe function.
 */
export function subscribe(onEvent) {
  const receive = (event) => {
    if (event.type === 'Reconnected' ||
        (event.type === 'StatusChanged' && event.component === 'journal' && event.status === 'notes_invalidated')) notesChanged();
    onEvent(event);
  };
  if (DEMO) {
    let off = () => {};
    demoBackend().then((d) => { off = d.subscribe(receive); });
    return () => off();
  }
  const es = new EventSource('/api/events?client=ui');
  let opened = false;
  es.onopen = () => {
    if (opened) receive({ type: 'Reconnected' });
    opened = true;
  };
  for (const name of EVENTS) {
    es.addEventListener(name, (e) => {
      let data = {};
      try { data = JSON.parse(e.data); } catch { /* ignore malformed frames */ }
      receive({ type: name, ...data });
    });
  }
  return () => es.close();
}
