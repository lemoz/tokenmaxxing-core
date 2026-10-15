// DOM-contract smoke only. This is not browser rendering or mobile layout proof.
import assert from 'node:assert/strict';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.events = {}; this.disabled = false; }
  append(...items) { this.children.push(...items); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, action) { this.events[key] = action; }
  replaceChildren(...items) { this.children = items; }
  get textContent() { return this.children.map(c => c.textContent ?? String(c)).join(''); }
  set textContent(value) { this.children = [String(value)]; }
}
globalThis.Node = Element;
globalThis.document = {createElement: tag => new Element(tag), createTextNode: text => ({textContent: text})};
const {renderAnalysis, workLedger} = await import('../tokenmaxxing/ui/analysis.js');
const flatten = node => [node, ...(node.children || []).flatMap(flatten)];
const findButton = (node, title) => {
  const button = flatten(node).find(n => n.tag === 'button' && n.textContent === title);
  assert.ok(button, title); return button;
};
const eventId = 'event:' + 'a'.repeat(64);
const row = {event_id: eventId, session_id: 'b'.repeat(64), revision_id: 'c'.repeat(64), provider: 'codex',
  kind: 'message', diagnostics: [], parts: [
    {part: '/payload/content/0/text', text: 'A😀B', evidence_type: 'recorded_request'},
    {part: '/payload/content/1/text', text: '🛰 later', evidence_type: 'recorded_request'}]};
const ledger = {enabled: true, episodes: [], runs: [], counts: {episodes: 0, assessments: 0, observations: 0}};
const packet = {coverage: {included: 1, excluded: [], unselected: 0}, evidence: []};
const preview = {packet_id: 'd'.repeat(64), packet, can_analyze: true, external_prompt: 'synthetic exact reviewed prompt', local_provenance: {}, limits: {}, provider: {model: 'synthetic'}};
const calls = [], inspected = [], notices = [];
const c = {s: {project: 'Synthetic'}, open: async id => inspected.push(id), refresh: async () => {},
  run: async action => action(), notice: (...args) => notices.push(args), api: async (route, data, query) => {
    calls.push({route, data, query});
    if (route === '/api/analysis') return ledger;
    if (route === '/api/analysis/candidates') return {items: [row], total: 1, offset: 0, limit: 50};
    if (route === '/api/analysis/preview') return preview;
    if (route === '/api/analysis/start') return {run_id: 'synthetic', status: 'succeeded', reused: true};
    throw new Error('Unexpected test route');
  }};
let tree = await renderAnalysis(c);
assert.equal(calls.filter(c => c.data).length, 0, 'Browsing cannot submit a packet');
await findButton(tree, 'Inspect immutable source detail').events.click();
assert.deepEqual(inspected, [eventId]);
const checkbox = flatten(tree).find(n => n.tag === 'input' && n.attributes.type === 'checkbox');
checkbox.checked = true; checkbox.events.change();
await findButton(tree, 'Preview exact external analysis packet').events.click();
assert.equal(calls.filter(c => c.route === '/api/analysis/start').length, 0, 'Preview cannot run inference');
const submitted = calls.find(c => c.route === '/api/analysis/preview').data;
assert.equal(submitted.selections[0].scope, 'personal_reviewed');
assert.equal(submitted.selections[0].event_id, eventId);
assert.equal(submitted.selections[0].end, 3, 'Default offsets count Unicode characters, not UTF-16 units');
const partSelector = flatten(tree).find(n => n.tag === 'select' && n.attributes.name === 'part');
partSelector.events.change({target: {value: row.parts[1].part}});
assert.equal(c.s.analysisDrafts.Synthetic.selected[eventId].end, 7, 'Changing text parts preserves Unicode offsets');
assert.equal(c.s.analysisDrafts.Synthetic.preview, null);
await findButton(tree, 'Preview exact external analysis packet').events.click();
tree = await renderAnalysis(c);
await findButton(tree, 'Analyze this exact reviewed packet').events.click();
assert.deepEqual(calls.find(c => c.route === '/api/analysis/start').data, {packet_id: preview.packet_id});
assert.ok(notices.some(n => n[0].includes('No model call')));
// A changed selection invalidates the still-rendered preview immediately.
tree = await renderAnalysis(c);
c.s.analysisDrafts.Synthetic.preview = null;
const before = calls.filter(c => c.route === '/api/analysis/start').length;
await findButton(tree, 'Analyze this exact reviewed packet').events.click();
assert.equal(calls.filter(c => c.route === '/api/analysis/start').length, before);
assert.ok(notices.some(n => n[0].includes('Selection changed')));
// Model text stays a text node; links navigate to actual evidence IDs.
const claim = {text: '<script>synthetic inert text</script>', basis: 'gap', citations: [eventId], contradictions: []};
const assessment = {version: 1, created_at: 'synthetic', provider: {model: 'synthetic'}, stale: true,
  body: {title: claim, state: 'insufficient', work: [claim], limits: [claim]}, packet,
  result: {project: {purpose: [claim]}, profile: {supported_work: [claim], attribution: [claim]}}};
const rendered = workLedger(c, {counts: {episodes: 1, assessments: 1, observations: 0}, episodes: [
  {id: 'e'.repeat(64), project: 'Synthetic', assessments: [assessment], observations: [], evidence_ids: [eventId]}]}, true);
assert.ok(rendered.textContent.includes('Stale:'));
assert.ok(rendered.textContent.includes('<script>synthetic inert text</script>'));
assert.equal(flatten(rendered).filter(n => n.tag === 'script').length, 0);
assert.ok(flatten(rendered).some(n => n.attributes?.['data-event-id'] === eventId));
// Delay a real preview action while the editable selection changes.
let resolvePreview;
const delayedPreview = new Promise(resolve => { resolvePreview = resolve; });
const delayedCalls = [];
const delayed = {...c, s: {project: 'Synthetic'}, api: async (route, data, query) => {
  delayedCalls.push({route, data, query});
  if (route === '/api/analysis/preview') return delayedPreview;
  return c.api(route, data, query);
}};
tree = await renderAnalysis(delayed);
const delayedCheckbox = flatten(tree).find(n => n.tag === 'input' && n.attributes.type === 'checkbox');
delayedCheckbox.checked = true; delayedCheckbox.events.change();
const pendingPreview = findButton(tree, 'Preview exact external analysis packet').events.click();
delayedCheckbox.checked = false; delayedCheckbox.events.change();
assert.equal(Object.keys(delayed.s.analysisDrafts.Synthetic.selected).length, 0);
assert.equal(delayed.s.analysisDrafts.Synthetic.preview, null);
resolvePreview(preview);
await pendingPreview;
tree = await renderAnalysis(delayed);
const restoredAnalyze = flatten(tree).find(n => n.tag === 'button' && n.textContent === 'Analyze this exact reviewed packet');
if (restoredAnalyze) await restoredAnalyze.events.click();
assert.equal(delayed.s.analysisDrafts.Synthetic.preview, null, 'A delayed response cannot restore deselected content');
assert.equal(restoredAnalyze, undefined, 'No stale external preview or Analyze action is restored');
assert.equal(delayedCalls.filter(c => c.route === '/api/analysis/start').length, 0);
// Even if the API reuses the same response object, an older Analyze action
// belongs to its original draft revision, not the newly reviewed revision.
tree = await renderAnalysis(c);
await findButton(tree, 'Preview exact external analysis packet').events.click();
tree = await renderAnalysis(c);
const olderAnalyze = findButton(tree, 'Analyze this exact reviewed packet');
const startOffset = flatten(tree).find(n => n.tag === 'input' && n.attributes.name === 'start');
startOffset.value = 1; startOffset.events.input();
await findButton(tree, 'Preview exact external analysis packet').events.click();
const callsBeforeOldAnalyze = calls.filter(c => c.route === '/api/analysis/start').length;
assert.equal(c.s.analysisDrafts.Synthetic.preview, preview, 'Fixture intentionally reuses object identity');
await olderAnalyze.events.click();
assert.equal(calls.filter(c => c.route === '/api/analysis/start').length, callsBeforeOldAnalyze);
tree = await renderAnalysis(c);
await findButton(tree, 'Analyze this exact reviewed packet').events.click();
assert.equal(calls.filter(c => c.route === '/api/analysis/start').length, callsBeforeOldAnalyze + 1);

const episodeId = 'e'.repeat(64);
const observation = {id: 'f'.repeat(64), body: {method: 'Synthetic attestation', status: 'passed', evidence: [eventId]}};
const contextLedger = {...ledger, counts: {episodes: 1, assessments: 1, observations: 1}, episodes: [
  {id: episodeId, project: 'Synthetic', assessments: [assessment], observations: [observation], evidence_ids: [eventId]}]};
const inputNamed = (tree, name) => flatten(tree).find(n => n.tag === 'input' && n.attributes.name === name);
const selectNamed = (tree, name) => flatten(tree).find(n => n.tag === 'select' && n.attributes.name === name);
for (const [label, change] of [
  ['start offset', tree => { const n = inputNamed(tree, 'start'); n.value = 1; n.events.input(); }],
  ['end offset', tree => { const n = inputNamed(tree, 'end'); n.value = 2; n.events.input(); }],
  ['text part', tree => selectNamed(tree, 'part').events.change({target: {value: row.parts[1].part}})],
  ['remove selection', tree => findButton(tree, 'Remove selection').events.click()],
  ['update target', tree => selectNamed(tree, 'episode').events.change({target: {value: ''}})],
  ['observation inclusion', tree => inputNamed(tree, 'observation').events.change({target: {checked: true}})],
  ['page offset', tree => findButton(tree, 'Next').events.click()],
]) {
  let resolve;
  const pending = new Promise(done => { resolve = done; });
  let starts = 0;
  const context = {...c, s: {project: 'Synthetic'}, api: async (route, data, query) => {
    if (route === '/api/analysis') return contextLedger;
    if (route === '/api/analysis/candidates') return {items: [row], total: 100, offset: 0, limit: 50};
    if (route === '/api/analysis/preview') return pending;
    if (route === '/api/analysis/start') starts++;
    return c.api(route, data, query);
  }};
  let current = await renderAnalysis(context);
  const checkbox = inputNamed(current, 'select-' + eventId);
  checkbox.checked = true; checkbox.events.change();
  await selectNamed(current, 'episode').events.change({target: {value: episodeId}});
  current = await renderAnalysis(context);
  const request = findButton(current, 'Preview exact external analysis packet').events.click();
  await change(current);
  resolve(preview); await request;
  current = await renderAnalysis(context);
  assert.equal(context.s.analysisDrafts.Synthetic.preview, null, label + ' invalidates a pending response');
  assert.equal(flatten(current).some(n => n.tag === 'button' && n.textContent === 'Analyze this exact reviewed packet'), false);
  assert.equal(starts, 0);
}
console.log('DOM-contract smoke passed: Unicode offsets, explicit preview/Analyze, delayed-preview invalidation, source navigation, inert model text. No browser/layout acceptance.');
