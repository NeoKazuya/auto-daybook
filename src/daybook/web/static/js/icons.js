// Inline line icons, so the UI needs no icon font and works offline.
const P = {
  book: '<path d="M3 5.5c2.5-1 5.5-1 9 1 3.5-2 6.5-2 9-1V19c-2.5-1-5.5-1-9 1-3.5-2-6.5-2-9-1z"/><path d="M12 6.5V20"/><path d="M6 9h3M6 12h3M15 9h3M15 12h3"/>',
  contents: '<path d="M4 5.5c2-1 4.5-1 7 .5v13c-2.5-1.5-5-1.5-7-.5z"/><path d="M20 5.5c-2-1-4.5-1-7 .5v13c2.5-1.5 5-1.5 7-.5z"/><path d="M13.5 3v5l1.5-1 1.5 1V3"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="1.5"/><path d="M4 9.5h16M8.5 3v4M15.5 3v4"/>',
  sticky: '<path d="M5 4h14v10l-6 6H5z"/><path d="M13 20v-6h6"/><path d="M8.5 8.5h7M8.5 11.5h4"/>',
  quill: '<path d="M20 4c-6 1-11 6-13 13l-1.5 3"/><path d="M20 4c-.5 4-2.5 7-6 9l-5 1"/><path d="M14 8l-3 3"/>',
  tag: '<path d="M4 6.5V12l8 8 8-8-8-8H6.5z"/><circle cx="8.5" cy="8.5" r="1.3"/>',
  archive: '<rect x="3.5" y="4.5" width="17" height="4.5" rx="1"/><path d="M5 9v10.5h14V9"/><path d="M10 13h4"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  pause: '<path d="M9 6v12M15 6v12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
  chevron: '<path d="M9.5 6l6 6-6 6"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  trash: '<path d="M5 7h14M10 7V4.5h4V7M7 7l1 13h8l1-13"/>',
  send: '<path d="M4 12l16-7-6 16-2.5-6.5z"/><path d="M11.5 14.5L20 5"/>',
  shield: '<path d="M12 3.5l7 2.5v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/>',
  music: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  cpu: '<rect x="7" y="7" width="10" height="10" rx="1"/><path d="M10 3.5V7M14 3.5V7M10 17v3.5M14 17v3.5M3.5 10H7M3.5 14H7M17 10h3.5M17 14h3.5"/>',
  eyeoff: '<path d="M3 3l18 18"/><path d="M10.6 6.2A9.6 9.6 0 0 1 12 6c5 0 8.5 4.5 9.5 6-.5.8-1.5 2.1-2.9 3.3M6.3 7.6C4.5 8.8 3.2 10.6 2.5 12c1 1.5 4.5 6 9.5 6 1.5 0 2.9-.4 4.1-1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="1.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
  moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
  layers: '<path d="M12 4l8.5 4.5L12 13 3.5 8.5z"/><path d="M3.5 12.5L12 17l8.5-4.5"/><path d="M3.5 16.5L12 21l8.5-4.5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  bolt: '<path d="M13 3L5 13.5h6L10 21l8-10.5h-6z"/>',
  rewind: '<path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.5"/><path d="M4 4v4.5h4.5"/><path d="M12 8v4l3 2"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5M12 17.3v.2"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  timer: '<circle cx="12" cy="13" r="7.5"/><path d="M12 9v4.5h3M9.5 2.5h5"/>',
};

export function icon(name) {
  const span = document.createElement('span');
  span.className = 'icon';
  span.setAttribute('aria-hidden', 'true');
  span.innerHTML =
    `<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" ` +
    `stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${P[name] ?? P.spark}</svg>`;
  return span;
}

/** Swap every <span data-icon="name"> in static markup for its SVG. */
export function hydrateIcons(root = document) {
  for (const el of root.querySelectorAll('[data-icon]')) {
    el.replaceWith(icon(el.dataset.icon));
  }
}
