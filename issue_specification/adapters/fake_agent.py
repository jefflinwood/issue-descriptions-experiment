"""Deterministic stand-in agent for tests. Never calls a model.

Usage: python fake_agent.py MODE [PATCH]
Modes: apply (git-apply PATCH in cwd), noop, fail, sleep (hang until killed,
spawning a child to exercise process-group cleanup), untracked (add a new test
file plus files that must be excluded from capture), commit (apply and commit,
which the harness must still capture), probe (try reading protected paths given
in FAKE_PROBE_TARGETS and report).
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def emit(**record):
    print(json.dumps(record), flush=True)


CLAUDE_EVENTS = os.environ.get("FAKE_EVENT_FORMAT") == "claude"


def claude_finish():
    """Claude Code stream-json shape, including a duplicated streamed message and modelUsage."""
    message = dict(id="msg_fake", model="fake-model", usage=dict(input_tokens=3, cache_creation_input_tokens=20,
                   cache_read_input_tokens=100, output_tokens=7),
                   content=[dict(type="tool_use", id="toolu_fake", name="Bash", input={})])
    emit(type="assistant", session_id="fake-session", message=message)
    emit(type="assistant", session_id="fake-session", message=message)
    emit(type="result", subtype="success", is_error=False, num_turns=2, result="done; assumed q parameter",
         session_id="fake-session", total_cost_usd=0.001,
         usage=dict(input_tokens=3, cache_creation_input_tokens=20, cache_read_input_tokens=100, output_tokens=7),
         modelUsage={"fake-model": dict(inputTokens=3, outputTokens=7)})


def main():
    mode = sys.argv[1]
    patch = sys.argv[2] if len(sys.argv) > 2 else None
    prompt = sys.stdin.read()
    if CLAUDE_EVENTS:
        emit(type="system", subtype="init", session_id="fake-session", model="fake-model", tools=["Bash"])
    emit(type="thread.started", thread_id=f"fake-{os.getpid()}")
    emit(type="item.completed", item=dict(id="i0", type="agent_message", text=f"prompt bytes={len(prompt)}"))
    if mode in ("apply", "commit", "untracked") and patch:
        subprocess.run(["git", "apply", "--binary", patch], check=True)
        emit(type="item.completed", item=dict(id="i1", type="file_change", changes=[]))
    if mode == "untracked":
        Path("tests/test_fake_extra.py").write_text("def test_fake_extra():\n    assert True\n")
        Path(".env").write_text("SECRET=should-not-be-captured\n")
        Path("local.db").write_bytes(b"sqlite")
        Path(".pytest_cache").mkdir(exist_ok=True)
        Path(".pytest_cache/state").write_text("cache")
    if mode == "commit":
        subprocess.run(["git", "-c", "user.email=a@example.invalid", "-c", "user.name=Fake",
                        "-c", "commit.gpgsign=false", "commit", "-qam", "agent commit"], check=True)
    if mode == "probe":
        targets = json.loads(os.environ.get("FAKE_PROBE_TARGETS", "{}"))
        readable = {}
        for name, path in targets.items():
            try:
                Path(path).read_bytes()
                readable[name] = True
            except OSError:
                readable[name] = False
        Path("probe-result.json").write_text(json.dumps(readable))
    if mode == "sleep":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        Path(os.environ.get("FAKE_CHILD_PID_FILE", "child.pid")).write_text(str(child.pid))
        time.sleep(600)
    if mode == "fail":
        emit(type="turn.failed", error=dict(message="simulated agent failure"))
        sys.exit(3)
    if CLAUDE_EVENTS:
        claude_finish()
        return
    emit(type="item.completed", item=dict(id="i2", type="agent_message", text="done; assumed q parameter"))
    emit(type="turn.completed", usage=dict(input_tokens=1000, cached_input_tokens=400, cache_write_input_tokens=0,
                                           output_tokens=50, reasoning_output_tokens=10))


if __name__ == "__main__":
    main()
