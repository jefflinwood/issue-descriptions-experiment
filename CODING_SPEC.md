# Experiment 002: Issue Specification

Status: implementation specification and draft experimental protocol.
Prepared: 2026-11-20. No experimental runs or findings are implied by this document.

## 1. Objective and deliverable

Build a Python experiment repository at:

`~/Projects/agent-native/experiments/issue-specification`

Measure how the description of the same software task affects implementation
outcomes across four coding-agent configurations. Compare correctness, regressions,
review findings, completion rate, elapsed time, and reported resource use. Preserve
sufficient provenance to reproduce the workload and audit each result.

This repository contains the harness, benchmark definitions, evaluation suite,
protocol, and reporting tools. Ridgeline is the application under test, cloned into
isolated run directories; do not develop candidate features in the source checkout.

The experiment supports InstantRocket's draft issue-sculpting lab and the opening
lesson of its agent-native “issue to deployed” course. Learners refine a vague task
into an executable specification. The experiment evaluates actual implementations
under different frozen descriptions. Deployment remains a later course activity;
this benchmark ends with independent implementation verification.

## 2. Source repositories and reuse

### Application under test: Ridgeline Coffee

- Local source: `~/Projects/ridgeline`.
- Public source: https://github.com/jefflinwood/ridgeline.
- Local HEAD observed during planning:
  `353c3ccdf3acdaae9acae78b5132241061b5decc`.
- Flask application, SQLAlchemy models, Jinja templates, and pytest tests.
- Relevant entry points include `app/orders.py`, `app/models.py`,
  `app/templates/orders/index.html`, and `tests/conftest.py`.
- Tests configure an isolated SQLite database with `AUTO_SEED = False`.

Use the public remote and a full commit SHA for the publishable benchmark. Verify
that the selected SHA is fetchable there before freezing it. Support an explicit
local-source override for development and record that provenance separately.
Exclude uncommitted local changes. Never seed or reset the user's `ridgeline.db`.

Inspect actual code and tests when building the fixture. Ridgeline's current
`AGENTS.md` incorrectly says there is no test suite; the repository has tests.
Document a fixed, treatment-independent correction in the common instructions,
retaining the original instructions as part of the pinned baseline.

### Harness reference: Experiment 001

Use `~/Projects/agent-native/experiments/agent-concurrency-time` as the engineering
base. Read its README, `pyproject.toml`, `benchmark/config.json`, acceptance suite,
`concurrency_harness/runner.py`, CLI, reporting code, and tests before implementing.
Record the reference repository commit when adapting code and preserve applicable
license notices.

Reuse or adapt the useful mechanisms:

- Python 3.11+ harness with a small dependency footprint; prefer the standard
  library for orchestration, JSON configuration, subprocesses, and reporting.
- Fresh clones, explicit revisions, isolated virtual environments, dependency
  constraints, baseline checks, and environment provenance.
- Structured command records, UTC and monotonic timestamps, stdout/stderr,
  process-group termination on timeout, and immutable run directories.
- External acceptance and original regression suites, hashes, and final patches.
- Pure planning commands and reports reconstructed from saved run artifacts.

Replace the concurrency scheduler and ten-task workload. This study uses one
issue, one agent session per attempt, no integration agent, no resumed sessions,
no nested agents, and sequential experiment attempts. Do not copy persistent-agent
or repair treatments into the primary matrix. Adapt provider-specific usage parsing
rather than assuming Codex and Claude emit the same event format.

## 3. Required agent/model matrix

Test the latest available members of these four requested families, selected and
pinned at protocol freeze:

| Configuration | Coding harness | Current model identifier to verify in preflight |
| --- | --- | --- |
| OpenAI Terra | Codex CLI | `gpt-6.1-terra` |
| OpenAI Luna | Codex CLI | `gpt-6-luna` |
| Anthropic Sonnet | Claude Code CLI | `claude-sonnet-5-5` |
| Anthropic Haiku | Claude Code CLI | `claude-haiku-5-5` |

These IDs were checked against official provider catalogs on 2026-11-20:

- OpenAI: https://developers.openai.com/api/docs/models
- Anthropic: https://platform.claude.com/docs/en/models/overview

“Latest” is a selection rule before the study, not a floating setting during it.
Recheck catalogs and actual account/CLI availability before pilots. Persist exact
requested IDs, any resolved/observed model IDs returned by the harness, CLI versions,
provider, authentication mode without credentials, reasoning/thinking settings,
and the date and source of model selection. Prefer a fixed version/snapshot where
offered. Distinguish a requested identifier from an independently observed backend
version; use null when not available. Never silently substitute another model.
If the selected version changes, create a new protocol version and output cohort.

Codex must run both Terra and Luna. Use Claude Code for Sonnet and Haiku; do not
assume Codex can run Anthropic models. Verify supported CLI flags from the installed
versions before implementing adapters. Each adapter explicitly selects its model,
starts a fresh noninteractive session, captures structured events, and disables
subagent delegation and inherited optional integrations where supported.

Choose and record one supported effort setting per configuration during pilots;
keep it fixed across descriptions. Use medium reasoning for Terra/Luna as the
initial pilot setting, subject to actual model support. Choose explicit supported
Sonnet/Haiku thinking and effort settings rather than assuming that identically
named settings imply equal compute. Pin context/output limits where configurable.
Record unavoidable differences in system prompts, tools, and context management.

This is a comparison of **model-plus-coding-harness configurations**. Within-model
comparisons isolate description treatments more closely. Cross-provider differences
also include harness behavior and must not be attributed purely to model quality.
A shared direct-API agent loop would be a separate future study.

## 4. Workload: one real Ridgeline issue

Adapt the lab's abstract issue-list example to Ridgeline's existing order list.
Do not add an unrelated issue-tracking subsystem merely to reproduce that example.

Proposed task: **“Let people search the orders list using a shareable URL.”**

The baseline already initializes a DataTables browser-side search control. The
proposed missing behavior is server-side, URL-addressable filtering, not simply
adding another search box. Verify that distinction on the pinned baseline before
freezing the task. If baseline behavior already meets the intended outcome, choose
a different bounded task and version all descriptions and acceptance checks together.

Prepare a stakeholder answer sheet and canonical behavioral contract. A proposed
contract to validate against the fixture is:

1. A GET `q` parameter on `/orders/` filters orders before server-side rendering.
2. Missing, empty, and whitespace-only `q` preserve the baseline visible-order set.
3. A trimmed nonempty query matches order numbers by case-insensitive substring.
4. For staff, matching also includes customer names, using OR semantics.
5. For customer users, matching searches only their own order numbers and never
   broadens existing authorization or exposes another customer's data.
6. Query input is literal text: SQL wildcard characters such as `%` and `_` are
   not interpreted as user-supplied wildcard operators.
7. Existing staff status/type filters combine with search using AND semantics;
   existing ordering and access behavior remain intact.
8. A labeled GET search form submits the query, shows its current value safely,
   and retains applicable active filters. Filter links retain the query.
9. Clearing search preserves applicable status/type filters; a no-result query
   produces an understandable empty state.
10. No new schema, dependencies, unrelated endpoints, or replacement of existing
    client-side sorting/pagination is required. The existing browser-side filter
    remains local to the returned rows and is distinguished from URL search.
11. Existing tests still pass, focused candidate tests cover the change, and
    README usage examples explain the URL search.

These are proposed stakeholder choices, not facts supplied by the vague request.
Freeze them before main collection. Use a common wrapper to require preserving
existing access rules, writing relevant tests, and documenting behavior in every
condition; do not accidentally reveal the richer contract in that wrapper.

Acceptance data must include multiple users/accounts, overlapping names, mixed
case, literal wildcard characters, matching/nonmatching order numbers, and
combinations of existing filters. Use deterministic fixtures independent of the
user's database and the current date. Exercise HTTP responses and submitted-form
behavior; do not enforce a particular SQLAlchemy expression or private helper name.

## 5. Description treatments and run schedule

Version and hash the exact description files and common wrapper:

- **D1 — vague:** the short starting request, with no additional product answers.
- **D2 — clarified:** desired behavior, scope, and consequential stakeholder answers.
- **D3 — agent-ready:** D2 behavior plus concrete acceptance examples, repository
  entry points, constraints, and verification guidance.
- **D4 — expanded:** the same substantive facts, examples, and context as D3,
  expressed at greater length, without additional requirements or solution hints.

Maintain a requirement-by-description inventory. Have D3/D4 reviewed for semantic
equivalence before freezing them. D1–D3 compare practical packages of information;
they do not isolate writing style or length. D3/D4 provide a limited verbosity
comparison, subject to the equivalence review.

Initial main plan: **4 model configurations × 4 descriptions × 5 repeats = 80
attempts**, excluding pilots. Five repeats are exploratory, not a power claim.
Confirm feasibility and budget with pilots, then freeze counts and stop rules.
Never reduce or extend particular cells because their outcomes look favorable.

Use five randomized complete blocks, each containing all 16 combinations once.
Record seed, planned order, and actual order. Run attempts sequentially on the same
host to avoid introducing concurrency contention as another treatment. Never reuse
candidate code, sessions, or task-specific memory between attempts.

Primary study: no human clarification after dispatch. The common prompt permits
recording assumptions and proceeding; an agent that stops for answers is recorded
as incomplete. Human-answer or model-assisted specification treatments require a
separate versioned study. Do not feed hidden evaluation failures back for repairs.
Normal agent iteration against visible tests within its single session is allowed.

## 6. Execution lifecycle and isolation

For each planned attempt:

1. Reserve a new immutable run ID; refuse an existing output directory.
2. Save resolved config, protocol, treatment text, hashes, and scheduled cell.
3. Clone the selected baseline, verify the full SHA, create a disposable working
   checkout, install pinned dependencies into a fresh environment, and record
   interpreter/platform/installed versions. Avoid sharing writable dependency
   environments between candidate and evaluator.
4. Run the original baseline suite. Record setup or baseline failures explicitly;
   no agent result is invented for a run that never launched.
5. Start one fresh selected agent with the exact wrapper and description. Give it
   only the candidate repository and tools required for editing and local tests.
6. Capture all events and final state. The harness owns staging, commits, and
   patch capture; include legitimate untracked source/test files while excluding
   environments, secrets, transient databases, and build caches.
7. Reconstruct the final candidate in a clean evaluation checkout, then run the
   immutable original regression suite, candidate tests, and independent checks
   as separate recorded phases.
8. Save per-criterion results, diffs, final commit/tree identity, usage, timing,
   termination status, and a machine-readable summary regardless of success.

Keep the harness repository, other treatment files, stakeholder answer sheet,
reference implementation, sibling worktrees, reference harness, evaluation files,
and previous outputs outside the agent-visible filesystem. A clean environment
must not expose this `CODING_SPEC.md` or the full contract to D1/D2 agents. Merely
using a sibling directory or hashing files is not a read-access security boundary.
Implement an OS/container sandbox or equivalent tested isolation. Do not expose
private acceptance results through accessible logs while the agent is running.

Sanitize inherited instructions, memory, skills, plugins, and MCP configuration
where the harness permits; record effective instructions and any unavoidable
host-level settings. Keep the pinned repository instructions consistent across
conditions. Auth stays available only to the coding CLI, never copied into the
candidate worktree or published artifacts. Block candidate access to source
remotes and sibling results during the agent phase; dependency setup happens first.
Record network access policies and deviations.

Initial limits: 1,800 seconds per agent session and 600 seconds per setup or
evaluation command, configurable and frozen after pilots. Preserve a candidate
snapshot even on timeout when possible, but a timed-out run cannot receive the
primary successful-completion outcome. Capture and label any passing partial
artifact checks separately. Terminate complete subprocess groups on timeout or
interrupt, including children. Do not auto-retry model failures or resume sessions.

Infrastructure failures remain visible. Any replacement attempt must get a new ID,
link to the failed attempt, follow a predeclared classification/rerun policy, and
never silently replace the original record.

## 7. Independent evaluation and analysis

Freeze the behavioral contract and checks before inspecting main-run implementations.
Use reference and deliberately faulty implementations to validate the evaluator.
At minimum, negative controls must detect no-op implementation, missing auth
scoping, incorrect OR/AND composition, wildcard handling, and a form that cannot
submit the intended query. Run original tests outside the candidate's editable
suite and verify hashes. Candidate tests supplement rather than replace them.

Primary outcome: completed attempt that satisfies every frozen acceptance criterion
and the original regression suite within the assigned limits. Report separately:

- Full intended-behavior acceptance, criterion-level results, and regressions.
- A minimal common outcome: URL search actually filters visible orders and does
  not broaden authorization. Validate that this is justified for every treatment.
- Explicit-instruction violations versus plausible choices about unspecified
  behavior. A D1 mismatch is not automatically disobedience.
- Candidate-test results, test weakening, out-of-scope changes, maintainability,
  and documentation using a frozen human-review rubric. Blind model/treatment
  labels during review where feasible; code can still reveal clues.
- Incomplete, timeout, tool-error, setup-failure, and infrastructure-failure counts.

Preserve all scheduled attempts and report both scheduled and agent-started
counts. Primary scheduled-attempt completion rate includes failures; show an
additional agent-started rate so setup outages are not mistaken for coding ability.
Do not quietly exclude failed cells or report successful timings as if they
represented all attempts.

Capture total attempt time, setup time, agent time, and evaluation time separately.
Save raw provider usage and normalized fields where possible: input, cached input,
output, reasoning tokens, and tool calls. Missing fields are null, never zero.
Do not double-count nested usage events or totals repeated in streaming records;
validate parsers against saved fixtures. Report billable cost only from documented
usage and dated pricing, or provider-reported cost with its provenance. Subscription
access is not equivalent to an observed per-run API charge.

Report raw outcomes, rates with binomial uncertainty intervals, distributions of
time/cost, and per-model comparisons between descriptions. Keep model-by-description
patterns visible rather than pooling all configurations. Five repeats per cell on
one task do not establish a universal model or specification ranking. More detail
may change requirements or constrain implementations; longer is not assumed better.

## 8. Repository structure

```text
CODING_SPEC.md
README.md
pyproject.toml
.gitignore
issue_specification/
  __init__.py
  cli.py                  # plan, preflight, run, report
  config.py               # strict validation and protocol hashing
  schedule.py             # seeded blocked schedule
  runner.py               # isolated attempt lifecycle
  processes.py            # subprocess logs/timeouts/cleanup
  evaluation.py           # immutable checks and outcome extraction
  reporting.py
  adapters/
    base.py
    codex.py
    claude_code.py
benchmark/
  config.json
  protocol.md
  requirements.json       # criterion IDs and disclosure inventory
  stakeholder-answers.md
  review-rubric.md
  constraints.txt
  descriptions/
    common.txt
    d1-vague.md
    d2-clarified.md
    d3-agent-ready.md
    d4-expanded.md
  evaluation/             # never mounted in agent environment
    acceptance.py
    fixtures.py
  reference/              # evaluator controls, never agent-visible
    reference.patch
    negative-controls/
tests/
  fixtures/               # synthetic CLI event traces, no credentials
results/                  # ignored; append-only execution artifacts
```

Separate Python harness packaging from Ridgeline's dependency environment. Do not
carry Experiment 001 package constraints forward without validating the selected
baseline and interpreter. Pin validated versions and record `pip freeze --all`.
Keep documentation and config paths portable; expand user paths in local config
instead of hard-coding `/Users/jeff` throughout the implementation.

## 9. CLI and artifacts

Proposed commands, to be implemented:

```sh
python3 -m issue_specification.cli plan --config benchmark/config.json
python3 -m issue_specification.cli preflight --config benchmark/config.json
python3 -m issue_specification.cli run --config benchmark/config.json --run-index 0 --output results/pilot
python3 -m issue_specification.cli run --config benchmark/config.json --output results/main-002
python3 -m issue_specification.cli report --output results/main-002
```

`plan` validates local inputs, prints the exact 80-cell schedule and limits, and
launches no agents or network calls. `preflight` checks executables, versions,
credentials availability without revealing secrets, dependency setup, isolation,
baseline tests, and remote SHA availability. Any paid model probe requires an
explicit flag such as `--probe-models`; ordinary preflight must not silently launch
billable jobs. `run` is the explicit execution boundary for paid agent attempts.
A pilot must be labeled and excluded from main reports. No collection is required
merely to finish implementing the harness.

Each run saves resolved configuration, prompts/hashes, provenance, events JSONL,
command output, raw CLI events, model usage, final patch and commit identity,
evaluation records, and summary JSON. Aggregate JSON/CSV and Markdown reports read
saved records, never current defaults. Reports must also work on failed/partial
runs and must reject incompatible protocol hashes rather than merging silently.
Existing outputs are never overwritten or automatically cleaned.

## 10. Implementation milestones and acceptance

1. **Freeze the fixture and protocol.** Verify Ridgeline baseline behavior, define
   the missing outcome, prepare stakeholder answers and descriptions, and publish
   the disclosure inventory. Keep all work labeled draft until reviewed.
2. **Build deterministic harness foundations.** Config, schedule, process handling,
   run records, clean workspaces, and fake agent adapters. `plan` lists 80 attempts
   with one instance of each combination per block.
3. **Implement evaluator and controls.** Reference passes; baseline fails the new
   feature checks while passing original regressions; negative controls fail for
   the intended reasons. UI checks should exercise forms, not only inspect HTML.
4. **Implement real CLI adapters and preflight.** Explicit IDs, versions, fresh
   sessions, normalized events, isolation, cancellation, and missing-usage behavior.
   Account access errors fail clearly without model substitution.
5. **Implement reporting.** All attempts remain visible; failed results have no
   invented successful duration, zero-token inference, or fabricated score.
6. **Pilot and freeze main collection.** Only when explicitly running trials,
   validate all four configurations and pipeline behavior, then freeze versions,
   limits, pricing references, analysis rules, and randomized order. Preserve pilots
   separately. Run and report the 80-attempt study after the protocol is ready.

Automated tests must cover deterministic balanced schedules, invalid model/config
selection, config-hash consistency, immutable outputs, fresh attempt state,
process-group timeout cleanup, partial/failure records, evaluator integrity,
acceptance controls, provider-specific event parsing, and reporting denominators.
Use fake subprocesses/agents for routine tests; CI must not invoke paid models or
require user credentials. Include an integration smoke test from fake agent through
clean evaluation and report generation. Validate the sandbox by attempting access
to evaluator files, sibling results, and harness instructions from the candidate.

Document installation, local vs public source configuration, model selection and
freeze policy, authentication setup without secret values, commands, resource
limits, metrics, failures, study limitations, and artifact publication. No pushes,
pull requests, deployment, or edits to the source Ridgeline/reference experiment
repositories are needed to implement this experiment.
