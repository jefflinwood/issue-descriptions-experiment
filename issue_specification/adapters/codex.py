"""Codex CLI adapter (``codex exec --json``).

Event shapes were checked against saved Experiment 001 traces (codex-cli 0.167):
``thread.started`` (thread_id), ``item.started``/``item.completed`` (item.type in
command_execution, file_change, agent_message, ...), ``turn.completed`` (usage with
input_tokens [inclusive of cached], cached_input_tokens, cache_write_input_tokens,
output_tokens, reasoning_output_tokens), ``turn.failed`` and ``error``. The JSONL
stream does not report a backend model, so the observed model is null.
"""
from __future__ import annotations

from .base import Adapter, classify_error, empty_usage, read_jsonl

TOOL_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call", "tool_call"}
DELEGATION_ITEMS = {"collab_tool_call"}


class CodexAdapter(Adapter):
    harness = "codex"
    required_flags = ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json", "--model",
                      "--dangerously-bypass-approvals-and-sandbox", "--cd", "--config", "--disable",
                      "--skip-git-repo-check")
    passthrough_env = ("CODEX_HOME", "OPENAI_API_KEY", "OPENAI_BASE_URL", "SSL_CERT_FILE")
    help_argv = ("exec", "--help")

    def argv(self, repo, prompt_path):
        e = self.entry
        return [e["executable"], "exec",
                "--ignore-user-config",          # no inherited model/MCP/profile config; auth still from CODEX_HOME
                "--ignore-rules",                # no user/project execpolicy rules
                "--ephemeral",                   # fresh session, nothing persisted for resumption
                "--json",
                "--model", e["model"],
                "-c", f'model_reasoning_effort="{e["reasoning_effort"]}"',
                "--disable", "multi_agent",       # no subagent delegation
                "-c", 'web_search="disabled"',
                # macOS refuses nested Seatbelt sandboxes; the harness sandbox is the boundary.
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "--cd", str(repo),
                "-"]

    def settings_record(self):
        return dict(reasoning_effort=self.entry["reasoning_effort"], inner_sandbox="disabled (outer Seatbelt)",
                    user_config="ignored", rules="ignored", session_persistence="ephemeral",
                    multi_agent="disabled", web_search="disabled (requested via -c; verify in pilot)",
                    repository_instructions="AGENTS.md auto-loaded by Codex")

    def auth_status_argv(self):
        return [self.entry["executable"], "login", "status"]

    def startup_check(self, repo):
        return self.argv(repo, None), "No prompt provided"

    def parse(self, stdout_path, stderr_path=None):
        records, invalid = read_jsonl(stdout_path)
        usage = empty_usage()
        raw_usage, session_ids, errors, messages = [], [], [], []
        tool_ids, delegation = set(), 0
        for record in records:
            kind = record.get("type")
            if kind == "thread.started" and record.get("thread_id"):
                session_ids.append(record["thread_id"])
            elif kind == "turn.completed" and isinstance(record.get("usage"), dict):
                raw_usage.append(record["usage"])
            elif kind == "item.completed" and isinstance(record.get("item"), dict):
                item = record["item"]
                if item.get("type") in TOOL_ITEMS:
                    tool_ids.add(item.get("id") or id(item))
                if item.get("type") in DELEGATION_ITEMS:
                    delegation += 1
                if item.get("type") == "agent_message" and item.get("text"):
                    messages.append(item["text"])
            elif kind in ("turn.failed", "error"):
                error = record.get("error") if isinstance(record.get("error"), dict) else record
                errors.append(str(error.get("message") or error))
        if raw_usage:
            # One `codex exec` call is one turn; if several turns appear, each reports its own usage.
            def total(key):
                values = [u.get(key) for u in raw_usage]
                return None if any(v is None for v in values) else sum(values)
            usage.update(input_tokens=total("input_tokens"), cached_input_tokens=total("cached_input_tokens"),
                         cache_write_input_tokens=total("cache_write_input_tokens"),
                         output_tokens=total("output_tokens"), reasoning_tokens=total("reasoning_output_tokens"))
            usage["input_tokens_total"] = usage["input_tokens"]  # OpenAI input_tokens already includes cached
        if records:
            usage["tool_calls"] = len(tool_ids)
        stderr_text = ""
        if stderr_path:
            try:
                with open(stderr_path, errors="replace") as stream:
                    stderr_text = stream.read()[-4000:]
            except OSError:
                pass
        return dict(usage=usage, raw_usage=raw_usage,
                    usage_aggregation="sum of turn.completed usage" if raw_usage else None,
                    observed_model=None, observed_model_source=None,
                    session_ids=sorted(set(session_ids)), final_message=messages[-1] if messages else None,
                    errors=errors, error_kind=classify_error(" ".join(errors) + stderr_text) if errors else None,
                    delegation_events=delegation, invalid_lines=invalid, event_count=len(records),
                    cli_reported_cost_usd=None)
