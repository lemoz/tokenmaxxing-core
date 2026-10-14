import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from scripts.make_synthetic_pilot import ARTIFACT, HOSTILE, fixtures
from tokenmaxxing.pilot_store import Store
from tokenmaxxing.server import PilotServer, UI


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tokenmaxxing-synthetic-http-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.inputs = self.root / 'inputs'; self.inputs.mkdir(mode=0o700)
        fixtures(self.inputs)
        self.store = Store(self.root / 'private-pilot', [self.inputs], [self.inputs])
        self.addCleanup(self.store.close)
        self.server = PilotServer(self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.thread.join(timeout=3)
        self.server.server_close(); self.store.close()
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, headers=None, method=None, raw=None, authorized=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        defaults = {'Host': self.server.authority}
        if authorized: defaults['Authorization'] = 'Bearer ' + self.server.token
        if data is not None or raw is not None:
            defaults.update({'Content-Type': 'application/json', 'Origin': self.server.origin})
        if headers:
            for key, value in headers.items():
                if value is None: defaults.pop(key, None)
                else: defaults[key] = value
        body = raw if raw is not None else json.dumps(data) if data is not None else None
        try:
            conn.request(method or ('POST' if body is not None else 'GET'), path, body=body, headers=defaults)
            response = conn.getresponse()
            content = response.read()
            return response.status, dict(response.getheaders()), content
        finally:
            conn.close()

    def api(self, path, data=None):
        status, _, raw = self.request(path, data)
        self.assertEqual(status, 200, raw)
        return json.loads(raw)

    def import_codex(self, name='synthetic-codex.jsonl'):
        return self.api('/api/import', {'path': str(self.inputs / name), 'format': 'codex-jsonl', 'project': 'Synthetic Lantern'})

    def test_real_http_auth_host_origin_content_type_schema_and_fixed_errors(self):
        for route in ('/api/overview', '/api/evidence', '/api/sessions', '/api/outcomes', '/api/evidence/detail?event_id=secret'):
            self.assertEqual(self.request(route, authorized=False)[0], 401)
        self.assertEqual(self.request('/api/overview', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/overview', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/overview', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        body = {'path': str(self.inputs / 'synthetic-codex.jsonl'), 'format': 'codex-jsonl', 'project': 'Synthetic'}
        for headers in ({'Origin': None}, {'Origin': 'null'}, {'Origin': 'http://localhost:' + str(self.server.server_port)}, {'Origin': 'https://evil.example'}):
            self.assertEqual(self.request('/api/import', body, headers)[0], 403)
        self.assertEqual(self.request('/api/import', body, {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('/api/import', body, authorized=False)[0], 401)
        self.assertEqual(self.request('/api/delete')[0], 404)
        for route in ('/api/evidence?offset=-1', '/api/evidence?limit=0', '/api/evidence?limit=101',
                      '/api/evidence?provider=unknown', '/api/overview?secret=value', '/api/evidence?query=a&query=b',
                      '/api/revisions?session_id=../../private', '/api/evidence/detail?event_id=secret'):
            self.assertEqual(self.request(route)[0], 400, route)
        for raw in ('{"path":"SECRET","path":"repeat"}', '{"path":NaN}', '[]', '"SECRET"', '{bad SECRET'):
            status, _, response = self.request('/api/import', raw=raw)
            self.assertEqual(status, 400)
            self.assertNotIn(b'SECRET', response)
        self.assertEqual(self.api('/api/overview')['sessions'], 0)

    def test_real_http_import_profile_outcome_historical_move_delete_and_reload(self):
        first = self.import_codex()
        self.assertEqual(self.import_codex('synthetic-codex-copy.jsonl')['added_records'], 0)
        page = self.api('/api/evidence?limit=2&offset=1')
        self.assertEqual(page['total'], 5)
        event = page['items'][0]['id']
        detail = self.api('/api/evidence/detail?' + urlencode({'event_id': event}))
        self.assertEqual(detail['body']['locator']['line'], 2)
        receipt = self.api('/api/outcome', {'session_id': first['session_id'], 'evidence': [event], 'kind': 'check',
            'path': str(self.inputs / 'synthetic-artifact.txt'), 'expected': hashlib.sha256(ARTIFACT).hexdigest()})
        self.assertTrue(receipt['receipt']['observation']['match'])
        self.assertEqual(self.api('/api/overview')['outcomes'], 1)
        self.import_codex('synthetic-codex-revision.jsonl')
        self.assertTrue(self.api('/api/outcomes')['items'][0]['historical'])
        self.assertFalse(self.api('/api/evidence/detail?' + urlencode({'event_id': event}))['current'])
        self.api('/api/move', {'session_id': first['session_id'], 'project': 'Corrected synthetic project'})
        self.assertEqual(self.api('/api/overview')['projects'][0]['project'], 'Corrected synthetic project')
        self.api('/api/rename', {'old': 'Corrected synthetic project', 'new': 'Synthetic renamed'})
        self.assertEqual(self.api('/api/outcomes')['items'][0]['project'], 'Synthetic renamed')
        self.assertEqual(self.request('/api/delete', {'session_id': first['session_id'], 'confirmation': 'yes'})[0], 400)
        self.api('/api/delete', {'session_id': first['session_id'], 'confirmation': 'DELETE ' + first['session_id']})
        self.assertEqual(self.api('/api/overview')['sessions'], 0)
        self.assertEqual(self.api('/api/outcomes')['total'], 0)
        self.assertTrue((self.inputs / 'synthetic-codex.jsonl').exists())

    def test_real_http_all_selected_formats_scope_and_recoverable_import(self):
        for file, fmt, selected in [('synthetic-codex.jsonl', 'codex-jsonl', None),
                                    ('synthetic-legacy.json', 'codex-legacy-json', None),
                                    ('synthetic-claude.jsonl', 'claude-code-jsonl', None),
                                    ('synthetic-claude-child.jsonl', 'claude-code-jsonl', None),
                                    ('synthetic-hermes.sqlite3', 'hermes-sqlite', 'synthetic-hermes')]:
            data = {'path': str(self.inputs / file), 'format': fmt, 'project': 'Synthetic selected formats'}
            if selected: data['selected_session'] = selected
            result = self.api('/api/import', data)
            self.assertTrue(result['new_session'])
        self.assertEqual(self.api('/api/overview')['sessions'], 5)
        status, _, response = self.request('/api/import', {'path': str(self.root / 'SECRET'), 'format': 'codex-jsonl', 'project': 'No half project'})
        self.assertEqual(status, 400); self.assertNotIn(b'SECRET', response)
        self.assertEqual(self.api('/api/overview')['sessions'], 5)
        row = self.api('/api/evidence')['items'][0]
        for data in ({'session_id': row['session_id'], 'evidence': [row['id']], 'kind': 'check', 'path': str(self.inputs / 'synthetic-artifact.txt')},
                     {'session_id': row['session_id'], 'evidence': [], 'kind': 'report', 'claim': 'Synthetic'},
                     {'session_id': row['session_id'], 'evidence': [row['id']], 'kind': 'check', 'expected': 'a' * 64, 'path': str(self.root / 'outside.txt')}):
            self.assertEqual(self.request('/api/outcome', data)[0], 400)
        self.assertEqual(self.api('/api/overview')['outcomes'], 0)

    def test_static_no_secret_inert_sinks_csp_and_untrusted_roundtrip(self):
        for asset in ('/', '/app.js', '/dom.js', '/history.js', '/import.js', '/profile.js', '/outcomes.js', '/style.css'):
            status, headers, body = self.request(asset, authorized=False)
            self.assertEqual(status, 200)
            self.assertNotIn(self.server.token.encode(), body)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
            self.assertNotIn('unsafe-inline', headers['Content-Security-Policy'])
            for forbidden in (b'innerHTML', b'outerHTML', b'insertAdjacentHTML', b'document.write', b'eval(', b'new Function'):
                self.assertNotIn(forbidden, body)
        self.import_codex()
        page = self.api('/api/evidence?' + urlencode({'query': 'tokenmaxxingInjected'}))
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['body']['source_record']['payload']['untrusted'], HOSTILE)
        self.assertNotIn(b'Access-Control-Allow-Origin', str(self.request('/')[1]).encode())


class StartupTests(unittest.TestCase):
    def test_cli_serve_empty_scope_port_binding_no_token_log_and_owned_stop(self):
        with tempfile.TemporaryDirectory(prefix='tokenmaxxing-synthetic-startup-') as tmp:
            root = Path(tmp).resolve()
            proc = subprocess.Popen([sys.executable, '-m', 'tokenmaxxing', 'serve', '--store', str(root / 'private-pilot'), '--port', '0'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                # subprocess startup has an outer bounded wait and only reads a
                # content-free readiness line; never an authentication token.
                import selectors
                with selectors.DefaultSelector() as selector:
                    selector.register(proc.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=10), 'startup did not become ready')
                ready = json.loads(proc.stdout.readline())
                self.assertEqual(ready['status'], 'ready')
                self.assertTrue(ready['origin'].startswith('http://127.0.0.1:'))
                self.assertNotIn('token', ready)
                port = int(ready['origin'].rsplit(':', 1)[1])
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                conn.request('GET', '/api/overview')
                self.assertEqual(conn.getresponse().status, 401); conn.close()
            finally:
                proc.send_signal(signal.SIGINT)
                try: stdout, stderr = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.communicate(); raise
            self.assertEqual(proc.returncode, 0, stderr)
            reopened = Store(root / 'private-pilot')
            try: self.assertEqual(reopened.overview()['records'], 0)
            finally: reopened.close()


if __name__ == '__main__':
    unittest.main()
