import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from issue_specification.adapters import adapter_for
from issue_specification.config import load_config
from issue_specification.processes import Recorder

from tests.helpers import BENCHMARK, FIXTURES


def entries():
    config = load_config(BENCHMARK / "config.json")
    return {c["id"]: c for c in config["configurations"]}


class CodexParserTests(unittest.TestCase):
    def setUp(self):
        self.adapter = adapter_for(entries()["terra"])

    def test_success_trace(self):
        parsed = self.adapter.parse(FIXTURES / "codex-success.jsonl")
        u = parsed["usage"]
        self.assertEqual((u["input_tokens"], u["cached_input_tokens"], u["output_tokens"], u["reasoning_tokens"]),
                         (145942, 129664, 1975, 70))
        self.assertEqual(u["input_tokens_total"], 145942)  # OpenAI input already includes cached tokens
        self.assertEqual(u["tool_calls"], 2)               # one command + one file change, started events ignored
        self.assertIsNone(parsed["observed_model"])        # Codex JSONL does not report a backend model
        self.assertEqual(parsed["session_ids"], ["thread-synthetic-1"])
        self.assertEqual(parsed["invalid_lines"], 1)
        self.assertIn("Assumed", parsed["final_message"])
        self.assertEqual(parsed["errors"], [])

    def test_missing_usage_is_null_not_zero(self):
        parsed = self.adapter.parse(FIXTURES / "codex-no-usage.jsonl")
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "input_tokens_total"):
            self.assertIsNone(parsed["usage"][key], key)
        self.assertEqual(parsed["usage"]["tool_calls"], 0)
        self.assertIsNone(self.adapter.parse(FIXTURES / "missing.jsonl")["usage"]["tool_calls"])

    def test_access_error_classified_without_substitution(self):
        parsed = self.adapter.parse(FIXTURES / "codex-model-error.jsonl")
        self.assertEqual(parsed["error_kind"], "access_or_model")
        self.assertTrue(parsed["errors"])

    def test_argv_selects_exact_model_fresh_session_no_delegation(self):
        argv = self.adapter.argv(Path("/tmp/repo"), None)
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-6.1-terra")
        for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--json"):
            self.assertIn(flag, argv)
        self.assertIn('model_reasoning_effort="medium"', argv)
        self.assertEqual(argv[argv.index("--disable") + 1], "multi_agent")
        self.assertNotIn("resume", argv)
        self.assertNotIn("--last", argv)
        self.assertEqual(argv[-1], "-")


class ClaudeParserTests(unittest.TestCase):
    def setUp(self):
        self.adapter = adapter_for(entries()["sonnet"])

    def test_result_totals_used_and_streamed_duplicates_not_double_counted(self):
        parsed = self.adapter.parse(FIXTURES / "claude-success.jsonl")
        u = parsed["usage"]
        self.assertEqual((u["input_tokens"], u["cache_write_input_tokens"], u["cached_input_tokens"], u["output_tokens"]),
                         (15, 2300, 2000, 160))
        self.assertEqual(u["input_tokens_total"], 15 + 2300 + 2000)
        self.assertIsNone(u["reasoning_tokens"])
        self.assertEqual(u["tool_calls"], 3)   # toolu_1 repeated while streaming counts once
        self.assertEqual(parsed["usage_aggregation"], "result")
        self.assertEqual(parsed["observed_model"], "claude-sonnet-5-5")
        self.assertEqual(parsed["cli_reported_cost_usd"], 0.0421)
        self.assertIn("estimate", parsed["cli_reported_cost_provenance"])
        self.assertEqual(parsed["delegation_events"], 0)

    def test_fallback_dedupes_assistant_messages_by_id(self):
        parsed = self.adapter.parse(FIXTURES / "claude-no-result.jsonl")
        u = parsed["usage"]
        # msg_a counted once (last copy: output 11) + msg_b
        self.assertEqual((u["input_tokens"], u["cache_write_input_tokens"], u["cached_input_tokens"], u["output_tokens"]),
                         (10, 100, 100, 16))
        self.assertEqual(parsed["usage_aggregation"], "assistant messages de-duplicated by id")
        self.assertEqual(parsed["delegation_events"], 1)
        self.assertIsNone(parsed["cli_reported_cost_usd"])

    def test_access_error(self):
        parsed = self.adapter.parse(FIXTURES / "claude-access-error.jsonl")
        self.assertEqual(parsed["error_kind"], "access_or_model")

    def test_argv_selects_exact_model_and_disables_customizations(self):
        argv = self.adapter.argv(Path("/tmp/repo"), None)
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-5-5")
        self.assertEqual(argv[argv.index("--effort") + 1], "medium")
        for flag in ("--print", "--no-session-persistence", "--safe-mode", "--strict-mcp-config",
                     "--disable-slash-commands"):
            self.assertIn(flag, argv)
        self.assertIn("Agent", argv[argv.index("--disallowedTools") + 1])
        self.assertNotIn("--resume", argv)
        self.assertNotIn("--continue", argv)
        api = adapter_for(dict(entries()["haiku"], auth_mode="api-key")).argv(Path("/tmp/repo"), None)
        self.assertIn("--bare", api)
        self.assertNotIn("--safe-mode", api)

    def test_environment_passes_names_from_allowlist_only(self):
        env = self.adapter.environment({"PATH": "/bin", "HOME": "/h", "AWS_SECRET_ACCESS_KEY": "x",
                                        "ANTHROPIC_API_KEY": "k"}, {"TMPDIR": "/t"})
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["TMPDIR"], "/t")


class ProcessTests(unittest.TestCase):
    def test_timeout_terminates_whole_process_group_including_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = Recorder(root, grace_seconds=1)
            pid_file = root / "grandchild.pid"
            script = ("import subprocess, sys, time\n"
                      "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                      f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                      "time.sleep(120)\n")
            started = time.monotonic()
            result = recorder.run([sys.executable, "-c", script], timeout=2, phase="test", label="sleeper")
            self.assertTrue(result["timed_out"])
            self.assertLess(time.monotonic() - started, 30)
            grandchild = int(pid_file.read_text())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                self.fail("grandchild survived timeout cleanup")
            events = (root / "events.jsonl").read_text()
            self.assertIn('"timed_out": true', events)
            self.assertTrue((root / result["stdout"]).exists())

    def test_command_records_have_utc_monotonic_and_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = Recorder(Path(directory))
            result = recorder.run([sys.executable, "-c", "print('hi')"], timeout=30, phase="test")
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(recorder.output(result).strip(), "hi")
            self.assertIn("started_utc", result)
            self.assertIn("monotonic_seconds", (Path(directory) / "events.jsonl").read_text())

    def test_missing_executable_is_recorded_not_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Recorder(Path(directory)).run(["/nonexistent/agent"], timeout=5, phase="test")
            self.assertIsNone(result["returncode"])
            self.assertIn("start_error", result)


if __name__ == "__main__":
    unittest.main()
