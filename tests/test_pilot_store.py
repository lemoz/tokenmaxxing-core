from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.make_synthetic_pilot import ARTIFACT, fixtures
from tokenmaxxing.outcomes import artifact_check
from tokenmaxxing.pilot_store import Store
from tokenmaxxing.private_paths import REPOSITORY, Scope
from tokenmaxxing.records import NormalizationError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tokenmaxxing-synthetic-pilot-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.inputs = self.root / 'inputs'; self.inputs.mkdir(mode=0o700)
        fixtures(self.inputs)
        self.store = Store(self.root / 'private-pilot', [self.inputs], [self.inputs])
        self.addCleanup(lambda: self.store.close())

    def imported(self, filename='synthetic-codex.jsonl', fmt='codex-jsonl', project='Synthetic Lantern', selected=None):
        return self.store.import_source(str(self.inputs / filename), fmt, project, selected)

    def evidence(self, imported):
        return self.store.evidence(session_id=imported['session_id'])['items']

    def test_empty_first_start_copy_repeat_revision_current_and_reselection(self):
        self.assertEqual(self.store.overview()['sessions'], 0)
        first = self.imported()
        old = self.evidence(first)
        for filename in ('synthetic-codex.jsonl', 'synthetic-codex-copy.jsonl'):
            repeated = self.imported(filename, project='Must not create project')
            self.assertEqual(repeated['session_id'], first['session_id'])
            self.assertEqual(repeated['added_records'], 0)
            self.assertTrue(repeated['grouping_unchanged'])
        changed = self.imported('synthetic-codex-revision.jsonl')
        self.assertEqual(changed['session_id'], first['session_id'])
        overview = self.store.overview()
        self.assertEqual((overview['sessions'], overview['records'], overview['revisions']), (1, 6, 2))
        self.assertEqual(len(overview['projects']), 1)
        self.assertFalse(self.store.detail(old[0]['id'])['current'])
        self.assertEqual(len(self.store.detail(old[0]['id'])['source_references']), 2)
        self.imported()
        self.assertEqual(self.store.overview()['records'], 5)
        self.assertTrue(self.store.detail(old[0]['id'])['current'])
        self.assertEqual(self.store.overview()['revisions'], 2)

    def test_claude_main_child_tool_result_not_intent_and_all_formats(self):
        main = self.imported('synthetic-claude.jsonl', 'claude-code-jsonl')
        child = self.imported('synthetic-claude-child.jsonl', 'claude-code-jsonl')
        self.assertNotEqual(main['session_id'], child['session_id'])
        rows = self.evidence(main)
        self.assertEqual([r['category'] for r in rows], ['intent', 'observed', 'observed'])
        child_header = self.store.detail(self.evidence(child)[0]['id'])['header']
        self.assertEqual(child_header['parent_session_relation'], 'observed_main_session_context')
        self.imported('synthetic-legacy.json', 'codex-legacy-json')
        hermes = self.imported('synthetic-hermes.sqlite3', 'hermes-sqlite', selected='synthetic-hermes')
        rows = self.evidence(hermes)
        self.assertEqual(sum(r['category'] == 'intent' for r in rows), 1)
        self.assertEqual(self.store.overview()['sessions'], 4)

    def test_search_paginate_raw_unsupported_and_no_hidden_ingestion_cap(self):
        result = self.imported()
        page = self.store.evidence(limit=2, offset=2)
        self.assertEqual(page['total'], 5)
        self.assertEqual([r['ordinal'] for r in page['items']], [2, 3])
        self.assertEqual(self.store.evidence(query='tokenmaxxingInjected')['total'], 1)
        self.assertEqual(self.store.evidence(query='synthetic_read', provider='codex')['total'], 1)
        self.assertEqual(self.store.evidence(project='absent')['total'], 0)
        self.assertEqual(self.store.evidence(category_filter='intent')['total'], 1)
        self.assertEqual(self.store.evidence(session_id=result['session_id'], offset=4)['total'], 5)

    def test_search_retains_lone_surrogates_and_normal_unicode(self):
        from tokenmaxxing.normalize import inspect, normalize
        source = self.inputs / 'synthetic-codex.jsonl'
        records = [json.loads(line) for line in source.read_text().splitlines()]
        retained = {'surrogate': '\ud800', 'unicode': 'café 世界'}
        records[0]['unknown_text'] = retained
        source.write_text('\n'.join(json.dumps(row, ensure_ascii=True) for row in records) + '\n')
        output = self.root / 'normalized'; output.mkdir(mode=0o700)
        normalized = output / 'session.jsonl'
        summary = normalize(source, normalized, 'codex-jsonl')
        self.assertEqual(inspect(normalized), summary)
        imported = self.imported()
        rows = self.evidence(imported)
        self.assertEqual(self.store.detail(rows[0]['id'])['body']['source_record']['unknown_text'], retained)
        self.assertEqual(self.store.evidence(query='café 世界')['total'], 1)
        self.assertEqual(self.store.evidence(query='\\ud800')['total'], 1)
        self.assertEqual(self.imported()['added_records'], 0)

    def test_native_session_identity_retains_lone_surrogates(self):
        from tokenmaxxing.normalize import inspect, normalize
        for name, fmt in [('synthetic-codex.jsonl', 'codex-jsonl'),
                          ('synthetic-claude.jsonl', 'claude-code-jsonl')]:
            with self.subTest(fmt=fmt):
                source = self.inputs / name
                records = [json.loads(line) for line in source.read_text().splitlines()]
                if fmt == 'codex-jsonl':
                    records[0]['payload']['id'] = '\ud800'
                else:
                    for record in records:
                        record['sessionId'] = '\ud800'
                source.write_text(''.join(json.dumps(record, ensure_ascii=True) + '\n' for record in records))
                output = self.root / fmt; output.mkdir(mode=0o700)
                normalized = output / 'session.jsonl'
                summary = normalize(source, normalized, fmt)
                self.assertEqual(inspect(normalized), summary)
                header = json.loads(normalized.read_text().splitlines()[0])
                imported = self.imported(name, fmt, fmt)
                session = self.store.sessions(project=fmt)['items'][0]
                self.assertEqual(session['header'], header)
                self.assertEqual(session['native_identity'], header['session_id'])
                self.assertEqual(self.imported(name, fmt, fmt)['added_records'], 0)
                self.assertEqual(len(self.evidence(imported)), summary['events'])

    def test_outcome_match_mismatch_empty_missing_report_and_dedup(self):
        imported = self.imported(); event = self.evidence(imported)[1]['id']
        args = dict(session_id=imported['session_id'], evidence=[event], kind='check',
                    path=str(self.inputs / 'synthetic-artifact.txt'), expected=hashlib.sha256(ARTIFACT).hexdigest())
        first = self.store.record_outcome(**args)
        self.assertTrue(first['receipt']['observation']['match'])
        repeated = self.store.record_outcome(**args)
        self.assertFalse(repeated['added'])
        self.assertEqual(repeated['receipt'], first['receipt'])
        mismatch = self.store.record_outcome(**{**args, 'expected': '0' * 64})
        self.assertEqual(mismatch['receipt']['observation']['status'], 'mismatch')
        for filename, status in [('synthetic-empty.txt', 'empty'), ('synthetic-missing.txt', 'missing')]:
            receipt = self.store.record_outcome(**{**args, 'path': str(self.inputs / filename)})
            self.assertEqual(receipt['receipt']['observation']['status'], status)
            self.assertFalse(receipt['receipt']['observation']['match'])
        for expected in ('', None, 'q' * 64):
            with self.assertRaises(NormalizationError):
                self.store.record_outcome(**{**args, 'expected': expected})
        report = self.store.record_outcome(imported['session_id'], [event], 'report', 'Synthetic human claim')
        self.assertEqual(report['receipt']['status'], 'unverified_human_report')
        self.assertFalse(self.store.record_outcome(imported['session_id'], [event], 'report', 'Synthetic human claim')['added'])
        self.assertEqual(self.store.overview()['outcomes'], 5)
        with self.assertRaises(NormalizationError):
            self.store.record_outcome(imported['session_id'], [], 'check', path=args['path'], expected=args['expected'])

    def test_changed_artifact_cannot_match(self):
        scope = Scope([self.inputs])
        original_open = scope.open
        @contextmanager
        def changing(path):
            with original_open(path) as stream:
                class ChangedStream:
                    def fileno(self): return stream.fileno()
                    def read(self, size):
                        data = stream.read(size)
                        if data:
                            with open(path, 'ab') as out:
                                out.write(b'changed')
                        return data
                # Change only after the first read so the stream terminates.
                wrapped = ChangedStream()
                original_read = wrapped.read
                def once(size):
                    wrapped.read = stream.read
                    return original_read(size)
                wrapped.read = once
                yield wrapped
        with patch.object(scope, 'open', changing):
            result = artifact_check(scope, str(self.inputs / 'synthetic-artifact.txt'), hashlib.sha256(ARTIFACT).hexdigest())
        self.assertEqual(result['status'], 'changed')
        self.assertFalse(result['match'])

    def test_historical_outcome_persistence_move_rename_delete_source_unchanged(self):
        source = self.inputs / 'synthetic-codex.jsonl'; before = source.read_bytes()
        imported = self.imported(); event = self.evidence(imported)[1]['id']
        self.store.record_outcome(imported['session_id'], [event], 'report', 'Synthetic report')
        self.imported('synthetic-codex-revision.jsonl')
        self.assertTrue(self.store.outcomes()['items'][0]['historical'])
        self.store.move(imported['session_id'], 'Corrected synthetic grouping')
        self.store.rename('Corrected synthetic grouping', 'Final synthetic label')
        self.assertEqual(self.store.outcomes()['items'][0]['project'], 'Final synthetic label')
        self.store.close()
        self.store = Store(self.root / 'private-pilot', [self.inputs], [self.inputs])
        self.assertEqual(self.store.overview()['revisions'], 2)
        self.assertEqual(self.store.outcomes()['items'][0]['links'][0]['id'], event)
        with self.assertRaises(NormalizationError):
            self.store.delete(imported['session_id'], 'yes')
        self.store.delete(imported['session_id'], 'DELETE ' + imported['session_id'])
        self.assertEqual(self.store.overview(), {'projects': [], 'sessions': 0, 'records': 0, 'revisions': 0, 'outcomes': 0})
        for table in ('events', 'sources', 'outcome_links'):
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
        self.assertEqual(source.read_bytes(), before)

    def test_concurrent_repeat_safe_and_failed_import_rollback_no_artifact(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.imported(), range(2)))
        self.assertEqual(sum(r['added_records'] for r in results), 5)
        self.assertEqual(self.store.overview()['sessions'], 1)
        self.store.db.execute("CREATE TRIGGER synthetic_fail BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        self.store.db.commit()
        with self.assertRaises(sqlite3.Error):
            self.imported('synthetic-legacy.json', 'codex-legacy-json', 'No half project')
        self.assertEqual(self.store.overview()['sessions'], 1)
        self.assertEqual(self.store.overview()['revisions'], 1)
        self.assertFalse(any(p.name.startswith('import-') for p in self.store.root.iterdir()))

    def test_scope_symlink_hardlink_and_unsafe_storage_rejection(self):
        with self.assertRaisesRegex(NormalizationError, 'path_outside_allowed_scope'):
            self.store.import_source(str(self.root / 'outside.jsonl'), 'codex-jsonl', 'Synthetic')
        link = self.inputs / 'synthetic-link.jsonl'; link.symlink_to(self.inputs / 'synthetic-codex.jsonl')
        with self.assertRaises((NormalizationError, OSError)):
            self.imported(link.name)
        link.unlink()
        hard = self.inputs / 'synthetic-hard.jsonl'; os.link(self.inputs / 'synthetic-codex.jsonl', hard)
        with self.assertRaises(NormalizationError): self.imported(hard.name)
        hard.unlink()
        with self.assertRaisesRegex(NormalizationError, 'private_store_already_open'):
            Store(self.store.root)
        unsafe = self.root / 'unsafe'; unsafe.mkdir(mode=0o755); unsafe.chmod(0o755)
        with self.assertRaises(NormalizationError): Store(unsafe)
        self.assertEqual(unsafe.stat().st_mode & 0o777, 0o755)
        with self.assertRaisesRegex(NormalizationError, 'outside_repository'):
            Store(REPOSITORY / 'private-test-must-not-exist')
        for file in self.store.root.iterdir():
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.store.path.chmod(0o644)
        with self.assertRaisesRegex(NormalizationError, 'private_file_0600_required'):
            self.store.overview()
        self.store.path.chmod(0o600)

    def test_cross_session_evidence_and_unresolved_identity_fallback(self):
        first = self.imported()
        second = self.imported('synthetic-claude.jsonl', 'claude-code-jsonl')
        with self.assertRaises(NormalizationError):
            self.store.record_outcome(first['session_id'], [self.evidence(second)[0]['id']], 'report', 'Synthetic')
        source = self.inputs / 'synthetic-unresolved.jsonl'
        source.write_text('{"type":"unknown"}\n')
        first = self.imported(source.name)
        copied = self.inputs / 'synthetic-unresolved-copy.jsonl'; copied.write_bytes(source.read_bytes())
        second = self.imported(copied.name)
        self.assertEqual(first['session_id'], second['session_id'])
        source.write_text('{"type":"changed-unknown"}\n')
        changed = self.imported(source.name)
        self.assertNotEqual(first['session_id'], changed['session_id'])


if __name__ == '__main__':
    unittest.main()
