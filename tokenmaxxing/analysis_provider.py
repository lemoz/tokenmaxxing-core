"""Existing-account Codex data-only adapter. Never activated by tests implicitly."""

import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time

from .analysis_contract import LIMITS, SCHEMA, VERSION, prompt, shape
from .private_paths import check_private_file, private_directory
from .records import NormalizationError, encode, loads

DISABLED = ("apps plugins hooks memories chronicle browser_use browser_use_external computer_use "
            "in_app_browser in_app_local_automation multi_agent skill_search skill_mcp_dependency_install "
            "image_generation remote_plugin shell_snapshot workspace_dependencies code_mode code_mode_host "
            "shell_tool unified_exec view_image auth_elicitation mentions_v2 request_permissions_tool").split()
CONTRACT = "codex-cli-0.162.1-data-only-v1"
LEGACY_WARNING = r"(?:WARN: )?code-mode host(?: is)? disabled\.?"
HOST_DIAGNOSTIC = ("Code Mode is unavailable because code-mode host is disabled. Code mode will fail closed; "
                   "enable `features.code_mode_host` and install `codex-code-mode-host`.")
UNSTABLE_DIAGNOSTIC = (re.escape(
    "Under-development features enabled: skip_host_skill_discovery. Under-development features are incomplete "
    "and may behave unpredictably. To suppress this warning, set `suppress_unstable_features_warning = true` in ")
    + r"/(?:[^/`\x00-\x1f\x7f]+/)*config\.toml\.")


def private_write(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as stream:
        stream.write(text)


def known_warning(line):
    # Pin the installed router diagnostic, including its ERROR severity. No
    # arbitrary logger/prefix or generic error-item exemption is safe here.
    return isinstance(line, str) and bool(re.fullmatch(
        LEGACY_WARNING + r"|[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z "
        r"ERROR codex_core::tools::router: error=code-mode host is disabled", line))


def startup_diagnostic(item):
    if (not isinstance(item, dict) or set(item) != {"id", "type", "message"}
            or item["type"] != "error" or not isinstance(item["id"], str)
            or not re.fullmatch(r"item_[0-9]+", item["id"]) or not isinstance(item["message"], str)):
        return None
    if item["message"] == HOST_DIAGNOSTIC:
        return "disabled_host"
    if re.fullmatch(UNSTABLE_DIAGNOSTIC, item["message"]):
        return "unstable_feature"
    return None


class CodexProvider:
    def __init__(self, root, runner_prefix=(), executable=("codex",), timeout=300, cleanup_grace=15):
        self.root = private_directory(root)
        self.root_identity = (self.root.stat().st_dev, self.root.stat().st_ino)
        if (not isinstance(runner_prefix, (tuple, list)) or not isinstance(executable, (tuple, list))
                or not executable or any(not isinstance(x, str) or not x or "\x00" in x
                                         for x in [*runner_prefix, *executable])):
            raise NormalizationError("analysis_invalid_trusted_runner")
        if not 1 <= timeout <= 1800 or not 1 <= cleanup_grace <= 60:
            raise NormalizationError("analysis_invalid_provider_timeout")
        for arg in runner_prefix:
            remainder = arg.replace("{prompt_path}", "").replace("{cwd}", "")
            if "{" in remainder or "}" in remainder:
                raise NormalizationError("analysis_invalid_runner_placeholder")
        self.prefix, self.executable = tuple(runner_prefix), tuple(executable)
        self.timeout, self.cleanup_grace = timeout, cleanup_grace
        self.descriptor = {"provider": CONTRACT, "model": "gpt-6-astra", "reasoning": "xhigh",
                           "schema": VERSION, "runner": "trusted_prefix" if runner_prefix else "standalone"}

    def argv(self, cwd, schema, result, prompt_path):
        args = [*self.executable, "exec", "--model", "gpt-6-astra", "--ephemeral", "--ignore-user-config",
                "--strict-config", "--skip-git-repo-check", "--json", "--cd", str(cwd)]
        for config in ('model_reasoning_effort="xhigh"', 'approval_policy="never"',
                       'default_permissions="data_only"', 'permissions.data_only.filesystem={":root"="deny"}',
                       'permissions.data_only.network.enabled=false', 'project_doc_max_bytes=0',
                       'web_search="disabled"'):
            args.extend(["-c", config])
        for feature in DISABLED:
            args.extend(["--disable", feature])
        args.extend(["--enable", "skip_host_skill_discovery", "--output-schema", str(schema),
                     "--output-last-message", str(result), "-"])
        return [a.replace("{cwd}", str(cwd)).replace("{prompt_path}", str(prompt_path)) for a in self.prefix] + args

    @staticmethod
    def _signal(group, sig):
        try:
            os.killpg(group, sig)
            return True
        except ProcessLookupError:
            return False

    def _cleanup(self, proc):
        # Only the new session owned by this invocation, including descendants
        # left behind after its leader exits. Never kill by process name.
        denied = None
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                if not self._signal(proc.pid, sig):
                    proc.wait(timeout=5)
                    return
            except PermissionError as exc:
                denied = exc
            deadline = time.monotonic() + self.cleanup_grace
            while time.monotonic() < deadline:
                proc.poll()
                try:
                    if not self._signal(proc.pid, 0):
                        proc.wait(timeout=5)
                        return
                except PermissionError as exc:
                    # Darwin can report EPERM while the terminated group is
                    # awaiting reaping. Poll again, but require ESRCH: neither
                    # EPERM nor a reaped leader proves descendants are gone.
                    denied = exc
                time.sleep(0.05)
        if denied is not None:
            raise denied
        raise subprocess.TimeoutExpired(proc.args, 2 * self.cleanup_grace)

    def analyze(self, packet, cancelled):
        if cancelled.is_set():
            raise NormalizationError("analysis_cancelled")
        private_directory(self.root, create=False)
        if (self.root.stat().st_dev, self.root.stat().st_ino) != self.root_identity:
            raise NormalizationError("analysis_provider_directory_changed")
        with tempfile.TemporaryDirectory(prefix="analysis-", dir=self.root) as stage:
            base = Path(stage)
            cwd = base / "empty"; cwd.mkdir(mode=0o700)
            schema, result, source = base / "schema.json", base / "result.json", base / "prompt.txt"
            private_write(schema, encode(SCHEMA)); private_write(source, prompt(packet))
            private_write(result, "")
            env = {k: v for k, v in os.environ.items() if k in
                   {"PATH", "HOME", "USER", "LOGNAME", "CODEX_HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL"}}
            chunks = {"stdout": bytearray(), "stderr": bytearray()}
            stderr_positions = []
            proc = None
            try:
                with source.open("rb") as stdin:
                    proc = subprocess.Popen(self.argv(cwd, schema, result, source), stdin=stdin,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env, start_new_session=True)
                    deadline = time.monotonic() + self.timeout
                    with selectors.DefaultSelector() as selector:
                        for name in chunks:
                            pipe = getattr(proc, name)
                            os.set_blocking(pipe.fileno(), False)
                            selector.register(pipe, selectors.EVENT_READ, name)
                        while selector.get_map() or proc.poll() is None:
                            if cancelled.is_set():
                                raise NormalizationError("analysis_cancelled")
                            if time.monotonic() >= deadline:
                                raise NormalizationError("analysis_provider_timeout")
                            if result.stat().st_size > LIMITS["result_bytes"]:
                                raise NormalizationError("analysis_provider_output_limit")
                            # Drain stderr first when both pipes are ready. A
                            # startup diagnostic may already be buffered with
                            # stdout; separate pipes do not establish write order.
                            for key, _ in sorted(selector.select(timeout=0.05), key=lambda ready: ready[0].data):
                                raw = os.read(key.fileobj.fileno(), 65536)
                                if not raw:
                                    selector.unregister(key.fileobj)
                                else:
                                    chunks[key.data].extend(raw)
                                    if key.data == "stderr":
                                        stderr_positions.append((len(chunks["stderr"]), len(chunks["stdout"])))
                                    if len(chunks[key.data]) > 1024 * 1024:
                                        raise NormalizationError("analysis_provider_output_limit")
                    if proc.wait() != 0:
                        raise NormalizationError("analysis_provider_exit_failure")
                final_message = self._protocol(chunks, stderr_positions)
                check_private_file(result)
                if result.stat().st_size > LIMITS["result_bytes"]:
                    raise NormalizationError("analysis_provider_output_limit")
                value = loads(result.read_text())
                shape(value, SCHEMA)
                if loads(final_message) != value:
                    raise NormalizationError("analysis_provider_final_result_mismatch")
                return value
            except NormalizationError:
                raise
            except (OSError, ValueError, UnicodeError, subprocess.SubprocessError, RecursionError):
                raise NormalizationError("analysis_provider_invalid_or_unavailable") from None
            finally:
                if proc is not None:
                    try:
                        try:
                            self._cleanup(proc)
                        except (OSError, subprocess.SubprocessError):
                            # Keep a denied/failed group reconciliation explicit.
                            # Reap the owned leader where possible; never claim
                            # that this alone reconciles all descendants.
                            try:
                                if proc.poll() is None:
                                    proc.kill()
                                proc.wait(timeout=5)
                            finally:
                                raise NormalizationError("analysis_provider_cleanup_unconfirmed") from None
                    finally:
                        proc.stdout.close(); proc.stderr.close()

    @staticmethod
    def _protocol(chunks, stderr_positions=()):
        stderr = chunks["stderr"].decode("utf-8")
        if any(line.strip() and not known_warning(line) for line in stderr.splitlines()):
            raise NormalizationError("analysis_provider_unrecognized_diagnostic")
        thread_started, started, completed, final_message = False, False, False, None
        startup_seen, startup_ids = set(), set()
        stdout_offset, turn_start_offset = 0, None
        for line in bytes(chunks["stdout"]).splitlines(keepends=True):
            stdout_offset += len(line)
            event = loads(line.decode("utf-8"))
            if not isinstance(event, dict):
                raise NormalizationError("analysis_provider_protocol_error")
            kind = event.get("type")
            if kind == "thread.started" and not thread_started and not started:
                if set(event) != {"type", "thread_id"} or not isinstance(event["thread_id"], str) or not event["thread_id"]:
                    raise NormalizationError("analysis_provider_protocol_error")
                thread_started = True
                continue
            if kind == "turn.started" and not started:
                started = True
                turn_start_offset = stdout_offset
            elif kind == "item.completed" and thread_started and not started:
                diagnostic = startup_diagnostic(event.get("item"))
                if (set(event) != {"type", "item"} or diagnostic is None or diagnostic in startup_seen
                        or event["item"]["id"] in startup_ids):
                    raise NormalizationError("analysis_provider_protocol_error")
                startup_seen.add(diagnostic)
                startup_ids.add(event["item"]["id"])
            elif kind == "turn.completed" and started and not completed:
                completed = True
            elif kind in ("item.started", "item.updated", "item.completed") and started and not completed:
                item = event.get("item")
                if not isinstance(item, dict) or item.get("type") not in ("agent_message", "reasoning"):
                    raise NormalizationError("analysis_provider_action_forbidden")
                if kind == "item.completed" and item["type"] == "agent_message":
                    if not isinstance(item.get("text"), str):
                        raise NormalizationError("analysis_provider_protocol_error")
                    final_message = item["text"]
            elif (kind == "warning" and thread_started and not started
                  and set(event) == {"type", "message"} and isinstance(event["message"], str)
                  and re.fullmatch(LEGACY_WARNING, event["message"]) and "legacy" not in startup_seen):
                startup_seen.add("legacy")
            elif kind in ("turn.failed", "error"):
                raise NormalizationError("analysis_provider_failed_turn")
            else:
                raise NormalizationError("analysis_provider_protocol_error")
        if not completed or final_message is None:
            raise NormalizationError("analysis_provider_incomplete_turn")
        previous_end = 0
        for stderr_end, stdout_end in stderr_positions:
            if stdout_end >= turn_start_offset and chunks["stderr"][previous_end:stderr_end].strip():
                raise NormalizationError("analysis_provider_unrecognized_diagnostic")
            previous_end = stderr_end
        return final_message
