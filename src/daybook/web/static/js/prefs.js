// Display preferences. These belong to this browser, not to Daybook's data, so they
// live in localStorage and never go through the API.
const KEY = 'daybook.prefs';
const DEFAULTS = { textSize: 'medium', hour12: false, startPage: 'contents', developer: false };

// Root font sizes: everything set in rem scales from these; the columns stay fixed.
export const TEXT_SIZES = { small: '75%', medium: '81.25%', large: '93.75%' };

let prefs = { ...DEFAULTS, ...read() };

function read() {
  try { return JSON.parse(localStorage.getItem(KEY)) ?? {}; } catch { return {}; }
}

export const getPref = (key) => prefs[key];

export function setPref(key, value) {
  prefs = { ...prefs, [key]: value };
  try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* storage blocked */ }
  applyPrefs();
}

export function applyPrefs() {
  document.documentElement.style.fontSize = TEXT_SIZES[prefs.textSize] ?? '';
}
