from pathlib import Path
from copy import deepcopy
import errno
import itertools
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from analysis_fixtures import STARTUP_ITEMS, STARTUP_STDERR, seed
from tokenmaxxing.analysis_contract import validate
from tokenmaxxing.analysis_provider import CodexProvider, DISABLED
from tokenmaxxing.pilot_store import Store
from tokenmaxxing.records import NormalizationError, encode


class StartupProtocolTests(unittest.TestCase):
    def events(self, startup=()):
        return [{"type": "thread.started", "thread_id": "synthetic"}, *deepcopy(startup),
                {"type": "turn.started"},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "intermediate"}},
                {"type": "item.completed", "item": {"type": "agent_message", "text": '{"synthetic":true}'}},
                {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]

    def protocol(self, events, stderr=""):
        return CodexProvider._protocol({"stdout": ("\n".join(map(encode, events)) + "\n").encode(),
                                        "stderr": stderr.encode()})

    def test_observed_startup_items_and_stderr(self):
        for startup, stderr in ((STARTUP_ITEMS, ""), ([], STARTUP_STDERR), (STARTUP_ITEMS, STARTUP_STDERR),
                                (STARTUP_ITEMS[:1], ""), (STARTUP_ITEMS[1:], "")):
            with self.subTest(startup=len(startup), stderr=bool(stderr)):
                self.assertEqual(self.protocol(self.events(startup), stderr), '{"synthetic":true}')

    def test_timestamp_and_inert_config_path_variation(self):
        startup = deepcopy(STARTUP_ITEMS)
        startup[0]["item"]["message"] = startup[0]["item"]["message"].replace(
            "/Users/example/.codex/config.toml", "/synthetic/config home/config.toml")
        self.assertEqual(self.protocol(self.events(startup), STARTUP_STDERR.replace(
            "2026-10-15T16:40:50.948348Z", "2027-01-02T03:04:05Z")), '{"synthetic":true}')

    def test_simplified_warning_is_still_accepted_at_startup(self):
        for message in ("code-mode host disabled", "WARN: code-mode host disabled"):
            with self.subTest(message=message):
                self.assertEqual(self.protocol(self.events(), message), '{"synthetic":true}')
                self.assertEqual(self.protocol(self.events([{"type": "warning", "message": message}])),
                                 '{"synthetic":true}')

    def test_wrong_router_feature_and_other_diagnostics_fail(self):
        for stderr in (STARTUP_STDERR.replace("tools::router", "other::router"),
                       STARTUP_STDERR.replace("host is disabled", "host failed"),
                       STARTUP_STDERR.replace("ERROR", "WARN"),
                       "WARN other::router: code-mode host disabled", "ERROR: code-mode host disabled",
                       STARTUP_STDERR + "\nunknown synthetic warning SECRET"):
            with self.subTest(stderr=stderr):
                with self.assertRaisesRegex(NormalizationError, "^analysis_provider_unrecognized_diagnostic$"):
                    self.protocol(self.events(), stderr)
        for message in (STARTUP_ITEMS[0]["item"]["message"].replace("skip_host_skill_discovery", "code_mode"),
                        STARTUP_ITEMS[0]["item"]["message"].replace("skip_host_skill_discovery", "skip_host_skill_discovery, code_mode"),
                        STARTUP_ITEMS[1]["item"]["message"] + " SECRET", "unknown synthetic error SECRET"):
            event = deepcopy(STARTUP_ITEMS[0]); event["item"]["message"] = message
            with self.subTest(message=message):
                with self.assertRaisesRegex(NormalizationError, "^analysis_provider_protocol_error$"):
                    self.protocol(self.events([event]))

    def test_malformed_json_and_non_utf8_are_rejected(self):
        for raw in (b'not json\n', b'{"type":"turn.started","type":"turn.completed"}\n',
                    b'{"type":NaN}\n', b'\xff\n', '{"type":"turn.started"}'.encode("utf-16")):
            with self.subTest(raw=raw):
                with self.assertRaises((ValueError, UnicodeError)):
                    CodexProvider._protocol({"stdout": raw, "stderr": b""})

    def test_known_diagnostics_require_startup_position_and_exact_item_shape(self):
        warning = {"type": "warning", "message": "code-mode host disabled"}
        for event in [*STARTUP_ITEMS, warning]:
            for offset in (0, 2, 4, 5):
                events = self.events(); events.insert(offset, deepcopy(event))
                with self.subTest(kind=event["type"], offset=offset):
                    with self.assertRaises(NormalizationError): self.protocol(events)
        for change in ({"type": "item.started"}, {"type": "item.updated"},
                       {"extra": "unexpected"}, {"item": None},
                       {"item": {**STARTUP_ITEMS[0]["item"], "command": "synthetic"}},
                       {"item": {**STARTUP_ITEMS[0]["item"], "id": None}}):
            event = deepcopy(STARTUP_ITEMS[0]); event.update(change)
            with self.subTest(change=change):
                with self.assertRaises(NormalizationError): self.protocol(self.events([event]))
        with self.assertRaises(NormalizationError):
            self.protocol(self.events([STARTUP_ITEMS[0], STARTUP_ITEMS[0]]))

    def test_actions_failures_and_late_events_still_fail(self):
        for event, code in (({"type": "turn.failed"}, "failed_turn"), ({"type": "error"}, "failed_turn"),
                            ({"type": "item.started", "item": {"type": "command_execution"}}, "action_forbidden"),
                            ({"type": "item.completed", "item": {"type": "error", "message": "SECRET"}}, "action_forbidden")):
            events = self.events(STARTUP_ITEMS); events.insert(-1, event)
            with self.subTest(event=event):
                with self.assertRaisesRegex(NormalizationError, "^analysis_provider_" + code + "$"):
                    self.protocol(events)
        for event in self.events():
            with self.subTest(late=event["type"]):
                with self.assertRaises(NormalizationError): self.protocol(self.events() + [event])

    def test_malformed_warning_uses_fixed_failure_code(self):
        for message in (None, {}, [], 42):
            with self.subTest(message=message):
                with self.assertRaisesRegex(NormalizationError, "^analysis_provider_protocol_error$"):
                    self.protocol(self.events([{"type": "warning", "message": message}]))

    def test_stderr_observed_after_turn_start_is_rejected(self):
        events = self.events()
        stdout = ("\n".join(map(encode, events)) + "\n").encode()
        boundary = len(("\n".join(map(encode, events[:2])) + "\n").encode())
        for diagnostic in (STARTUP_STDERR, "code-mode host disabled"):
            stderr = (diagnostic + "\n").encode()
            with self.subTest(diagnostic=diagnostic):
                with self.assertRaisesRegex(NormalizationError, "^analysis_provider_unrecognized_diagnostic$"):
                    CodexProvider._protocol({"stdout": stdout, "stderr": stderr}, [(len(stderr), boundary)])


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fake-provider-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / "store", [self.root], [self.root])
        self.addCleanup(self.store.close)
        self.selection = seed(self.store, self.root)
        self.script = Path(__file__).with_name("fake_codex.py").resolve()

    def provider(self, scenario="ok", timeout=5, extra=()):
        return CodexProvider(self.root / "provider", executable=(sys.executable, str(self.script), scenario, *extra), timeout=timeout, cleanup_grace=1)

    def packet(self, provider):
        self.store.analysis.configure(["Alpha"], provider, [self.root])
        return self.store.analysis.preview("Alpha", self.selection)["packet"]

    def test_exact_native_command_fence_prefix_and_no_legacy_fallback(self):
        provider = self.provider()
        args = provider.argv("/private-empty", "/private-schema", "/private-result", "/private-prompt")
        for flag in ("--ephemeral", "--ignore-user-config", "--strict-config", "--skip-git-repo-check", "--json"):
            self.assertIn(flag, args)
        for feature in DISABLED:
            self.assertIn(["--disable", feature], [args[i:i+2] for i in range(len(args)-1)])
        self.assertNotIn("--sandbox", args)
        self.assertNotIn("tools.view_image=false", args)
        provider.prefix = ("trusted-supervisor", "--prompt", "{prompt_path}", "--cwd", "{cwd}", "--")
        argv = provider.argv("/private-empty", "/private-schema", "/private-result", "/private-prompt")
        self.assertEqual(argv[:6], ["trusted-supervisor", "--prompt", "/private-prompt", "--cwd", "/private-empty", "--"])
        self.assertRaises(NormalizationError, CodexProvider, self.root / "provider", runner_prefix=['bad-{unknown}'])

    def test_fake_executable_success_startup_warning_and_private_cleanup(self):
        for scenario in ("ok", "warning", "startup"):
            with self.subTest(scenario=scenario):
                provider = self.provider(scenario); packet = self.packet(provider)
                value = provider.analyze(packet, threading.Event())
                validate(value, packet)
                self.assertEqual(list(provider.root.iterdir()), [])
                self.store._check_files()

    def test_startup_fake_still_requires_schema_final_binding_and_safe_turn(self):
        for scenario, code in (("startup_schema", "analysis_invalid_schema"),
                               ("startup_mismatch", "analysis_provider_final_result_mismatch"),
                               ("startup_action", "analysis_provider_action_forbidden"),
                               ("startup_failed", "analysis_provider_failed_turn"),
                               ("startup_late", "analysis_provider_action_forbidden"),
                               ("late_stderr", "analysis_provider_unrecognized_diagnostic"),
                               ("startup_after_completed", "analysis_provider_protocol_error"),
                               ("startup_malformed", "analysis_provider_invalid_or_unavailable")):
            with self.subTest(scenario=scenario):
                provider = self.provider(scenario); packet = self.packet(provider)
                with self.assertRaisesRegex(NormalizationError, "^" + code + "$"):
                    provider.analyze(packet, threading.Event())
                self.assertEqual(list(provider.root.iterdir()), [])

    def test_failed_turn_actions_unknown_errors_malformed_and_binding(self):
        for scenario in ("invalid_json", "schema", "citation", "binding", "exit", "failed", "action", "incomplete", "unknown_error"):
            with self.subTest(scenario=scenario):
                provider = self.provider(scenario); packet = self.packet(provider)
                with self.assertRaises(NormalizationError) as raised:
                    validate(provider.analyze(packet, threading.Event()), packet)
                self.assertNotIn("SECRET", str(raised.exception))
                self.assertEqual(list(provider.root.iterdir()), [])

    def test_timeout_cancellation_and_owned_descendant_cleanup(self):
        pid_file = self.root / "synthetic-child.pid"
        provider = self.provider("timeout_child", timeout=1, extra=(str(pid_file),))
        packet = self.packet(provider)
        with self.assertRaisesRegex(NormalizationError, "timeout"):
            provider.analyze(packet, threading.Event())
        if pid_file.exists():
            pid = int(pid_file.read_text())
            # A just-reparented zombie may persist briefly; a live process must
            # not. kill(0) is conservative and will fail this test if unreaped.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try: os.kill(pid, 0)
                except ProcessLookupError: break
                time.sleep(.05)
            else: self.fail("owned synthetic descendant remains visible")
        else:
            self.fail("fake did not start descendant before timeout")
        cancelled = threading.Event(); cancelled.set()
        with self.assertRaisesRegex(NormalizationError, "cancelled"):
            self.provider("timeout").analyze(packet, cancelled)
        self.assertEqual(list(provider.root.iterdir()), [])

    def test_cleanup_reaps_leader_after_transient_group_probe_denial(self):
        provider = self.provider()
        proc = Mock(pid=123, args=["synthetic-owned-process"])
        provider._signal = Mock(side_effect=[True, PermissionError(errno.EPERM, "synthetic"), False])
        with patch("tokenmaxxing.analysis_provider.time.sleep"):
            provider._cleanup(proc)
        self.assertEqual(proc.poll.call_count, 2)
        proc.wait.assert_called_once_with(timeout=5)
        self.assertEqual([call.args for call in provider._signal.call_args_list],
                         [(proc.pid, signal.SIGTERM), (proc.pid, 0), (proc.pid, 0)])

    def test_active_cancellation_escalates_and_reconciles_owned_descendant(self):
        pid_file = self.root / "synthetic-stubborn-child.pid"
        provider = self.provider("timeout_stubborn_child", extra=(str(pid_file),))
        packet = self.packet(provider)
        cancelled = threading.Event()
        def cancel_after_child_ready():
            deadline = time.monotonic() + 3
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            cancelled.set()
        worker = threading.Thread(target=cancel_after_child_ready)
        worker.start()
        try:
            with patch.object(provider, "_signal", wraps=provider._signal) as signals:
                with self.assertRaisesRegex(NormalizationError, "^analysis_cancelled$"):
                    provider.analyze(packet, cancelled)
                self.assertIn(signal.SIGKILL, [call.args[1] for call in signals.call_args_list])
        finally:
            cancelled.set()
            worker.join(timeout=4)
        self.assertFalse(worker.is_alive())
        self.assertTrue(pid_file.exists(), "fake did not start descendant before cancellation")
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try: os.kill(pid, 0)
            except ProcessLookupError: break
            time.sleep(.05)
        else: self.fail("owned synthetic descendant remains visible after cancellation")
        self.assertEqual(list(provider.root.iterdir()), [])

    def test_denied_cleanup_remains_failure_after_valid_fake_result(self):
        provider = self.provider()
        packet = self.packet(provider)
        with patch.object(provider, "_signal", side_effect=PermissionError(errno.EPERM, "synthetic")):
            with self.assertRaisesRegex(NormalizationError, "^analysis_provider_cleanup_unconfirmed$"):
                provider.analyze(packet, threading.Event())
        self.assertEqual(list(provider.root.iterdir()), [])

    def test_cleanup_requires_group_exit_after_kill_and_retains_denials(self):
        for denied in (False, True):
            with self.subTest(denied=denied):
                provider = self.provider()
                proc = Mock(pid=123, args=["synthetic-owned-process"])
                provider._signal = Mock(side_effect=PermissionError(errno.EPERM, "synthetic")) if denied else Mock(return_value=True)
                with patch("tokenmaxxing.analysis_provider.time.monotonic", side_effect=itertools.count(0, .25)), \
                        patch("tokenmaxxing.analysis_provider.time.sleep"):
                    with self.assertRaises(PermissionError if denied else subprocess.TimeoutExpired):
                        provider._cleanup(proc)
                proc.wait.assert_not_called()
                self.assertIn((proc.pid, signal.SIGKILL), [call.args for call in provider._signal.call_args_list])
                self.assertTrue(all(call.args[0] == proc.pid for call in provider._signal.call_args_list))
