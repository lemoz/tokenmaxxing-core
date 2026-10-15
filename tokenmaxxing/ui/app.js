import {el, paragraph, empty, option} from './dom.js';
import {renderImport} from './import.js';
import {renderHistory, openEvidence} from './history.js';
import {renderProfile} from './profile.js';
import {renderOutcomes} from './outcomes.js';
import {renderAnalysis} from './analysis.js';

const titles = {import: 'Import selected sources', history: 'Inspect the evidence', analysis: 'Understand the work', profile: 'Evidence-linked work profile', outcomes: 'Record and check outcomes'};
const descriptions = {
  import: 'Choose a format, a selected file and a project label. First start is empty; nothing is discovered automatically.',
  history: 'Inspect canonical messages beside exact source locators and retained records. Historical revisions remain accessible.',
  analysis: 'Review selected excerpts, inspect a source-linked interpretation, and update the same work as evidence evolves.',
  profile: 'Recorded requests, observed evidence and outcome receipts stay separate. Grouping is your correctable label.',
  outcomes: 'Link exact evidence to an explicit byte assertion or an unverified human report.'
};
let token = '';
if (location.hash.startsWith('#token=')) {
  const supplied = location.hash.slice(7);
  history.replaceState(null, '', location.pathname);
  if (/^[A-Za-z0-9_-]{43}$/.test(supplied)) token = supplied;
  try { sessionStorage.setItem('tokenmaxxing-token', token); } catch (_) { /* memory-only authorization still works */ }
} else {
  try { token = sessionStorage.getItem('tokenmaxxing-token') || ''; } catch (_) { /* launch URL required */ }
}
const s = {view: 'import', project: '', query: '', provider: '', category: '', session: '', revision: '',
  offset: 0, importOffset: 0, outcomeOffset: 0, sessionOffset: 0, evidenceOffset: 0,
  outcomeSession: '', outcomeRevision: '', selectedEvidence: new Set(), mode: 'check', draft: {}};
let generation = 0;
function notice(message, error = false) {
  const node = document.querySelector('#notice');
  node.textContent = message;
  node.className = 'notice' + (error ? ' error' : '');
  node.hidden = !message;
}
async function api(route, data, query = {}) {
  const suffix = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== '' && v !== undefined)).toString();
  const response = await fetch(route + (suffix ? '?' + suffix : ''), {
    method: data === undefined ? 'GET' : 'POST', cache: 'no-store', credentials: 'omit',
    headers: {Authorization: 'Bearer ' + token, ...(data === undefined ? {} : {'Content-Type': 'application/json'})},
    ...(data === undefined ? {} : {body: JSON.stringify(data)})
  });
  const result = await response.json();
  if (!response.ok) throw new Error(response.status === 401 ? 'Authorization required. Open the private launch URL from this running server; restart tokens are different.' : result.error || 'Local request failed.');
  return result;
}
let pendingActions = 0;
let disabledControls = [];
async function run(action, pending = 'Saving to private storage…') {
  notice(pending);
  if (!pendingActions++) {
    disabledControls = [...document.querySelectorAll('button')].map(control => [control, control.disabled]);
  }
  disabledControls.forEach(([control]) => control.disabled = true);
  try { await action(); }
  catch (error) { notice(error.message || 'Local request failed. Your form is available to retry.', true); }
  finally {
    if (!--pendingActions) {
      disabledControls.forEach(([control, disabled]) => control.disabled = disabled);
      disabledControls = [];
    }
  }
}
async function refresh() {
  const ticket = ++generation;
  const overview = await api('/api/overview');
  const historical = await api('/api/analysis/projects');
  for (const name of historical.projects) {
    if (!overview.projects.some(p => p.project === name)) overview.projects.push({project: name, sessions: 0,
      records: 0, intent: 0, observed: 0, retained: 0, outcomes: {}, historical_analysis: true});
  }
  if (ticket !== generation) return;
  if (s.project && !overview.projects.some(p => p.project === s.project)) s.project = '';
  const project = document.querySelector('#global-project');
  project.replaceChildren(option('', 'All projects'), ...overview.projects.map(p => option(p.project, p.project)));
  project.value = s.project;
  document.querySelector('#view-title').textContent = titles[s.view];
  document.querySelector('#description').textContent = descriptions[s.view];
  document.querySelectorAll('[data-view]').forEach(node => {
    if (node.dataset.view === s.view) node.setAttribute('aria-current', 'page');
    else node.removeAttribute('aria-current');
  });
  const scoped = s.project ? overview.projects.find(p => p.project === s.project) : overview;
  const countOutcomes = s.project ? Object.values(scoped.outcomes).reduce((a, b) => a + b, 0) : overview.outcomes;
  document.querySelector('#stats').replaceChildren(...[
    [scoped.records, 'Current revision records'], [scoped.sessions, 'Imported session contexts'], [countOutcomes, 'Outcome receipts · all revisions']
  ].map(([value, title]) => el('div', {class: 'stat'}, el('strong', {}, value), el('span', {}, title))));
  const view = {import: renderImport, history: renderHistory, analysis: renderAnalysis, profile: renderProfile, outcomes: renderOutcomes}[s.view];
  const result = await view({...ctx, overview, isCurrent: () => ticket === generation});
  if (ticket === generation) document.querySelector('#content').replaceChildren(result);
}
async function navigate(view, changes = {}) {
  if (Object.hasOwnProperty.call(changes, 'project') && changes.project !== s.project) {
    Object.assign(s, {session: '', revision: '', selected: '', importOffset: 0, outcomeOffset: 0,
      sessionOffset: 0, evidenceOffset: 0, outcomeSession: '', outcomeRevision: '', selectedEvidence: new Set()});
  }
  Object.assign(s, {view, offset: 0}, changes);
  notice('');
  await refresh();
  document.querySelector('#view-title').focus();
}
const ctx = {s, api, run, refresh, navigate, notice, open: id => run(() => openEvidence(ctx, id), 'Loading linked evidence…')};
document.querySelectorAll('[data-view]').forEach(node => node.addEventListener('click', () => run(() => navigate(node.dataset.view), 'Loading…')));
document.querySelector('#global-project').addEventListener('change', event => run(async () => {
  Object.assign(s, {project: event.target.value, session: '', revision: '', selected: '', category: '', offset: 0,
    importOffset: 0, outcomeOffset: 0, sessionOffset: 0, evidenceOffset: 0, outcomeSession: '', outcomeRevision: '', selectedEvidence: new Set()});
  await refresh(); notice('');
}, 'Loading selected project…'));
if (!token) {
  document.querySelector('#content').replaceChildren(empty('Open the private launch URL from the running server. Use --open-browser at startup. No private data has been loaded.'));
} else run(async () => { await refresh(); notice(''); }, 'Loading private store…');
