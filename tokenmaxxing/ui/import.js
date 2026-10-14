import {el, button, paragraph, badge, panel, empty, field, input, json, details, select, pager, form, submit} from './dom.js';

export async function renderImport(c) {
  const {s, api, run, refresh, notice, navigate} = c;
  const page = await api('/api/sessions', undefined, {project: s.project, offset: s.importOffset});
  const session = input('selected_session', {id: 'selected-session'});
  const format = select('format', [['codex-jsonl', 'Codex JSONL'], ['codex-legacy-json', 'Codex legacy session/items'], ['claude-code-jsonl', 'Claude Code JSONL'], ['hermes-sqlite', 'Hermes SQLite · native validation pending']], 'codex-jsonl', () => {
    session.required = format.value === 'hermes-sqlite';
    session.disabled = !session.required;
  });
  format.id = 'source-format'; session.disabled = true;
  const importForm = form('import-form', values => run(async () => {
    const result = await api('/api/import', values);
    await refresh();
    notice(`${result.added_records} new revision records; ${result.new_session ? 'new session' : 'existing session'}. ${result.grouping_unchanged ? 'Existing project preserved; use Correct grouping to move it.' : 'Source remains unchanged.'} Coverage: ${result.summary.coverage}.`);
  }, 'Importing the explicitly selected source…'),
  field('Source format', format),
  field('Selected absolute file path', input('path', {required: true, id: 'source-path', spellcheck: 'false'}), 'Only files within startup --allow-source grants can be read.'),
  field('Selected Hermes session ID', session, 'Required for Hermes. No session discovery or default selection.'),
  field('Project label', input('project', {required: true, maxlength: '80', value: s.project, id: 'import-project'}), 'A user-declared grouping, correctable later.'),
  paragraph('Native Hermes histories have not yet been validated. Coverage warnings remain visible.', 'privacy'),
  submit('Import selected source'));
  const receipts = page.items.map(item => {
    const h = item.header;
    const move = form('move-' + item.id, values => run(async () => {
      await api('/api/move', {session_id: item.id, project: values.project});
      await refresh(); notice('Session grouping updated. Evidence and outcome identities are unchanged.');
    }), field('Correct project grouping', input('project', {value: item.project, required: true, maxlength: '80'})), submit('Move this session'));
    const deletion = form('delete-' + item.id, values => run(async () => {
      await api('/api/delete', {session_id: item.id, confirmation: values.confirmation});
      s.importOffset = 0; await refresh(); notice('Selected session, all its revisions and linked checks removed from pilot storage. Original sources unchanged.');
    }), paragraph('Removes this session, every imported revision and all linked checks from the pilot only. No secure-erasure guarantee for SQLite/filesystem slack.'),
    field('Type DELETE followed by the full session key below', input('confirmation', {required: true, autocomplete: 'off'})),
    el('pre', {}, 'DELETE ' + item.id), el('button', {class: 'danger', type: 'submit'}, 'Remove session and linked outcomes'));
    return el('article', {class: 'receipt', 'data-session-id': item.id},
      el('h3', {}, item.project), paragraph(`${item.provider} · ${h.session_kind || 'recorded session context'} · ${item.summary.events} current records`),
      paragraph(h.native_session_id || 'Unresolved native identity · revision fallback', 'mono path'),
      paragraph(h.source.reference, 'mono path'), badge(item.summary.coverage, item.summary.coverage === 'partial' ? 'gap' : ''),
      el('div', {class: 'actions'}, button('Browse session', () => run(() => navigate('history', {session: item.id, revision: '', query: '', category: '', provider: ''}))),
        button('Repeat import', () => run(async () => {
          const result = await api('/api/import', {path: h.source.reference, format: item.format, project: item.project,
            ...(item.format === 'hermes-sqlite' ? {selected_session: h.native_session_id} : {})});
          await refresh(); notice(`${result.added_records} new revision records. Most recently selected revision is now current.`);
        }, 'Reading the selected source again…'))),
      details('Identity, summary and coverage', json(h), json(item.summary)),
      details('Correct grouping', move), details('Remove this imported session', deletion));
  });
  return el('div', {class: 'two-col'}, panel('01 / Explicit selection, local only', importForm),
    panel('02 / Imported session contexts', paragraph('Repeated and copied inputs keep one session. Changed inputs retain immutable revisions; most recent selection supplies current counts.'),
      receipts.length ? receipts : empty('No imported sessions in this scope.'),
      pager(page, offset => run(async () => { s.importOffset = offset; await refresh(); notice(''); }))));
}
