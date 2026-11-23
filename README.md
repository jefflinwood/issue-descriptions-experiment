# Experiment 002: Issue specification

**Status: protocol `002.2` (reorder workload) collected 2026-11-21 in `results/main-003`; see
[findings/002.2-reorder.md](findings/002.2-reorder.md).**
An earlier workload, URL order search (protocol `002.1`), ran all 80 attempts on
2026-11-21. It is kept as an introductory finding in
[findings/002.1-url-search-introductory.md](findings/002.1-url-search-introductory.md).

The experiment asks how the description of one software task affects what coding
agents build. The current task is a real Ridgeline Coffee issue, *“Let customers
reorder a past order.”* It is written four ways:

- **D1**: vague.
- **D2**: clarified stakeholder answers.
- **D3**: agent-ready, with examples, entry points, constraints, and verification.
- **D4**: the D3 facts inside a realistic, noisy issue thread (abandoned ideas, reversed
  decisions, declined scope creep, chatter).

Each description is run against four model-plus-harness configurations: Codex
CLI with `gpt-6.1-terra` and `gpt-6-luna`, and Claude Code with
`claude-sonnet-5-5` and `claude-haiku-5-5`. The main plan is 4 × 4 × 5 repeats =
**80 sequential attempts**. Each attempt is one fresh agent session in an isolated
clone, followed by independent evaluation in a clean checkout.

Implementation spec: [CODING_SPEC.md](CODING_SPEC.md). Protocol:
[benchmark/protocol.md](benchmark/protocol.md).

## Layout

```text
issue_specification/       harness (standard library only)
  cli.py                   plan, preflight, controls, run, report
  config.py                strict validation, portable protocol hash
  schedule.py              seeded randomized complete blocks
  runner.py                attempt lifecycle: setup → agent → capture → evaluation
  processes.py             command records, timeouts, process-group cleanup
  isolation.py             macOS Seatbelt profile + canary probe
  evaluation.py            immutable suites, phases, per-criterion results, controls
  reporting.py             JSON/CSV/Markdown from saved records only
  adapters/                codex.py, claude_code.py, fake (tests only)
benchmark/
  config.json              protocol 002.2-draft.1 (reorder)
  protocol.md, stakeholder-answers.md, review-rubric.md, requirements.json
  constraints.txt          pip freeze of the validated Ridgeline environment (Python 3.14)
  descriptions/            common.txt + D1–D4
  evaluation/              acceptance.py, fixtures.py, baseline_surface.json (never agent-visible)
  reference/               reference.patch + negative-controls/ (never agent-visible)
  archive/002.1-url-search/  the retired workload exactly as run (reproduces hash 205b9bde…)
findings/                  written-up results (002.1 introductory, 002.2 reorder + post-hoc script)
tests/                     unittest suite; fixtures/ holds synthetic CLI traces
results/                   ignored; append-only run artifacts
```

## Install

You need macOS for real attempts (the agent sandbox is Seatbelt), Git, and
Python 3.11 or later. Validation was done with 3.14.5. You also need the pinned
CLI versions (`codex-cli 0.171.0`, `2.1.330 (Claude Code)`) logged in through
their usual setup. The harness has no Python dependencies. Ridgeline's
dependencies are installed into fresh per-attempt virtualenvs from
`benchmark/constraints.txt`.

Authentication stays with each CLI (`~/.codex`, Claude Code's keychain or
`ANTHROPIC_API_KEY`). The harness never copies credentials. It passes
auth-related environment variables to the CLI by name only, and records variable
names, never their values. Preflight keeps only a coarse auth-mode word (for
example `chatgpt` or `subscription`). Claude configurations declare
`auth_mode: oauth`, which runs `--safe-mode`, or `api-key`, which runs `--bare`
and requires `ANTHROPIC_API_KEY`.

## Commands

```sh
python3 -m issue_specification.cli plan                  # 80-attempt schedule; no network, no agents
python3 -m issue_specification.cli preflight             # CLIs, flags, auth presence, public SHA, sandbox, deps, baseline
python3 -m issue_specification.cli preflight --probe-models   # BILLABLE: one trivial prompt per model
python3 -m issue_specification.cli controls              # evaluator self-check: reference + negative controls
python3 -m issue_specification.cli run --cohort pilot --run-index 0 --output results/pilot
python3 -m issue_specification.cli run --output results/main-003            # full matrix (paid)
python3 -m issue_specification.cli run --output results/main-003 --continue # only attempts not yet started
python3 -m issue_specification.cli report --output results/main-003
```

`run` is the only command that launches paid agents. A single `--run-index`
attempt must carry `--cohort pilot` (or `development`). Pilot outputs belong in
their own directory and never mix with main reports, because reports reject other
cohorts and protocol hashes. Existing attempt directories are never rerun or
overwritten. `--continue` skips any attempt that has a directory, including
failed ones. Only `setup_failed` and `infrastructure_failure` attempts can be
replaced: `run --replace <id> --reason "..."` creates a new linked `…-r1` record
and keeps the original. Reports are written with a UTC timestamp suffix.

## Source configuration

The default source is the public `https://github.com/jefflinwood/ridgeline.git`
at full SHA `353c3ccdf3acdaae9acae78b5132241061b5decc`. Preflight confirms the
remote serves that SHA. For offline development, pass
`--source-override ~/Projects/ridgeline` or set `source.local_override`. Only
committed objects are cloned, so uncommitted local changes are excluded. The
override is recorded as `source_kind: local-override` and changes the protocol
hash, so it can never merge with public-source results. Each attempt makes its
own bare clone, so the user's checkout and `ridgeline.db` are never touched.

## Model selection and freeze policy

The model IDs are the latest family members in the provider catalogs, checked on
2026-11-20 and recorded in `model_selection`. They are passed exactly, never
aliased, and never substituted. A missing model or access error fails the
attempt as `agent_error` with `error_kind: access_or_model`. Codex JSONL reports
no backend model, so `observed_model` is null for Codex. Claude Code reports the
model in stream-json, and the harness records it.

Effort is `medium` for all four configurations, and Claude thinking uses the CLI
default. These are *provisional*. Choose and freeze them in pilots, together with
time limits. Any change to a model, CLI version, setting, description, or
evaluator file changes the protocol hash and requires a new protocol version and
output directory. The freeze checklist is in `benchmark/protocol.md`.

## Isolation

The agent runs under `sandbox-exec` with a generated profile, saved per attempt as
`sandbox.sb`:

- **Reads hidden:** `~/Projects` (this harness, all results, the Ridgeline
  source, Experiment 001, sibling projects), the attempt's own run directory, and
  CLI memory, skill, plugin, session, and history paths. Only the workspace is
  re-allowed.
- **Writes allowed:** the workspace, temporary directories, and CLI state
  directories.
- **Ancestor metadata:** the workspace's parent directories can be `stat`-ed (Codex
  exits with EPERM otherwise), but not listed or read into.
- **Git and pip:** the candidate clone has no remote, Git is limited to the file
  protocol, and pip runs with no index. The evaluation suite is created only after
  the agent exits.
- **Validation:** preflight checks the boundary with read and write canaries.
  `tests/test_runner_integration.py` repeats them from inside a sandboxed fake
  agent.

Known limits, recorded with each run:

- Seatbelt cannot filter network traffic by hostname, so HTTPS egress is still
  reachable.
- macOS refuses nested sandboxes. Codex runs with its own sandbox bypassed, and
  Claude Code skips permission prompts. The harness sandbox is the boundary for
  both.
- Codex auto-loads `AGENTS.md`; Claude Code does not. The common wrapper points
  every agent at it and states the treatment-independent correction that the
  repository does have tests.

## Metrics and outcomes

- **Primary:** the session completed (exit 0, no timeout, nonempty change), all
  acceptance criteria AC01–AC10 pass, and the immutable original regression suite
  passes. Rates are reported per scheduled attempt and per agent-started attempt,
  with Wilson 95% intervals.
- **Minimal common outcome:** a reorder control leads to one new order with the
  original active products (directly, or after submitting the prefilled form), and
  no reorder aimed at another account's order creates, changes, or reveals
  anything. Both are justified for D1, which doesn't say how reordering works.
- **Secondary:**
  - Per-criterion results.
  - Explicit violations vs. unspecified-choice mismatches, from the disclosure
    inventory in `requirements.json`.
  - Candidate-test results, original-test touches, and out-of-scope files.
  - Human review against `review-rubric.md`.
  - Termination counts.
  - Total, setup, agent, and evaluation time.
  - Tokens and tool calls (null when unreported, never zero).
  - Claude Code's reported cost, labeled a CLI estimate.

No dated pricing table is configured, so no billable cost is computed.

## Artifacts

Each attempt directory holds:

- **Inputs:** `attempt.json` (cell, hashes, replacement link),
  `config.resolved.json`, and `prompt.txt`.
- **Provenance:** `metadata.json` (platform, interpreter, CLI and Git versions,
  harness file hashes), `candidate-dependencies.txt`, and
  `evaluator-dependencies.txt` (`pip freeze --all`).
- **Agent run:** `sandbox.sb`, `agent.json` (argv, environment variable names,
  settings), `events.jsonl` (UTC and monotonic timestamps), `commands/` (every
  command's stdout and stderr, including raw CLI events), and `usage.json`.
- **Results:** `final.patch`, `evaluation.json`, and `summary.json`. A summary is
  written even when the attempt fails.

Each attempt deletes its two virtualenvs (about 210 MB) once its summary is
written. The exact package lists stay in `candidate-dependencies.txt` and
`evaluator-dependencies.txt`, and the environments can be rebuilt from
`constraints.txt`. A kept attempt is about 5 MB, so 80 attempts need about 0.4 GB.
Set `"retain_environments": true` to keep them. On 2026-11-21 the disk filled during
a pilot; the virtualenvs of all earlier runs, including `main-002`, were removed
with the owner's approval, and no evidence files were deleted.
Artifacts can contain source code and agent transcripts. Review them
before publishing, and do not publish `~/.codex`, keychain, or environment
contents.

## Evaluation integrity

- **Self-check:** `controls` applies `reference.patch` and seven negative controls
  to the baseline: no-op, no ownership check, inactive products included, dates
  copied, historical prices honored, repeated lines overwritten, and a one-click
  order placed without the form. The reference passes every criterion. Each control
  fails exactly its expected criteria while the original suite still passes.
- **Checks:** acceptance checks use deterministic fixtures independent of the
  user's database and the date. They exercise HTTP responses and submit parsed
  forms the way a browser would.
- **Hashing:** suites are hashed before and after every phase.
- **Patch check:** the captured patch is re-applied to a clean checkout and must
  reproduce the captured tree.

## Harness tests

```sh
python3 -m pip install pytest==8.4.2
python3 -m unittest discover -s tests -t .
```

The suite covers balanced schedules, config validation, protocol hashing,
disclosure leaks in D1 and the wrapper, parsers on synthetic traces, process-group
timeout cleanup, immutable outputs, setup failures, capture, sandbox canaries
(macOS), report denominators, and protocol/cohort rejection. With a Ridgeline
clone (`ISSUE_SPEC_RIDGELINE`, default `~/Projects/ridgeline`) and its
dependencies importable, it also runs a fake-agent pipeline through clean
evaluation and reporting, plus the evaluator controls. CI does the same on Linux
without the sandbox (fake agents only) and never calls a model.

## Limitations

This is one task, on one host, with five repeats per cell. That is exploratory
and cannot establish a general model or specification ranking.

Cross-provider differences include harness behavior: system prompts, tools, and
context management all differ. Within-model comparisons are cleaner.

D1–D3 compare packages of information, not writing style. D3/D4 test verbosity
only if the pending equivalence review agrees they are equivalent.

The acceptance checks are public to the evaluator, not secret from a
deliberately adversarial agent outside the sandbox.

The Claude Code parser was validated only on synthetic traces. Re-check it on a
pilot trace.

## Provenance

The harness adapts mechanisms from Experiment 001
(`~/Projects/agent-native/experiments/agent-concurrency-time`, commit
`81b268c1fb59af29e5ea66e027c40ad953bef304`, same author, no license file present).
Those mechanisms are fresh clones, explicit revisions, constraints, command
records, process-group timeouts, immutable outputs, and reports from saved
artifacts. Its concurrency scheduler, persistent-agent treatment, and ten-task
workload are not used.
