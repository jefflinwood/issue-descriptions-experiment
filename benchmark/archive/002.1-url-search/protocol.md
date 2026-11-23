# Experiment 002 protocol — issue specification

Version `002-draft.1`, prepared 2026-11-20. No runs have been collected and no
findings are implied. Settings marked *provisional* are chosen in pilots and then
frozen. Freezing creates a new protocol version, a new protocol hash, and a new
output directory.

## Question

For one real Ridgeline task, how does the issue description (D1 vague, D2
clarified, D3 agent-ready, D4 expanded) affect implementation outcomes for four
model-plus-coding-harness configurations? The outcomes are correctness,
regressions, review findings, completion, time, and reported resource use.

## Units and design

- **Attempt**: one fresh, noninteractive agent session on one fresh clone of
  Ridgeline `353c3ccdf3acdaae9acae78b5132241061b5decc`. Each attempt has no human
  clarification, no resumed session, no nested agents, and no integration agent.
- **Cells**: 4 configurations × 4 descriptions = 16.
- **Schedule**: 5 randomized complete blocks (seed `20261120`), each with all 16
  cells once, giving 80 attempts. They run sequentially on one host. The planned
  order is in `experiment.json`. The actual order is the attempt start times in
  each `summary.json`.
- Five repeats per cell is exploratory, not a power calculation. Counts and stop
  rules are frozen after pilots. No cell is extended or cut because of its results.

## Configurations

| ID | Harness | Model (requested) | Effort | Notes |
|---|---|---|---|---|
| terra | Codex CLI 0.171.0 | `gpt-6.1-terra` | reasoning medium (provisional) | Codex auto-loads AGENTS.md |
| luna | Codex CLI 0.171.0 | `gpt-6-luna` | reasoning medium (provisional) | |
| sonnet | Claude Code 2.1.330 | `claude-sonnet-5-5` | `--effort medium`, CLI-default thinking (provisional) | `--safe-mode` (OAuth) |
| haiku | Claude Code 2.1.330 | `claude-haiku-5-5` | `--effort medium`, CLI-default thinking (provisional) | effort support to verify in pilot |

Model IDs were taken from the provider catalogs on 2026-11-20. Before pilots,
recheck the catalogs and account access (`preflight --probe-models`). Requested IDs
are passed explicitly and never substituted. Observed backend model IDs are
recorded where the CLI reports them (Claude Code stream-json) and are null where
it does not (Codex JSONL). Identically named effort settings do not imply equal
compute across providers.

Unavoidable harness differences, recorded per attempt in `agent.json`:
- System prompts, tool sets, and context management are each CLI's own.
- Codex auto-loads `AGENTS.md`. Claude Code does not, so the common wrapper points
  every agent to it and states the fixed correction about tests.
- macOS refuses nested Seatbelt sandboxes. Codex's own sandbox is disabled
  (`--dangerously-bypass-approvals-and-sandbox`) and Claude Code skips permission
  prompts. The harness Seatbelt profile is the boundary for both.
- Claude Code is limited to `Bash, Read, Edit, Write`, with Agent/Task/Web tools
  denied. Codex has multi-agent disabled and web search disabled through `-c`
  (whether the CLI honors this is checked in the pilot).

## Treatments

`descriptions/common.txt` is prepended to every description. It requires
preserving access rules, writing focused tests, and documenting in README. It
corrects AGENTS.md's claim that there are no tests, and permits recording
assumptions and proceeding. It does not reveal the contract.

The requirement-by-description disclosure inventory is in `requirements.json`.
D3 and D4 share every fact, example, entry point, constraint, and verification
note. D4 is longer prose only. **A semantic-equivalence review of D3/D4 is
required before freezing** (recorded in `requirements.json`). D1–D3 compare
practical packages of information. They do not isolate style or length.

## Isolation

Each attempt's agent runs under `sandbox-exec` with a generated profile, saved as
`sandbox.sb`:
- Read and write are denied under `~/Projects` (the harness, every output, the
  Ridgeline source, Experiment 001, sibling projects), the attempt's own run
  directory, and CLI memory, skill, plugin, session, and history paths. Only the
  attempt workspace is re-allowed.
- Writes are allowed only in the workspace, temporary directories, and CLI state
  paths.
- The workspace's parent directories allow metadata (`stat`) only, because Codex
  resolves its working directory through them. They cannot be listed, and nothing
  inside them is readable.
- CLI output reaches the log files through pipes. Inherited descriptors pointing
  into hidden paths make Node.js abort at startup.
- The candidate clone has no Git remote, `GIT_ALLOW_PROTOCOL=file`, and
  `PIP_NO_INDEX=1`. Dependencies are installed before the agent starts.
- The evaluation suite is created only after the agent exits.

**Deviation:** Seatbelt cannot filter network traffic by hostname, so general
HTTPS egress stays reachable during the agent phase. Preflight validates the
sandbox with read and write canaries (spec, evaluator, stakeholder answers,
reference patch, other descriptions, a sibling result, sampled deny paths).

## Lifecycle and limits

Setup, the agent, capture, and evaluation are separate recorded phases. Limits
are 1,800 s per agent session and 600 s per setup or evaluation command, with
process-group SIGTERM, then SIGKILL after 10 s (provisional until pilots). There
are no automatic retries and no resumption.

The harness stages and commits the working tree, including untracked source and
test files. Environments, databases, caches, and `.env` files are excluded. Agent
commits are counted and still captured. The patch is re-applied to a clean
checkout, and its tree must equal the captured tree.

## Outcomes

- **Primary**: the attempt completed (agent exit 0, no timeout, nonempty change),
  passes every criterion AC01–AC11, and passes the immutable original regression
  suite. Rates are reported per scheduled attempt (failures included) and per
  agent-started attempt.
- **Minimal common outcome**: MIN-SEARCH (some URL-addressable search narrows the
  list, discovered from a GET form or common parameter names, so a D1 choice of
  name is not penalized) **and** MIN-AUTH (no search parameter shows a customer
  another account's order) **and** regression pass, in a completed attempt.
- **Secondary**: per-criterion results; explicit violations vs. partially specified
  vs. unspecified mismatches (from the disclosure inventory); candidate-test
  results; original-test touches; out-of-scope files; human-review rubric scores;
  termination counts; total/setup/agent/evaluation time; tokens and tool calls
  (null when unreported); CLI-reported cost (labeled an estimate).
- Timed-out or failed attempts can still have their snapshot evaluated (labeled
  `snapshot_after_<termination>`). They never count as primary successes.

## Failure handling and replacement policy

All attempts remain in the dataset. Only `setup_failed` and
`infrastructure_failure` attempts may be replaced, at most once each, through
`run --replace <id> --reason <text>`. The replacement gets a new linked ID
(`…-r1`). Reports show both records and the replacement count, and use the latest
record for the scheduled cell. Agent errors, timeouts, access errors, and
incomplete sessions are outcomes and are never replaced.

## Analysis

Report raw counts, rates with Wilson 95% intervals, and time/usage distributions
per configuration × description, without pooling configurations. Within-model
description comparisons are the cleanest contrasts. Cross-provider differences
include harness behavior. One task with five repeats cannot establish a general
ranking. More detail may also constrain implementations; longer is not assumed
better. Billable cost is reported only from dated pricing or provider-reported
cost with its provenance. Subscription access is not a per-run charge.

## Freeze checklist

1. Stakeholder answers and the requirement inventory reviewed.
2. D3/D4 equivalence review recorded.
3. `controls` passes: reference passes every criterion; the baseline and the
   no-auth, AND/OR, OR-with-filters, wildcard, and broken-form controls fail
   exactly their expected criteria.
4. `preflight --probe-models` passes for all four configurations. Observed model
   IDs, effort/thinking support, and CLI versions recorded.
5. Pilots (`--cohort pilot`, separate output) confirm the pipeline for every
   configuration, the parser against real traces, and time limits.
6. Version bumped to `002.1` (no `draft`), limits and settings frozen, and the
   main collection started in a new output directory.

## Freeze record — protocol 002.1 (2026-11-21)

- **Pilots:** cohort `pilot` in `results/pilot` (002-draft.1; both Claude attempts hit a
  fixed harness bug) and `results/pilot-2` (Claude reruns). All four configurations
  completed. Pilots are excluded from main reports.
- **Settings frozen:** effort `medium` for all four configurations; Claude thinking
  at the CLI default; limits of 1,800 s per agent session and 600 s per command. All
  pilot sessions finished in under 2 minutes.
- **AC11 changed after pilots:** the README component accepts a `?q=` example *or* a
  README that names the `q` parameter. The pilot that prompted it,
  `002-b1-terra-d1`, documented "the `q` URL parameter" without an example URL.
- **Model access:** `preflight --probe-models` passed for all four IDs. Claude Code
  reported the exact requested model IDs; Codex does not report one.
- **Deviation:** the D3/D4 semantic-equivalence review (checklist item 2) was not
  recorded before freezing. The study owner chose to proceed. D3/D4 contrasts must be
  reported as conditional on a later equivalence review.
