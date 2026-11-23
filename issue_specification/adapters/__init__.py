"""Adapters for coding-agent CLIs."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys

from .base import Adapter
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter


class FakeAdapter(CodexAdapter):
    """Runs ``fake_agent.py``; emits Codex-shaped events. For tests only."""

    harness = "fake"
    required_flags = ()

    def prepare(self, workspace):
        # The harness tree is unreadable inside the sandbox, so stage the stand-in's files.
        tools = Path(workspace) / "fake-agent"
        tools.mkdir()
        shutil.copyfile(Path(__file__).with_name("fake_agent.py"), tools / "fake_agent.py")
        self.staged_patch = None
        if self.entry.get("patch"):
            self.staged_patch = tools / "candidate.patch"
            shutil.copyfile(self.entry["patch"], self.staged_patch)
        self.tools = tools

    def argv(self, repo, prompt_path):
        argv = [self.entry.get("python") or sys.executable, "-I", str(self.tools / "fake_agent.py"),
                self.entry.get("mode", "apply")]
        if self.staged_patch:
            argv.append(str(self.staged_patch))
        return argv

    def environment(self, base_env, extra):
        env = super().environment(base_env, extra)
        for key in ("FAKE_PROBE_TARGETS", "FAKE_CHILD_PID_FILE"):
            if key in base_env:
                env[key] = base_env[key]
        env["FAKE_EVENT_FORMAT"] = self.entry.get("event_format", "codex")
        return env

    def parse(self, stdout_path, stderr_path=None):
        if self.entry.get("event_format") == "claude":
            return ClaudeCodeAdapter.parse(self, stdout_path, stderr_path)
        return super().parse(stdout_path, stderr_path)

    #: The stand-in's "installed" version; configs must request exactly this.
    installed_version = "fake-cli 1"

    def version_argv(self):
        return [sys.executable, "-c", f"print({self.installed_version!r})"]

    def settings_record(self):
        return dict(mode=self.entry.get("mode", "apply"))

    def auth_status_argv(self):
        return None


def adapter_for(entry) -> Adapter:
    return {"codex": CodexAdapter, "claude_code": ClaudeCodeAdapter, "fake": FakeAdapter}[entry["harness"]](entry)
