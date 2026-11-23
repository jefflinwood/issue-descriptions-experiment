"""Claude Code CLI adapter (``claude -p --output-format stream-json``).

Stream records: ``system``/``init`` (session_id, model, tools), ``assistant``
(message.id, message.model, message.usage, content tool_use blocks; the same
message can be emitted more than once while streaming), ``user`` (tool results)
and a final ``result`` (usage totals, total_cost_usd, num_turns, is_error,
modelUsage). Normalized usage is taken from the single ``result`` record; only if
it is missing are assistant messages de-duplicated by message ID and summed.
The parser fixture is synthetic and must be re-validated against a pilot trace.
"""
from __future__ import annotations

import json

from .base import Adapter, classify_error, empty_usage, read_jsonl

DELEGATION_TOOLS = {"Agent", "Task"}


class ClaudeCodeAdapter(Adapter):
    harness = "claude_code"
    required_flags = ("--print", "--model", "--output-format", "--verbose", "--no-session-persistence",
                      "--effort", "--strict-mcp-config", "--mcp-config", "--disable-slash-commands",
                      "--tools", "--disallowedTools", "--dangerously-skip-permissions", "--safe-mode", "--bare",
                      "--no-chrome")
    passthrough_env = ("ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "SSL_CERT_FILE")
    tools = "Bash,Read,Edit,Write"
    denied_tools = "Agent,Task,WebFetch,WebSearch"

    def argv(self, repo, prompt_path):
        e = self.entry
        mode = ["--bare"] if e["auth_mode"] == "api-key" else ["--safe-mode"]
        return [e["executable"], "--print",
                "--model", e["model"],
                "--effort", e["effort"],
                "--output-format", "stream-json", "--verbose",
                "--no-session-persistence",
                *mode,                                   # no user CLAUDE.md, skills, plugins, hooks, MCP, memory
                "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": {}}),
                "--disable-slash-commands",
                "--no-chrome",
                "--tools", self.tools,
                "--disallowedTools", self.denied_tools,  # no subagent delegation or web access
                "--dangerously-skip-permissions"]        # noninteractive; the harness Seatbelt is the boundary

    def environment(self, base_env, extra):
        env = super().environment(base_env, extra)
        if self.entry.get("thinking_tokens") is not None:
            env["MAX_THINKING_TOKENS"] = str(self.entry["thinking_tokens"])
        env["DISABLE_AUTOUPDATER"] = "1"
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        return env

    def settings_record(self):
        e = self.entry
        return dict(effort=e["effort"],
                    thinking_tokens=e.get("thinking_tokens"),
                    thinking="CLI default (not pinned)" if e.get("thinking_tokens") is None else "MAX_THINKING_TOKENS",
                    auth_mode=e["auth_mode"],
                    customization="--bare" if e["auth_mode"] == "api-key" else "--safe-mode",
                    tools=self.tools, disallowed_tools=self.denied_tools, mcp="none (strict empty config)",
                    session_persistence="disabled", permissions="skip (outer Seatbelt)",
                    repository_instructions="AGENTS.md not auto-loaded by Claude Code; the common wrapper points to it")

    def auth_status_argv(self):
        return [self.entry["executable"], "auth", "status"]

    def parse(self, stdout_path, stderr_path=None):
        records, invalid = read_jsonl(stdout_path)
        usage = empty_usage()
        session_ids, errors, init_model, result = set(), [], None, None
        assistant_usage, tool_ids, models, delegation = {}, set(), set(), 0
        final_message = None
        for record in records:
            kind = record.get("type")
            if record.get("session_id"):
                session_ids.add(record["session_id"])
            if kind == "system" and record.get("subtype") == "init":
                init_model = record.get("model")
            elif kind == "assistant" and isinstance(record.get("message"), dict):
                message = record["message"]
                if message.get("model"):
                    models.add(message["model"])
                if message.get("id") and isinstance(message.get("usage"), dict):
                    assistant_usage[message["id"]] = message["usage"]  # last copy wins; no double counting
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_ids.add(block.get("id"))
                        if block.get("name") in DELEGATION_TOOLS:
                            delegation += 1
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        final_message = block["text"]
            elif kind == "result":
                result = record
        raw_usage = None
        source = None
        if result and isinstance(result.get("usage"), dict):
            raw_usage, source = result["usage"], "result"
        elif assistant_usage:
            raw_usage, source = self._sum(assistant_usage.values()), "assistant messages de-duplicated by id"
        if raw_usage:
            usage["input_tokens"] = raw_usage.get("input_tokens")
            usage["cached_input_tokens"] = raw_usage.get("cache_read_input_tokens")
            usage["cache_write_input_tokens"] = raw_usage.get("cache_creation_input_tokens")
            usage["output_tokens"] = raw_usage.get("output_tokens")
            parts = (usage["input_tokens"], usage["cached_input_tokens"], usage["cache_write_input_tokens"])
            usage["input_tokens_total"] = None if any(p is None for p in parts) else sum(parts)
        if records:
            usage["tool_calls"] = len(tool_ids)
        if result:
            if result.get("is_error") or (result.get("subtype") not in (None, "success")):
                errors.append(str(result.get("result") or result.get("subtype")))
            if result.get("result"):
                final_message = result["result"]
        stderr_text = ""
        if stderr_path:
            try:
                with open(stderr_path, errors="replace") as stream:
                    stderr_text = stream.read()[-4000:]
            except OSError:
                pass
        observed = sorted(models) or ([init_model] if init_model else [])
        model_usage = (result or {}).get("modelUsage")
        return dict(usage=usage, raw_usage=raw_usage, usage_aggregation=source,
                    observed_model=observed[0] if len(observed) == 1 else (observed or None),
                    observed_model_source="stream-json assistant/init records" if observed else None,
                    model_usage=model_usage, session_ids=sorted(session_ids), final_message=final_message,
                    errors=errors, error_kind=classify_error(" ".join(errors) + stderr_text) if errors else None,
                    delegation_events=delegation, invalid_lines=invalid, event_count=len(records),
                    num_turns=(result or {}).get("num_turns"),
                    cli_reported_cost_usd=(result or {}).get("total_cost_usd"),
                    cli_reported_cost_provenance=("Claude Code total_cost_usd: CLI-computed estimate, not an "
                                                  "observed charge (especially under subscription auth)")
                    if result and result.get("total_cost_usd") is not None else None)

    @staticmethod
    def _sum(usages):
        keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
        usages = list(usages)
        return {k: (None if any(u.get(k) is None for u in usages) else sum(u[k] for u in usages)) for k in keys}
