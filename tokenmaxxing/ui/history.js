import {el, button, paragraph, badge, panel, empty, field, input, json, details, select, pager, form, submit, preview} from './dom.js';

export async function openEvidence(c, id) {
  const item = await c.api('/api/evidence/detail', undefined, {event_id: id});
  await c.navigate('history', {project: item.project, session: item.session_id, revision: item.revision_id,
    selected: id, offset: Math.floor(item.ordinal / 50) * 50, query: '', provider: '', category: ''});
}

export function detail(c, item) {
  const body = item.body;
  const meta = [
    ['Project label', item.project], ['Provider', item.provider], ['Session context', item.header.session_id || 'Unresolved'],
    ['Revision', item.revision_id], ['Evidence ID', item.id], ['Order', body.order], ['Locator', JSON.stringify(body.locator)],
    ['Source revision digest', body.source_revision_id]
  ];
  return el('article', {class: 'detail-panel', id: 'record-detail', tabindex: '-1'},
    el('h2', {}, 'Selected source record'), badge(item.current ? 'Current selected revision' : 'Historical revision · excluded from current profile', item.current ? '' : 'gap'),
    el('dl', {class: 'metadata'}, meta.flatMap(([key, value]) => [el('dt', {}, key), el('dd', {}, value)])),
    paragraph('Parent links and source roles are observed context; they do not prove spawn origin, human ownership, or outcomes.', 'privacy'),
    el('section', {class: 'detail-section'}, el('h3', {}, 'Canonical message / tool view'), json(body.canonical),
      body.interactions ? details('Ordered tool interactions · additive to event counts', json(body.interactions)) : null),
    el('section', {class: 'detail-section'}, el('h3', {}, 'Coverage and provenance'), json(body.diagnostics),
      json(item.summary.session_diagnostics), details('Exact source references and field pointers', json(item.source_references), json(body.locator), json(body.field_pointers), json(body.source_field_pointers || body.block_pointers)),
      details('Retained source record · inert text', json(body.source_record)), details('Session metadata and full envelope', json(item.header), json(body))),
    el('div', {class: 'actions'}, button('Open project profile', () => c.run(() => c.navigate('profile', {project: item.project}))),
      button('Link an outcome', () => c.run(() => c.navigate('outcomes', {project: item.project, outcomeSession: item.session_id,
        outcomeRevision: item.revision_id, evidenceOffset: Math.floor(item.ordinal / 50) * 50,
        selectedEvidence: new Set([item.id])})), {'data-action': 'link-outcome'})));
}

export async function renderHistory(c) {
  const {s, api, run, refresh, notice} = c;
  const page = await api('/api/evidence', undefined, {project: s.project, provider: s.provider, query: s.query,
    session_id: s.session, revision_id: s.revision, category_filter: s.category, offset: s.offset});
  const filters = form('history-form', values => run(async () => {
    Object.assign(s, {query: values.query, provider: values.provider, offset: 0});
    await refresh(); notice('');
  }), field('Search stored evidence', input('query', {type: 'search', value: s.query, id: 'history-query'})),
    field('Provider', select('provider', [['', 'All providers'], ['codex', 'Codex'], ['claude', 'Claude Code'], ['hermes', 'Hermes']], s.provider)), submit('Search'));
  filters.className = 'filters';
  const context = el('div', {class: 'actions'}, button('Clear evidence filters', () => run(async () => {
    Object.assign(s, {query: '', provider: '', session: '', revision: '', category: '', offset: 0}); await refresh(); notice('');
  })));
  if (s.session) {
    const revisions = await api('/api/revisions', undefined, {session_id: s.session});
    context.append(field('Revision selection', select('revision', [['', 'Current selected revision'], ...revisions.items.map(r => [r.id, `${r.current ? 'Current' : 'Historical'} · ${r.created_at} · ${r.digest.slice(0, 12)}`])], s.revision,
      event => run(async () => { s.revision = event.target.value; s.offset = 0; await refresh(); notice(''); }))));
  }
  const selected = page.items.find(r => r.id === s.selected) || page.items[0];
  const detailHost = el('div', {}, selected ? detail(c, await api('/api/evidence/detail', undefined, {event_id: selected.id})) : empty('Select a record to inspect evidence.'));
  const list = el('section', {class: 'record-list', 'aria-label': 'Evidence records'}, page.items.length ? page.items.map(row => button([
    el('span', {class: 'record-title'}, `${row.provider} · ${row.body.kind} · record ${row.ordinal}`),
    el('span', {class: 'record-preview'}, preview(row)), paragraph(row.project),
    badge(row.current ? row.category : 'Historical revision', row.current ? '' : 'gap')
  ], () => run(async () => {
    const value = await api('/api/evidence/detail', undefined, {event_id: row.id});
    s.selected = row.id;
    list.querySelectorAll('[data-event-id]').forEach(n => n.setAttribute('aria-pressed', String(n.dataset.eventId === row.id)));
    detailHost.replaceChildren(detail(c, value)); notice(''); document.querySelector('#record-detail').focus();
  }), {class: 'record', 'data-event-id': row.id, 'aria-pressed': String(selected.id === row.id), 'aria-controls': 'record-detail'})) : empty('No matching evidence. Adjust filters or import a selected source.'));
  return el('div', {}, filters, context,
    paragraph(`${page.total} matching stored records. Search includes canonical views, tools, locators and retained source fields. Pages do not limit ingestion.`),
    pager(page, offset => run(async () => { s.offset = offset; await refresh(); notice(''); })),
    el('div', {class: 'history-grid'}, list, detailHost));
}
