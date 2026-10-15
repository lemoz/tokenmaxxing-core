# Tokenmaxxing Core

An independent session-normalization foundation for evidence-backed work profiles.

The core supports Codex JSONL, older Codex `session`/`items` JSON, Claude Code JSONL, and an explicit selected-session Hermes SQLite adapter built against synthetic stores. It preserves provenance, provides conservative canonical message/tool views, and makes missing or unsupported evidence explicit. Native Hermes validation remains pending.

A private local pilot adds persistent imports, project grouping, searchable history, an evidence-linked work ledger and scoped artifact checks. First start is empty. An optional reviewed analysis flow interprets selected work evidence with linked support and uncertainty. It does not verify personal expertise, score users, or rank token usage. Rankings and public launch are outside this pilot.

The explicit [`catalog` command](docs/project-discovery.md) maps metadata JSONL batches into conservative project candidates with identity coverage, provenance and operator aliases.

## Private evidence workbench

```sh
python3 -m tokenmaxxing serve \
  --store /absolute/private/pilot \
  --allow-source /explicit/history/file-or-root \
  --allow-artifact /explicit/artifact/file-or-root \
  --port 8765 --open-browser
```

The parent directory must already exist. The pilot creates its storage root with mode 0700 and its SQLite files with mode 0600. Storage must be outside the source repository. Existing unsafe permissions, links and unrelated directory contents are refused; nothing is automatically chmodded. Use canonical absolute paths without symlinks (on macOS, `/private/tmp`, not `/tmp`). No read roots are granted by default.

The server binds only `127.0.0.1`. Each process has a fresh bearer token, passed to the browser in a URL fragment and removed from the address bar. The browser keeps it in session storage for reloads. `--open-browser` avoids printing the private URL; without that flag an interactive terminal prints it. Redirected output contains no token. Stop with Ctrl-C and relaunch to reopen the persistent store; reopen the new private launch URL after a restart.

Read [private pilot operation and acceptance](docs/private-pilot.md) for storage semantics, the deterministic **synthetic** fixture recipe, exact browser controls, security boundaries and pending native/browser/review checks. Import only histories you are authorized to read; native Hermes compatibility is not yet verified.

## Run locally

Python 3.9+ standard library on POSIX (macOS/Linux for the private pilot). No package installation, provider connection or cloud service is needed. File normalizers remain independently useful; Hermes uses read-only SQLite. The pilot uses local HTTP and a separate SQLite store. CI exercises Python 3.11.

```sh
git clone https://github.com/lemoz/tokenmaxxing-core.git
cd tokenmaxxing-core
python3 -m unittest discover -s tests -v
```

## Normalize and inspect

Choose an existing private output directory outside this repository, with mode 0700. Use a new output filename; existing destinations are refused, including symlinks and hard links.

```sh
python3 -m tokenmaxxing normalize --source /absolute/source.jsonl --format codex-jsonl --output /absolute/private/session.normalized.jsonl
python3 -m tokenmaxxing inspect --source /absolute/private/session.normalized.jsonl
```

For legacy `session`/`items` JSON, select `--format codex-legacy-json` explicitly.

For a Claude main session or subagent file, select `--format claude-code-jsonl`:

```sh
python3 -m tokenmaxxing normalize --source /absolute/claude-source.jsonl --format claude-code-jsonl --output /absolute/private/claude.normalized.jsonl
python3 -m tokenmaxxing inspect --source /absolute/private/claude.normalized.jsonl
```

Claude string and ordered-list message content stay intact. Ordered `tool_use`
and `tool_result` blocks also appear in each message's `interactions` list, with
exact source pointers. Summary `tool_interactions` and `tool_interaction_types`
count these separately; a message containing tools remains one source-record
event. Codex artifacts and summary semantics are unchanged.

Claude root identity uses observed `sessionId`; a subagent additionally needs
consistent `agentId` and `isSidechain` evidence. Root and child identities stay
distinct. A child's `parent_session_id` means observed main session context,
not verified spawning or immediate parent-agent ancestry. `parentUuid` remains
a record edge. Filenames and the unsupported `session_id` alias do not establish
identity. Missing or ambiguous evidence leaves identity unresolved; conflicting
native IDs or sidechain declarations fail safely. See the contract for exact rules.

Output is private JSONL: a session header, one event per input record/item (including metadata and unsupported records), and a structural summary footer. Output files are created with mode 0600. The CLI prints only a content-free structural summary. Never direct transcript content to stdout, git or a shared report.

Exit codes:

- 0: parsed with no known coverage warnings.
- 2: failed; no normalized output published by the normalizer.
- 3: output produced with explicit missing or unsupported evidence.

Neither exit 0 nor a source status means successful work or a complete work history. Repeated unchanged inputs and unchanged copies have stable event identities. Standalone normalization does not itself maintain an ingestion store. The separate pilot deduplicates explicit native identities within provider/format and retains immutable source revisions. JSONL is processed incrementally; legacy JSON is parsed in memory.

Explicit selected Hermes normalization:

```sh
python3 -m tokenmaxxing normalize --source /explicit/synthetic-hermes.sqlite3 \
  --format hermes-sqlite --selected-session synthetic-hermes \
  --output /absolute/private/hermes.normalized.jsonl
python3 -m tokenmaxxing inspect --source /absolute/private/hermes.normalized.jsonl
```

Hermes output always includes `native_validation_pending` and exits 3. The selected snapshot digest covers the complete retained session/message rows and column inventory, not the SQLite file or other sessions. No schema version is asserted as current native proof. See [Hermes snapshot details](docs/private-pilot.md#hermes-snapshot-contract).

## Private source validation

Inspection verifies the normalized envelope and recomputes its summary, not its authenticity against original sources. For an explicitly authorized source:

```sh
python3 scripts/validate_source.py --source /absolute/source.jsonl --format codex-jsonl --output-dir /absolute/private/proof
```

The output directory must be new, under an existing parent. The validator creates it with mode 0700, writes private artifacts, compares source records and canonical fields/blocks to their locators, repeats imports, checks copied-input identities, and verifies the original source was unchanged. It never prints transcript content and does not discover other files.

The same validator accepts `--format claude-code-jsonl` and checks message,
relationship and tool-interaction fields and pointers. Its source-equality path
requires valid object-shaped JSONL records; it fails on invalid lines that the
normalizer itself retains. Only the operator should run it against authorized
private histories. Synthetic checks do not establish native-file or corpus-wide
correctness.

Claude metadata retention is not deep semantic support. Unknown records/blocks,
images/documents, redaction, summaries/compaction and progress/tool projections
remain explicit gaps. Thinking text and opaque signatures are retained; omitted
or redacted reasoning cannot be recovered. Tool outputs and source status fields
never prove success. This slice does not reconstruct cross-file graphs, stitch
sessions or perform global deduplication.

Read [the normalization contract](docs/normalization-contract.md) before interpreting results. All committed tests use synthetic inputs. Original histories, normalized content and private validation reports must stay outside the repository.

## Contributing

Use feature branches, focused commits and pull requests. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development flow and validation expectations.

## Project interpretation

The private pilot also supports explicitly reviewed evidence packets, an optional
fenced existing-account Codex analyzer, stable work episodes, independently
observed outcome attestations and an evolving evidence-linked profile. Inference
is disabled by default. See [the analysis contract and operator flow](docs/project-analysis.md).
