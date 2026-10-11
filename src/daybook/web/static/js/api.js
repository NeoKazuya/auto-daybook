// The UI's only door to data: the HTTP routes and the SSE stream (ARCHITECTURE §6.9).
// When the page is served without a token (opened from a plain static server), every
// call goes to the in-browser demo instead, so the pages can be designed and reviewed
// before `daybook run --mock-api` exists.

const token = document.querySelector('meta[name="daybook-token"]')?.content ?? '';
export const DEMO = !token || new URLSearchParams(location.search).has('demo');

let demo = null;
async function demoBackend() {
  demo ??= await import('./demo.js');
  return demo;
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function call(method, path, body) {
  if (DEMO) return (await demoBackend()).handle(method, path, body);
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') headers['X-Daybook-Token'] = token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, 'Daybook isn\'t responding. Is `daybook run` still going?');
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? `Request failed (${res.status})`);
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
  setPrivacy: (body) => call('PUT', '/api/privacy', body),
  tick: () => call('POST', '/api/tick'),
  // Not in ARCHITECTURE §6.9 yet: both wrap existing Portal reads (`search`, `totals`)
  // and need a shared-contract PR to wire.py before the real backend serves them.
  search: (text) => call('GET', `/api/search${q({ q: text })}`),
  totals: (by, from, to) => call('GET', `/api/totals${q({ by, from, to })}`),
};

/** Subscribe to every bus event. Returns an unsubscribe function. */
export function subscribe(onEvent) {
  if (DEMO) {
    let off = () => {};
    demoBackend().then((d) => { off = d.subscribe(onEvent); });
    return () => off();
  }
  const es = new EventSource('/api/events?client=ui');
  es.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data)); } catch { /* ignore malformed frames */ }
  };
  return () => es.close();
}
