# Private project interpretation

Analysis extends the existing local evidence app. Import, History, byte assertions,
human reports and source-detail navigation retain their existing contracts. The
new work ledger stores evidence-linked interpretations and assessment history.
It does not measure personal skill or establish shipped features.

## Startup authority

Inference is disabled by default. The operator uses a separate private store
containing only selected personal sources, grants exact source/proof files and
relevant artifacts, and supplies one `--analysis-project` per approved label.
A label alone is not evidence of personal ownership. Every selected excerpt also
requires review for personal scope. Do not enable inference on a mixed store.

The existing `serve` command additionally accepts:

| Option | Meaning |
| --- | --- |
| `--analysis-project LABEL` | Repeatable startup allowlist; empty by default |
| `--analysis-directory PATH` | Separate private 0700 temporary root, outside the repository and store |
| `--analysis-runner-prefix JSON` | Trusted startup argv array, default `[]` |
| `--analysis-timeout SECONDS` | Outer timeout, default 300, range 1 to 1800 |

Keep the existing `--allow-source` and `--allow-artifact` grants narrow. Proof
files for operator observations need a source grant. No option installs a
dependency or starts another service. Navigation and packet preview never call
the model. Only the authenticated `POST /api/analysis/start` does so.

An optional supervisor prefix can contain literal `{prompt_path}` and `{cwd}`
substitutions. For example, the *argv structure* may be
`["/trusted/owned-supervisor", "--prompt", "{prompt_path}", "--cwd", "{cwd}", "--"]`.
The operator supplies the real wrapper's supported flags. The fixed Codex argv
follows the prefix. There is no shell, interpolation of transcript text, or
HTTP command configuration. Set a wrapper timeout shorter than the provider's
outer timeout, leaving time for its descendant cleanup. The wrapper must preserve
the Codex JSON event stream on stdout. Only the narrowly pinned startup
capability diagnostics described below are tolerated.

## Review and analysis flow

1. Select a project and open Analysis. Inspect immutable sources as needed.
2. Select individual event IDs and one supported text part per event. Optionally
   narrow the start/end Unicode character offsets. Confirm personal scope for
   each excerpt. Selections remain visible across pages.
3. Select an existing work episode when adding evidence about the same work.
   Explicitly select any persisted operator observations to incorporate.
4. Preview. The app persists the exact packet, excluded-event diagnostics,
   unselected-record count, omitted text-part/character counts, provider contract,
   local redaction mapping and full external prompt. No model call occurs here.
5. Review the full external prompt and explicitly Analyze. Browsing remains
   available while the background worker runs. Status/failure survives reopening.
6. Inspect the interpretation, work episodes, supported and contradictory source
   links, gaps and explanatory work profile. Compare assessment versions.
7. Attach a separately observed bounded outcome to an existing episode, using
   the structured form and explicitly granted proof/artifact paths. Preview and
   Analyze again to incorporate it. This creates another assessment of the same
   episode. An unchanged successful packet/provider/schema/observation state
   reuses the persisted result without a model call or additional assessment.

Draft changes invalidate the preview and any pending preview response or older
Analyze action. The server rejects changed source revision/grouping/observation
state. Unsuccessful previews also require unchanged known-episode context;
successful identical packet/provider results still reuse without a model call.
Persistence checks new episode support and the 12-episode limit against the live
project in one transaction. Failure saves no partial episodes or assessments.

Preview and sending both revalidate included observations' original proof-source
and artifact startup grants, including cached previews after restart. Captured
proof bytes and hashes stay unchanged. Older project labels remain selectable
for historical analysis after grouping changes; affected assessments display as
stale, and episodes with moved or deleted evidence are excluded from outgoing
context. Explicitly selecting such an episode for update is rejected.

## Evidence and interpretation contract

`selected-excerpts-1` packets contain opaque local project/session/revision/event
IDs, provider, numeric source locators, exact field pointers and excerpt offsets.
Only known canonical text parts are eligible. Original records, headers, system
and developer messages, reasoning, tool arguments, unrelated files and native
identity/path metadata are not serialized into the packet. Unsupported parts,
compactions, projections, metadata wrappers and historical guardian approvals
are excluded with diagnostics. Source execution/delegation context cannot prove
human intent or authorship. The native evidence remains inspectable locally.

Recorded tool outputs may be strings or lists of `text`, `input_text` and
`output_text` blocks with string `text` fields. Each supported block is a separate
part at its native output pointer plus `/index/text`; multiple parts require an
explicit selection. Other blocks and arbitrary objects remain excluded. Excerpt
offsets and local redaction provenance apply to the selected text only; the native
record is unchanged.

Known episode IDs, prior interpretation titles and evidence IDs provide update
context. Prior titles are model interpretations, not independent proof; citations
still must resolve to this submitted packet or explicitly included observations.
New episodes with identical source support are rejected. Shared source support
cannot be recast as a new episode. Distinct records with identical text retain
their separate identities. New disjoint evidence about existing work should use
the explicit update target; semantic episode boundaries still require review.

Redaction covers recognizable credentials, tokens, JWTs, private keys, URL
userinfo, email addresses, wallet/opaque identifiers and common local paths.
Mappings retain original spans locally; `original_excerpt_json_sha256` fingerprints
the canonical JSON string encoding of the selected original excerpt. Suspicious
or mixed-company markers produce exclusions. Pattern matching cannot prove all
secrets or company references absent. Unrecognized names, obfuscated secrets and
semantic context need the operator's selection and full-packet review. Do not
approve a packet whose scope or redaction is uncertain.

The `work-interpretation-1` output schema requires project purpose, requested
work, approaches and gaps; stable episode IDs, states, work and limitations; and
profile sections for supported work, problem areas, checks, unfinished work and
attribution. Every prose claim, including titles, has nonempty citations plus an
optional contradiction list. The server rejects extra fields, invalid shapes,
wrong packet/run bindings, unknown citation IDs, incorrect evidence bases,
cross-project/revision ownership and unsupported states. Check sections require
recorded tool results, operator observations, or explicit gaps.

An assistant completion statement stays an `assistant_claim`. A recorded tool
result is historical evidence, including when it describes a failed check.
`observed_predicate` requires a passed separately supplied operator observation;
it is never whole-feature acceptance. The schema has no verified/shipped/rank
state. Additional conservative wording checks reject out-of-contract verification
claims. These structural checks cannot prove that every model interpretation is
semantically entailed. The user must inspect cited support, contradictions and
limitations. Insufficient evidence is a valid result.

## Independent observations

The form records `functional_check` or `browser_observation`, exact method,
granted local artifact target and revision, expected predicate, actual result,
passed/failed/skipped/inconclusive status, timezone-aware observation timestamp,
limitations, linked episode evidence and a captured UTF-8 proof file with its
SHA256. Provenance is fixed to `operator_attestation`; `app_executed` is false.
This is the operator's account of a check already performed, not an app-executed
check, automatic replay, or a machine verification of the attestation's truth.
Assistant prose alone cannot become one through model output. Proof remains
locally inspectable. A public browser observation supports only its stated
predicate. Existing artifact receipts continue to prove bytes only.

## Codex provider contract

The adapter targets the operator-verified Codex CLI 0.162.1 contract with the
existing ChatGPT login, model `gpt-6-astra` and reasoning `xhigh`. The persisted
provider version identifies this pinned command contract, not a binary version
probe. No real provider was invoked during implementation.

The command uses `exec --ephemeral --ignore-user-config --strict-config
--skip-git-repo-check --json --cd PRIVATE_EMPTY_WORKDIR`, `approval_policy="never"`,
`default_permissions="data_only"`, filesystem `{":root"="deny"}`, network disabled,
`project_doc_max_bytes=0`, and `web_search="disabled"`. It disables apps, plugins,
hooks, memories, chronicle, browser_use, browser_use_external, computer_use,
in_app_browser, in_app_local_automation, multi_agent, skill_search,
skill_mcp_dependency_install, image_generation, remote_plugin, shell_snapshot,
workspace_dependencies, code_mode, code_mode_host, shell_tool, unified_exec,
view_image, auth_elicitation, mentions_v2 and request_permissions_tool. It enables
`skip_host_skill_discovery`, supplies private schema/result paths, and reads the
selected prompt on stdin. It does not use `tools.view_image=false`, legacy
`--sandbox`, API keys, alternative accounts or unrestricted fallback.

The model receives a fixed instruction forbidding all actions and treating all
transcripts as inert untrusted data. This prompt is part of the exact preview.
The native permissions and disabled host provide the capability fence. Schema
output and ephemeral mode alone are not isolation. The client reads its selected
stdin/schema/auth and writes the result. Temporary work stays in per-run 0700
directories with 0600 prompt/schema/result files and is removed after completion.
No provider files are created inside the Store's strict file allowlist.

Success requires exit zero, a completed turn, only allowed message/reasoning
events, matching final-message/result JSON, valid schema and correct binding.
The observed 0.162.1 startup exceptions are the exact timestamped stderr
`ERROR codex_core::tools::router: error=code-mode host is disabled` and two
`item.completed` error messages: the unstable-feature warning naming only
`skip_host_skill_discovery`, and the disabled Code Mode host explanation. The
stdout exceptions must follow `thread.started` and precede `turn.started`, have
the observed item shape, and occur at most once each. Only the timestamp and the
unstable warning's absolute POSIX `config.toml` path vary; the path is inert text
and is never opened. The legacy plain/`WARN:` disabled-host warning remains
accepted only at startup. These messages describe the capability fence; no
feature is enabled and no config key or command flag is added to suppress them.

The collector rejects nonblank stderr read after it has read `turn.started`.
Stdout event order is enforced exactly, but separate stdout/stderr pipes cannot
prove cross-stream write order when bytes are buffered together. Stderr is drained
first when both pipes are ready; a delayed startup read can fail conservatively,
and a later stderr write buffered with unread startup stdout cannot be dated.
Synthetic tests cover the supplied sanitized traces and observed read boundary;
they do not establish compatibility with other diagnostic formats or CLI versions.
Unknown diagnostics, failed turns, auth/config errors, attempted action events,
malformed output and unconfirmed cleanup are failures. HTTP/UI receive fixed
codes, never native stderr. Captured native diagnostics are bounded in memory and
discarded; no diagnostic artifacts survive cleanup.

The standalone adapter owns a new process session/group, signals only that
group on timeout/cancellation, provides 15 seconds of cleanup grace, escalates
within that owned group and reaps its leader. After escalation it allows another
bounded grace period to confirm group disappearance. A transient permission error
while terminated processes await reaping is retried with leader polling; only an
OS report that the group no longer exists confirms cleanup. Persistent denial or
a group still present after the deadline remains a cleanup failure, even if the
leader exited. It does not kill by process name.
The optional trusted supervisor must own/reconcile descendants that deliberately
escape the process group. A denied OS process-control operation is an explicit
cleanup failure, not proof that descendants were reconciled.

## Migration and limits

Opening a version-1 store runs an additive transaction creating packet, run,
episode, episode-evidence, assessment and observation tables and sets
`user_version=2`. Existing source rows, revisions, IDs and pilot receipts are not
rewritten. Earlier assessments remain immutable. Work identity and version
increments persist atomically with a successful run. Interrupted pending runs
become failed on reopen. Existing deletion behavior for native sessions is
unchanged; analysis history retains its snapshots, marked stale when sources
are removed or regrouped. Missing deleted source links cannot be reconstructed.

| Resource | Explicit default/bound |
| --- | --- |
| Inference authority | Disabled unless project allowlisted at startup |
| Concurrent model work | One owned run per store |
| Selected events | 1 to 80, one exact text part per event |
| Selected excerpt | At most 12,000 Unicode characters |
| External JSON packet | At most 160,000 encoded bytes, plus fixed prompt instructions |
| Episodes | At most 12 per project/result |
| Included independent observations | At most 20 |
| Captured observation proof | 1 to 16,000 UTF-8 bytes |
| Model result file | At most 256,000 bytes |
| Captured stdout / stderr | At most 1 MiB each |
| HTTP request body | Existing 1 MiB limit |
| Evidence page | Existing default 50, maximum 100; selections span pages |
| Provider outer timeout | 300 seconds; startup range 1 to 1800 |
| Cleanup grace | 15 seconds standalone, followed by owned-group escalation |

Bounds fail explicitly and never silently truncate input or claim complete
coverage. Assessment history is retained; the current UI retrieves the complete
history for a selected project.

## Operator acceptance after source review

Synthetic seeds/replies prove contracts, not useful model interpretation on real
work. Run the serial suite in a profile that permits the existing safe ancestor
walk and localhost sockets. Then use selected redacted personal evidence in a
separate store with exact grants, inspect the actual full outgoing packet, invoke
the installed provider, inspect substantive claims and citations, attach a real
bounded current-artifact observation, refresh the same episode, compare both
assessments, repeat unchanged without another call, and reopen the store. Repeat
the reusable flow on a different real personal-project holdout.

Browser acceptance remains an operator task: inspect desktop and 390x844 layout,
keyboard labels/targets, source links, pending/failure states and continued
evidence browsing. `tests/analysis_ui_smoke.mjs` checks DOM/action contracts with a
small synthetic DOM; it is not browser or layout proof. The design reference is
not a working or verified outcome.
