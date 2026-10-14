import {el, button, paragraph, badge, panel, empty, field, input, details, form, submit, preview} from './dom.js';

export async function renderProfile(c) {
  const projects = c.overview.projects.filter(p => !c.s.project || p.project === c.s.project);
  if (!projects.length) return empty('No project evidence yet. Import a selected source with a project label to start the ledger.');
  const cards = [];
  for (const project of projects) {
    const sections = [];
    for (const [kind, title] of [['intent', 'Recorded requests / intent'], ['observed', 'Observed messages / tools'], ['retained', 'Retained metadata / unsupported']]) {
      const page = await c.api('/api/evidence', undefined, {project: project.project, category_filter: kind, limit: 3});
      sections.push(el('section', {class: 'ledger-section'}, el('h3', {}, title),
        paragraph(`${project[kind]} current records · showing ${page.items.length}`),
        kind === 'intent' ? paragraph('User-role text is recorded intent, not proof of who authored it. Tool-result-only messages are excluded.') : null,
        page.items.length ? page.items.map(row => el('div', {class: 'ledger-item'}, paragraph(preview(row), 'small'),
          button('Inspect exact evidence', () => c.open(row.id), {'data-event-id': row.id}))) : paragraph('No evidence in this category.'),
        button('Browse all ' + kind + ' records', () => c.run(() => c.navigate('history', {project: project.project, session: '', revision: '', query: '', provider: '', category: kind})))));
    }
    const outcomes = await c.api('/api/outcomes', undefined, {project: project.project, limit: 3});
    const rename = form('rename-' + cards.length, values => c.run(async () => {
      await c.api('/api/rename', {old: project.project, new: values.project});
      if (c.s.project === project.project) c.s.project = values.project.trim();
      await c.refresh(); c.notice('Project label corrected. Source evidence is unchanged.');
    }), field('Project label', input('project', {value: project.project, required: true, maxlength: '80'})), submit('Save project label'));
    cards.push(el('article', {class: 'panel project-card'},
      el('header', {class: 'project-head'}, el('div', {class: 'eyebrow'}, 'User-declared project grouping'),
        el('h2', {}, project.project), paragraph(`${project.records} current records · ${project.sessions} session contexts`),
        badge('Evidence ledger · no score')),
      el('div', {class: 'project-ledger'}, sections),
      el('section', {class: 'ledger-section'}, el('h3', {}, 'Outcome ledger'),
        paragraph(`${outcomes.total} persisted receipts across current and historical revisions. Showing ${outcomes.items.length}.`),
        outcomes.items.length ? outcomes.items.map(o => el('div', {class: 'ledger-item'},
          badge(o.kind === 'report' ? 'Unverified human report' : 'Byte assertion: ' + o.receipt.observation.status,
            o.kind === 'check' && o.receipt.observation.match ? 'match' : 'gap'),
          paragraph(o.receipt.claim || 'Local SHA256 assertion; no business, deployment or causal verification.'),
          o.historical ? badge('Historical evidence links · current revision differs', 'gap') : null,
          ...o.links.map(link => button('Inspect linked evidence', () => c.open(link.id), {'data-event-id': link.id})))) : paragraph('No outcome checks or reports recorded.'),
        button('Open full outcome ledger', () => c.run(() => c.navigate('outcomes', {project: project.project, outcomeOffset: 0})))),
      el('footer', {class: 'project-foot'}, details('Correct project label', rename))));
  }
  return el('div', {class: 'projects'}, paragraph('Only the most recently selected revision contributes current evidence counts. That selection does not establish chronological newness. Sources and agent statements do not establish expertise or accomplishment.', 'privacy'), cards);
}
