// Skills: the reference page. A list of skills with their status, and a review card
// for the selected one (docs/skills.md): goal, signals as questions, rules in words,
// permissions, the raw file with live parse errors, and the approval controls.
import { api } from '../api.js';
import { h, icon, btn, busy, ribbon, panel, stat, emptyState, errorState, skeletons, confirmDialog, toast, folio } from '../ui.js';

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
  let selected = params.get('id');
  let notice = null;
  const count = h('span.counter', '…');
  const list = h('div.rows', skeletons(3));
  const detail = h('div');
  const stats = h('div.stats');

  root.append(
    ribbon(page, [h('a.pill', { href: '#/board?q=' + encodeURIComponent('/skill ') }, icon('plus'), 'New skill from chat')]),
    h('div.grid-2',
      panel('Skills & toggles', 'check', [count], list),
      detail),
    stats);

  async function refresh() {
    try {
      skills = await api.skills();
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
      const s = skills.find((x) => x.id === selected);
      detail.replaceChildren(review(s));
    }
    const review_n = skills.filter((s) => s.status === 'needs_review').length;
    stats.replaceChildren(
      stat('quill', 'Skills', `${active} active`),
      stat('alert', 'Waiting for review', `${review_n}`),
      stat('bolt', 'Fired today', `${skills.reduce((t, s) => t + (s.fired_today ?? 0), 0)} times`),
      h('a.stat.action', { href: '#/board?q=' + encodeURIComponent('/skill ') },
        h('div.stat-icon', icon('plus')),
        h('div', { style: 'text-align:right' }, h('div.stat-label', 'Describe it in chat'), h('div.stat-value', { style: 'font-size:1.15rem' }, 'New skill'))));
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
    const editor = h('textarea.textarea', { spellcheck: false, 'aria-label': 'Skill file' }, s.text);
    const parse = h('div.errors', parseView(s.errors));
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

    const approved = s.status === 'active' || s.status === 'disabled';
    const buttons = [
      s.status === 'needs_review' && btn('Approve & enable', { icon: 'check', kind: 'ember',
        onclick: act(() => api.approveSkill(s.id, s.hash), `${s.name} is now active.`) }),
      s.status === 'disabled' && btn('Enable', { icon: 'check', kind: 'primary', onclick: act(() => api.enableSkill(s.id), `${s.name} enabled.`) }),
      s.status === 'active' && btn('Disable', { icon: 'pause', onclick: act(() => api.disableSkill(s.id), `${s.name} disabled. Its approval is kept.`) }),
      btn('Save edits', { icon: 'edit', title: 'Saving any edit returns the skill to needs review',
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
        h('div',
          h('div.label', s.origin === 'builtin' ? 'Built-in skill' : s.created_by === 'chat' ? 'Made from chat' : 'Your skill'),
          h('h2', s.name),
          s.request && h('p.muted', { style: 'margin:0.3rem 0 0' }, `From your request: “${s.request}”`)),
        section('Goal', h('p.goal', s.goal)),
        section('It asks the model', h('ul', s.signals.map((g) => h('li', g.ask)))),
        section('Rules', h('ul', s.rules.map((r) => h('li', ruleInWords(r))))),
        section('Permissions', h('ul', s.permissions.map((p) =>
          h('li.perm', h('span', p.describe ?? p.action), h('span.tag.quiet', p.action))))),
        approved && s.status !== 'active' ? h('p.muted', { style: 'margin:0' }, 'Approved. Enabling it needs no new review.') : null,
        section('Skill file', editor, parse),
        h('div.actions-bar', buttons)));
  }

  await refresh();
  return { onEvent: (e) => { if (e.type === 'SkillChanged' && !document.activeElement?.matches('textarea')) refresh(); } };
}

function section(title, ...body) {
  return h('div.review-sec', h('div.label', title), ...body);
}

function parseView(errors) {
  if (!errors?.length) return [h('span.parse-ok', '✓ The file parses.')];
  return errors.map((e) => h('div', `${e.at}: ${e.message}`));
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

function step({ action, args = {} }) {
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

function ruleInWords(rule) {
  return [
    ...trigger(rule.when), ' ',
    rule.do.map(step).join(', then '), '.',
    rule.cooldown ? ` At most once every ${dur(rule.cooldown)}.` : '',
  ];
}
