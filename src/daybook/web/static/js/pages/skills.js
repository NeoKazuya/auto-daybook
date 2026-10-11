// Skills: the reference page. A list of skills with their status, and a review card
// for the selected one: its name, its prompt, its rules in words, and the approval controls.
// Developer mode (Settings) adds the questions it asks the model, the permissions, and the raw
// skill file with live parse errors.
import { api } from '../api.js';
import { getPref } from '../prefs.js';
import { h, icon, btn, busy, ribbon, panel, split, emptyState, errorState, skeletons, confirmDialog, toast, folio } from '../ui.js';

const page = { route: 'skills', label: 'Skills', icon: 'quill', render };
export default page;

const STATUS = {
  active: { label: 'Active', check: '', mark: 'check', tag: '' },
  needs_review: { label: 'Needs review', check: '.warn', mark: 'alert', tag: '.ember' },
  disabled: { label: 'Disabled', check: '.off', mark: 'pause', tag: '.quiet' },
  invalid: { label: 'Invalid', check: '.bad', mark: 'x', tag: '.danger' },
};

async function render(root, params) {
  let skills = [];
  let catalog = [];
  let selected = params.get('id');
  let notice = null;
  const count = h('span.counter', '…');
  const newSkill = h('a.btn', { href: '#/board?q=' + encodeURIComponent('/skill ') }, icon('plus'), 'New skill');
  const list = h('div.rows.skill-scroll', skeletons(3));
  const detail = h('div');

  root.append(
    ribbon(page),
    split(panel('Skills & toggles', 'check', [count, newSkill], list), detail,
      { key: 'skills', initial: 60, label: 'Resize skills and review card' }));

  async function refresh() {
    try {
      ({ entries: skills, catalog } = await api.skills());
      if (!skills.some((s) => s.id === selected)) selected = skills.find((s) => s.status === 'needs_review')?.id ?? skills[0]?.id;
      draw();
    } catch (err) {
      list.replaceChildren(errorState(err, refresh));
    }
  }

  function draw() {
    const active = skills.filter((s) => s.status === 'active').length;
    count.textContent = `${active} / ${skills.length} enabled`;
    if (!skills.length) {
      list.replaceChildren(emptyState('quill', 'No skills yet', 'Describe one on the Sticky Board, for example “remind me to stand up after meetings”.'));
      detail.replaceChildren();
    } else {
      list.replaceChildren(...skills.map(row));
      list.querySelector('.selected')?.scrollIntoView({ block: 'nearest' });
      const s = skills.find((x) => x.id === selected);
      detail.replaceChildren(review(s));
    }
  }

  function row(s, i) {
    const st = STATUS[s.status] ?? STATUS.invalid;
    return h(`div.row.clickable${s.id === selected ? '.selected' : ''}${s.status === 'disabled' ? '.dim' : ''}`,
      { onclick: () => { selected = s.id; notice = null; draw(); history.replaceState(null, '', `#/skills?id=${s.id}`); },
        role: 'button', tabindex: 0, onkeydown: (e) => e.key === 'Enter' && e.currentTarget.click() },
      h('div.row-main',
        h(`span.check${st.check}`, icon(st.mark)),
        h('div.row-text',
          h('span.row-title', s.name),
          h('span.row-sub', s.goal.split('. ')[0].replace(/\.$/, '')))),
      h('div.row-end',
        h('span.folio', folio(i + 1)),
        h(`span.tag${st.tag}`, st.label)));
  }

  function review(s) {
    const st = STATUS[s.status] ?? STATUS.invalid;
    const dev = getPref('developer');
    let editor = null;
    let parse = null;
    if (dev) {
      editor = h('textarea.textarea', { spellcheck: false, 'aria-label': 'Skill file' }, s.text);
      parse = h('div.errors', parseView(s.errors));
      let timer;
      editor.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          try {
            const r = await api.parseSkill(s.id, editor.value);
            parse.replaceChildren(...parseView(r.errors));
          } catch (err) {
            parse.replaceChildren(h('div', `Couldn't check the file: ${err.message}`));
          }
        }, 350);
      });
    }

    const run = async (button, fn, done) => {
      try {
        await busy(button, fn);
        if (done) toast(done);
        notice = null;
      } catch (err) {
        if (err.status === 409) notice = err.message;
      }
      await refresh();
    };
    const act = (fn, done) => (e) => run(e.currentTarget, fn, done);

    const buttons = [
      s.status === 'needs_review' && btn('Approve & enable', { icon: 'check', kind: 'ember',
        onclick: act(() => api.approveSkill(s.id, s.hash), `${s.name} is now active.`) }),
      s.status === 'disabled' && btn('Enable', { icon: 'check', kind: 'primary', onclick: act(() => api.enableSkill(s.id), `${s.name} enabled.`) }),
      s.status === 'active' && btn('Disable', { icon: 'pause', onclick: act(() => api.disableSkill(s.id), `${s.name} disabled. Its approval is kept.`) }),
      dev && btn('Save edits', { icon: 'edit', title: 'Saving any edit returns the skill to needs review',
        onclick: act(() => api.saveSkill(s.id, editor.value), 'Saved. Review the new version before it can run.') }),
      s.origin !== 'builtin' && btn('Delete', { icon: 'trash', kind: 'danger', onclick: async (e) => {
        const button = e.currentTarget;
        const ok = await confirmDialog({ title: `Delete “${s.name}”?`, body: 'The skill file is removed. Notes it already caused stay in the timeline.', confirm: 'Delete skill', danger: true });
        if (ok) await run(button, () => api.deleteSkill(s.id), 'Skill deleted.');
      } }),
    ];

    return panel('Review card', 'shield', h(`span.tag${st.tag}`, st.label),
      h('div.review',
        notice && h('div.notice', notice),
        h('h2', s.name),
        h('p.goal', s.goal),
        dev && h('div.review-sec', h('div.label', 'It asks the model'), h('ul', s.signals.map((g) => h('li', g.ask)))),
        h('div.review-sec', h('div.label', 'Rules'), h('ul', s.rules.map((r) => h('li', ruleInWords(r))))),
        dev && h('div.review-sec', h('div.label', 'Permissions'), h('ul', s.allow.map((action) =>
          h('li.perm', h('span', catalog.find((c) => c.name === action)?.description ?? action), h('span.tag.quiet', action))))),
        dev && h('div.review-sec', h('div.label', 'Skill file'), editor, parse),
        h('div.actions-bar', buttons)));
  }

  await refresh();
  return { onEvent: (types) => {
    if ((types.has('SkillsChanged') || types.has('Reconnected')) && !document.activeElement?.matches('textarea')) refresh();
  } };
}

function parseView(errors) {
  if (!errors?.length) return [h('span.parse-ok', '✓ The file parses.')];
  return errors.map((e) => h('div', `${e.location}: ${e.message}`));
}

/* ---------- rules in words ---------- */

const human = (name) => name.replace(/_/g, ' ').replace(/^in /, 'in a ');
const DUR = { s: 'seconds', m: 'minutes', h: 'hours' };
const dur = (d) => { const m = /^(\d+)([smh])$/.exec(d); return m ? `${m[1]} ${DUR[m[2]]}` : d; };

function trigger(when) {
  let m;
  if ((m = /^(\w+) updates$/.exec(when))) return ['On every tick, with the current ', h('em', human(m[1])), ':'];
  if ((m = /^(\w+) becomes (\S+)$/.exec(when))) return ['When ', h('em', human(m[1])), ' becomes ', h('em', m[2]), ':'];
  if ((m = /^(\w+) is (\S+) for (\S+)$/.exec(when))) return ['When ', h('em', human(m[1])), ' has been ', h('em', m[2]), ` for ${dur(m[3])}:`];
  if ((m = /^every (\S+)$/.exec(when))) return [`Every ${dur(m[1])} while you're here:`];
  return [`When ${when}:`];
}

function step({ action, arguments: args = {} }) {
  const v = (x) => (typeof x === 'string' && x.startsWith('$') ? `the current ${human(x.slice(1))}` : x);
  switch (action) {
    case 'overlay.show': return `show a full-screen overlay “${args.title}”${args.minutes ? ` for up to ${args.minutes} min` : ''}`;
    case 'overlay.hide': return 'hide the overlay';
    case 'music.play_mood': return `play ${v(args.mood)} music`;
    case 'music.stop': return 'stop the music';
    case 'notify.send': return `send a notification “${args.title}”`;
    case 'desktop.open_url': return `open ${args.url}`;
    case 'desktop.launch': return `start ${args.app}`;
    case 'desktop.focus': return `bring ${args.app} to the front`;
    default: return action;
  }
}

// Cooldowns arrive in seconds.
function seconds(n) {
  if (n % 3600 === 0) return `${n / 3600} ${n === 3600 ? 'hour' : 'hours'}`;
  if (n % 60 === 0) return `${n / 60} ${n === 60 ? 'minute' : 'minutes'}`;
  return `${n} seconds`;
}

function ruleInWords(rule) {
  return [
    ...trigger(rule.when), ' ',
    rule.steps.map(step).join(', then '), '.',
    rule.cooldown ? ` At most once every ${seconds(rule.cooldown)}.` : '',
  ];
}
