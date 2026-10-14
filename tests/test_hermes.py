import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from scripts.make_synthetic_pilot import fixtures
from tokenmaxxing import hermes
from tokenmaxxing.normalize import inspect, normalize
from tokenmaxxing.records import NormalizationError, encode


class HermesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tokenmaxxing-synthetic-hermes-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        fixtures(self.root)
        self.source = self.root / 'synthetic-hermes.sqlite3'

    def normalize(self, name='out', source=None, selected='synthetic-hermes'):
        path = self.root / (name + '.jsonl')
        result = normalize(source or self.source, path, 'hermes-sqlite', selected_session=selected)
        values = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(result, inspect(path))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        return values, result

    def test_complete_selected_rows_blob_fidelity_provenance_and_readonly(self):
        before = self.source.read_bytes()
        before_stat = self.source.stat()
        values, result = self.normalize()
        self.assertEqual(result['events'], 7)
        self.assertEqual(result['tool_interactions'], 2)
        self.assertIn('native_validation_pending', result['session_diagnostics'])
        conn = sqlite3.connect(self.source.as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        for item in values[1:-1]:
            loc = item['locator']
            row = conn.execute('SELECT * FROM ' + loc['table'] + ' WHERE id=?', (loc['primary_key']['id'],)).fetchone()
            self.assertEqual(item['source_record'], hermes.row_value(row))
            self.assertEqual(set(item['source_field_pointers']), set(row.keys()))
            self.assertEqual(item['outcome'], 'unknown')
        self.assertEqual(values[2]['source_record']['display_identity'], {'sqlite_type': 'blob', 'base64': base64.b64encode(b'\x00\xff\x01').decode()})
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.source.stat().st_mtime_ns, before_stat.st_mtime_ns)
        self.assertFalse((self.root / 'synthetic-hermes.sqlite3-journal').exists())
        self.assertEqual(values[0]['source']['digest_scope'], hermes.DIGEST_SCOPE)
        self.assertNotIn('size', values[0]['source'])
        with hermes.snapshot(self.source, 'synthetic-hermes') as (_, _, records):
            self.assertEqual(len(list(records())), 7)

    def test_repeat_copy_selected_digest_not_db_file_or_other_sessions(self):
        first, _ = self.normalize()
        copy = self.root / 'synthetic-hermes-copy.sqlite3'
        shutil.copyfile(self.source, copy)
        second, _ = self.normalize('copy', copy)
        self.assertEqual(first[1:], second[1:])
        conn = sqlite3.connect(self.source)
        conn.execute("UPDATE messages SET content='other synthetic session changed' WHERE id=99")
        conn.commit(); conn.close()
        third, _ = self.normalize('other-change')
        self.assertEqual(first[1:], third[1:])
        conn = sqlite3.connect(self.source)
        conn.execute("UPDATE messages SET content='selected changed' WHERE id=1")
        conn.commit(); conn.close()
        fourth, _ = self.normalize('selected-change')
        self.assertEqual(first[0]['session_id'], fourth[0]['session_id'])
        self.assertNotEqual(first[1]['event_id'], fourth[1]['event_id'])

    def test_minimal_and_unknown_shapes_are_explicit_gaps(self):
        values, result = self.normalize(source=self.root / 'synthetic-hermes-minimal.sqlite3', selected='synthetic-hermes-minimal')
        self.assertIn('optional_schema_absent', result['session_diagnostics'])
        self.assertEqual(len(values), 4)
        conn = sqlite3.connect(self.source)
        conn.execute("UPDATE messages SET tool_calls=?, content=? WHERE id=2", ('{"not":"list"}', b'\xff'))
        conn.commit(); conn.close()
        values, result = self.normalize('unknown')
        self.assertIn('unsupported_tool_calls', result['diagnostics'])
        self.assertIn('malformed_tool_calls', result['diagnostics'])
        self.assertIn('unsupported_role', result['diagnostics'])
        self.assertIn('inactive_evidence', result['diagnostics'])
        self.assertIn('compacted_evidence', result['diagnostics'])
        self.assertIn('summary_evidence', result['diagnostics'])
        self.assertIn('unsupported_content', result['diagnostics'])

    def test_wal_selected_snapshot_and_invalid_text_retention(self):
        writer = sqlite3.connect(self.source)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE messages SET content=CAST(x'ff00' AS TEXT) WHERE id=1")
        writer.commit()
        database_bytes = self.source.read_bytes()
        wal = Path(str(self.source) + '-wal')
        wal_bytes = wal.read_bytes()
        values, _ = self.normalize()
        self.assertEqual(values[2]['source_record']['content'], {'sqlite_type': 'text', 'base64': '/wA='})
        self.assertEqual(self.source.read_bytes(), database_bytes)
        self.assertEqual(wal.read_bytes(), wal_bytes)

    def test_tool_shapes_preserve_storage_and_bounded_diagnostics(self):
        writer = sqlite3.connect(self.source)
        self.addCleanup(writer.close)
        shapes = ['null', '"double encoded"', '[4]', '[{"type":"function","function":{},"id":5}]']
        for index, raw in enumerate(shapes):
            writer.execute('UPDATE messages SET tool_calls=? WHERE id=2', (raw,))
            writer.commit()
            values, summary = self.normalize('shape-' + str(index))
            self.assertEqual(values[3]['source_record']['tool_calls'], raw)
            self.assertTrue(set(summary['diagnostics']) <= hermes.DIAGNOSTICS)
            self.assertIn('unsupported_tool_calls', summary['diagnostics'])

    def test_missing_selection_schema_and_output_safety(self):
        for selected in (None, '', 'absent'):
            with self.assertRaises(NormalizationError):
                normalize(self.source, self.root / 'failed.jsonl', 'hermes-sqlite', selected_session=selected)
            self.assertFalse((self.root / 'failed.jsonl').exists())
        self.normalize()
        with self.assertRaisesRegex(NormalizationError, 'output_already_exists'):
            self.normalize()
        bad = self.root / 'synthetic-bad.sqlite3'
        conn = sqlite3.connect(bad)
        conn.executescript('CREATE TABLE sessions(id TEXT); CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT);')
        conn.close()
        with self.assertRaisesRegex(NormalizationError, 'unsupported_hermes_schema'):
            self.normalize('bad', bad)
        self.assertFalse(any(p.name.startswith('.tokenmaxxing-') for p in self.root.iterdir()))

    def test_inspector_recomputes_rows_views_snapshot_and_summary(self):
        values, _ = self.normalize()
        mutations = [lambda v: v[2]['canonical'].update(content='tampered'),
                     lambda v: v[2]['source_record'].update(content='tampered'),
                     lambda v: v[0]['source'].update(digest_scope='db_file'),
                     lambda v: v[0].update(parent_session_relation='verified_spawn'),
                     lambda v: v[-1].update(events=999)]
        for i, mutate in enumerate(mutations):
            changed = json.loads(json.dumps(values)); mutate(changed)
            path = self.root / ('tamper-' + str(i))
            path.write_text('\n'.join(encode(v) for v in changed) + '\n')
            with self.assertRaises(NormalizationError):
                inspect(path)

    def test_cli_selected_normalize_inspect_and_content_free_errors(self):
        output = self.root / 'cli.jsonl'
        result = subprocess.run([sys.executable, '-m', 'tokenmaxxing', 'normalize', '--source', str(self.source),
            '--format', 'hermes-sqlite', '--selected-session', 'synthetic-hermes', '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 3, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary['events'], 7)
        checked = subprocess.run([sys.executable, '-m', 'tokenmaxxing', 'inspect', '--source', str(output)], capture_output=True, text=True)
        self.assertEqual(json.loads(checked.stdout), summary)
        self.assertNotIn('synthetic-hermes', result.stdout)
        failed = subprocess.run([sys.executable, '-m', 'tokenmaxxing', 'normalize', '--source', str(self.source),
            '--format', 'hermes-sqlite', '--output', str(self.root / 'fail')], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stderr), {'error': 'selected_session_required'})


if __name__ == '__main__':
    unittest.main()
