"""Attempt lifecycle with real Git, real sandbox (on macOS), real pytest, and fake agents."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import unittest

from issue_specification import cli, evaluation, reporting
from issue_specification.config import asset_hashes, load_config
from issue_specification.processes import Recorder
from issue_specification.runner import Attempt, compose_prompt

from tests.helpers import (BENCHMARK, ROOT, fake_entry, git, has_ridgeline_dependencies, isolation_mode,
                           ridgeline_source, synthetic_source, write_config)


class OfflineAttempt(Attempt):
    """Creates real venvs but links the test interpreter's packages instead of downloading."""

    def _venv(self, path, label):
        self.recorder.checked([sys.executable, "-m", "venv", "--without-pip", path], cwd=self.root,
                              timeout=self.timeout, phase="setup", label=f"venv-{label}")
        site = next((path / "lib").glob("python*/site-packages"))
        (site / "test-parent-packages.pth").write_text(sysconfig.get_paths()["purelib"] + "\n")
        (self.root / f"{label}-dependencies.txt").write_text("linked from test interpreter\n")
        return path / "bin" / "python"


def start(output, config, cell_index=0, cls=OfflineAttempt):
    manifest = cli.manifest_for(config, "development")
    output.mkdir(parents=True, exist_ok=True)
    (output / "experiment.json").write_text(json.dumps(manifest))
    return cls(output, config, manifest, manifest["schedule"][cell_index]), manifest


class SyntheticLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(os.path.realpath(directory.name))
        self.source, self.revision = synthetic_source(self.tmp)

    def config(self, *entries, **overrides):
        return load_config(write_config(self.tmp, source=self.source, revision=self.revision,
                                        entries=list(entries), **overrides))

    def test_agent_failure_is_recorded_and_attempt_directory_is_immutable(self):
        config = self.config(fake_entry(mode="fail"))
        attempt, manifest = start(self.tmp / "out", config)
        summary = attempt.run()
        self.assertEqual(summary["termination"], "agent_error")
        self.assertTrue(summary["agent_started"])
        self.assertIsNotNone(summary["timing"]["agent_seconds"])
        self.assertFalse(summary["outcome"]["primary_success"])
        self.assertEqual(summary["outcome"]["evaluated_from"], "snapshot_after_agent_error")
        for name in ("summary.json", "events.jsonl", "final.patch", "prompt.txt", "attempt.json", "metadata.json",
                     "usage.json", "evaluation.json", "agent.json"):
            self.assertTrue((attempt.root / name).exists(), name)
        with self.assertRaises(FileExistsError):
            OfflineAttempt(self.tmp / "out", config, manifest, manifest["schedule"][0])

    def test_claude_format_session_is_parsed_and_recorded(self):
        """Regression (pilot 000/001): Claude parser output must survive the usage.json write."""
        entry = dict(fake_entry(mode="noop"), event_format="claude")
        attempt, _ = start(self.tmp / "out", self.config(entry))
        summary = attempt.run()
        self.assertEqual(summary["termination"], "incomplete_no_changes", summary["errors"])
        self.assertEqual(summary["errors"], [])
        self.assertIsNotNone(summary["timing"]["agent_seconds"])
        self.assertEqual(summary["provenance"]["observed_model"], "fake-model")
        normalized = summary["usage"]["normalized"]
        self.assertEqual((normalized["input_tokens_total"], normalized["output_tokens"], normalized["tool_calls"]),
                         (123, 7, 1))
        self.assertEqual(summary["usage"]["cli_reported_cost_usd"], 0.001)
        usage = json.loads((attempt.root / "usage.json").read_text())
        self.assertEqual(usage["model_usage"], {"fake-model": {"inputTokens": 3, "outputTokens": 7}})
        self.assertEqual(usage["raw"]["output_tokens"], 7)

    def test_parser_failure_keeps_session_record_and_still_evaluates(self):
        config = self.config(fake_entry(mode="noop"))
        attempt, _ = start(self.tmp / "out", config)
        def broken(*args, **kwargs):
            raise ValueError("simulated parser bug")
        attempt.adapter.parse = broken
        summary = attempt.run()
        self.assertEqual(summary["termination"], "incomplete_no_changes")
        self.assertEqual(summary["agent"]["returncode"], 0)
        self.assertIsNotNone(summary["timing"]["agent_seconds"])
        self.assertIsNone(summary["usage"]["normalized"])
        self.assertIn("parser failed", summary["errors"][0]["message"])
        self.assertIsNotNone(summary["outcome"])

    def test_virtualenvs_are_pruned_after_summary_but_dependency_records_kept(self):
        attempt, _ = start(self.tmp / "out", self.config(fake_entry(mode="noop")))
        summary = attempt.run()
        self.assertEqual(summary["termination"], "incomplete_no_changes", summary["errors"])
        self.assertFalse(attempt.candidate_venv.exists())
        self.assertFalse(attempt.evaluator_venv.exists())
        self.assertEqual(sorted(summary["environments"]["pruned"]), ["evaluator-venv", "workspace/venv"])
        self.assertTrue((attempt.root / "candidate-dependencies.txt").exists())
        self.assertTrue((attempt.root / "evaluator-dependencies.txt").exists())
        self.assertTrue((attempt.repo / ".git").exists(), "the candidate repository must be kept")

    def test_virtualenvs_retained_when_configured(self):
        attempt, _ = start(self.tmp / "out", self.config(fake_entry(mode="noop"), retain_environments=True))
        summary = attempt.run()
        self.assertTrue(summary["environments"]["retained"])
        self.assertTrue((attempt.candidate_venv / "pyvenv.cfg").exists())
        self.assertTrue((attempt.evaluator_venv / "pyvenv.cfg").exists())

    def test_timeout_kills_agent_process_group_and_keeps_snapshot(self):
        config = self.config(fake_entry(mode="sleep"),
                             limits={"agent_timeout_seconds": 3, "command_timeout_seconds": 300,
                                     "terminate_grace_seconds": 1})
        attempt, _ = start(self.tmp / "out", config)
        pid_file = attempt.workspace / "tmp" / "child.pid"
        os.environ["FAKE_CHILD_PID_FILE"] = str(pid_file)
        self.addCleanup(os.environ.pop, "FAKE_CHILD_PID_FILE", None)
        summary = attempt.run()
        self.assertEqual(summary["termination"], "timeout")
        self.assertTrue(summary["agent"]["timed_out"])
        self.assertFalse(summary["outcome"]["primary_success"])
        child = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                break
        else:
            self.fail("agent child process survived the timeout")

    def test_setup_failure_has_no_agent_result(self):
        config = self.config(fake_entry())
        config["source"]["revision"] = "0" * 40
        attempt, _ = start(self.tmp / "out", config)
        summary = attempt.run()
        self.assertEqual(summary["termination"], "setup_failed")
        self.assertFalse(summary["agent_started"])
        self.assertIsNone(summary["timing"]["agent_seconds"])
        self.assertIsNone(summary["outcome"])
        self.assertIsNone(summary["usage"])
        events = (attempt.root / "events.jsonl").read_text()
        self.assertNotIn('"phase": "agent"', events)

    def test_cli_version_mismatch_fails_setup_without_substitution(self):
        entry = fake_entry()
        config = self.config(entry)
        config["configurations"][0]["cli_version"] = "fake-cli 2"
        attempt, _ = start(self.tmp / "out", config)
        summary = attempt.run()
        self.assertEqual(summary["termination"], "setup_failed")
        self.assertIn("expected CLI version", summary["errors"][0]["message"])

    def test_fresh_isolated_candidate_and_complete_capture(self):
        patch = self.tmp / "candidate.patch"
        work = self.tmp / "patchwork"
        git(self.tmp, "clone", "--quiet", str(self.source), str(work))
        (work / "app.py").write_text("VALUE = 2\n")
        (work / "tests/test_new.py").write_text("def test_new():\n    assert True\n")
        git(work, "add", "-A")
        patch.write_text(subprocess.run(["git", "diff", "--cached", "--binary"], cwd=work, capture_output=True,
                                        text=True, check=True).stdout)
        config = self.config(fake_entry(mode="commit", patch=patch))
        first, _ = start(self.tmp / "out", config, 0)
        summary = first.run()
        self.assertEqual(summary["termination"], "completed", summary["errors"])
        self.assertEqual(summary["capture"]["agent_commits"], 1)
        self.assertIn("tests/test_new.py", summary["capture"]["name_status"])
        self.assertEqual(git(first.repo, "remote"), "")
        self.assertEqual((first.root / "prompt.txt").read_text(), compose_prompt(config, summary["description"]))
        second, _ = start(self.tmp / "out", config, 1)
        self.assertNotEqual(second.workspace, first.workspace)
        self.assertFalse(second.workspace.exists())
        self.assertFalse((first.workspace / "evaluation").exists())
        self.assertTrue(summary["outcome"]["static"]["tests_changed"])

    @unittest.skipUnless(isolation_mode() == "macos-seatbelt", "Seatbelt sandbox requires macOS")
    def test_sandbox_hides_evaluator_harness_and_sibling_results(self):
        config = self.config(fake_entry(mode="probe"))
        sibling = self.tmp / "out" / "999-sibling"
        sibling.mkdir(parents=True)
        (sibling / "summary.json").write_text('{"result": "secret"}')
        targets = {"spec": str(ROOT / "CODING_SPEC.md"), "acceptance": str(BENCHMARK / "evaluation/acceptance.py"),
                   "answers": str(BENCHMARK / "stakeholder-answers.md"),
                   "reference": str(BENCHMARK / "reference/reference.patch"),
                   "d4": str(BENCHMARK / "descriptions/d4-expanded.md"), "sibling": str(sibling / "summary.json"),
                   "source": str(self.source / "app.py")}
        os.environ["FAKE_PROBE_TARGETS"] = json.dumps(targets)
        self.addCleanup(os.environ.pop, "FAKE_PROBE_TARGETS", None)
        attempt, _ = start(self.tmp / "out", config)
        summary = attempt.run()
        self.assertEqual(summary["termination"], "completed", summary["errors"])
        readable = json.loads((attempt.repo / "probe-result.json").read_text())
        self.assertEqual(readable, {name: False for name in targets})
        self.assertIn("deny file-read*", (attempt.root / "sandbox.sb").read_text())


@unittest.skipUnless(isolation_mode() == "macos-seatbelt", "Seatbelt sandbox requires macOS")
class SandboxedStdioTests(unittest.TestCase):
    """Regression: Node-based CLIs (Codex) abort if their stdio points into a sandbox-denied directory."""

    def run_sandboxed(self, argv):
        from issue_specification import isolation
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory)) / "run"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            recorder = Recorder(root, grace_seconds=1)
            profile = root / "sandbox.sb"
            profile.write_text(isolation.seatbelt_profile(workspace, [str(root)], []))
            result = recorder.run(isolation.wrap(argv, profile, "macos-seatbelt"), cwd=workspace,
                                  stdin_text="input", timeout=60, phase="test")
            return result, recorder.output(result), (root / result["stderr"]).read_text()

    def test_child_can_stat_its_stdio_while_log_directory_is_denied(self):
        script = "import os, sys; os.fstat(0); os.fstat(1); os.fstat(2); print('ok', sys.stdin.read())"
        result, stdout, stderr = self.run_sandboxed([sys.executable, "-I", "-c", script])
        self.assertEqual(result["returncode"], 0, stderr)
        self.assertEqual(stdout.strip(), "ok input")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_starts_inside_sandbox(self):
        result, stdout, stderr = self.run_sandboxed([shutil.which("node"), "-e", "console.log('node ok')"])
        self.assertEqual(result["returncode"], 0, stderr[-500:])
        self.assertEqual(stdout.strip(), "node ok")

    def test_ancestors_can_be_stat_ed_but_not_listed_or_read(self):
        """Regression: Codex resolves its cwd via parent stats. Metadata only; contents stay hidden."""
        script = ("import json, os, pathlib\n"
                  "here = pathlib.Path.cwd(); out = {}\n"
                  "for p in [here.parent, here.parent.parent]:\n"
                  "    out[str(p)] = {}\n"
                  "    try: os.stat(p); out[str(p)]['stat'] = True\n"
                  "    except OSError: out[str(p)]['stat'] = False\n"
                  "    try: os.listdir(p); out[str(p)]['list'] = True\n"
                  "    except OSError: out[str(p)]['list'] = False\n"
                  "try: open(here.parent / 'secret.txt').read(); out['sibling_read'] = True\n"
                  "except OSError: out['sibling_read'] = False\n"
                  "try: os.stat(here.parent / 'secret.txt'); out['sibling_stat'] = True\n"
                  "except OSError: out['sibling_stat'] = False\n"
                  "print(json.dumps(out))\n")
        from issue_specification import isolation
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory)) / "run"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            (root / "secret.txt").write_text("hidden")
            recorder = Recorder(root, grace_seconds=1)
            profile = root / "sandbox.sb"
            profile.write_text(isolation.seatbelt_profile(workspace, [str(root.parent)], []))
            result = recorder.run(isolation.wrap([sys.executable, "-I", "-c", script], profile, "macos-seatbelt"),
                                  cwd=workspace, timeout=60, phase="test")
            self.assertEqual(result["returncode"], 0, (root / result["stderr"]).read_text())
            observed = json.loads(recorder.output(result))
        self.assertFalse(observed.pop("sibling_read"))
        self.assertFalse(observed.pop("sibling_stat"))
        for path, access in observed.items():
            self.assertEqual(access, {"stat": True, "list": False}, path)


@unittest.skipUnless(ridgeline_source() and has_ridgeline_dependencies(),
                     "needs a Ridgeline clone (ISSUE_SPEC_RIDGELINE) and its dependencies installed")
class RidgelinePipelineTests(unittest.TestCase):
    """Integration smoke test: fake agent → clean evaluation → report, plus evaluator controls."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(os.path.realpath(directory.name))
        self.source = ridgeline_source()
        self.revision = load_config(BENCHMARK / "config.json")["source"]["revision"]

    def test_reference_and_noop_through_pipeline_and_report(self):
        entries = [fake_entry("fake-ref", mode="untracked", patch=BENCHMARK / "reference/reference.patch"),
                   fake_entry("fake-noop", mode="noop")]
        config = load_config(write_config(self.tmp, source=self.source, revision=self.revision, entries=entries,
                                          descriptions=("D1",)))
        output = self.tmp / "out"
        manifest = cli.manifest_for(config, "development")
        output.mkdir()
        (output / "experiment.json").write_text(json.dumps(manifest))
        results = {}
        for cell in manifest["schedule"]:
            results[cell["configuration"]] = OfflineAttempt(output, config, manifest, cell).run()
        ref, noop = results["fake-ref"], results["fake-noop"]
        self.assertEqual(ref["termination"], "completed", ref["errors"])
        self.assertTrue(ref["outcome"]["primary_success"], ref["outcome"]["criteria"])
        self.assertNotIn(".env", ref["capture"]["name_status"])
        self.assertIn(".env", ref["capture"]["excluded_paths"])
        self.assertEqual(noop["termination"], "incomplete_no_changes")
        self.assertFalse(noop["outcome"]["primary_success"])
        self.assertTrue(noop["outcome"]["regression_passed"])
        self.assertIn("AC04", noop["outcome"]["classification"]["unspecified_mismatches"])
        report = reporting.build(output)
        self.assertEqual(report["recorded_attempts"], 2)
        rates = {g["configuration"]: g["primary_success_scheduled"]["successes"] for g in report["groups"]}
        self.assertEqual(rates, {"fake-ref": 1, "fake-noop": 0})

    def test_evaluator_controls_fail_for_intended_reasons(self):
        config = load_config(write_config(self.tmp, source=self.source, revision=self.revision,
                                          entries=[fake_entry()]))
        work = self.tmp / "controls"
        work.mkdir()
        results = evaluation.validate_controls(config, asset_hashes(config), Recorder(work), sys.executable, work)
        self.assertEqual(set(results), {"reference", "noop", "no-ownership-check", "includes-inactive",
                                        "copies-dates", "historical-prices", "repeated-lines-overwritten",
                                        "one-click-auto-create"})
        for name, result in results.items():
            self.assertTrue(result["ok"], (name, result))
        self.assertIn("MIN-AUTH", results["no-ownership-check"]["failed"])
        self.assertEqual([c for c in results["historical-prices"]["failed"] if c != "AC10"], ["AC05"])
        # A one-click design is a different product decision, not a broken reorder.
        self.assertNotIn("MIN-REORDER", results["one-click-auto-create"]["failed"])

    def test_archived_url_search_controls_still_pass(self):
        config = load_config(BENCHMARK / "archive/002.1-url-search/config.json", source_override=str(self.source))
        work = self.tmp / "archived-controls"
        work.mkdir()
        results = evaluation.validate_controls(config, asset_hashes(config), Recorder(work), sys.executable, work)
        self.assertTrue(all(r["ok"] for r in results.values()), results)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "issue_specification.cli", *args], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)

    def test_plan_launches_nothing_and_lists_80_attempts(self):
        result = self.run_cli("plan")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["attempts"], 80)
        self.assertTrue(plan["balanced_complete_blocks"])
        self.assertEqual(plan["limits"]["agent_timeout_seconds"], 1800)

    def test_single_run_requires_pilot_label(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli("run", "--output", directory, "--run-index", "0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--cohort pilot", result.stderr)
            self.assertFalse((Path(directory) / "experiment.json").exists())

    def test_output_with_different_protocol_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "experiment.json").write_text(json.dumps({"protocol_hash": "x", "cohort": "pilot"}))
            result = self.run_cli("run", "--output", directory, "--run-index", "0", "--cohort", "pilot")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("use a new output directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
