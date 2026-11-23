# Experiment 002 protocol — issue specification

Version `002.2` (reorder workload), frozen 2026-11-21 after pilots. See the freeze
record at the end.

**History.** Protocol `002.1` used the task *“Let people search the orders list using
a shareable URL.”* Its 80-attempt run (`results/main-002`) is kept as an introductory
finding (`findings/002.1-url-search-introductory.md`), and its workload is archived in
`archive/002.1-url-search/` (the archived config reproduces hash `205b9bdeb7fcbc00`).
The task was retired because Ridgeline's existing DataTables browser search let a
vague request be met client-side or server-side, which confounded the treatments.

## Workload

*“Let customers reorder a past order.”* At the pinned baseline there is no reorder
control, route, or parameter, and no client-side feature overlaps it. The contract
(AC01–AC10) and its stakeholder answers are in `requirements.json` and
`stakeholder-answers.md`.

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

- **D1**: the one-line request.
- **D2**: the request plus the approved product decisions and scope.
- **D3**: D2 plus 11 acceptance examples, repository entry points, constraints, and
  verification guidance.
- **D4**: a realistic, noisy issue thread. It contains every D3 fact verbatim in its
  final state, plus noise: an abandoned one-click idea, two decisions that are
  explicitly reversed later in the thread, declined scope creep (API and MCP), an
  unrelated deploy aside, and chatter. The noise inventory is in `requirements.json`.

D1–D3 compare practical packages of information. **D3 vs D4 tests robustness to a
messy issue with identical final facts**; it replaced 002.1's pure-verbosity D4,
which scored at ceiling alongside D3. A failure under D4 that does not occur under D3
most likely means the agent followed a superseded comment or the declined scope.
**An equivalence review is required before freezing** (recorded in
`requirements.json`): every D3 fact must appear in D4's final state, and no noise may
add a requirement or leave a reversal ambiguous.

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
  passes every criterion AC01–AC10, and passes the immutable original regression
  suite. Rates are reported per scheduled attempt (failures included) and per
  agent-started attempt.
- **Minimal common outcome**: MIN-REORDER (a reorder control leads to one new order
  for the same account with the original active products, either directly or after
  submitting the form it shows, so a D1 one-click design is not penalized) **and**
  MIN-AUTH (no reorder control or common reorder URL aimed at another account's
  order creates, changes, or reveals anything) **and** regression pass, in a
  completed attempt.
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
3. `controls` passes: reference passes every criterion; the no-op baseline and the
   no-ownership-check, includes-inactive, copies-dates, historical-prices,
   repeated-lines-overwritten, and one-click-auto-create controls fail exactly their
   expected criteria.
4. `preflight --probe-models` passes for all four configurations. Observed model
   IDs, effort/thinking support, and CLI versions recorded.
5. Pilots (`--cohort pilot`, separate output) confirm the pipeline for every
   configuration, the parser against real traces, and time limits.
6. Version bumped to `002.2` (no `draft`), limits and settings frozen, and the
   main collection started in a new output directory.

## Freeze record — protocol 002.2 (2026-11-21, reorder)

- **Reviews:** stakeholder answers approved by the study owner (all decisions drafted
  by Claude accepted unchanged). D3/D4 review approved: D4 is a noisy issue thread
  whose final state matches D3, with unambiguous reversals and no added requirement.
- **Evaluator:** `controls` passed on the frozen protocol. The reference passes every
  criterion, and the no-op baseline and six faulty implementations each fail exactly
  their expected criteria.
- **Pilots** (cohort `pilot`, excluded from main reports): `results/pilot-reorder`
  holds haiku D2 (primary success), terra D1 (minimal outcome only), luna D4 (primary
  success), and sonnet D3 (`setup_failed`: disk full). `results/pilot-reorder-2` holds
  the sonnet D3 rerun (primary success). All four configurations completed.
- **Settings frozen:** carried over unchanged from 002.1. Effort `medium` for all
  four configurations; Claude thinking at the CLI default; 1,800 s per agent session
  and 600 s per command. Pilot agent time was 52–163 s.
- **Disk incident and harness change:** the disk filled during the sonnet D3 pilot
  setup. With the owner's approval, the per-attempt virtualenvs of all earlier runs
  (including `main-002`) were deleted; no evidence files were removed. From this
  version on, each attempt deletes its own virtualenvs after writing its summary
  (`retain_environments: false`) and keeps the exact dependency records.
- **No recorded deviations** from the freeze checklist.

## History: freeze record — protocol 002.1 (2026-11-21, URL search)

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
