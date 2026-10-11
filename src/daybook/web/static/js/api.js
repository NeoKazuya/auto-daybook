// The UI's only door to data: the HTTP routes and the SSE stream, shaped by
// src/daybook/web/wire.py (ARCHITECTURE §6.9). When the page is served without a
// token (opened from a plain static server), every call goes to the in-browser demo
// in demo.js instead, which returns the same wire shapes.

const token = document.querySelector('meta[name="daybook-token"]')?.content ?? '';
export const DEMO = !token || new URLSearchParams(location.search).has('demo');

/**
 * Features the UI has that the server doesn't serve yet. They work on demo data and
 * stay unavailable against a real server until Lane A adds them to wire.py.
 */
export const supports = {
  search: DEMO,          // GET /api/search (Portal.search)
  totals: DEMO,          // GET /api/totals (Portal.totals)
  monitoringRules: DEMO, // schedule, apps_mode, allowed_apps, retention_days in /api/privacy
};

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
  editSegment: (id, patch) => call('PATCH', `/api/segments/${id}`, patch),
  deleteSegment: (id) => call('DELETE', `/api/segments/${id}`),
  editObservation: (id, patch) => call('PATCH', `/api/observations/${id}`, patch),
  deleteObservation: (id) => call('DELETE', `/api/observations/${id}`),
  forget: (from, to) => call('POST', '/api/forget', { from, to }),
  forgetAll: () => call('POST', '/api/forget-all'),
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
  search: (text) => call('GET', `/api/search${q({ q: text })}`),
  totals: (by, from, to) => call('GET', `/api/totals${q({ by, from, to })}`),
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
  if (DEMO) {
    let off = () => {};
    demoBackend().then((d) => { off = d.subscribe(onEvent); });
    return () => off();
  }
  const es = new EventSource('/api/events?client=ui');
  let opened = false;
  es.onopen = () => {
    if (opened) onEvent({ type: 'Reconnected' });
    opened = true;
  };
  for (const name of EVENTS) {
    es.addEventListener(name, (e) => {
      let data = {};
      try { data = JSON.parse(e.data); } catch { /* ignore malformed frames */ }
      onEvent({ type: name, ...data });
    });
  }
  return () => es.close();
}
