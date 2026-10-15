"""Deterministic fake executable. No model, network or account calls."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis_fixtures import STARTUP_ITEMS, STARTUP_STDERR, response

scenario = sys.argv[1]
if scenario == "child":
    time.sleep(60)
    sys.exit(0)
if scenario == "stubborn_child":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(sys.argv[2]).write_text(str(os.getpid()))
    time.sleep(60)
    sys.exit(0)
raw = sys.stdin.read()
packet = json.loads(raw.split("UNTRUSTED_SELECTED_DATA_JSON\n", 1)[1])
value = response(packet)
output = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
schema = Path(sys.argv[sys.argv.index("--output-schema") + 1])
assert json.loads(schema.read_text())["additionalProperties"] is False
assert '--sandbox' not in sys.argv
assert 'tools.view_image=false' not in sys.argv
assert 'permissions.data_only.filesystem={":root"="deny"}' in sys.argv
assert sys.argv[-1] == '-'
if scenario == "timeout_child":
    child = subprocess.Popen([sys.executable, __file__, "child"])
    Path(sys.argv[2]).write_text(str(child.pid))
    time.sleep(60)
if scenario == "timeout_stubborn_child":
    subprocess.Popen([sys.executable, __file__, "stubborn_child", sys.argv[2]])
    time.sleep(60)
if scenario == "timeout": time.sleep(60)
if scenario == "invalid_json": output.write_text("not json")
elif scenario == "schema":
    value["extra"] = "invalid"
    output.write_text(json.dumps(value))
else:
    if scenario == "citation": value["project"]["purpose"][0]["citations"] = ['event:' + 'f' * 64]
    if scenario == "binding": value["run_id"] = 'incorrect'
    output.write_text(json.dumps(value))
if scenario == "warning": print("code-mode host disabled", file=sys.stderr)
if scenario.startswith("startup"):
    print(STARTUP_STDERR, file=sys.stderr, flush=True)
if scenario == "unknown_error": print("unexpected synthetic startup error SECRET", file=sys.stderr)
if scenario == "exit": sys.exit(2)
events = [{"type": "thread.started", "thread_id": "synthetic"}]
if scenario.startswith("startup"): events.extend(STARTUP_ITEMS)
events.append({"type": "turn.started"})
if scenario in ("action", "startup_action"): events.append({"type": "item.started", "item": {"type": "command_execution", "command": "synthetic forbidden action"}})
if scenario == "startup_schema":
    value["extra"] = "invalid"
    output.write_text(json.dumps(value))
if scenario == "startup_mismatch": value["run_id"] = "different-final-message"
if scenario == "startup_malformed": print("not json", flush=True)
if scenario == "startup_late": events.extend(STARTUP_ITEMS)
events.append({"type": "item.completed", "item": {"type": "agent_message", "text": "Synthetic intermediate message."}})
events.append({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}})
if scenario in ("failed", "startup_failed"): events.append({"type": "turn.failed", "error": {"message": "private synthetic error SECRET"}})
elif scenario != "incomplete": events.append({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}})
if scenario == "startup_after_completed": events.extend(STARTUP_ITEMS)
for event in events:
    print(json.dumps(event), flush=True)
    if scenario == "late_stderr" and event["type"] == "turn.started":
        time.sleep(.2)
        print(STARTUP_STDERR, file=sys.stderr, flush=True)
