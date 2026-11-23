"""Command line: plan, preflight, controls, run, report.

`plan` and `report` launch nothing. `preflight` launches no model unless
`--probe-models` is given. `run` is the explicit boundary for paid agent attempts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

from . import evaluation, isolation, reporting
from .adapters import adapter_for
from .config import ConfigError, asset_hashes, load_config, portable_settings, protocol_hash
from .processes import Recorder, utc_now
from .runner import REPLACEABLE, Attempt, harness_provenance
from .schedule import build_schedule

COHORTS = ("main", "pilot", "development")


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def manifest_for(config, cohort):
    return dict(protocol_version=config["protocol_version"], status=config.get("status"),
                protocol_hash=protocol_hash(config), cohort=cohort, settings=portable_settings(config),
                asset_sha256=asset_hashes(config), schedule=build_schedule(config),
                source_kind=config["source"]["kind"])


def cmd_plan(args, config):
    schedule = build_schedule(config)
    blocks = {}
    for row in schedule:
        blocks.setdefault(row["block"], set()).add((row["configuration"], row["description"]))
    expected = {(c["id"], d) for c in config["configurations"] for d in config["descriptions"]}
    balanced = all(cells == expected for cells in blocks.values()) and all(
        sum(1 for r in schedule if r["block"] == b) == len(expected) for b in blocks)
    plan = dict(protocol_version=config["protocol_version"], status=config.get("status"),
                protocol_hash=protocol_hash(config), source=dict(kind=config["source"]["kind"],
                                                                 repository=config["source"]["repository"],
                                                                 revision=config["source"]["revision"]),
                configurations=[{k: c.get(k) for k in ("id", "label", "harness", "model", "cli_version",
                                                       "reasoning_effort", "effort", "thinking_tokens", "auth_mode",
                                                       "settings_status")} for c in config["configurations"]],
                descriptions=sorted(config["descriptions"]), blocks=config["blocks"], seed=config["seed"],
                attempts=len(schedule), balanced_complete_blocks=balanced, limits=config["limits"],
                isolation=dict(mode=config["isolation"]["mode"]), schedule=schedule)
    print(json.dumps(plan, indent=2))
    if not balanced:
        raise SystemExit("schedule is not balanced")


# ---------------------------------------------------------------------------- preflight
def check(results, name, ok, detail=None, blocking=True):
    results.append(dict(check=name, ok=bool(ok), blocking=blocking, detail=detail))
    marker = "ok  " if ok else ("FAIL" if blocking else "warn")
    print(f"[{marker}] {name}" + (f": {detail}" if detail else ""), flush=True)


def cmd_preflight(args, config):
    results = []
    work = Path(args.workdir or Path(config["harness_root"]) / "results" / "preflight" / stamp()).resolve()
    work.mkdir(parents=True, exist_ok=False)
    recorder = Recorder(work, config["limits"]["terminate_grace_seconds"])
    t = config["limits"]["command_timeout_seconds"]
    check(results, "config valid", True, f"protocol {config['protocol_version']} hash {protocol_hash(config)[:16]}")
    check(results, "python", True, f"{config['python_executable']} ({platform.python_version()})")

    for entry in config["configurations"]:
        adapter = adapter_for(entry)
        if entry["harness"] == "fake":
            continue
        exe = shutil.which(entry["executable"])
        check(results, f"{entry['id']}: executable", exe, exe or f"{entry['executable']} not on PATH")
        if not exe:
            continue
        version = subprocess.run(adapter.version_argv(), capture_output=True, text=True, timeout=60).stdout.strip()
        check(results, f"{entry['id']}: CLI version", version == entry["cli_version"],
              f"expected {entry['cli_version']!r}, observed {version!r}")
        help_text = subprocess.run([entry["executable"], *adapter.help_argv], capture_output=True, text=True,
                                   timeout=60)
        text = help_text.stdout + help_text.stderr
        missing = [f for f in adapter.required_flags if f not in text]
        check(results, f"{entry['id']}: adapter flags supported", not missing, f"missing {missing}" if missing else
              f"{len(adapter.required_flags)} flags present in help")
        status_argv = adapter.auth_status_argv()
        if status_argv:
            # Only a coarse mode word is kept; account names and tokens are never printed or stored.
            status = subprocess.run(status_argv, capture_output=True, text=True, timeout=60)
            mode = adapter.sanitize_status(status.stdout + status.stderr)
            check(results, f"{entry['id']}: credentials available", status.returncode == 0 and mode != "not logged in",
                  f"auth status exit {status.returncode}; mode: {mode}")

    # Public source availability of the frozen SHA.
    if config["source"]["kind"] == "public" or args.check_public:
        probe = work / "fetch-probe.git"
        recorder.checked(["git", "init", "--bare", "--quiet", probe], timeout=t, phase="preflight", label="init")
        fetched = recorder.run(["git", "--git-dir", probe, "fetch", "--quiet", "--depth=1",
                                config["source"]["repository"], config["source"]["revision"]],
                               timeout=t, phase="preflight", label="fetch-sha")
        check(results, "public remote serves frozen SHA", fetched["returncode"] == 0,
              f"{config['source']['repository']} @ {config['source']['revision'][:12]}")
    else:
        check(results, "source", True, f"local override {config['source']['clone_from']} (provenance: local-override)",
              blocking=False)

    # Isolation: sandbox availability, canaries, and CLIs starting inside the sandbox.
    mode = config["isolation"]["mode"]
    ok, detail = isolation.available(mode)
    check(results, f"isolation {mode} available", ok, detail)
    if ok and mode == "macos-seatbelt":
        output_root = Path(config["harness_root"]) / "results"
        run_root = work / "attempt"
        workspace = run_root / "workspace"
        workspace.mkdir(parents=True)
        sibling = work / "sibling-attempt" / "summary.json"
        sibling.parent.mkdir()
        sibling.write_text('{"secret": "sibling result"}')
        targets = {"harness spec": str(Path(config["harness_root"]) / "CODING_SPEC.md"),
                   "acceptance checks": config["assets"]["acceptance"],
                   "stakeholder answers": config["assets"]["stakeholder_answers"],
                   "reference patch": config["assets"]["reference_patch"],
                   "other description": config["descriptions"][sorted(config["descriptions"])[-1]],
                   "sibling result": str(sibling)}
        for denied in config["isolation"]["deny_read"]:
            found = next((p for p in Path(denied).glob("*") if p.is_file()), None) if Path(denied).is_dir() else None
            if found:
                targets[f"deny_read sample {Path(denied).name}"] = str(found)
        writes = {"harness root": str(Path(config["harness_root"]) / ".write-probe"),
                  "home": str(Path.home() / ".issue-spec-write-probe")}
        denied = isolation.protected_paths(config, output_root, run_root)
        profile = isolation.seatbelt_profile(workspace, denied, config["isolation"]["write_allow"])
        result = isolation.probe(profile, workspace, targets, writes)
        check(results, "sandbox hides protected files and blocks outside writes", result.get("ok"),
              f"leaked: {result.get('leaked')}" if not result.get("ok") else f"{len(targets)} read and "
              f"{len(writes)} write canaries denied; workspace writable")
        (run_root / "sandbox.sb").write_text(profile)
        for entry in config["configurations"]:
            if entry["harness"] == "fake" or not shutil.which(entry["executable"]):
                continue
            adapter = adapter_for(entry)
            argv = isolation.wrap(adapter.version_argv(), run_root / "sandbox.sb", mode)
            started = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=workspace)
            check(results, f"{entry['id']}: CLI starts inside sandbox", started.returncode == 0,
                  started.stdout.strip() or started.stderr.strip()[-300:])
            repo = workspace / f"startup-{entry['id']}"
            startup = adapter.startup_check(repo)
            if startup:
                # Real agent argv, logs routed through the recorder, empty prompt: no provider request.
                repo.mkdir()
                subprocess.run(["git", "init", "--quiet", repo], check=True)
                env = adapter.environment(os.environ, dict(TMPDIR=str(workspace)))
                startup_argv, marker = startup
                outcome = recorder.run(isolation.wrap(startup_argv, run_root / "sandbox.sb", mode), cwd=repo, env=env,
                                       stdin_text="", timeout=60, phase="preflight", label=f"startup-{entry['id']}")
                text = recorder.output(outcome) + (work / outcome["stderr"]).read_text(errors="replace")
                check(results, f"{entry['id']}: agent command starts inside sandbox (no request)", marker in text,
                      f"exit {outcome['returncode']}: {text.strip().splitlines()[-1][:200] if text.strip() else ''}")

    if not args.skip_dependencies:
        dependency_check(config, recorder, work, results)
    if args.probe_models:
        probe_models(config, work, results, mode)
    else:
        check(results, "model access probe", True, "skipped (pass --probe-models to launch billable probes)",
              blocking=False)
    (work / "preflight.json").write_text(json.dumps(dict(utc=utc_now(), results=results), indent=2))
    blocking = [r for r in results if r["blocking"] and not r["ok"]]
    print(f"\npreflight record: {work / 'preflight.json'}")
    if blocking:
        raise SystemExit(f"{len(blocking)} blocking preflight check(s) failed")


def dependency_check(config, recorder, work, results):
    t = config["limits"]["command_timeout_seconds"]
    try:
        repo = work / "baseline"
        recorder.checked(["git", "clone", "--quiet", config["source"]["clone_from"], repo], timeout=t,
                         phase="preflight", label="clone")
        recorder.checked(["git", "checkout", "--quiet", config["source"]["revision"]], cwd=repo, timeout=t,
                         phase="preflight", label="checkout")
        venv = work / "venv"
        recorder.checked([config["python_executable"], "-m", "venv", venv], timeout=t, phase="preflight", label="venv")
        python = venv / "bin/python"
        recorder.checked([python, "-m", "pip", "install", "--quiet", "-r", repo / config["dependencies"]["requirements_file"],
                          "-c", config["assets"]["constraints"]], cwd=repo, timeout=t, phase="preflight",
                         label="pip", env=dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1"))
        check(results, "dependencies install with frozen constraints", True)
        tests = recorder.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=repo,
                             env=evaluation.phase_env(repo, True), timeout=t, phase="preflight", label="baseline-tests")
        check(results, "baseline test suite passes", tests["returncode"] == 0,
              recorder.output(tests).strip().splitlines()[-1] if recorder.output(tests).strip() else None)
    except Exception as error:  # report, do not hide
        check(results, "dependency setup", False, str(error)[:500])


def probe_models(config, work, results, mode):
    """Billable: one trivial noninteractive call per configuration, in a scratch Git repo."""
    for entry in config["configurations"]:
        if entry["harness"] == "fake":
            continue
        adapter = adapter_for(entry)
        root = work / f"probe-{entry['id']}"
        workspace = root / "workspace"
        repo = workspace / "repo"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "--quiet", repo], check=True)
        adapter.prepare(workspace)
        profile = root / "sandbox.sb"
        profile.write_text(isolation.seatbelt_profile(
            workspace, isolation.protected_paths(config, Path(config["harness_root"]) / "results", root),
            config["isolation"]["write_allow"]))
        recorder = Recorder(root, config["limits"]["terminate_grace_seconds"])
        env = adapter.environment(os.environ, dict(TMPDIR=str(workspace)))
        result = recorder.run(isolation.wrap(adapter.argv(repo, None), profile, mode), cwd=repo, env=env,
                              stdin_text="Reply with exactly the word READY and do nothing else.", timeout=300,
                              phase="probe", label="model-probe")
        parsed = adapter.parse(root / result["stdout"], root / result["stderr"])
        ok = result["returncode"] == 0 and not result["timed_out"] and not parsed["errors"]
        check(results, f"{entry['id']}: model {entry['model']} responds", ok,
              f"observed model {parsed['observed_model']!r}; errors {parsed['errors'][:2]}; "
              f"error kind {parsed['error_kind']}; usage {parsed['usage']}")


# ---------------------------------------------------------------------------- controls
def cmd_controls(args, config):
    work = Path(args.workdir or Path(config["harness_root"]) / "results" / "controls" / stamp()).resolve()
    work.mkdir(parents=True, exist_ok=False)
    recorder = Recorder(work, config["limits"]["terminate_grace_seconds"])
    python = args.python
    if not python:
        venv = work / "evaluator-venv"
        t = config["limits"]["command_timeout_seconds"]
        recorder.checked([config["python_executable"], "-m", "venv", venv], timeout=t, phase="controls", label="venv")
        source = work / "deps-source"
        recorder.checked(["git", "clone", "--quiet", config["source"]["clone_from"], source], timeout=t,
                         phase="controls", label="clone-deps")
        recorder.checked(["git", "checkout", "--quiet", config["source"]["revision"]], cwd=source, timeout=t,
                         phase="controls", label="checkout-deps")
        recorder.checked([venv / "bin/python", "-m", "pip", "install", "--quiet", "-r",
                          source / config["dependencies"]["requirements_file"], "-c", config["assets"]["constraints"]],
                         cwd=source, timeout=t, phase="controls", label="pip",
                         env=dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1"))
        python = str(venv / "bin/python")
    results = evaluation.validate_controls(config, asset_hashes(config), recorder, python, work)
    (work / "controls.json").write_text(json.dumps(results, indent=2))
    if not args.python and not config.get("retain_environments", False):
        shutil.rmtree(work / "evaluator-venv", ignore_errors=True)  # rebuilt from constraints on every run
    for name, result in results.items():
        print(f"[{'ok  ' if result['ok'] else 'FAIL'}] {name}: failed {result['failed']} "
              f"(expected {result['expected_failures']}); regression {result['regression_passed']}")
    if not all(r["ok"] for r in results.values()):
        raise SystemExit("evaluator controls did not behave as expected")


# ---------------------------------------------------------------------------- run
def prepare_output(output, config, cohort):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = manifest_for(config, cohort)
    path = output / "experiment.json"
    if path.exists():
        existing = json.loads(path.read_text())
        if existing["protocol_hash"] != manifest["protocol_hash"] or existing["cohort"] != cohort:
            raise SystemExit(f"{output} already holds protocol {existing['protocol_hash'][:16]} cohort "
                             f"{existing['cohort']}; use a new output directory")
        return output, existing
    manifest["created_utc"] = utc_now()
    manifest["harness"] = harness_provenance()
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return output, manifest


def cmd_run(args, config):
    if args.run_index is not None and args.cohort == "main":
        raise SystemExit("--run-index runs a single attempt; label it with --cohort pilot or development")
    if config["isolation"]["mode"] == "macos-seatbelt":
        ok, detail = isolation.available("macos-seatbelt")
        if not ok:
            raise SystemExit(f"isolation unavailable: {detail}")
    output, manifest = prepare_output(args.output, config, args.cohort)
    schedule = manifest["schedule"]
    if args.replace:
        return replace_attempt(args, config, output, manifest)
    if args.run_index is not None:
        if not 0 <= args.run_index < len(schedule):
            raise SystemExit("--run-index out of range")
        selected = [schedule[args.run_index]]
    else:
        selected = schedule
    existing = [c["attempt_id"] for c in selected if (output / c["attempt_id"]).exists()]
    if existing and not args.continue_matrix:
        raise SystemExit(f"{len(existing)} scheduled attempt(s) already exist (first: {existing[0]}). Existing "
                         "attempts are never rerun; pass --continue to run only attempts that have no directory.")
    failures = 0
    for cell in selected:
        if (output / cell["attempt_id"]).exists():
            continue
        if asset_hashes(config) != manifest["asset_sha256"]:
            raise SystemExit("benchmark inputs changed during collection; start a new protocol version")
        print(f"[{utc_now()}] starting {cell['attempt_id']}", flush=True)
        summary = Attempt(output, config, manifest, cell).run()
        outcome = summary.get("outcome") or {}
        print(f"[{utc_now()}] {cell['attempt_id']}: termination={summary['termination']} "
              f"primary={outcome.get('primary_success')} minimal={outcome.get('minimal_outcome')}", flush=True)
        failures += not outcome.get("primary_success")
    report = reporting.build(output)
    paths = reporting.write(output, report, suffix="-" + stamp())
    print(f"report: {paths['markdown']}")
    return 0


def replace_attempt(args, config, output, manifest):
    original = output / args.replace / "summary.json"
    if not original.exists():
        raise SystemExit(f"no summary for {args.replace}")
    record = json.loads(original.read_text())
    if record.get("termination") not in REPLACEABLE:
        raise SystemExit(f"{args.replace} ended with {record.get('termination')!r}; only "
                         f"{sorted(REPLACEABLE)} may be replaced under the predeclared policy")
    if not args.reason:
        raise SystemExit("--reason is required for a replacement")
    cell = next(c for c in manifest["schedule"] if c["attempt_id"] == record["scheduled_attempt_id"])
    prior = sorted(p.name for p in output.glob(f"{cell['attempt_id']}-r*"))
    if len(prior) >= config.get("max_replacements_per_attempt", 1):
        raise SystemExit(f"{cell['attempt_id']} already has {len(prior)} replacement(s)")
    cell = dict(cell, replacement_id=f"{cell['attempt_id']}-r{len(prior) + 1}")
    summary = Attempt(output, config, manifest, cell, replaces=record["attempt_id"],
                      replacement_reason=args.reason).run()
    print(json.dumps({k: summary[k] for k in ("attempt_id", "replaces", "termination")}))


def cmd_report(args):
    report = reporting.build(args.output)
    paths = reporting.write(args.output, report, suffix="-" + stamp())
    print(reporting.to_markdown(report))
    print(f"written: {', '.join(str(p) for p in paths.values())}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="issue_specification", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "preflight", "controls", "run"):
        p = sub.add_parser(name)
        p.add_argument("--config", default="benchmark/config.json")
        p.add_argument("--source-override", help="clone from this local Git repository (records local provenance)")
    sub.choices["preflight"].add_argument("--probe-models", action="store_true",
                                          help="BILLABLE: send one trivial prompt to each configured model")
    sub.choices["preflight"].add_argument("--skip-dependencies", action="store_true")
    sub.choices["preflight"].add_argument("--check-public", action="store_true",
                                          help="also verify the public SHA when using a local override")
    sub.choices["preflight"].add_argument("--workdir")
    sub.choices["controls"].add_argument("--python", help="interpreter that already has Ridgeline dependencies")
    sub.choices["controls"].add_argument("--workdir")
    run = sub.choices["run"]
    run.add_argument("--output", required=True)
    run.add_argument("--cohort", choices=COHORTS, default="main")
    run.add_argument("--run-index", type=int)
    run.add_argument("--continue", dest="continue_matrix", action="store_true",
                     help="run only scheduled attempts without a directory; never touches existing ones")
    run.add_argument("--replace", help="attempt ID with an infrastructure/setup failure to replace (new linked ID)")
    run.add_argument("--reason")
    report = sub.add_parser("report")
    report.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "report":
            return cmd_report(args)
        config = load_config(args.config, source_override=args.source_override)
        return {"plan": cmd_plan, "preflight": cmd_preflight, "controls": cmd_controls, "run": cmd_run}[args.command](
            args, config)
    except (ConfigError, reporting.ReportError) as error:
        raise SystemExit(f"error: {error}")


if __name__ == "__main__":
    main()
