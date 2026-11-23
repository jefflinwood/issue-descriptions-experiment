"""One isolated attempt: setup → single agent session → capture → clean evaluation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback

from . import evaluation, isolation
from .adapters import adapter_for
from .config import configuration, sha256_bytes, sha256_file
from .processes import CommandFailed, Recorder, utc_now

CAPTURE_EXCLUDES = (".pytest_cache/", "__pycache__/", "*.pyc", ".coverage", "htmlcov/", "*.db", "*.sqlite",
                    "*.sqlite3", ".env", ".env.*", "*.pem", "*.key", "venv/", ".venv/", "instance/",
                    "node_modules/", "*.egg-info/", ".DS_Store", ".codex/", ".claude/")
REPLACEABLE = {"setup_failed", "infrastructure_failure"}


class SetupFailure(RuntimeError):
    pass


def harness_provenance():
    root = Path(__file__).resolve().parent
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    commit = None
    try:
        commit = subprocess.run(["git", "-C", str(root.parent), "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return dict(harness_commit=commit,
                harness_sha256={p.relative_to(root.parent).as_posix(): sha256_file(p) for p in files})


def compose_prompt(config, description):
    common = Path(config["common_instructions"]).read_text()
    issue = Path(config["descriptions"][description]).read_text()
    return common.rstrip("\n") + "\n" + issue


class Attempt:
    def __init__(self, output_root, config, manifest, cell, *, replaces=None, replacement_reason=None):
        self.output_root = Path(output_root).resolve()
        self.config, self.manifest, self.cell = config, manifest, cell
        self.attempt_id = cell["attempt_id"] if not replaces else cell["replacement_id"]
        self.root = self.output_root / self.attempt_id
        self.root.mkdir(parents=False, exist_ok=False)   # reserve: never reuse or overwrite
        self.recorder = Recorder(self.root, config["limits"]["terminate_grace_seconds"])
        self.entry = configuration(config, cell["configuration"])
        self.adapter = adapter_for(self.entry)
        self.revision = config["source"]["revision"]
        self.workspace = self.root / "workspace"
        self.repo = self.workspace / "repo"
        self.candidate_venv = self.workspace / "venv"
        self.evaluator_venv = self.root / "evaluator-venv"
        self.baseline_git = self.root / "baseline.git"
        self.timeout = config["limits"]["command_timeout_seconds"]
        self.summary = dict(
            attempt_id=self.attempt_id, scheduled_attempt_id=cell["attempt_id"], index=cell["index"],
            block=cell["block"], position=cell["position"], configuration=cell["configuration"],
            description=cell["description"], cohort=manifest["cohort"], protocol_hash=manifest["protocol_hash"],
            protocol_version=config["protocol_version"], replaces=replaces, replacement_reason=replacement_reason,
            status="running", termination=None, agent_started=False, started_utc=utc_now(),
            timing=dict(setup_seconds=None, agent_seconds=None, capture_seconds=None, evaluation_seconds=None,
                        total_seconds=None),
            provenance=self._provenance(), usage=None, outcome=None, errors=[])

    # ------------------------------------------------------------------ records
    def _provenance(self, observed=None):
        e, observed = self.entry, observed or {}
        return dict(source_kind=self.config["source"]["kind"], repository=self.config["source"]["repository"],
                    clone_from=self.config["source"]["clone_from"] if self.config["source"]["kind"] != "public" else None,
                    requested_revision=self.revision, observed_revision=observed.get("revision"),
                    provider=e["provider"], harness=e["harness"], requested_model=e["model"],
                    observed_model=observed.get("observed_model"),
                    observed_model_source=observed.get("observed_model_source"),
                    requested_cli_version=e["cli_version"], observed_cli_version=observed.get("cli_version"),
                    settings=self.adapter.settings_record(), model_selection=e.get("model_selection"),
                    auth_mode=e.get("auth_mode"), isolation_mode=self.config["isolation"]["mode"])

    def write(self, name, value):
        evaluation.write_json(self.root / name, value)

    # ------------------------------------------------------------------ lifecycle
    def run(self):
        started = time.monotonic()
        prompt = compose_prompt(self.config, self.cell["description"])
        (self.root / "prompt.txt").write_text(prompt)
        self.write("attempt.json", dict(cell=self.cell, attempt_id=self.attempt_id, replaces=self.summary["replaces"],
                                        replacement_reason=self.summary["replacement_reason"],
                                        cohort=self.manifest["cohort"], protocol_hash=self.manifest["protocol_hash"],
                                        prompt_sha256=sha256_bytes(prompt.encode()),
                                        description_sha256=self.manifest["asset_sha256"][f"description:{self.cell['description']}"],
                                        common_sha256=self.manifest["asset_sha256"]["common_instructions"]))
        self.write("config.resolved.json", self.config)
        self.recorder.event("attempt_started", **{k: self.cell[k] for k in ("index", "block", "configuration", "description")})
        observed = {}
        try:
            phase_start = time.monotonic()
            try:
                observed.update(self.setup())
            finally:
                self.summary["timing"]["setup_seconds"] = time.monotonic() - phase_start
            self.summary["provenance"] = self._provenance(observed)
            phase_start = time.monotonic()
            agent = self.launch_agent(prompt)
            observed.update(observed_model=agent["parsed"]["observed_model"],
                            observed_model_source=agent["parsed"]["observed_model_source"])
            self.summary["provenance"] = self._provenance(observed)
            phase_start = time.monotonic()
            capture = self.capture()
            self.summary["timing"]["capture_seconds"] = time.monotonic() - phase_start
            self.summary["termination"] = self.classify(agent, capture)
            self.summary["capture"] = capture
            phase_start = time.monotonic()
            try:
                self.summary["outcome"] = self.evaluate(capture)
            finally:
                self.summary["timing"]["evaluation_seconds"] = time.monotonic() - phase_start
            self.summary["status"] = "finished"
        except SetupFailure as error:
            self.summary.update(status="finished", termination="setup_failed")
            self.summary["errors"].append(dict(phase="setup", message=str(error)))
        except KeyboardInterrupt:
            self.summary.update(status="interrupted", termination="interrupted")
            self.recorder.event("attempt_interrupted")
            raise
        except Exception as error:  # infrastructure/harness failures stay visible
            self.summary["status"] = "finished"
            if self.summary["termination"] is None:
                self.summary["termination"] = "infrastructure_failure"
            self.summary["errors"].append(dict(phase="harness", message=str(error), traceback=traceback.format_exc()))
        finally:
            self.summary["timing"]["total_seconds"] = time.monotonic() - started
            self.summary["finished_utc"] = utc_now()
            self.summary["environments"] = self.prune_environments()
            self.recorder.event("attempt_finished", status=self.summary["status"],
                                termination=self.summary["termination"])
            self.write("summary.json", self.summary)
        return self.summary

    def prune_environments(self):
        """Delete this attempt's virtualenvs (about 210 MB) unless the config retains them.

        They are rebuildable from constraints.txt and are recorded exactly in
        candidate-dependencies.txt / evaluator-dependencies.txt. Only directories that
        are this attempt's own virtualenvs (pyvenv.cfg present) are ever removed.
        """
        if self.config.get("retain_environments", False):
            return dict(retained=True, pruned=[])
        pruned, errors = [], []
        for path in (self.candidate_venv, self.evaluator_venv):
            path = Path(path)
            if path.parent.resolve() not in (self.workspace.resolve(), self.root.resolve()):
                continue
            if (path / "pyvenv.cfg").is_file():
                try:
                    shutil.rmtree(path)
                    pruned.append(str(path.relative_to(self.root)))
                except OSError as error:
                    errors.append(f"{path.name}: {error}")
        return dict(retained=False, pruned=pruned, errors=errors,
                    dependency_records=sorted(p.name for p in self.root.glob("*-dependencies.txt")))

    def git(self, *args, cwd=None, label="git"):
        return self.recorder.checked(["git", *args], cwd=cwd or self.repo, timeout=self.timeout,
                                     phase="setup", label=label)

    def setup(self):
        ok, detail = isolation.available(self.config["isolation"]["mode"])
        if not ok:
            raise SetupFailure(f"isolation unavailable: {detail}")
        try:
            return self._setup()
        except CommandFailed as error:
            raise SetupFailure(str(error)) from error

    def _setup(self):
        rec, t = self.recorder, self.timeout
        source = self.config["source"]["clone_from"]
        rec.checked(["git", "clone", "--bare", "--quiet", source, self.baseline_git], cwd=self.root, timeout=t,
                    phase="setup", label="clone-baseline")
        observed = rec.checked(["git", "--git-dir", self.baseline_git, "rev-parse", "--verify",
                                f"{self.revision}^{{commit}}"], timeout=t, phase="setup", label="verify-revision")
        if observed != self.revision:
            raise SetupFailure(f"baseline revision mismatch: {observed}")
        self.workspace.mkdir()
        (self.workspace / "tmp").mkdir()
        rec.checked(["git", "clone", "--quiet", "--no-hardlinks", self.baseline_git, self.repo], cwd=self.workspace,
                    timeout=t, phase="setup", label="clone-candidate")
        self.git("checkout", "--quiet", "-B", "main", self.revision, label="checkout-candidate")
        self.git("remote", "remove", "origin", label="remove-remote")
        for key, value in (("user.email", "harness@example.invalid"), ("user.name", "Issue Specification Harness"),
                           ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value, label="git-config")
        with (self.repo / ".git/info/exclude").open("a") as stream:
            stream.write("\n# harness capture exclusions\n" + "\n".join(CAPTURE_EXCLUDES) + "\n")
        shutil_copy(self.config["assets"]["constraints"], self.root / "constraints.txt")
        candidate_python = self._venv(self.candidate_venv, "candidate")
        self._venv(self.evaluator_venv, "evaluator")  # separate, never agent-visible
        cli_version = rec.checked(self.adapter.version_argv(), cwd=self.root, timeout=t, phase="setup",
                                  label="cli-version")
        metadata = dict(
            platform=platform.platform(), machine=platform.machine(), cpu_count=os.cpu_count(),
            harness_python=platform.python_version(), harness_executable=sys.executable,
            target_python=rec.checked([candidate_python, "--version"], timeout=t, phase="setup", label="python-version"),
            git_version=rec.checked(["git", "--version"], timeout=t, phase="setup", label="git-version"),
            cli_version=cli_version, revision=observed,
            dependency_files=dict(candidate="candidate-dependencies.txt", evaluator="evaluator-dependencies.txt"),
            isolation=dict(mode=self.config["isolation"]["mode"], network="provider and other HTTPS egress allowed; "
                           "candidate has no Git remote; GIT_ALLOW_PROTOCOL=file; PIP_NO_INDEX=1"),
            **harness_provenance())
        same = (self.root / "candidate-dependencies.txt").read_text() == (self.root / "evaluator-dependencies.txt").read_text()
        metadata["candidate_and_evaluator_dependencies_identical"] = same
        self.write("metadata.json", metadata)
        if cli_version != self.entry["cli_version"]:
            raise SetupFailure(f"expected CLI version {self.entry['cli_version']!r}, observed {cli_version!r}")
        baseline = rec.run([candidate_python, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=self.repo,
                           env=evaluation.phase_env(self.repo, disable_plugins=True), timeout=t, phase="setup",
                           label="baseline-tests")
        if baseline["returncode"] != 0 or baseline["timed_out"]:
            raise SetupFailure("baseline test suite failed before the agent launched")
        dirty = self.git("status", "--porcelain", label="baseline-clean")
        if dirty:
            raise SetupFailure(f"baseline checkout not clean after tests: {dirty[:500]}")
        return dict(revision=observed, cli_version=cli_version)

    def _venv(self, path, label):
        rec, t = self.recorder, self.timeout
        mode = self.config["dependencies"]["install"]
        args = [self.config["python_executable"], "-m", "venv"]
        if mode == "system-site":
            args.append("--system-site-packages")
        rec.checked([*args, path], cwd=self.root, timeout=t, phase="setup", label=f"venv-{label}")
        python = path / "bin" / "python"
        if mode == "pip":
            env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1")
            rec.checked([python, "-m", "pip", "install", "--quiet", "-r",
                         self.repo / self.config["dependencies"]["requirements_file"],
                         "-c", self.root / "constraints.txt"], cwd=self.repo, env=env, timeout=t, phase="setup",
                        label=f"pip-{label}")
        freeze = rec.checked([python, "-m", "pip", "freeze", "--all"], cwd=self.root, timeout=t, phase="setup",
                             label=f"freeze-{label}")
        (self.root / f"{label}-dependencies.txt").write_text(freeze + "\n")
        return python

    def launch_agent(self, prompt):
        self.adapter.prepare(self.workspace)
        mode = self.config["isolation"]["mode"]
        profile = self.root / "sandbox.sb"
        denied = isolation.protected_paths(self.config, self.output_root, self.root)
        profile.write_text(isolation.seatbelt_profile(self.workspace, denied, self.config["isolation"]["write_allow"]))
        venv_bin = self.candidate_venv / "bin"
        extra = dict(PATH=f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}", VIRTUAL_ENV=str(self.candidate_venv),
                     TMPDIR=str(self.workspace / "tmp"), PYTHONDONTWRITEBYTECODE="1", PIP_NO_INDEX="1",
                     PIP_DISABLE_PIP_VERSION_CHECK="1", GIT_ALLOW_PROTOCOL="file", GIT_TERMINAL_PROMPT="0")
        env = self.adapter.environment(os.environ, extra)
        argv = isolation.wrap(self.adapter.argv(self.repo, self.root / "prompt.txt"), profile, mode)
        self.write("agent.json", dict(argv=argv, env_keys=sorted(env), protected_paths=denied,
                                      write_allow=self.config["isolation"]["write_allow"],
                                      settings=self.adapter.settings_record()))
        self.summary["agent_started"] = True
        self.recorder.event("agent_started", configuration=self.entry["id"], model=self.entry["model"])
        result = self.recorder.run(argv, cwd=self.repo, env=env, stdin_text=prompt,
                                   timeout=self.config["limits"]["agent_timeout_seconds"], phase="agent", label="agent")
        self.recorder.event("agent_finished", returncode=result["returncode"], timed_out=result["timed_out"],
                            seconds=result["seconds"])
        # Record what the session did before any post-processing, so a later failure cannot erase it.
        self.summary["timing"]["agent_seconds"] = result["seconds"]
        self.summary["agent"] = dict(returncode=result["returncode"], timed_out=result["timed_out"],
                                     start_error=result.get("start_error"), seconds=result["seconds"],
                                     raw_events=result["stdout"], stderr=result["stderr"])
        try:
            parsed = self.adapter.parse(self.root / result["stdout"], self.root / result["stderr"])
        except Exception as error:  # a parser bug must not discard the session; the raw events stay on disk
            self.summary["errors"].append(dict(phase="parse", message=f"event parser failed: {error}",
                                               traceback=traceback.format_exc()))
            parsed = dict(usage=None, raw_usage=None, usage_aggregation=None, observed_model=None,
                          observed_model_source=None, session_ids=[], errors=[], error_kind=None,
                          delegation_events=None, event_count=None, parser_failed=True)
        self.summary["usage"] = dict(normalized=parsed["usage"], aggregation=parsed["usage_aggregation"],
                                     cli_reported_cost_usd=parsed.get("cli_reported_cost_usd"),
                                     cli_reported_cost_provenance=parsed.get("cli_reported_cost_provenance"))
        self.write("usage.json", {**parsed, "raw": parsed["raw_usage"]})
        self.summary["agent"].update(session_ids=parsed["session_ids"], errors=parsed["errors"][:5],
                                     error_kind=parsed["error_kind"], delegation_events=parsed["delegation_events"],
                                     event_count=parsed["event_count"])
        return dict(result=result, parsed=parsed)

    def capture(self):
        """The harness owns staging and commits; agent commits are recorded but still captured."""
        def g(*args, label="capture"):
            return self.recorder.checked(["git", *args], cwd=self.repo, timeout=self.timeout, phase="capture",
                                         label=label)
        head_before = g("rev-parse", "HEAD", label="head")
        agent_commits = int(g("rev-list", "--count", f"{self.revision}..HEAD", label="agent-commits") or 0)
        ignored = g("status", "--porcelain", "--ignored", "--untracked-files=all", label="status-ignored")
        g("add", "-A", label="stage")
        g("commit", "--allow-empty", "--no-verify", "--quiet", "-m", "Harness capture of candidate working tree",
          label="commit")
        final_commit = g("rev-parse", "HEAD", label="final-commit")
        final_tree = g("rev-parse", "HEAD^{tree}", label="final-tree")
        patch = self.recorder.run(["git", "diff", "--binary", "--full-index", self.revision, final_commit],
                                  cwd=self.repo, timeout=self.timeout, phase="capture", label="patch")
        patch_text = self.recorder.output(patch)
        (self.root / "final.patch").write_text(patch_text)
        name_status = g("diff", "--name-status", "-M", self.revision, final_commit, label="name-status")
        return dict(head_before_capture=head_before, agent_commits=agent_commits, final_commit=final_commit,
                    final_tree=final_tree, patch="final.patch", patch_sha256=sha256_bytes(patch_text.encode()),
                    patch_empty=not patch_text.strip(), name_status=name_status,
                    excluded_paths=[l[3:] for l in ignored.splitlines() if l.startswith("!! ")])

    def classify(self, agent, capture):
        result = agent["result"]
        if result.get("start_error"):
            return "agent_start_failed"
        if result["timed_out"]:
            return "timeout"
        if result["returncode"] != 0:
            return "agent_error"
        if capture["patch_empty"]:
            return "incomplete_no_changes"
        return "completed"

    def evaluate(self, capture):
        t = self.timeout
        evaluation_root = self.root / "evaluation"
        evaluation_root.mkdir()
        checkout = evaluation_root / "checkout"
        rec = self.recorder
        rec.checked(["git", "clone", "--quiet", "--no-hardlinks", self.baseline_git, checkout], cwd=evaluation_root,
                    timeout=t, phase="evaluation", label="clone-evaluation")
        def g(*args, label):
            return rec.checked(["git", *args], cwd=checkout, timeout=t, phase="evaluation", label=label)
        g("checkout", "--quiet", "-B", "evaluation", self.revision, label="checkout-evaluation")
        if not capture["patch_empty"]:
            g("apply", "--binary", "--index", self.root / "final.patch", label="apply-patch")
        tree = g("write-tree", label="evaluation-tree")
        if tree != capture["final_tree"]:
            raise evaluation.EvaluationIntegrityError(
                f"reconstructed tree {tree} differs from captured candidate tree {capture['final_tree']}")
        suite = evaluation_root / "suite"
        hashes = evaluation.build_suite(suite, self.config, self.manifest["asset_sha256"], rec, self.baseline_git,
                                        self.revision)
        self.write("evaluation-suite-sha256.json", hashes)
        python = self.evaluator_venv / "bin" / "python"
        junit = evaluation_root / "junit"
        junit.mkdir()
        phases = {}
        specs = [
            ("regression", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--import-mode=importlib",
                            "-c", suite / "original/pytest.ini", "--rootdir", suite / "original",
                            "--junitxml", junit / "regression.xml", suite / "original/tests"], checkout, True),
            ("candidate", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "--junitxml", junit / "candidate.xml"], checkout, True),
            ("acceptance", [python, "-m", "pytest", "-q", "--import-mode=importlib",
                            "-c", suite / "acceptance/pytest.ini", "--rootdir", suite / "acceptance",
                            "--junitxml", junit / "acceptance.xml", suite / "acceptance/acceptance.py"],
             suite / "acceptance", True),
        ]
        for label, argv, cwd, disable in specs:
            evaluation.verify_suite(suite, hashes)
            phases[label] = evaluation.run_phase(rec, label, argv, cwd, evaluation.phase_env(checkout, disable),
                                                 junit / f"{label}.xml", t)
            evaluation.verify_suite(suite, hashes)
        readme = rec.run(["git", "--git-dir", self.baseline_git, "show", f"{self.revision}:README.md"],
                         timeout=t, phase="evaluation", label="baseline-readme")
        requirements = json.loads(Path(self.config["assets"]["requirements"]).read_text())
        static = evaluation.static_checks((self.root / "final.patch").read_text(), capture["name_status"], checkout,
                                          rec.output(readme), evaluation.readme_pattern(requirements))
        completed = self.summary["termination"] == "completed"
        outcome = evaluation.summarize(requirements, phases, static, completed)
        outcome["evaluated_from"] = "completed_session" if completed else f"snapshot_after_{self.summary['termination']}"
        outcome["classification"] = evaluation.disclosure_classification(requirements, self.cell["description"],
                                                                          outcome["criteria"])
        outcome["static"] = static
        outcome["phases"] = {k: {kk: vv for kk, vv in v.items() if kk != "tests"} for k, v in phases.items()}
        self.write("evaluation.json", dict(phases=phases, static=static, outcome=outcome))
        return {k: v for k, v in outcome.items() if k != "static"} | {"static": {
            k: static[k] for k in ("tests_changed", "readme_documents_query", "original_tests_touched",
                                   "original_test_lines_removed", "possible_test_weakening",
                                   "out_of_scope_files")}}


def shutil_copy(source, destination):
    import shutil
    shutil.copyfile(source, destination)
