// In-browser stand-in for the API, used only when the page has no token.
// Shapes follow ARCHITECTURE §4 until web/wire.py lands; when it does, switch to
// `daybook run --mock-api` and delete this file.

const today = new Date();
const at = (hm) => {
  const [hh, mm] = hm.split(':').map(Number);
  const d = new Date(today);
  d.setHours(hh, mm, 0, 0);
  return d.toISOString();
};
const secs = (a, b) => (new Date(b) - new Date(a)) / 1000;

let nextId = 100;
const obs = (start, end, app, title, seen, visible_text = '') =>
  ({ id: `o${nextId++}`, started_at: at(start), last_seen_at: at(end), window: { app, title }, seen, visible_text });

const CATALOG = {
  'music.play_mood': 'Change the music to a mood from your library',
  'music.stop': 'Stop the music',
  'overlay.show': 'Cover the screen with a full-screen message',
  'overlay.hide': 'Hide the full-screen message',
  'notify.send': 'Send a desktop notification',
  'desktop.open_url': 'Open a web page',
  'desktop.launch': 'Start an app',
  'desktop.focus': 'Bring an app to the front',
};

const db = {
  segments: [
    { id: 's1', title: 'Reading the architecture doc', kind: 'reading', authored: 'model', open: false,
      intent: 'Getting oriented on the capture pipeline before starting Lane B.',
      observations: [
        obs('09:02', '09:21', 'Firefox', 'ARCHITECTURE.md · NeoKazuya/auto-daybook', 'A GitHub page showing the Architecture document, scrolled to the Runtime section.', '## 3. Runtime\nOne process, daybookd, runs four loops'),
        obs('09:21', '09:38', 'Firefox', 'lane-b.md · NeoKazuya/auto-daybook', 'The Lane B brief with its scope checklist expanded.', 'Lane B: build the UI and the shell'),
      ] },
    { id: 's2', title: 'Setting up the GX10 dev instance', kind: 'admin', authored: 'model', open: false,
      intent: 'Getting a mock API running on port 8780 to build against.',
      observations: [
        obs('09:38', '09:55', 'Terminal', 'asus@1s6m: ~/auto-daybook', 'A terminal running the setup script; package installs scrolling past.', 'scripts/gx10/setup.sh\nInstalling python3.12-venv'),
        obs('09:55', '10:10', 'Terminal', 'tmux: daybook-b', 'A tmux session with the mock API log in one pane and a shell in the other.', 'Uvicorn running on http://127.0.0.1:8780'),
      ] },
    { id: 's3', title: 'Building the Daily Book view', kind: 'coding', authored: 'user', open: false,
      intent: 'Laying out segment cards against the mock timeline route.',
      observations: [
        obs('10:25', '10:51', 'Code', 'timeline.js — auto-daybook', 'VS Code with a JavaScript file open, rendering segment cards.', 'function renderSegment(seg) {'),
        obs('10:51', '11:20', 'Firefox', 'Daybook · Daily Book', 'The Daybook web page showing a list of timeline cards.', ''),
      ] },
    { id: 's4', title: 'Lane sync call', kind: 'meeting', authored: 'model', open: false,
      intent: 'Agreeing the wire.py contract with Lane A.',
      observations: [
        obs('11:20', '11:52', 'Firefox', 'Meet – Lane sync', 'A Google Meet call with three participant tiles and a shared screen of wire.py.', 'class TimelineOut(BaseModel):'),
      ] },
    { id: 's5', title: 'Reading the Qt Quick docs', kind: 'browsing', authored: 'model', open: false,
      intent: 'Checking how to make a frameless, always-on-top window.',
      observations: [
        obs('12:33', '13:05', 'Firefox', 'Window QML Type | Qt Quick 6', 'Qt documentation for the Window type, scrolled to the flags property.', 'flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint'),
      ] },
    { id: 's6', title: 'Overlay prototype in QML', kind: 'coding', authored: 'model', open: false,
      intent: 'Building the full-screen break overlay with a fade-in.',
      observations: [
        obs('13:05', '13:22', 'Code', 'Overlay.qml — auto-daybook', 'VS Code with a QML file defining a translucent full-screen rectangle.', 'opacity: 0.0\nBehavior on opacity { NumberAnimation { duration: 180 } }'),
        obs('13:22', '13:40', 'python3', 'daybook-shell', 'A full-screen dark overlay titled "Time for a break" with a countdown.', 'Time for a break · 4:52'),
      ] },
    { id: 's7', title: 'Reviewing a skill draft', kind: 'writing', authored: 'model', open: false,
      intent: 'Tightening the wording of the stand-after-meeting reminder.',
      observations: [
        obs('13:44', '14:10', 'Code', 'stand-after-meeting.skill.md', 'A Markdown skill file with YAML front matter and a short goal paragraph.', 'when: in_meeting becomes false'),
      ] },
    { id: 's8', title: 'Debugging SSE reconnect', kind: 'coding', authored: 'model', open: true,
      intent: 'Making the timeline recover after the event stream drops.',
      observations: [
        obs('14:10', '14:28', 'Code', 'api.js — auto-daybook', 'VS Code with an EventSource handler and a failing test in the panel below.', 'es.onerror = () => reconnect()'),
        obs('14:28', '14:41', 'Firefox', 'Daybook · Daily Book', 'Browser dev tools open on the Network tab, filtered to event-stream.', '/api/events?client=ui  (pending)'),
      ] },
  ],
  spans: [
    { state: 'paused', start: at('10:10'), end: at('10:25') },
    { state: 'away', start: at('11:52'), end: at('12:30') },
    { state: 'withheld', start: at('12:30'), end: at('12:33') },
    { state: 'overlay', start: at('13:40'), end: at('13:44') },
  ],
  privacy: {
    paused: false,
    exclusions: [
      { match: 'app', value: 'KeePassXC' },
      { match: 'app', value: '1Password' },
      { match: 'app', value: 'Bitwarden' },
      { match: 'title', value: 'Private Browsing' },
      { match: 'title', value: 'Incognito' },
      { match: 'title', value: 'Daybook ·' },
    ],
    keep_frames: false,
  },
  deletion_pending: false,
  skills: [
    skill('music', 'Mood music', 'builtin', 'active', true,
      "Play local music that matches what I'm doing, and change it when my activity changes. Focus music while I code or debug, calm music while I read, break music when I'm not working.",
      [{ name: 'mood', ask: 'Which kind of music fits what the user is doing right now?', type: 'music.mood' }],
      [{ when: 'mood updates', do: [{ action: 'music.play_mood', args: { mood: '$mood' } }] }],
      ['music.play_mood']),
    skill('break', 'Break reminder', 'builtin', 'disabled', false,
      'After a long stretch of focused work, cover the screen with a break reminder and switch to break music.',
      [{ name: 'focused', ask: 'Is the user doing sustained, focused work (coding, debugging, writing, studying) rather than browsing, chatting, watching media, or idling?', type: 'bool' }],
      [{ when: 'focused is true for 50m', do: [
        { action: 'overlay.show', args: { title: 'Time for a break', minutes: 5 } },
        { action: 'music.play_mood', args: { mood: 'break', min_dwell: '0s' } }] }],
      ['overlay.show', 'music.play_mood']),
    skill('break-demo', 'Break reminder (demo, 2 min)', 'builtin', 'active', true,
      'After a long stretch of focused work, cover the screen with a break reminder and switch to break music.',
      [{ name: 'focused', ask: 'Is the user doing sustained, focused work (coding, debugging, writing, studying) rather than browsing, chatting, watching media, or idling?', type: 'bool' }],
      [{ when: 'focused is true for 2m', do: [
        { action: 'overlay.show', args: { title: 'Time for a break', minutes: 5 } },
        { action: 'music.play_mood', args: { mood: 'break', min_dwell: '0s' } }] }],
      ['overlay.show', 'music.play_mood']),
    skill('water', 'Water reminder', 'user', 'disabled', false,
      "While I'm at the computer, nudge me to drink some water every 45 minutes.",
      [{ name: 'at_desk', ask: 'Is the user actively using the computer right now?', type: 'bool' }],
      [{ when: 'every 45m', do: [{ action: 'notify.send', args: { title: 'Drink some water', body: 'A glass now keeps the afternoon headache away.' } }] }],
      ['notify.send']),
    Object.assign(skill('refocus', 'Gentle refocus', 'user', 'needs_review', false,
      'If I drift into social media, news or videos for more than 10 minutes, send me a quiet nudge to get back to what I was doing.',
      [{ name: 'drifting', ask: 'Is the user browsing social media, news, or videos unrelated to their work?', type: 'bool' }],
      [{ when: 'drifting is true for 10m', cooldown: '30m', do: [{ action: 'notify.send', args: { title: 'Back to it?', body: "You've been off task for $drifting.for." } }] }],
      ['notify.send']), { request: 'nudge me if I get distracted for more than 10 minutes' }),
  ],
  status: {
    model: { name: 'qwen3.6:35b', reachable: true, latency_ms: 2900, calls_per_min: 6, queue: 0 },
    now_playing: { mood: 'focus', title: 'Cipher', artist: 'Kevin MacLeod' },
  },
};


function skill(id, name, origin, status, enabled, goal, signals, rules, allow) {
  return {
    id, name, origin, status, enabled, goal, signals, rules,
    created_by: origin === 'builtin' ? 'builtin' : 'chat',
    hash: `sha256:${id.padEnd(12, '0').slice(0, 12)}`,
    permissions: allow.map((action) => ({ action, describe: CATALOG[action] })),
    errors: [],
    text: skillText(id, name, signals, rules, allow, goal),
  };
}

function skillText(id, name, signals, rules, allow, goal) {
  const sig = signals.map((s) => `  ${s.name}:\n    ask: ${s.ask}\n    type: ${s.type}`).join('\n');
  const rul = rules.map((r) =>
    `  - when: ${r.when}\n    do:\n` +
    r.do.map((d) => `      - ${d.action}: { ${Object.entries(d.args).map(([k, v]) => `${k}: ${v}`).join(', ')} }`).join('\n')).join('\n');
  return `---\nid: ${id}\nname: ${name}\nsignals:\n${sig}\nrules:\n${rul}\nallow: [${allow.join(', ')}]\n---\n${goal}\n`;
}

/* ---------- derived views (the real backend computes these; see Portal) ---------- */

function segmentOut(s) {
  const o = s.observations;
  return {
    ...s,
    started_at: o[0]?.started_at,
    ended_at: o.at(-1)?.last_seen_at,
    active: o.reduce((t, x) => t + secs(x.started_at, x.last_seen_at), 0),
  };
}

function captureState() {
  return db.privacy.paused ? 'paused' : 'running';
}

/* ---------- live events ---------- */

const listeners = new Set();
export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
const emit = (type, data = {}) => listeners.forEach((fn) => fn({ type, ...data }));

// Every 15 s the open segment grows, like a running capture loop.
setInterval(() => {
  if (db.privacy.paused) return;
  const open = db.segments.find((s) => s.open);
  const last = open?.observations.at(-1);
  if (!last) return;
  last.last_seen_at = new Date(new Date(last.last_seen_at).getTime() + 15000).toISOString();
  emit('ObservationExtended', { segment: open.id });
}, 15000);

/* ---------- routes ---------- */

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (status, detail) => Object.assign(new Error(detail), { status });

export async function handle(method, path, body) {
  await wait(180 + Math.random() * 220);
  const url = new URL(path, 'http://demo.invalid');
  const parts = url.pathname.split('/').filter(Boolean).slice(1); // drop "api"
  const key = `${method} /${parts[0]}`;

  switch (key) {
    case 'GET /status':
      return {
        capture: captureState(),
        deletion_pending: db.deletion_pending,
        overlay: null,
        ...structuredClone(db.status),
        now_playing: db.status.now_playing,
      };
    case 'GET /now': {
      const open = db.segments.find((s) => s.open);
      return { capture: captureState(), open_segment: open ? segmentOut(open) : null };
    }
    case 'GET /timeline':
      return { segments: db.segments.map(segmentOut), spans: db.spans };
    case 'PATCH /segments': {
      const s = db.segments.find((x) => x.id === parts[1]);
      if (!s) throw fail(404, 'That segment no longer exists.');
      Object.assign(s, body, { authored: 'user' });
      emit('SegmentEdited', { segment: s.id });
      return segmentOut(s);
    }
    case 'DELETE /segments':
      db.segments = db.segments.filter((x) => x.id !== parts[1]);
      emit('Forgotten');
      return null;
    case 'PATCH /observations': {
      const o = db.segments.flatMap((s) => s.observations).find((x) => x.id === parts[1]);
      if (!o) throw fail(404, 'That note no longer exists.');
      Object.assign(o, body);
      emit('ObservationEdited');
      return o;
    }
    case 'DELETE /observations':
      for (const s of db.segments) s.observations = s.observations.filter((o) => o.id !== parts[1]);
      db.segments = db.segments.filter((s) => s.observations.length);
      emit('Forgotten');
      return null;
    case 'POST /forget': {
      const from = new Date(body.from);
      for (const s of db.segments) s.observations = s.observations.filter((o) => new Date(o.started_at) < from);
      db.segments = db.segments.filter((s) => s.observations.length);
      emit('Forgotten');
      return null;
    }
    case 'POST /forget-all':
      db.segments = [];
      db.spans = [];
      emit('Forgotten');
      return null;
    case 'POST /chat':
      return chat(body.message);
    case 'GET /skills':
      return db.skills;
    case 'PUT /skills': {
      const s = db.skills.find((x) => x.id === parts[1]);
      const errors = parseErrors(body.text);
      Object.assign(s, { text: body.text, errors, status: errors.length ? 'invalid' : 'needs_review', hash: `sha256:${Math.random().toString(16).slice(2, 14)}` });
      emit('SkillChanged', { skill: s.id });
      return s;
    }
    case 'POST /skills': {
      if (parts[1] === 'parse') return { errors: parseErrors(body.text) };
      const s = db.skills.find((x) => x.id === parts[1]);
      if (!s) throw fail(404, 'That skill no longer exists.');
      const action = parts[2];
      if (action === 'approve') {
        if (body.hash !== s.hash) throw fail(409, 'The skill changed since you reviewed it. Here is the new version.');
        Object.assign(s, { status: 'active', enabled: true, origin: s.origin });
      } else if (action === 'enable') {
        if (s.status === 'needs_review' || s.status === 'invalid') throw fail(409, 'This version hasn\'t been reviewed yet.');
        Object.assign(s, { status: 'active', enabled: true });
      } else if (action === 'disable') {
        Object.assign(s, { status: 'disabled', enabled: false });
      }
      emit('SkillChanged', { skill: s.id });
      return s;
    }
    case 'DELETE /skills':
      db.skills = db.skills.filter((x) => x.id !== parts[1]);
      emit('SkillChanged');
      return null;
    case 'GET /privacy':
      return { ...db.privacy, screenshots_stored: 0, deletion_pending: db.deletion_pending };
    case 'PUT /privacy':
      Object.assign(db.privacy, body);
      emit('CaptureStateChanged', { state: captureState() });
      return { ...db.privacy, screenshots_stored: 0, deletion_pending: db.deletion_pending };
    case 'POST /tick':
      emit('TickApplied');
      return null;
    case 'GET /search':
      return search(url.searchParams.get('q') ?? '');
    case 'GET /totals':
      return totals(url.searchParams.get('by') ?? 'kind');
    default:
      throw fail(404, `No demo route for ${method} ${url.pathname}`);
  }
}

function search(text) {
  const needle = text.trim().toLowerCase();
  if (!needle) return [];
  const hits = [];
  for (const s of db.segments) {
    for (const o of s.observations) {
      const hay = [s.title, s.intent, o.window.title, o.seen, o.visible_text].join(' \n ');
      if (hay.toLowerCase().includes(needle)) {
        hits.push({ segment_id: s.id, observation_id: o.id, at: o.started_at, kind: s.kind, title: s.title, snippet: o.seen + (o.visible_text ? ` — ${o.visible_text.split('\n')[0]}` : '') });
      }
    }
  }
  return hits;
}

function totals(by) {
  const sums = new Map();
  for (const s of db.segments) {
    for (const o of s.observations) {
      const k = by === 'app' ? o.window.app : by === 'title' ? s.title : s.kind;
      sums.set(k, (sums.get(k) ?? 0) + secs(o.started_at, o.last_seen_at));
    }
  }
  return [...sums].map(([key, active]) => ({ key, active })).sort((a, b) => b.active - a.active);
}

function parseErrors(text) {
  const errors = [];
  if (!text.startsWith('---')) errors.push({ at: 'front matter', message: 'The file must start with --- and YAML front matter.' });
  if (!/^id:\s*[a-z0-9-]{1,40}\s*$/m.test(text)) errors.push({ at: 'id', message: 'Missing or invalid id (lowercase letters, digits and dashes).' });
  if (!/^signals:/m.test(text)) errors.push({ at: 'signals', message: 'At least one signal is required.' });
  if (!/^rules:/m.test(text)) errors.push({ at: 'rules', message: 'At least one rule is required.' });
  if (!/^allow:/m.test(text)) errors.push({ at: 'allow', message: 'List the actions this skill may use under allow.' });
  for (const m of text.matchAll(/when:\s*(.+)/g)) {
    if (!/^(\w+ updates|\w+ becomes \S+|\w+ is \S+ for \d+[smh]|every \d+[smh])\s*$/.test(m[1].trim())) {
      errors.push({ at: 'rules[].when', message: `"${m[1].trim()}" isn't one of the four trigger forms.` });
    }
  }
  return errors;
}

async function chat(message) {
  await wait(1400);
  const text = message.trim();
  const wantsSkill = text.startsWith('/skill') || (!text.startsWith('/ask') && /\b(remind|every time|whenever|when i)\b/i.test(text));
  if (wantsSkill) {
    if (!db.skills.some((s) => s.id === 'stand-after-meeting')) {
      const s = skill('stand-after-meeting', 'Stand up after meetings', 'user', 'needs_review', false,
        'Every time I finish a meeting, remind me to stand up.',
        [{ name: 'in_meeting', ask: 'Is the user currently in a video call or online meeting (Zoom, Google Meet, Microsoft Teams, Jitsi, a Discord or Slack call, or similar)?', type: 'bool' }],
        [{ when: 'in_meeting becomes false', cooldown: '10m', do: [{ action: 'overlay.show', args: { title: "Meeting's over", body: 'Stand up and stretch for a minute before the next thing.', minutes: 1 } }] }],
        ['overlay.show']);
      s.created_by = 'chat';
      s.request = text.replace(/^\/skill\s*/, '');
      db.skills.push(s);
      emit('SkillChanged', { skill: s.id });
    }
    return { kind: 'draft', skill_id: 'stand-after-meeting', summary: 'Shows a full-screen “Meeting’s over” reminder once each time a call ends.' };
  }
  if (/paus|10:1/i.test(text)) {
    return { kind: 'answer', text: "I don't have notes for that: capture was paused from 10:10 to 10:25.", cites: [], window: { label: 'today' } };
  }
  const recent = db.segments.slice(-3).map(segmentOut);
  return {
    kind: 'answer',
    text: `In the last hour you reviewed the stand-up skill draft for ${Math.round(recent[1].active / 60)} minutes, then moved on to debugging the event stream reconnect, which you're still on (${Math.round(recent[2].active / 60)} minutes so far). A short break overlay came up at 13:40.`,
    cites: recent.slice(1).map((s) => ({ segment_id: s.id, title: s.title })),
    window: { label: 'last hour' },
  };
}
