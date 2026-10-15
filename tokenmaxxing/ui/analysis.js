import {el, button, paragraph, badge, panel, empty, field, input, json, details, select, pager, form, submit} from './dom.js';

const words = value => value.replaceAll('_', ' ');
// Match Python's Unicode character offsets, including text outside the BMP.
const characters = value => Array.from(value).length;
function draft(c) {
  c.s.analysisDrafts ||= {};
  return c.s.analysisDrafts[c.s.project] ||= {selected: {}, target: '', observations: [], offset: 0, preview: null, revision: 0, busy: false};
}
function invalidate(d) {
  d.revision = (d.revision || 0) + 1;
  d.preview = null;
}
function claim(c, value, observations) {
  return el('div', {class: 'ledger-item'}, badge('Model ' + words(value.basis)), paragraph(value.text, ''),
    el('div', {class: 'actions'}, ...value.citations.map(id => citation(c, id, observations, 'Support')),
      ...value.contradictions.map(id => citation(c, id, observations, 'Contradiction'))));
}
function citation(c, id, observations, title) {
  if (id.startsWith('event:')) return button(title + ' · source record', () => c.open(id), {'data-event-id': id});
  const observation = observations.find(o => 'observation:' + o.id === id);
  return observation ? details(title + ' · operator attestation', json(observation.body),
    ...observation.body.evidence.map(event => button('Inspect linked source', () => c.open(event), {'data-event-id': event}))) :
    paragraph('Historical observation reference: ' + id);
}
function interpretation(c, assessment, observations, profile) {
  const result = assessment.result;
  const sections = profile ? result.profile : result.project;
  return el('div', {class: 'stack'},
    paragraph(`Assessment ${assessment.version} · ${assessment.created_at} · ${assessment.provider.model}`),
    assessment.stale ? badge('Stale: project evidence or observation state changed. Preview a refresh.', 'gap') : null,
    ...Object.entries(sections).map(([key, values]) => details(words(key), ...values.map(v => claim(c, v, observations)))),
    details('Exact submitted packet and coverage', json(assessment.packet)),
    paragraph('Model interpretation of agent-assisted artifacts. Recorded roles do not establish human authorship or personal expertise.', 'privacy'));
}

export function workLedger(c, ledger, profile = false) {
  return el('div', {class: 'stack'},
    paragraph(`${ledger.counts.episodes} persisted work episodes · ${ledger.counts.assessments} assessments · ${ledger.counts.observations} operator observations`),
    ...ledger.episodes.map(episode => {
      const latest = episode.assessments[episode.assessments.length - 1];
      return panel(latest.body.title.text,
        badge('Interpretation: ' + words(latest.body.state)),
        claim(c, latest.body.title, episode.observations),
        ...latest.body.work.map(v => claim(c, v, episode.observations)),
        details('Limitations and conflicting evidence', ...latest.body.limits.map(v => claim(c, v, episode.observations))),
        interpretation(c, latest, episode.observations, profile),
        details('Compare assessment history', ...episode.assessments.map(a => details('Assessment ' + a.version,
          ...a.body.work.map(v => claim(c, v, episode.observations)),
          ...a.body.limits.map(v => claim(c, v, episode.observations)), interpretation(c, a, episode.observations, profile)))),
        details('Independent observations: operator attestation, app did not execute',
          ...episode.observations.map(o => details(o.body.kind + ' · ' + o.body.status, json(o.body),
            ...o.body.evidence.map(id => button('Inspect linked source', () => c.open(id), {'data-event-id': id}))))),
        button('Add evidence or an observation to this episode', () => c.run(async () => {
          const project = episode.project;
          c.s.analysisDrafts ||= {};
          c.s.analysisDrafts[project] ||= {selected: {}, observations: [], offset: 0, busy: false};
          c.s.analysisDrafts[project].target = episode.id;
          invalidate(c.s.analysisDrafts[project]);
          await c.navigate('analysis', {project});
        })));
    }));
}

function observationForm(c, episode, d) {
  return details('Attach a separately observed relevant outcome',
    paragraph('You attest to a check already performed. This app reads the explicitly granted proof file and stores the attestation. It executes no check, command or URL. A browser observation cannot establish full private-feature acceptance.', 'privacy'),
    form('independent-observation', (values, node) => c.run(async () => {
      const evidence = [...node.querySelectorAll('input[name=linked]:checked')].map(n => n.value);
      const observation = Object.fromEntries(Object.entries(values).filter(([key]) => key !== 'linked'));
      observation.provenance = 'operator_attestation'; observation.evidence = evidence;
      const response = await c.api('/api/analysis/observation', {project: c.s.project, episode_id: episode.id, observation});
      d.observations = [...new Set([...d.observations, response.id])]; invalidate(d);
      await c.refresh(); c.notice('Operator attestation saved. Select evidence and preview a refresh to incorporate it into this same episode.');
    }),
    field('Observation type', select('kind', [['functional_check', 'Bounded functional check'], ['browser_observation', 'Browser observation']], 'functional_check')),
    ...[['method', 'Exact method used'], ['target_path', 'Granted artifact path'], ['target_revision', 'Exact checked revision'],
      ['expected_predicate', 'Expected narrow predicate'], ['actual_result', 'Actual observed result'],
      ['observed_at', 'Observation timestamp with timezone'], ['limitations', 'Limitations and unchecked behavior'],
      ['proof_path', 'Granted UTF-8 proof file path']].map(([name, title]) => field(title, input(name, {required: true, maxlength: '4000'}))),
    field('Observed status', select('status', [['passed', 'Predicate passed'], ['failed', 'Predicate failed'], ['skipped', 'Skipped'], ['inconclusive', 'Inconclusive']], 'inconclusive')),
    el('fieldset', {}, el('legend', {}, 'Explicitly link relevant episode evidence'),
      ...episode.evidence_ids.map(id => field(id, input('linked', {type: 'checkbox', value: id})))),
    submit('Save operator attestation')));
}

async function poll(c, project, runId, d) {
  try {
    const run = await c.api('/api/analysis/run', undefined, {run_id: runId});
    if (run.status === 'pending') {
      d.poll = setTimeout(() => poll(c, project, runId, d), 1000);
      return;
    }
    d.busy = false; d.runId = '';
    if (c.s.project === project && ['analysis', 'profile'].includes(c.s.view)) await c.refresh();
    c.notice(run.status === 'succeeded' ? 'Assessment persisted. Source-linked work and history are ready.' : run.error, run.status !== 'succeeded');
  } catch (error) {
    d.busy = false;
    c.notice('Run status unavailable. Reopen Analysis to inspect persisted status. ' + error.message, true);
  }
}

export async function renderAnalysis(c) {
  if (!c.s.project) return empty('Select one project to review evidence for analysis. Selecting or browsing a project transmits nothing to a model.');
  const project = c.s.project;
  const d = draft(c);
  const ledger = await c.api('/api/analysis', undefined, {project});
  const pending = ledger.runs.find(r => r.status === 'pending');
  if (pending && !d.busy) {
    d.busy = true; d.runId = pending.id;
    d.poll = setTimeout(() => poll(c, project, pending.id, d), 1000);
  }
  const page = await c.api('/api/analysis/candidates', undefined, {project, offset: d.offset});
  const selection = panel('Review and explicitly select excerpts',
    paragraph('Only approved personal projects can use inference. Review each excerpt for personal scope. Unknown or mixed-company content must stay unselected. Redaction is limited; preview every byte before Analyze.', 'privacy'),
    field('Work to update', select('episode', [['', 'New work: no existing episode'], ...ledger.episodes.map(e => [e.id, e.assessments[e.assessments.length - 1].body.title.text])], d.target,
      event => c.run(async () => { d.target = event.target.value; d.observations = []; invalidate(d); await c.refresh(); }))),
    paragraph('Choose the existing episode for later evidence about the same work. Wording changes do not create a second accomplishment.'),
    pager(page, offset => c.run(async () => { d.offset = offset; invalidate(d); await c.refresh(); })),
    ...page.items.map(row => {
      const saved = d.selected[row.event_id];
      const choices = row.parts.map(p => [p.part, p.part + ' · ' + words(p.evidence_type)]);
      let part = row.parts.find(p => p.part === saved?.part) || row.parts[0];
      const check = input('select-' + row.event_id, {type: 'checkbox', checked: !!saved, disabled: !part});
      const from = input('start', {type: 'number', min: '0', value: saved?.start ?? 0, 'aria-label': 'Excerpt start character'});
      const to = input('end', {type: 'number', min: '1', value: saved?.end ?? (part ? characters(part.text) : 0), 'aria-label': 'Excerpt end character'});
      const sourceText = el('pre', {}, part?.text || 'No supported text part. Original remains in History.');
      const save = () => {
        invalidate(d);
        if (check.checked && part) d.selected[row.event_id] = {event_id: row.event_id, session_id: row.session_id,
          revision_id: row.revision_id, scope: 'personal_reviewed', part: part.part, start: Number(from.value), end: Number(to.value)};
        else delete d.selected[row.event_id];
      };
      check.addEventListener('change', save); from.addEventListener('input', save); to.addEventListener('input', save);
      return details(row.provider + ' · ' + row.kind + ' · ' + row.event_id.slice(-12),
        button('Inspect immutable source detail', () => c.open(row.event_id), {'data-event-id': row.event_id}),
        field('Exact supported text part', select('part', choices, part?.part || '', event => {
          part = row.parts.find(p => p.part === event.target.value);
          from.value = 0; to.value = characters(part.text); sourceText.textContent = part.text; save();
        })), sourceText, json(row.diagnostics),
        el('div', {class: 'excerpt-range'}, field('Start character, inclusive', from), field('End character, exclusive', to)),
        field('I reviewed this excerpt as selected personal-project evidence', check));
    }),
    details('Selections retained across pages', ...Object.values(d.selected).map(item => el('div', {class: 'ledger-item'},
      paragraph(item.event_id + ' · characters ' + item.start + ' to ' + item.end),
      button('Inspect selected source', () => c.open(item.event_id), {'data-event-id': item.event_id}),
      button('Remove selection', () => c.run(async () => { delete d.selected[item.event_id]; invalidate(d); await c.refresh(); }))))),
    ...ledger.episodes.filter(e => e.id === d.target).map(e => el('div', {},
      ...e.observations.map(o => field('Include operator observation: ' + o.body.method + ' · ' + o.body.status,
        input('observation', {type: 'checkbox', checked: d.observations.includes(o.id), onchange: event => {
          invalidate(d); d.observations = event.target.checked ? [...d.observations, o.id] : d.observations.filter(id => id !== o.id);
        }}))), observationForm(c, e, d))),
    button('Preview exact external analysis packet', () => c.run(async () => {
      const revision = d.revision;
      const preview = await c.api('/api/analysis/preview', {project, selections: Object.values(d.selected), update_episode_id: d.target, observation_ids: d.observations});
      if (d.revision !== revision) return;
      d.preview = preview; d.previewRevision = revision;
      await c.refresh(); c.notice('Preview saved locally. Nothing has been sent to the model.');
    }), {class: 'primary', disabled: !ledger.enabled || d.busy}));
  const host = el('div', {class: 'stack'},
    ledger.enabled ? paragraph('Inference uses the configured existing Codex account. The reviewed packet leaves this computer only after Analyze.', 'privacy') :
      empty('Inference is disabled for this project. The operator must configure its startup allowlist and private provider directory.'),
    d.busy ? panel('Analysis pending', paragraph('You can keep browsing evidence while inference runs.'),
      button('Cancel owned analysis run', () => c.run(async () => { await c.api('/api/analysis/cancel', {run_id: d.runId}); c.notice('Cancellation requested. Waiting for owned process cleanup.'); }))) : null,
    selection);
  if (d.preview) {
    const p = d.preview;
    const revision = d.previewRevision;
    const start = button('Analyze this exact reviewed packet', async () => {
      if (d.revision !== revision || d.previewRevision !== revision || d.preview !== p) {
        c.notice('Selection changed. Preview the packet again.', true); return;
      }
      start.disabled = true;
      try {
        const run = await c.api('/api/analysis/start', {packet_id: p.packet_id});
        d.busy = run.status === 'pending'; d.runId = run.run_id;
        await c.refresh();
        c.notice(run.reused && !d.busy ? 'Identical successful assessment reused. No model call or additional assessment.' : 'Analysis pending. Evidence browsing remains available.');
        if (d.busy) poll(c, project, run.run_id, d);
      } catch (error) { c.notice(error.message, true); start.disabled = false; }
    }, {class: 'primary', disabled: !p.can_analyze || d.busy});
    host.append(panel('Exact external packet preview',
      paragraph(`${p.packet.coverage.included} included · ${p.packet.coverage.excluded.length} excluded · ${p.packet.coverage.unselected} unselected current records`),
      json(p.packet.coverage), details('Full external prompt, including interpretation instructions', el('pre', {}, p.external_prompt)),
      details('Local-only original excerpt mapping', json(p.local_provenance)),
      details('Explicit resource limits and provider', json(p.limits), json(p.provider)), start));
  }
  host.append(workLedger(c, ledger), details('Persisted run history', ...ledger.runs.map(r => paragraph(r.created_at + ' · ' + r.status + (r.error ? ' · ' + r.error : '')))));
  return host;
}
