import {el, button, paragraph, badge, panel, empty, field, input, json, details, select, pager, form, submit, preview} from './dom.js';

function receipt(c, item) {
  const value = item.receipt;
  return el('article', {class: 'receipt', 'data-outcome-id': item.id},
    badge(item.kind === 'report' ? 'Unverified human report' : 'Byte assertion: ' + value.observation.status,
      item.kind === 'check' && value.observation.match ? 'match' : 'gap'),
    el('h3', {}, value.claim || 'Local file SHA256 assertion'), paragraph(item.project),
    paragraph(item.kind === 'report' ? 'A human statement, not independently verified.' : 'A match verifies nonempty bytes at observation time only. It does not establish business success, deployment or agent causation.'),
    item.historical ? badge('Historical evidence links · retained, not current', 'gap') : null,
    paragraph('First recorded: ' + item.created_at), details('Full persisted receipt', json(value)),
    el('div', {class: 'actions'}, item.links.map(link => button(link.current ? 'View current evidence' : 'View historical evidence', () => c.open(link.id), {'data-event-id': link.id}))));
}

export async function renderOutcomes(c) {
  const {s, api, run, refresh, notice} = c;
  if (!c.overview.sessions) return empty('Import evidence before recording an outcome. Every check and report requires an exact evidence link.');
  const sessions = await api('/api/sessions', undefined, {project: s.project, offset: s.sessionOffset});
  if (!s.outcomeSession && sessions.items.length) s.outcomeSession = sessions.items[0].id;
  let revisionPage;
  try { revisionPage = s.outcomeSession ? await api('/api/revisions', undefined, {session_id: s.outcomeSession}) : {items: []}; }
  catch (_) {
    s.outcomeSession = sessions.items.length ? sessions.items[0].id : '';
    s.outcomeRevision = ''; s.selectedEvidence.clear();
    revisionPage = s.outcomeSession ? await api('/api/revisions', undefined, {session_id: s.outcomeSession}) : {items: []};
  }
  const sessionOptions = sessions.items.map(i => [i.id, `${i.project} · ${i.provider} · ${i.header.native_session_id || 'unresolved'}`]);
  if (s.outcomeSession && !sessionOptions.some(([id]) => id === s.outcomeSession)) sessionOptions.unshift([s.outcomeSession, 'Selected linked session · ' + s.outcomeSession.slice(0, 12)]);
  const sourceSelect = select('session_id', sessionOptions, s.outcomeSession, event => run(async () => {
    s.outcomeSession = event.target.value; s.outcomeRevision = ''; s.selectedEvidence.clear(); s.evidenceOffset = 0;
    await refresh(); notice('');
  })); sourceSelect.id = 'outcome-session';
  const revisionSelect = select('revision_id', [['', 'Current selected revision'], ...revisionPage.items.map(r => [r.id, `${r.current ? 'Current' : 'Historical'} · ${r.digest.slice(0, 12)}`])], s.outcomeRevision, event => run(async () => {
    s.outcomeRevision = event.target.value; s.selectedEvidence.clear(); s.evidenceOffset = 0; await refresh(); notice('');
  })); revisionSelect.id = 'outcome-revision';
  const evidence = await api('/api/evidence', undefined, {session_id: s.outcomeSession, revision_id: s.outcomeRevision, offset: s.evidenceOffset});
  const evidenceCount = paragraph(`${s.selectedEvidence.size} explicitly selected evidence links`);
  const checklist = el('div', {class: 'checklist'}, evidence.items.map(row => {
    const control = input('evidence', {type: 'checkbox', value: row.id, checked: s.selectedEvidence.has(row.id)});
    control.addEventListener('change', () => {
      if (control.checked) s.selectedEvidence.add(row.id); else s.selectedEvidence.delete(row.id);
      evidenceCount.textContent = `${s.selectedEvidence.size} explicitly selected evidence links`;
    });
    return el('label', {class: 'check-row'}, control, el('span', {}, `${row.ordinal} · ${row.body.kind} · ${preview(row)}`));
  }));
  const mode = select('kind', [['check', 'Check local artifact'], ['report', 'Unverified human report']], s.mode, event => run(async () => {
    s.mode = event.target.value; await refresh(); notice('');
  })); mode.id = 'outcome-kind';
  function draftField(name, control) {
    control.value = s.draft[name] || '';
    control.addEventListener('input', () => s.draft[name] = control.value);
    return control;
  }
  const action = form('outcome-form', values => run(async () => {
    const data = {session_id: s.outcomeSession, evidence: [...s.selectedEvidence], kind: s.mode, note: values.note};
    if (s.mode === 'report') data.claim = values.claim;
    else { data.path = values.path; data.expected = values.expected; }
    const result = await api('/api/outcome', data);
    await refresh();
    const status = s.mode === 'report' ? 'Unverified human report' : 'Byte assertion: ' + result.receipt.observation.status;
    notice(`${status}. ${result.added ? 'Receipt persisted; profile updated.' : 'Identical receipt retained; no extra count. First observation time preserved.'}`,
      s.mode === 'check' && !result.receipt.observation.match);
  }, 'Recording the scoped observation…'),
    field('Outcome kind', mode), field('Evidence session', sourceSelect),
    pager(sessions, offset => run(async () => { s.sessionOffset = offset; await refresh(); notice(''); })),
    field('Evidence revision', revisionSelect),
    s.mode === 'report' ? field('What do you report?', draftField('claim', el('textarea', {name: 'claim', required: true, maxlength: '2000'}))) : [
      field('Selected absolute artifact path', draftField('path', input('path', {required: true, id: 'artifact-path', spellcheck: 'false'})), 'Must be inside startup --allow-artifact grants. File contents stay local.'),
      field('Expected SHA256 (required)', draftField('expected', input('expected', {required: true, id: 'expected-sha256', pattern: '[a-fA-F0-9]{64}', spellcheck: 'false'})), 'Supply the expected checksum explicitly; no existence-only success action.')],
    paragraph('Match verifies a nonempty byte assertion only. Missing, empty, changing or mismatched files never produce a successful check.', 'privacy'),
    el('fieldset', {}, el('legend', {}, 'Link existing evidence'), evidenceCount, checklist,
      pager(evidence, offset => run(async () => { s.evidenceOffset = offset; await refresh(); notice(''); }))),
    field('Notes (optional)', draftField('note', el('textarea', {name: 'note', maxlength: '2000'}))),
    submit(s.mode === 'report' ? 'Save unverified report' : 'Check expected SHA256'));
  const ledger = await api('/api/outcomes', undefined, {project: s.project, offset: s.outcomeOffset});
  return el('div', {class: 'two-col'}, panel('01 / Scoped outcome evidence', action), panel('02 / Persisted outcome ledger',
    paragraph('Identical assertion, evidence links and note retain one receipt and its original observation time. Different assertions remain separate observations.'),
    ledger.items.length ? ledger.items.map(item => receipt(c, item)) : empty('No outcome receipts in this scope.'),
    pager(ledger, offset => run(async () => { s.outcomeOffset = offset; await refresh(); notice(''); })),
    button('Open updated work profile', () => run(() => c.navigate('profile')))));
}
