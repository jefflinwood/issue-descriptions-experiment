"""Strict configuration loading and portable protocol hashing.

The protocol hash covers the *content* of every frozen input (descriptions,
wrapper, evaluation files, constraints, requirement inventory, reference
controls) plus the experiment settings. Machine-specific values such as absolute
paths and the resolved interpreter are recorded as provenance, not hashed, so the
same protocol hashes identically on another machine.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
DESCRIPTION_RE = re.compile(r"^D[0-9]+$")
HARNESSES = {"codex", "claude_code", "fake"}
PROVIDERS = {"codex": {"openai"}, "claude_code": {"anthropic"}, "fake": {"fake"}}
ISOLATION_MODES = {"macos-seatbelt", "none"}
ASSET_KEYS = ("constraints", "requirements", "acceptance", "fixtures", "baseline_surface",
              "protocol", "stakeholder_answers", "review_rubric", "reference_patch", "controls")
CODEX_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}
CLAUDE_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


class ConfigError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def expand(path: str, base: Path) -> Path:
    candidate = Path(os.path.expandvars(os.path.expanduser(path)))
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def _require(condition, message):
    if not condition:
        raise ConfigError(message)


def _positive_int(config, *keys):
    node = config
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    _require(type(node) is int and node > 0, f"{'.'.join(keys)} must be a positive integer")


def validate_configuration(entry, index):
    where = f"configurations[{index}]"
    _require(isinstance(entry, dict), f"{where} must be an object")
    for key in ("id", "label", "provider", "harness", "executable", "cli_version", "model"):
        _require(isinstance(entry.get(key), str) and entry[key].strip(), f"{where}.{key} is required")
    _require(ID_RE.match(entry["id"]), f"{where}.id must be lowercase alphanumeric/dash")
    harness = entry["harness"]
    _require(harness in HARNESSES, f"{where}.harness must be one of {sorted(HARNESSES)}")
    _require(entry["provider"] in PROVIDERS[harness],
             f"{where}: harness {harness} cannot run provider {entry['provider']!r}")
    model = entry["model"]
    _require(model == model.strip() and " " not in model, f"{where}.model must be an exact identifier")
    if harness == "codex":
        _require(not model.startswith("claude"), f"{where}: Codex cannot be assumed to run Anthropic model {model}")
        _require(entry.get("reasoning_effort") in CODEX_EFFORTS,
                 f"{where}.reasoning_effort must be one of {sorted(CODEX_EFFORTS)}")
    if harness == "claude_code":
        _require(model.startswith("claude-"), f"{where}: Claude Code requires an explicit claude-* model ID, not an alias")
        _require(model not in {"claude-sonnet", "claude-haiku", "claude-opus"},
                 f"{where}: use a full model identifier, not a floating alias")
        _require(entry.get("effort") in CLAUDE_EFFORTS, f"{where}.effort must be one of {sorted(CLAUDE_EFFORTS)}")
        _require(entry.get("auth_mode") in ("oauth", "api-key"), f"{where}.auth_mode must be 'oauth' or 'api-key'")
    selection = entry.get("model_selection")
    if harness != "fake":
        _require(isinstance(selection, dict) and selection.get("date") and selection.get("source"),
                 f"{where}.model_selection must record the selection date and source")


def load_config(path, *, source_override=None):
    """Return a resolved config. ``source_override`` replaces the clone source for development."""
    path = Path(path).resolve()
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot read config {path}: {error}") from error
    config = copy.deepcopy(raw)
    base = path.parent

    _require(isinstance(config.get("protocol_version"), str), "protocol_version is required")
    source = config.get("source")
    _require(isinstance(source, dict), "source is required")
    _require(isinstance(source.get("repository"), str), "source.repository is required")
    _require(isinstance(source.get("revision"), str) and SHA_RE.match(source["revision"]),
             "source.revision must be a full lowercase 40-character commit SHA")
    override = source_override or source.get("local_override")
    if override:
        resolved = expand(override, base)
        _require((resolved / ".git").exists() or (resolved / "HEAD").exists(),
                 f"local source override {resolved} is not a Git repository")
        source["clone_from"] = str(resolved)
        source["kind"] = "local-override"
    else:
        source["clone_from"] = source["repository"]
        source["kind"] = "public"

    _require(type(config.get("seed")) is int, "seed must be an integer")
    _positive_int(config, "blocks")
    for key in ("agent_timeout_seconds", "command_timeout_seconds", "terminate_grace_seconds"):
        _positive_int(config, "limits", key)

    descriptions = config.get("descriptions")
    _require(isinstance(descriptions, dict) and descriptions, "descriptions must be a non-empty object")
    for key in descriptions:
        _require(DESCRIPTION_RE.match(key), f"description key {key!r} must look like D1")
    config["descriptions"] = {k: str(expand(v, base)) for k, v in descriptions.items()}
    config["common_instructions"] = str(expand(config["common_instructions"], base))
    for key, value in list(config["descriptions"].items()) + [("common", config["common_instructions"])]:
        _require(Path(value).is_file(), f"description file for {key} not found: {value}")

    assets = config.get("assets", {})
    missing = [k for k in ASSET_KEYS if k not in assets]
    _require(not missing, f"assets missing: {missing}")
    config["assets"] = {k: str(expand(v, base)) for k, v in assets.items()}
    for key, value in config["assets"].items():
        _require(Path(value).exists(), f"asset {key} not found: {value}")

    configurations = config.get("configurations")
    _require(isinstance(configurations, list) and configurations, "configurations must be a non-empty list")
    for index, entry in enumerate(configurations):
        validate_configuration(entry, index)
    ids = [c["id"] for c in configurations]
    _require(len(ids) == len(set(ids)), "configuration IDs must be unique")

    isolation = config.get("isolation")
    _require(isinstance(isolation, dict) and isolation.get("mode") in ISOLATION_MODES,
             f"isolation.mode must be one of {sorted(ISOLATION_MODES)}")
    if isolation["mode"] == "none":
        _require(all(c["harness"] == "fake" for c in configurations),
                 "isolation.mode 'none' is allowed only for fake test agents; real agents require a sandbox")
    # macos-seatbelt is validated at run/preflight time so `plan` works on any host.
    config["isolation"]["deny_read"] = [str(expand(p, base)) for p in isolation.get("deny_read", [])]
    config["isolation"]["write_allow"] = [str(expand(p, base)) for p in isolation.get("write_allow", [])]

    deps = config.get("dependencies", {})
    _require(deps.get("install") in ("pip", "system-site"), "dependencies.install must be 'pip' or 'system-site'")
    config["harness_root"] = str(Path(__file__).resolve().parent.parent)
    config["config_path"] = str(path)
    config["python_executable"] = resolve_python(config.get("python"))
    return config


def resolve_python(selection):
    if selection is None:
        return sys.executable
    _require(isinstance(selection, str) and selection.strip(), "python must be null or an executable/path")
    found = shutil.which(selection)
    _require(found, f"configured Python interpreter {selection!r} was not found; set \"python\": null "
                    "to use the interpreter launching the harness")
    return found


def asset_hashes(config):
    """Content hashes of every frozen input file, keyed by portable names."""
    hashes = {f"description:{k}": sha256_file(v) for k, v in sorted(config["descriptions"].items())}
    hashes["common_instructions"] = sha256_file(config["common_instructions"])
    for key, value in sorted(config["assets"].items()):
        target = Path(value)
        if target.is_dir():
            for child in sorted(p for p in target.rglob("*") if p.is_file()):
                hashes[f"{key}:{child.relative_to(target).as_posix()}"] = sha256_file(child)
        else:
            hashes[key] = sha256_file(target)
    controls_dir = Path(config["assets"]["controls"]).parent
    for child in sorted(controls_dir.glob("*.patch")):
        hashes[f"control:{child.name}"] = sha256_file(child)
    return hashes


PORTABLE_EXCLUDE = {"harness_root", "config_path", "python_executable"}


def portable_settings(config):
    """Settings that define the protocol, with machine-local paths removed."""
    settings = {k: v for k, v in config.items() if k not in PORTABLE_EXCLUDE}
    settings = copy.deepcopy(settings)
    settings["descriptions"] = sorted(settings["descriptions"])
    settings.pop("common_instructions", None)
    settings["assets"] = sorted(settings["assets"])
    source = settings["source"]
    source.pop("clone_from", None)
    source.pop("local_override", None)
    home = str(Path.home())
    settings["isolation"] = {
        **settings["isolation"],
        "deny_read": [p.replace(home, "~") for p in settings["isolation"]["deny_read"]],
        "write_allow": [p.replace(home, "~") for p in settings["isolation"]["write_allow"]],
    }
    return settings


def protocol_hash(config):
    return sha256_bytes(canonical({"settings": portable_settings(config), "assets": asset_hashes(config)}))


def configuration(config, config_id):
    for entry in config["configurations"]:
        if entry["id"] == config_id:
            return entry
    raise ConfigError(f"unknown configuration {config_id!r}")
