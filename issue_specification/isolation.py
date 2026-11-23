"""Agent filesystem isolation.

``macos-seatbelt`` wraps the coding CLI in ``sandbox-exec`` with a generated
profile: read and write access is denied under every protected path (the harness
repository, all outputs, the source checkouts, sibling projects, CLI memory and
skill directories), then re-allowed only for the attempt's workspace. Writes are
further limited to the workspace, temporary directories and the CLI state paths
listed in the config.

Limitations, recorded with every run: Seatbelt cannot filter outbound network by
hostname, so provider APIs and other HTTPS hosts remain reachable; source remotes
are removed from the candidate clone and Git is limited to the file protocol.
Nested Seatbelt sandboxes are refused by macOS, so CLIs that sandbox their own
commands must run with that inner sandbox disabled inside this one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def real(path) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def _quote(path: str) -> str:
    return json.dumps(path)  # SBPL string literals accept JSON-style escaping for paths


def seatbelt_profile(workspace, deny_read, write_allow):
    workspace = real(workspace)
    denied = sorted({real(p) for p in deny_read})
    writable = sorted({real(p) for p in write_allow} | {workspace, real("/dev"), real("/private/tmp"),
                                                         real("/private/var/folders")})
    lines = ["(version 1)", "(allow default)"]
    for path in denied:
        lines.append(f"(deny file-read* file-write* (subpath {_quote(path)}))")
    lines.append("(deny file-write* (require-all " +
                 " ".join(f"(require-not (subpath {_quote(p)}))" for p in writable) + "))")
    lines.append(f"(allow file-read* file-write* (subpath {_quote(workspace)}))")
    # Tools resolve their working directory by stat-ing each parent (Codex exits with EPERM otherwise).
    # Allow metadata on those exact directories only: no listing and no access to anything inside them.
    for ancestor in Path(workspace).parents:
        lines.append(f"(allow file-read-metadata (literal {_quote(str(ancestor))}))")
    return "\n".join(lines) + "\n"


def protected_paths(config, output_root, run_root):
    paths = list(config["isolation"]["deny_read"])
    paths += [config["harness_root"], str(output_root), str(run_root)]
    if config["source"]["kind"] == "local-override":
        paths.append(config["source"]["clone_from"])
    return sorted({real(p) for p in paths})


def wrap(argv, profile_path, mode):
    if mode == "none":
        return list(argv)
    if mode == "macos-seatbelt":
        return [SANDBOX_EXEC, "-f", str(profile_path), *argv]
    raise ValueError(f"unknown isolation mode {mode}")


def available(mode):
    if mode == "none":
        return True, "no sandbox (fake agents only)"
    if mode == "macos-seatbelt":
        if sys.platform != "darwin":
            return False, "macos-seatbelt requires macOS"
        if not Path(SANDBOX_EXEC).exists():
            return False, f"{SANDBOX_EXEC} not found"
        return True, SANDBOX_EXEC
    return False, f"unknown mode {mode}"


PROBE = r'''
import json, os, sys
targets = json.loads(sys.argv[1]); workspace = sys.argv[2]; write_targets = json.loads(sys.argv[3])
result = {"readable": {}, "listable": {}, "writable": {}, "workspace_write": False}
for name, path in write_targets.items():
    try:
        with open(path, "x") as stream:
            stream.write("probe")
        os.remove(path)
        result["writable"][name] = True
    except FileExistsError:
        result["writable"][name] = None
    except OSError:
        result["writable"][name] = False
for name, path in targets.items():
    try:
        with open(path, "rb") as stream:
            stream.read(1)
        result["readable"][name] = True
    except OSError:
        result["readable"][name] = False
    try:
        os.listdir(os.path.dirname(path))
        result["listable"][name] = True
    except OSError:
        result["listable"][name] = False
try:
    probe = os.path.join(workspace, ".isolation-probe")
    with open(probe, "w") as stream:
        stream.write("ok")
    os.remove(probe)
    result["workspace_write"] = True
except OSError:
    pass
print(json.dumps(result))
'''


def probe(profile_text, workspace, targets, write_targets=None, python=sys.executable):
    """Run a probe inside the sandbox and report which protected targets were readable."""
    workspace = Path(workspace)
    profile = workspace.parent / "isolation-probe.sb"
    profile.write_text(profile_text)
    # Pass the probe source on stdin so no harness file needs to be readable.
    completed = subprocess.run([SANDBOX_EXEC, "-f", str(profile), python, "-I", "-", json.dumps(targets),
                                str(workspace), json.dumps(write_targets or {})], input=PROBE, capture_output=True, text=True, timeout=60,
                               cwd=str(workspace))
    if completed.returncode != 0:
        return dict(ok=False, error=completed.stderr[-2000:])
    observed = json.loads(completed.stdout)
    leaked = sorted(name for name, readable in observed["readable"].items() if readable)
    leaked += sorted(f"write:{name}" for name, ok in observed["writable"].items() if ok)
    return dict(ok=not leaked and observed["workspace_write"], leaked=leaked, **observed)
