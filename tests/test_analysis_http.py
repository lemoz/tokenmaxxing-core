"""Real loopback HTTP acceptance. A denied socket/Scope path is a failure, not PASS."""
import json
import threading
import time
import unittest
from urllib.parse import urlencode

import test_pilot_http as http_fixture
from analysis_fixtures import seed, SyntheticProvider, response
from tokenmaxxing.outcomes import now


class AnalysisHTTPTests(unittest.TestCase):
    request = http_fixture.HTTPTests.request
    api = http_fixture.HTTPTests.api
    stop = http_fixture.HTTPTests.stop

    def setUp(self):
        http_fixture.HTTPTests.setUp(self)
        self.provider = SyntheticProvider()
        self.store.analysis.configure(["Alpha"], self.provider, [self.inputs])
        self.selection = seed(self.store, self.inputs)

    def wait_run(self, run):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.api('/api/analysis/run?' + urlencode({'run_id': run['run_id']}))
            if result['status'] != 'pending': return result
            time.sleep(.01)
        self.fail('synthetic HTTP analysis timeout')

    def test_http_preview_pending_evidence_failure_static_and_reuse(self):
        gate = threading.Event()
        class Waiting(SyntheticProvider):
            def analyze(self, packet, cancelled):
                gate.wait(3)
                return response(packet)
        self.store.analysis.provider = Waiting()
        preview = self.api('/api/analysis/preview', {'project': 'Alpha', 'selections': self.selection})
        self.assertEqual(self.provider.calls, 0)
        start = self.api('/api/analysis/start', {'packet_id': preview['packet_id']})
        self.assertEqual(start['status'], 'pending')
        try:
            detail = self.api('/api/evidence/detail?' + urlencode({'event_id': self.selection[0]['event_id']}))
            self.assertEqual(detail['project'], 'Alpha')
            self.assertEqual(self.api('/api/analysis?project=Alpha')['runs'][0]['status'], 'pending')
        finally: gate.set()
        self.assertEqual(self.wait_run(start)['status'], 'succeeded')
        self.assertTrue(self.api('/api/analysis/start', {'packet_id': preview['packet_id']})['reused'])
        status, headers, content = self.request('/analysis.js', authorized=False)
        self.assertEqual(status, 200)
        self.assertIn(b'Analyze this exact reviewed packet', content)
        self.assertIn(b'data-event-id', content)
        self.assertIn(b'Analysis pending', content)
        self.assertIn(b'notice(error.message', content)
        self.assertNotIn(b'innerHTML', content)
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/api/analysis?project=Alpha', authorized=False)[0], 401)
        self.assertEqual(self.request('/api/analysis/start', {'packet_id': preview['packet_id'], 'command': 'never'})[0], 400)
        # A server-validated model failure persists, without an assessment.
        self.store.analysis.provider = SyntheticProvider(lambda value: {**value, 'extra': 'SECRET'})
        self.store.analysis.provider.descriptor = {**self.provider.descriptor, 'provider': 'invalid-synthetic'}
        failed = self.wait_run(self.api('/api/analysis/start', {'packet_id': preview['packet_id']}))
        self.assertEqual(failed['status'], 'failed')
        self.assertNotIn('SECRET', json.dumps(failed))
        self.assertEqual(self.api('/api/analysis?project=Alpha')['counts']['assessments'], 1)

    def test_actual_operator_attestation_attachment_through_granted_scope(self):
        preview = self.api('/api/analysis/preview', {'project': 'Alpha', 'selections': self.selection})
        baseline = self.wait_run(self.api('/api/analysis/start', {'packet_id': preview['packet_id']}))
        episode = baseline['result']['episodes'][0]['episode_id']
        proof = self.inputs / 'synthetic-observed-proof.txt'
        proof.write_text('Synthetic manually observed predicate: one stored note returned. No real check performed.\n')
        body = {'kind': 'functional_check', 'method': 'Synthetic operator observation fixture',
            'target_path': str(self.inputs / 'synthetic-artifact.txt'), 'target_revision': 'synthetic-r1',
            'expected_predicate': 'one stored note is returned offline', 'actual_result': 'one stored note returned',
            'status': 'passed', 'observed_at': now(), 'limitations': 'Synthetic fixture only; no actual outcome',
            'provenance': 'operator_attestation', 'proof_path': str(proof), 'evidence': [self.selection[0]['event_id']]}
        attached = self.api('/api/analysis/observation', {'project': 'Alpha', 'episode_id': episode, 'observation': body})
        self.assertFalse(attached['observation']['app_executed'])
        self.assertEqual(attached['observation']['proof'], proof.read_text())
        updated = self.api('/api/analysis/preview', {'project': 'Alpha', 'selections': self.selection,
            'update_episode_id': episode, 'observation_ids': [attached['id']]})
        result = self.wait_run(self.api('/api/analysis/start', {'packet_id': updated['packet_id']}))
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(self.api('/api/analysis?project=Alpha')['counts'], {'episodes': 1, 'assessments': 2, 'observations': 1})
