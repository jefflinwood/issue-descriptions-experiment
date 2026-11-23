"""Coding-CLI adapter interface and normalized usage records.

Every normalized numeric field is ``None`` when the CLI did not report it. A
missing value is never coerced to zero.
"""
from __future__ import annotations

import json
from pathlib import Path

USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "input_tokens_total",
                "output_tokens", "reasoning_tokens", "tool_calls")

BASE_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "__CF_USER_TEXT_ENCODING")


def empty_usage():
    return {key: None for key in USAGE_FIELDS}


def read_jsonl(path):
    records, invalid = [], 0
    try:
        text = Path(path).read_text(errors="replace")
    except FileNotFoundError:
        return [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            invalid += 1
            continue
        if isinstance(value, dict):
            records.append(value)
    return records, invalid


ACCESS_MARKERS = ("model_not_found", "does not exist", "not have access", "not available", "unsupported model",
                  "invalid model", "unknown model", "not supported", "unauthorized", "authentication",
                  "not logged in", "please log in", "invalid api key", "401", "403")


def classify_error(text):
    lowered = (text or "").lower()
    if any(marker in lowered for marker in ACCESS_MARKERS):
        return "access_or_model"
    if "rate limit" in lowered or "429" in lowered or "overloaded" in lowered:
        return "rate_limit"
    return "other" if lowered.strip() else None


class Adapter:
    harness = "base"
    #: Flags this adapter passes; preflight verifies each appears in the CLI's --help output.
    required_flags: tuple = ()
    #: Credential/selection variables passed through by name only (values never recorded).
    passthrough_env: tuple = ()
    help_argv: tuple = ("--help",)

    def __init__(self, entry):
        self.entry = entry

    def version_argv(self):
        return [self.entry["executable"], "--version"]

    def prepare(self, workspace: Path):
        """Stage adapter-owned files inside the workspace (outside the repository) before launch."""

    def argv(self, repo: Path, prompt_path: Path):
        raise NotImplementedError

    def environment(self, base_env, extra):
        env = {k: base_env[k] for k in BASE_ENV if k in base_env}
        env.update({k: base_env[k] for k in self.passthrough_env if k in base_env})
        env.update(extra)
        return env

    def settings_record(self):
        """Effort/thinking/isolation choices the adapter applies, for provenance."""
        return {}

    def parse(self, stdout_path, stderr_path=None):
        """Return dict(usage, raw_usage, observed_model, session_ids, final_message, errors, flags)."""
        raise NotImplementedError

    def auth_status_argv(self):
        return None

    def startup_check(self, repo):
        """(argv, success marker) for a free check that runs the real argv to the point of reading the
        prompt and exits on empty stdin without contacting the provider; None if unsupported."""
        return None

    @staticmethod
    def sanitize_status(text):
        """Keep only coarse auth-mode words from a status command; never secrets."""
        lowered = (text or "").lower()
        for mode in ("chatgpt", "api key", "oauth", "subscription", "claude.ai", "console", "logged in",
                     "not logged in"):
            if mode in lowered:
                return mode
        return "unknown"


def which(executable):
    from shutil import which as _which
    return _which(executable)


def merged_env_for_record(env):
    """Names only. Values may contain credentials."""
    return sorted(env)

