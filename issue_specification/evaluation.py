"""Independent evaluation: immutable suites, separate phases, per-criterion results."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from .config import sha256_file

# AC11 README component (stakeholder decision 2026-11-21): added README text shows a `?q=` URL example
# or names the `q` parameter (for example "the `q` URL parameter"). A bare letter q does not count.
README_QUERY = re.compile(r"[?&]q=|`q`|\bq\s+(?:url\s+|query\s+|search\s+)?param(?:eter)?\b", re.IGNORECASE)

SCOPE_PATHS = ("app/orders.py", "app/templates/orders/", "app/static/css/", "tests/", "README.md")
ACCEPTANCE_FILES = {"acceptance": "acceptance.py", "fixtures": "fixtures.py", "baseline_surface": "baseline_surface.json"}


class EvaluationIntegrityError(RuntimeError):
    pass


def build_suite(suite: Path, config, expected_hashes, recorder, baseline_git, revision):
    """Create the evaluation suite outside every agent-visible path and return its file hashes."""
    suite.mkdir(parents=False, exist_ok=False)
    acceptance = suite / "acceptance"
    acceptance.mkdir()
    for key, name in ACCEPTANCE_FILES.items():
        source = Path(config["assets"][key])
        if sha256_file(source) != expected_hashes[key]:
            raise EvaluationIntegrityError(f"benchmark asset {key} changed since the experiment manifest was written")
        shutil.copyfile(source, acceptance / name)
    (acceptance / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    original = suite / "original"
    original.mkdir()
    archive = suite / "original-tests.tar"
    recorder.checked(["git", "--git-dir", baseline_git, "archive", "--format=tar", "-o", archive, revision, "tests"],
                     timeout=config["limits"]["command_timeout_seconds"], phase="evaluation", label="archive-tests")
    recorder.checked(["tar", "-xf", archive, "-C", original], timeout=config["limits"]["command_timeout_seconds"],
                     phase="evaluation", label="extract-tests")
    archive.unlink()
    (original / "pytest.ini").write_text("[pytest]\n")
    return suite_hashes(suite)


def suite_hashes(suite: Path):
    return {p.relative_to(suite).as_posix(): sha256_file(p) for p in sorted(suite.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".xml")}


def verify_suite(suite, expected):
    observed = suite_hashes(suite)
    if observed != expected:
        changed = sorted(set(observed) ^ set(expected) | {k for k in expected if observed.get(k) != expected[k]})
        raise EvaluationIntegrityError(f"evaluation suite modified: {changed}")


def parse_junit(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    tests = []
    for case in root.iter("testcase"):
        outcome, message = "passed", None
        for child in case:
            if child.tag in ("failure", "error"):
                outcome, message = ("failed" if child.tag == "failure" else "error"), (child.get("message") or "")[:500]
            elif child.tag == "skipped":
                outcome, message = "skipped", (child.get("message") or "")[:500]
        tests.append(dict(name=case.get("name"), classname=case.get("classname"), outcome=outcome, message=message))
    return tests


def phase_env(pythonpath, disable_plugins):
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}
    env.update(PYTHONPATH=str(pythonpath), PYTHONDONTWRITEBYTECODE="1", PYTEST_ADDOPTS="")
    if disable_plugins:
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def run_phase(recorder, label, argv, cwd, env, junit, timeout):
    result = recorder.run(argv, cwd=cwd, env=env, timeout=timeout, phase="evaluation", label=label)
    tests = parse_junit(junit)
    counts = None
    if tests is not None:
        counts = {k: sum(t["outcome"] == k for t in tests) for k in ("passed", "failed", "error", "skipped")}
    passed = (result["returncode"] == 0 and not result["timed_out"] and tests is not None
              and bool(tests) and counts["failed"] == counts["error"] == 0)
    return dict(label=label, command_id=result["command_id"], returncode=result["returncode"],
                timed_out=result["timed_out"], seconds=result["seconds"], passed=passed, counts=counts,
                tests=tests, junit=str(Path(junit).relative_to(recorder.root)) if Path(junit).exists() else None)


def criterion_results(requirements, acceptance_phase):
    tests = acceptance_phase.get("tests") or []
    results = {}
    entries = [(c["id"], c.get("tests")) for c in requirements["criteria"] if c.get("tests")]
    entries += [(m["id"], m["tests"]) for m in requirements["minimal_outcome"]]
    for criterion, prefix in entries:
        matched = [t for t in tests if t["name"] and t["name"].startswith(prefix)]
        if not matched:
            results[criterion] = dict(passed=False, reason="no tests ran (collection or phase failure)",
                                      tests=0, failed=[])
            continue
        failed = [t["name"] for t in matched if t["outcome"] != "passed"]
        results[criterion] = dict(passed=not failed, tests=len(matched), failed=failed,
                                  messages=[t["message"] for t in matched if t["message"]][:5])
    return results


def composite_criterion(requirements):
    """The criterion assembled from phases and static checks (AC11 in 002.1)."""
    return next((c for c in requirements["criteria"] if c.get("kind") == "composite"), {"id": "AC11"})


def readme_pattern(requirements):
    pattern = composite_criterion(requirements).get("readme_pattern")
    return re.compile(pattern) if pattern else README_QUERY


def static_checks(patch_text, name_status, checkout: Path, baseline_readme: str, readme_regex=README_QUERY):
    changed = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            changed.append(dict(status=parts[0], path=parts[-1], source=parts[1] if len(parts) == 3 else None))
    paths = [c["path"] for c in changed]
    tests_changed = any(p.startswith("tests/") and p.endswith(".py") and c["status"][0] in "AMR"
                        for p, c in zip(paths, changed))
    original_tests_touched = [c["path"] for c in changed
                              if (c["source"] or c["path"]).startswith("tests/") and c["status"][0] in "MDR"]
    # Appending tests to an existing file is normal; removed or rewritten lines are the weakening signal.
    removed = {c["path"]: _file_hunk_removals(patch_text, c["source"] or c["path"]) for c in changed
               if (c["source"] or c["path"]).startswith("tests/") and c["status"][0] in "MDR"}
    original_test_lines_removed = {path: count for path, count in removed.items() if count}
    deleted_original_tests = [c["path"] for c in changed if c["path"].startswith("tests/") and c["status"][0] == "D"]
    readme = (checkout / "README.md")
    readme_text = readme.read_text(errors="replace") if readme.exists() else ""
    added = "\n".join(l[1:] for l in patch_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
    readme_added = _file_hunk_additions(patch_text, "README.md")
    readme_documents_query = readme_text != baseline_readme and bool(readme_regex.search(readme_added))
    out_of_scope = [p for p in paths if not p.startswith(SCOPE_PATHS)]
    return dict(changed_files=changed, tests_changed=tests_changed, original_tests_touched=original_tests_touched,
                original_test_lines_removed=original_test_lines_removed, deleted_original_tests=deleted_original_tests,
                possible_test_weakening=bool(original_test_lines_removed or deleted_original_tests),
                readme_changed=readme_text != baseline_readme, readme_documents_query=readme_documents_query,
                out_of_scope_files=out_of_scope, added_lines=len(added.splitlines()) if added else 0)


def _file_hunk_removals(patch_text, filename):
    count, active = 0, False
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            active = line.startswith(f"diff --git a/{filename} ")
        elif active and line.startswith("-") and not line.startswith("---"):
            count += 1
    return count


def _file_hunk_additions(patch_text, filename):
    lines, active = [], False
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            active = line.endswith(f" b/{filename}")
        elif active and line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return "\n".join(lines)


def composite(regression, candidate, static):
    components = dict(regression=bool(regression and regression["passed"]),
                      candidate_tests=bool(candidate and candidate["passed"]),
                      tests_changed=static["tests_changed"],
                      readme_documents_query=static["readme_documents_query"])
    return dict(passed=all(components.values()), components=components)


def summarize(requirements, phases, static, agent_completed):
    acceptance = phases.get("acceptance") or {}
    criteria = criterion_results(requirements, acceptance)
    criteria[composite_criterion(requirements)["id"]] = composite(phases.get("regression"), phases.get("candidate"), static)
    contract_ids = [c["id"] for c in requirements["criteria"]]
    all_criteria = all(criteria.get(c, {}).get("passed") for c in contract_ids)
    regression = bool(phases.get("regression") and phases["regression"]["passed"])
    minimal = all(criteria.get(m["id"], {}).get("passed") for m in requirements["minimal_outcome"])
    return dict(criteria=criteria,
                full_acceptance=all_criteria,
                regression_passed=regression,
                minimal_outcome=minimal and regression,
                primary_success=bool(agent_completed and all_criteria and regression),
                artifact_checks_passed=bool(all_criteria and regression))


def disclosure_classification(requirements, description, criteria):
    """Split failed criteria into explicit-instruction violations and unspecified-behavior mismatches."""
    explicit, partial, unspecified = [], [], []
    for c in requirements["criteria"]:
        result = criteria.get(c["id"])
        if not result or result.get("passed"):
            continue
        level = c["disclosure"].get(description, "absent")
        {"explicit": explicit, "partial": partial}.get(level, unspecified).append(c["id"])
    return dict(explicit_violations=explicit, partially_specified_failures=partial, unspecified_mismatches=unspecified)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str) + "\n")


def validate_controls(config, manifest_hashes, recorder, python, workdir, names=None):
    """Run the reference and every negative control through the real evaluation phases.

    Returns per-control results and whether each failed exactly the expected criteria.
    The baseline (no-op control) must fail the new-feature criteria while passing the
    original regression suite; the reference must pass everything.
    """
    workdir = Path(workdir)
    t = config["limits"]["command_timeout_seconds"]
    revision = config["source"]["revision"]
    requirements = json.loads(Path(config["assets"]["requirements"]).read_text())
    controls_spec = json.loads(Path(config["assets"]["controls"]).read_text())["controls"]
    controls_dir = Path(config["assets"]["controls"]).parent
    every = [c["id"] for c in requirements["criteria"]] + [m["id"] for m in requirements["minimal_outcome"]]
    plan = [("reference", Path(config["assets"]["reference_patch"]), [])]
    plan += [(name, controls_dir / spec["patch"], spec["fails"]) for name, spec in sorted(controls_spec.items())]
    if names:
        plan = [p for p in plan if p[0] in names]
    bare = workdir / "baseline.git"
    recorder.checked(["git", "clone", "--bare", "--quiet", config["source"]["clone_from"], bare], cwd=workdir,
                     timeout=t, phase="controls", label="clone-baseline")
    readme = recorder.run(["git", "--git-dir", bare, "show", f"{revision}:README.md"], timeout=t,
                          phase="controls", label="baseline-readme")
    baseline_readme = recorder.output(readme)
    results = {}
    for name, patch, expected_fails in plan:
        root = workdir / name
        root.mkdir()
        checkout = root / "checkout"
        recorder.checked(["git", "clone", "--quiet", bare, checkout], cwd=root, timeout=t, phase="controls",
                         label=f"{name}-clone")
        recorder.checked(["git", "checkout", "--quiet", "-B", "control", revision], cwd=checkout, timeout=t,
                         phase="controls", label=f"{name}-checkout")
        patch_text = patch.read_text()
        if patch_text.strip():
            recorder.checked(["git", "apply", "--binary", "--index", patch], cwd=checkout, timeout=t,
                             phase="controls", label=f"{name}-apply")
        suite = root / "suite"
        hashes = build_suite(suite, config, manifest_hashes, recorder, bare, revision)
        junit = root / "junit"
        junit.mkdir()
        phases = {}
        specs = [
            ("regression", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--import-mode=importlib",
                            "-c", suite / "original/pytest.ini", "--rootdir", suite / "original",
                            "--junitxml", junit / "regression.xml", suite / "original/tests"], checkout),
            ("candidate", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "--junitxml", junit / "candidate.xml"], checkout),
            ("acceptance", [python, "-m", "pytest", "-q", "--import-mode=importlib",
                            "-c", suite / "acceptance/pytest.ini", "--rootdir", suite / "acceptance",
                            "--junitxml", junit / "acceptance.xml", suite / "acceptance/acceptance.py"],
             suite / "acceptance"),
        ]
        for label, argv, cwd in specs:
            verify_suite(suite, hashes)
            phases[label] = run_phase(recorder, f"{name}-{label}", argv, cwd, phase_env(checkout, True),
                                      junit / f"{label}.xml", t)
            verify_suite(suite, hashes)
        name_status = recorder.output(recorder.run(["git", "diff", "--cached", "--name-status", "-M", revision],
                                                   cwd=checkout, timeout=t, phase="controls",
                                                   label=f"{name}-name-status"))
        static = static_checks(patch_text, name_status, checkout, baseline_readme, readme_pattern(requirements))
        outcome = summarize(requirements, phases, static, agent_completed=True)
        failed = sorted(cid for cid in every if not outcome["criteria"].get(cid, {}).get("passed"))
        if name == "reference":
            ok = not failed and outcome["regression_passed"] and outcome["primary_success"]
        else:
            # Every control keeps the baseline regression suite passing; acceptance failures must match exactly.
            acceptance_failed = [c for c in failed if c != composite_criterion(requirements)["id"]]
            ok = acceptance_failed == sorted(expected_fails) and outcome["regression_passed"]
        results[name] = dict(ok=ok, failed=failed, expected_failures=sorted(expected_fails),
                             regression_passed=outcome["regression_passed"],
                             candidate_tests_passed=phases["candidate"]["passed"],
                             primary_success=outcome["primary_success"])
    return results
