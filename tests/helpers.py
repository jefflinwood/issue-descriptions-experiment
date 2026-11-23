"""Shared test helpers: fake-agent configs and a tiny synthetic source repository."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
BENCHMARK = ROOT / "benchmark"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RIDGELINE_DEPS = ("flask", "flask_sqlalchemy", "flask_login", "openpyxl", "pytest")


def raw_config():
    return json.loads((BENCHMARK / "config.json").read_text())


def fake_entry(config_id="fake", mode="apply", patch=None):
    entry = {"id": config_id, "label": f"Fake {mode}", "provider": "fake", "harness": "fake",
             "executable": sys.executable, "cli_version": "fake-cli 1", "model": "fake-model",
             "reasoning_effort": "medium", "mode": mode}
    if patch:
        entry["patch"] = str(patch)
    return entry


def isolation_mode():
    return "macos-seatbelt" if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists() else "none"


def write_config(directory, *, source, revision, entries, descriptions=("D1", "D3"), blocks=1, **overrides):
    """A config file in ``directory`` that reuses the real benchmark assets by absolute path."""
    raw = copy.deepcopy(raw_config())
    raw["protocol_version"] = "002-test"
    raw["source"] = {"repository": str(source), "revision": revision, "local_override": str(source)}
    raw["descriptions"] = {k: str(BENCHMARK / v) for k, v in raw["descriptions"].items() if k in descriptions}
    raw["common_instructions"] = str(BENCHMARK / raw["common_instructions"])
    raw["assets"] = {k: str(BENCHMARK / v) for k, v in raw["assets"].items()}
    raw["configurations"] = entries
    raw["blocks"] = blocks
    raw["isolation"]["mode"] = isolation_mode()
    raw["limits"] = {"agent_timeout_seconds": 60, "command_timeout_seconds": 300, "terminate_grace_seconds": 1}
    for key, value in overrides.items():
        raw[key] = value
    path = Path(directory) / "config.json"
    path.write_text(json.dumps(raw, indent=1))
    return path


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def synthetic_source(directory):
    """Minimal repository with a passing pytest suite (no Ridgeline dependencies)."""
    source = Path(directory) / "source"
    (source / "tests").mkdir(parents=True)
    git(source, "init", "--quiet")
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "Test"), ("commit.gpgsign", "false")):
        git(source, "config", key, value)
    (source / "README.md").write_text("# Synthetic\n")
    (source / "requirements-dev.txt").write_text("")
    (source / "app.py").write_text("VALUE = 1\n")
    (source / "tests/test_base.py").write_text("def test_baseline():\n    assert True\n")
    git(source, "add", ".")
    git(source, "commit", "--quiet", "-m", "baseline")
    return source, git(source, "rev-parse", "HEAD")


def ridgeline_source():
    """A local Ridgeline clone for full-pipeline tests, or None."""
    candidate = os.environ.get("ISSUE_SPEC_RIDGELINE") or str(Path("~/Projects/ridgeline").expanduser())
    path = Path(candidate)
    if not (path / ".git").exists() and not (path / "HEAD").exists():
        return None
    revision = raw_config()["source"]["revision"]
    found = subprocess.run(["git", "-C", str(path), "cat-file", "-e", f"{revision}^{{commit}}"], capture_output=True)
    return path if found.returncode == 0 else None


def has_ridgeline_dependencies():
    import importlib.util
    return all(importlib.util.find_spec(name) for name in RIDGELINE_DEPS)


def copy_benchmark(directory):
    target = Path(directory) / "benchmark"
    shutil.copytree(BENCHMARK, target)
    return target
