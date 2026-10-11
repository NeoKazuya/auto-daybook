// Index Tags: the back-of-the-book index. Full-text search over notes, plus time
// grouped by kind and by app. Durations come from the server's totals, never the browser.
import { api } from '../api.js';
import { h, icon, ribbon, panel, emptyState, errorState, skeletons, clock, duration } from '../ui.js';

const page = { route: 'tags', label: 'Index Tags', icon: 'tag', render };
export default page;

async function render(root, params) {
  const input = h('input.input', { type: 'search', placeholder: 'Search your notes: a file, a site, a person, a phrase…', 'aria-label': 'Search notes', value: params.get('q') ?? '' });
  const results = h('div');
  const kinds = h('div', skeletons(1));
  const apps = h('div', skeletons(1));

  root.append(
    ribbon(page, [h('span.pill', 'INDEX', h('b', 'TODAY'))]),
    panel('Search the index', 'search', null,
      h('form.searchbar', { onsubmit: (e) => { e.preventDefault(); find(); } }, input,
        h('button.btn.primary', { type: 'submit' }, icon('search'), 'Search')),
      results),
    h('div.grid-2',
      panel('By kind', 'tag', null, kinds),
      panel('By app', 'layers', null, apps)));

  async function find() {
    const text = input.value.trim();
    history.replaceState(null, '', text ? `#/tags?q=${encodeURIComponent(text)}` : '#/tags');
    if (!text) { results.replaceChildren(); return; }
    results.replaceChildren(skeletons(2));
    try {
      const hits = await api.search(text);
      results.replaceChildren(hits.length
        ? h('div.rows', h('div.label', `${hits.length} ${hits.length === 1 ? 'match' : 'matches'} for “${text}”`), hits.map((x) => hit(x, text)))
        : emptyState('search', 'No matches', `Nothing in today's notes mentions “${text}”. Deleted and paused time is never searched.`));
    } catch (err) {
      results.replaceChildren(errorState(err, find));
    }
  }

  function hit(x, text) {
    return h('a.row.clickable', { href: `#/daily?seg=${encodeURIComponent(x.segment_id)}` },
      h('div.row-main',
        h('span.stamp', clock(x.at)),
        h('div.row-text',
          h('span.row-title', { style: 'font-size:1.1rem' }, x.title),
          h('span.hit-snippet', highlight(x.snippet, text)))),
      h('div.row-end', h('span.tag', x.kind), icon('chevron')));
  }

  function chips(host, by, onPick) {
    return api.totals(by).then((rows) => {
      host.replaceChildren(rows.length
        ? h('div.tagcloud', rows.map((r) => h('button.tag', { type: 'button', onclick: () => onPick(r.key) }, r.key, h('span', duration(r.active)))))
        : emptyState('tag', 'Nothing indexed yet', 'Tags appear as Daybook writes notes.'));
    }).catch((err) => host.replaceChildren(errorState(err, () => chips(host, by, onPick))));
  }

  const pick = (key) => { input.value = key; find(); };
  const refresh = () => Promise.all([chips(kinds, 'kind', pick), chips(apps, 'app', pick)]);
  await refresh();
  if (input.value) find();
  return { onEvent: refresh };
}

function highlight(snippet, text) {
  const i = snippet.toLowerCase().indexOf(text.toLowerCase());
  if (i < 0) return snippet;
  return [snippet.slice(0, i), h('mark', snippet.slice(i, i + text.length)), snippet.slice(i + text.length)];
}
