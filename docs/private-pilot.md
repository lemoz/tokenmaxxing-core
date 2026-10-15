# Private pilot operation and acceptance

This is source for a private local evidence workbench. Its implementation is not evidence of native-history correctness, browser acceptance, independent review, CI publication or merge. Native Hermes compatibility has not yet been validated. Import only explicitly authorized sources and never use the UI/CLI to route around an access restriction. Source publication and merge require the relevant approval.

## Startup

Run from the source checkout with Python 3.9+ and its standard library on macOS/Linux. The pilot uses POSIX permissions, `O_NOFOLLOW`, directory descriptors and `flock`. Import/history/outcome browsing uses no pip/npm install, external assets, model call or provider API. The optional [reviewed analysis flow](project-analysis.md) sends an explicitly previewed packet through the configured existing-account Codex CLI only after Analyze. It is disabled by default.

```sh
python3 -m tokenmaxxing serve --store /absolute/private/pilot \
  --allow-source /explicit/selected/history.jsonl \
  --allow-source /explicit/selected/synthetic-hermes.sqlite3 \
  --allow-artifact /explicit/artifacts --port 8765 --open-browser
```

Each repeated grant is an exact regular file or an existing directory root. No default roots, recursive discovery or browser file picker is supplied. Paths must be absolute, without `..`, symlink components or hardlinked files. Specify canonical macOS paths: `/private/tmp`, not `/tmp`. The artifact grant may include a directory so a missing selected artifact can yield a failed observation. An exact-file grant must exist at startup.

The store parent must exist. A new root is mode 0700; existing roots must be owned by the current user with exactly that mode. Only `pilot.sqlite3`, its SQLite sidecars and `pilot.lock` are accepted. Files must be owned, single-link regular files with mode 0600. The product does not repair unsafe permissions or delete unfamiliar contents. An unfinished staging directory after process termination causes startup to fail closed; the operator must inspect it rather than trigger automatic cleanup. Normal success and handled failures remove their staging artifacts.

The process locks this store exclusively; a second server fails. SQLite transactions serialize imports, and idempotent identities protect repeated requests. The single HTTP worker deliberately favors a small local pilot over concurrent UI throughput. Imports may occupy it until normalization finishes. No arbitrary source/message size cap is imposed; the request-form body limit is 1 MiB and history pages are at most 100 records. Those limits never truncate an imported source.

Only `127.0.0.1` can be bound. `--port 0` chooses an ephemeral loopback port. `--open-browser` opens the private fragment URL; the fragment is never in an HTTP request. The UI cleans the address bar and keeps authorization in tab session storage for reloads. Every process generates a different token. Without `--open-browser`, an interactive terminal shows the private URL; redirected startup output is content-free and does not print the token. Do not share the interactive URL. Restart with the same store, open the new launch URL, and all stored evidence remains available. Ctrl-C closes the owned HTTP socket and store lock.

## Identity, revisions and counts

The existing normalizer still writes the existing Codex/Claude envelope and event identities. The optional `selected_session` and source-opening hooks do not change the default file paths' bytes or summary semantics.

The pilot hashes `[provider, format, normalized session identity]` for its session key. Claude main and subagent identities are distinct. If the normalizer cannot resolve identity, the pilot explicitly falls back to `[provider, format, source revision]`. A changed unresolved source therefore cannot be safely joined to its earlier form; it becomes another context. There is no inferred path identity or cross-provider reconciliation.

Each session has immutable revisions identified by its key and source digest. Importing the same source, a copy, or a retry adds no records. Copy paths are retained as additional references. Importing a changed source adds a revision of the same resolved session. The revision in the most recent successful import is current, including when the user explicitly reimports older bytes; this is selection order, not proof of chronological newness. Current evidence and profile counts use that one revision only.

Original records, metadata, source locators and unknown fields are preserved inside the private SQLite store. A successful import deletes its temporary normalized staging file after transactional insertion. Failed normalization or insertion cannot leave a counted session, half project or dangling event. The process never executes source commands, source instructions or URLs.

A duplicate import with a different project label preserves the existing grouping. Use **Correct grouping → Move this session** or **Correct project label** to change labels deliberately. Projects are derived from existing sessions; duplicates cannot create empty projects. Moving a session carries all its revisions and linked outcomes to the corrected grouping without changing evidence identities.

History pages disclose their total and permit navigation across all matching stored records. Current evidence is the default; selecting a session exposes every retained revision. Source links from outcomes reopen the exact revision even after it becomes historical. Search covers canonical messages, tool interactions, locators and raw retained fields, including unsupported records.

The profile retains its source ledger and now adds optional substantive model interpretations, work episodes and assessment history above it. It separates recorded user-role text, observed messages/tools, retained metadata/unsupported evidence, model interpretations, operator observations and byte receipts. A user-role tool-result-only message is not intent. Explicit inactive, compacted, summary and Claude `isMeta` evidence is not new user intent. Framework messages and historical guardian instructions do not establish human intent, accomplishments or current authorization. Role labels cannot establish who authored a message or ownership of participants. Counters describe persisted records, work episodes and receipts, never expertise, rankings or token scores. See the [version-2 migration and analysis contract](project-analysis.md).

## Hermes snapshot contract

`--format hermes-sqlite --selected-session ID` is required. The adapter accepts physical `sessions` and `messages` tables with a single declared `id` primary key; it requires `messages.session_id`, `role`, and `content`. Missing optional columns become diagnostics. There is no version gate or claim that public schema version 31 describes the actual native target. Unsupported schemas fail with fixed content-free errors.

SQLite is opened with a `file:` URI, `mode=ro`, `uri=True`, `query_only=ON` and a selected-session read transaction with a 30-second deadline and a bounded lock wait. It performs no checkpoint, VACUUM, journal-mode change, backup, copy, restore or source replacement. It queries only the selected session and its messages, ordered by primary key, including inactive/compacted/summary rows. A session row is one metadata event; every selected message row is one event. Interaction counts are additive, not extra canonical events or accomplishments. Record IDs must be nonempty strings or integers in a consistent ordered domain; conflicting or unsupported identity fails explicitly.

The `hermes-selected-rows-v1` digest is SHA256 over newline-separated canonical JSON: first `[scope, table-column inventory]`, then `["sessions", complete session row]`, then every `["messages", complete selected row]`. Canonical JSON uses the existing sorted-key ASCII encoder, with no import timestamp or source path in identity. `snapshot_row_bytes` counts the encoded row portions, not DB-file size. Unchanged copies and unrelated session edits do not change selected evidence identity. The session metadata and column inventory stay in the header as well as the session metadata event.

Native SQLite values are retained as JSON scalars except BLOBs (`{"sqlite_type":"blob","base64":"..."}`), invalid UTF8 TEXT (same tag with `"text"`), and nonfinite REAL (`{"sqlite_type":"real","hex":"..."}`). This tagged encoding is declared in each header. Unknown BLOBs, durable message/tool UIDs, provider call IDs, linkage and unknown fields are retained; no semantic equivalence between them is inferred. Exact locators specify table, primary key and source-field JSON pointers.

Conservative `tool_calls` interpretation accepts a JSON TEXT list of function calls, retaining the original storage unchanged. Malformed JSON, other container shapes and invalid tool IDs/arguments are gaps. Nested JSON strings are not silently unwrapped. Parsed interaction pointers refer to the decoded `tool_calls` field; raw storage remains separately inspectable. `finish_reason`, `effect_disposition`, agent claims and tool output saying success never verify an outcome. A recorded `parent_session_id` is linkage only, not spawn/ownership proof. Every Hermes summary includes `native_validation_pending`.

The inspector replays Hermes views from complete rows, checks event IDs/order, snapshot digest, envelope and summary. Like existing inspection, it verifies internal consistency, not authenticity against a native source. The existing `scripts/validate_source.py` is a Codex/Claude validator; it is not a Hermes native validator.

## Outcome and deletion scope

Choose one imported session and explicitly link at least one existing evidence ID. Links can point to a historical revision. Artifact checks additionally require an allowed absolute path and a 64-digit expected SHA256. The only successful artifact assertion is **nonempty file bytes match the supplied SHA256 at observation time**. The receipt retains expected/actual hash, byte count, observed time, file path, before/after stat signatures, status and exact links. Content is not copied into the receipt. Changed, empty, missing, inaccessible and mismatched files are failed/unsupported observations. Unsafe or out-of-scope paths are rejected, not read.

Identical checks (same session, evidence set, note, file path, expected/actual hash, size and status) retain the first receipt and observation time. Rechecking does not inflate the ledger or falsely refresh the original timestamp. A different assertion or observed content yields another receipt. Human reports require a nonempty claim and are always labelled **unverified**. Profile counters include all persisted receipts; historical links are marked. A hash match does not prove business success, deployment, cause, agent attribution or expertise.

Remove a session in Import, expand **Remove this imported session**, and type `DELETE ` plus the exact displayed 64-character pilot session key. Deletion cascades through all that session's imported revisions, source references, events and linked checks. Original sources and artifacts are never deleted. No secure-erasure guarantee is made for SQLite/filesystem slack or external backups. Group corrections are separate from deletion.

## HTTP and filesystem boundaries

All private API routes require the per-process bearer token. Host must exactly match `127.0.0.1:PORT`. Write requests additionally require the exact browser Origin and `Content-Type: application/json`; foreign Origins, cross-site fetch metadata, duplicate query parameters, invalid IDs and unexpected fields are rejected. GET does not mutate. There is no wildcard CORS. Static assets have no token or private content. Responses carry `no-store`, `nosniff`, no-referrer, frame denial and a CSP with local scripts/styles/connect only. There are no inline scripts, remote assets or dynamic HTML parsing of recorded text.

Errors are fixed codes; access logging and request tracebacks are suppressed. The browser uses DOM text nodes and form values for all source values. Authorization is local-process access control, not protection against an already-compromised browser, same-user privileged process or arbitrary concurrent filesystem adversary. Source JSON files are opened through descriptor-relative, no-symlink traversal and checked for changes. SQLite is opened by the standard library's path-based API, with file-identity checks before/after opening and after row reads; the library does not expose a fully descriptor-relative SQLite VFS. This is a material local threat-model limitation, not a sandbox claim. Native paths must still be operator-authorized.

## Deterministic synthetic browser recipe (operator)

Choose a **new**, external-to-source private fixture path under an existing canonical parent. The following paths are examples the operator can create deliberately; the fixture script refuses existing directories and never reads histories.

```sh
python3 scripts/make_synthetic_pilot.py --directory /private/tmp/tokenmaxxing-synthetic-fixtures
python3 -m tokenmaxxing serve \
  --store /private/tmp/tokenmaxxing-synthetic-pilot \
  --allow-source /private/tmp/tokenmaxxing-synthetic-fixtures \
  --allow-artifact /private/tmp/tokenmaxxing-synthetic-fixtures \
  --port 8765 --open-browser
```

The script prints the deterministic artifact SHA256 and selected Hermes ID. The store starts empty; it never imports the fixtures automatically. Synthetic inputs include Codex original/copy/revision, legacy JSON, Claude main/child/tool-result-only content, a modern and minimal Hermes store, inert script/image text, a nonempty artifact and an empty artifact. No real native file names or contents are used.

| Browser action | Stable control / expected observation |
| --- | --- |
| Open views at 1440×1000 and 390×844 | `[data-view="import"]`, `[data-view="history"]`, `[data-view="profile"]`, `[data-view="outcomes"]`; check focus, 44px targets and horizontal overflow |
| Import selected Codex | `#import-form`, `#source-format=codex-jsonl`, `#source-path=.../synthetic-codex.jsonl`, `#import-project=Synthetic Lantern`; submit |
| Import legacy / Claude main / child | Corresponding fixture filename and explicit format; main and child are separate contexts |
| Import synthetic Hermes | `#source-format=hermes-sqlite`, `#source-path=.../synthetic-hermes.sqlite3`, `#selected-session=synthetic-hermes`; native-validation gap stays visible |
| Repeat/copy | **Repeat import** or select `synthetic-codex-copy.jsonl`; zero new records and no extra session/project |
| Search and inspect | `#history-query`, `#history-form`; `.record[data-event-id]`, `#record-detail`; retained source text remains inert, no external image request |
| Correct grouping | In Import, `[id^="move-"] [name="project"]`; submit; profile follows without evidence identity changes |
| Link outcome | In detail, `[data-action="link-outcome"]`; `#outcome-session`, `#outcome-revision`, `[name="evidence"]`; at least one selected |
| Artifact check | `#outcome-kind=check`, `#artifact-path=.../synthetic-artifact.txt`, `#expected-sha256` from fixture output, submit `#outcome-form` |
| Failures | Replace expected hash with 64 zeroes, or use `synthetic-empty.txt` / `synthetic-missing.txt`; receipt is never a match; no expected hash is rejected |
| Human report | `#outcome-kind=report`, `[name="claim"]`; receipt and profile say unverified |
| Changed revision | Import `synthetic-codex-revision.jsonl`; current record count becomes 6 for that session; old check retains historical exact links |
| Change project after linking historical evidence | Open an old receipt's exact evidence, choose **Link an outcome**, then change `#global-project` to a second imported project; the session/revision/evidence selection resets and that project's current evidence appears. Also open another project's full outcome ledger from Work profile; no prior project's evidence remains selected. |
| Pagination | `[data-page="next"]`, `[data-page="previous"]`; totals disclosed; evidence selection survives its pages |
| Reload/restart | Reload keeps persisted counts; restart same store and use new launch URL; receipts, grouping and history persist |
| Delete | `[id^="delete-"] [name="confirmation"]`; type exact displayed `DELETE <session-key>` and submit; selected context/revisions/checks disappear, fixture files remain |

A synthetic browser acceptance verdict must be recorded by the operator after actual interaction. Static/syntax tests and HTTP tests do not establish responsive pixel quality or the complete browser flow. No browser verdict is claimed here.

## Verification and remaining gates

Run focused adapter/store/HTTP tests first, then the established suite directly and serially:

```sh
python3 -m unittest discover -s tests -p 'test_hermes.py' -v
python3 -m unittest discover -s tests -p 'test_pilot_store.py' -v
python3 -m unittest discover -s tests -p 'test_pilot_http.py' -v
python3 -m unittest discover -s tests -v
```

Tests generate only synthetic inputs and temporary private stores. HTTP tests bind ephemeral loopback ports, stop their exact owned servers and close stores. Startup tests invoke the real `python -m tokenmaxxing serve` CLI, inspect only a content-free readiness line, and stop that exact child. One bounded concurrency test issues two repeat imports against the same transaction lock; the suite itself runs serially.

For a builder restricted to workspace-only writes, a byte-identical source/test snapshot can be placed under ignored `.scratch/verification-source`, with `TMPDIR` set to sibling `.scratch/runtime`. This separates synthetic runtime data from the executing source snapshot while retaining all builder writes in the authorized workspace. Production refuses store roots under its executing source tree. This test arrangement is not authorization for real private data anywhere under the original repository. Remove only these explicitly owned synthetic scratch directories after verification.

Operator acceptance IDs remain: `three_adapters`, `history_browser`, `project_profile`, `outcome_update`, `stable_private_imports`, `local_browser_proof`, `reviewed_integrated_source`, `usable_handoff`. Synthetic tests provide bounded implementation evidence only. Native Hermes support, actual private target-format proof, real-browser acceptance, independent Opus VERIFY/Astra fallback review, source-only PR/CI and merge remain operator work. Missing native proof must not be relabelled as completed `three_adapters` or a finished goal.
