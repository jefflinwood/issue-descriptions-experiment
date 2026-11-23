"""Reports reconstructed only from saved attempt records.

Every scheduled attempt stays visible. Denominators are explicit: the scheduled
rate includes setup and infrastructure failures; the agent-started rate is shown
separately. Missing usage stays null; failed attempts never receive a duration or
token count they did not produce.
"""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
import statistics


class ReportError(ValueError):
    pass


def wilson(successes, n, z=1.959963984540054):
    if not n:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def rate(successes, n):
    return dict(successes=successes, n=n, rate=round(successes / n, 4) if n else None, wilson95=wilson(successes, n))


def distribution(values):
    values = [v for v in values if v is not None]
    if not values:
        return dict(n=0, median=None, min=None, max=None)
    return dict(n=len(values), median=statistics.median(values), min=min(values), max=max(values))


def load(output):
    output = Path(output)
    manifest_path = output / "experiment.json"
    if not manifest_path.exists():
        raise ReportError(f"{output} has no experiment.json")
    manifest = json.loads(manifest_path.read_text())
    attempts = []
    for summary_path in sorted(output.glob("*/summary.json")):
        record = json.loads(summary_path.read_text())
        if record.get("protocol_hash") != manifest["protocol_hash"]:
            raise ReportError(f"{summary_path} has protocol hash {record.get('protocol_hash')}, "
                              f"experiment is {manifest['protocol_hash']}; refusing to merge")
        if record.get("cohort") != manifest["cohort"]:
            raise ReportError(f"{summary_path} is cohort {record.get('cohort')}, experiment is {manifest['cohort']}")
        record["directory"] = summary_path.parent.name
        attempts.append(record)
    # Reserved directories without a summary (e.g. a killed harness) are reported, not hidden.
    orphans = sorted(p.name for p in output.iterdir()
                     if p.is_dir() and (p / "attempt.json").exists() and not (p / "summary.json").exists())
    return manifest, attempts, orphans


def effective_records(manifest, attempts):
    """Scheduled cell → its latest replacement if any (replacements are linked, never silent)."""
    by_scheduled = {}
    for record in attempts:
        by_scheduled.setdefault(record["scheduled_attempt_id"], []).append(record)
    effective = {}
    for cell in manifest["schedule"]:
        records = sorted(by_scheduled.get(cell["attempt_id"], []), key=lambda r: (r.get("replaces") is not None,
                                                                                  r["attempt_id"]))
        effective[cell["attempt_id"]] = records[-1] if records else None
    return effective


def outcome_flags(record):
    outcome = record.get("outcome") or {}
    return dict(primary=bool(outcome.get("primary_success")), minimal=bool(outcome.get("minimal_outcome")
                                                                         and record.get("termination") == "completed"),
                full_acceptance_artifact=bool(outcome.get("full_acceptance")),
                regression=bool(outcome.get("regression_passed")))


def build(output):
    manifest, attempts, orphans = load(output)
    effective = effective_records(manifest, attempts)
    cells = {}
    for cell in manifest["schedule"]:
        key = (cell["configuration"], cell["description"])
        group = cells.setdefault(key, dict(configuration=key[0], description=key[1], scheduled=0, records=[]))
        group["scheduled"] += 1
        record = effective[cell["attempt_id"]]
        if record is not None:
            group["records"].append(record)
    groups = []
    for (config_id, description), group in sorted(cells.items()):
        records = group["records"]
        started = [r for r in records if r.get("agent_started")]
        flags = [outcome_flags(r) for r in records]
        terminations = {}
        for r in records:
            terminations[r.get("termination") or "unknown"] = terminations.get(r.get("termination") or "unknown", 0) + 1
        criterion_counts = {}
        classification = dict(explicit_violations={}, partially_specified_failures={}, unspecified_mismatches={})
        for r in records:
            outcome = r.get("outcome") or {}
            for cid, result in (outcome.get("criteria") or {}).items():
                c = criterion_counts.setdefault(cid, dict(passed=0, evaluated=0))
                c["evaluated"] += 1
                c["passed"] += bool(result.get("passed"))
            for kind, ids in (outcome.get("classification") or {}).items():
                for cid in ids:
                    classification[kind][cid] = classification[kind].get(cid, 0) + 1
        usage = {}
        for name in ("input_tokens_total", "cached_input_tokens", "output_tokens", "reasoning_tokens", "tool_calls"):
            values = [((r.get("usage") or {}).get("normalized") or {}).get(name) for r in started]
            usage[name] = dict(distribution(values), missing=sum(v is None for v in values))
        primary = sum(f["primary"] for f in flags)
        groups.append(dict(
            configuration=config_id, description=description, scheduled=group["scheduled"],
            recorded=len(records), not_run=group["scheduled"] - len(records), agent_started=len(started),
            terminations=terminations,
            primary_success_scheduled=rate(primary, group["scheduled"]),
            primary_success_agent_started=rate(primary, len(started)),
            minimal_outcome_scheduled=rate(sum(f["minimal"] for f in flags), group["scheduled"]),
            artifact_full_acceptance_any_termination=sum(f["full_acceptance_artifact"] and f["regression"]
                                                         for f in flags),
            criteria=criterion_counts, classification=classification,
            timing={k: distribution([(r.get("timing") or {}).get(k) for r in records])
                    for k in ("total_seconds", "setup_seconds", "agent_seconds", "evaluation_seconds")},
            agent_seconds_successful_only=distribution([(r.get("timing") or {}).get("agent_seconds")
                                                        for r, f in zip(records, flags) if f["primary"]]),
            usage=usage,
            cli_reported_cost_usd=distribution([(r.get("usage") or {}).get("cli_reported_cost_usd") for r in started]),
            review_flags=dict(
                original_tests_touched=sum(bool(((r.get("outcome") or {}).get("static") or {}).get("original_tests_touched"))
                                           for r in records),
                possible_test_weakening=sum(bool(((r.get("outcome") or {}).get("static") or {}).get("possible_test_weakening"))
                                            for r in records),
                out_of_scope_changes=sum(bool(((r.get("outcome") or {}).get("static") or {}).get("out_of_scope_files"))
                                         for r in records),
                delegation_events=sum(bool((r.get("agent") or {}).get("delegation_events")) for r in records))))
    provenance = []
    for r in attempts:
        p = r.get("provenance")
        if p not in provenance:
            provenance.append(p)
    rows = [attempt_row(r) for r in sorted(attempts, key=lambda r: r["attempt_id"])]
    return dict(cohort=manifest["cohort"], protocol_version=manifest["protocol_version"],
                protocol_hash=manifest["protocol_hash"], scheduled_attempts=len(manifest["schedule"]),
                recorded_attempts=len(attempts), replacements=sum(r.get("replaces") is not None for r in attempts),
                reserved_without_summary=orphans, groups=groups, provenance_variants=provenance, attempts=rows,
                notes=["Pilot cohorts are exploratory and excluded from main-study reports."
                       if manifest["cohort"] != "main" else
                       "Five repeats per cell on one task are exploratory; no general ranking is implied.",
                       "Cross-provider differences include harness behavior, not only model quality.",
                       "cli_reported_cost_usd is a CLI estimate, not an observed charge."])


def attempt_row(r):
    outcome = r.get("outcome") or {}
    usage = (r.get("usage") or {}).get("normalized") or {}
    timing = r.get("timing") or {}
    criteria = outcome.get("criteria") or {}
    return dict(attempt_id=r["attempt_id"], scheduled_attempt_id=r["scheduled_attempt_id"], replaces=r.get("replaces"),
                block=r["block"], position=r["position"], configuration=r["configuration"],
                description=r["description"], termination=r.get("termination"), agent_started=r.get("agent_started"),
                primary_success=outcome.get("primary_success"), minimal_outcome=outcome.get("minimal_outcome"),
                full_acceptance=outcome.get("full_acceptance"), regression_passed=outcome.get("regression_passed"),
                criteria_passed=";".join(sorted(k for k, v in criteria.items() if v.get("passed"))) or None,
                criteria_failed=";".join(sorted(k for k, v in criteria.items() if not v.get("passed"))) or None,
                total_seconds=timing.get("total_seconds"), setup_seconds=timing.get("setup_seconds"),
                agent_seconds=timing.get("agent_seconds"), evaluation_seconds=timing.get("evaluation_seconds"),
                input_tokens_total=usage.get("input_tokens_total"), cached_input_tokens=usage.get("cached_input_tokens"),
                output_tokens=usage.get("output_tokens"), reasoning_tokens=usage.get("reasoning_tokens"),
                tool_calls=usage.get("tool_calls"),
                requested_model=(r.get("provenance") or {}).get("requested_model"),
                observed_model=json.dumps((r.get("provenance") or {}).get("observed_model")))


def to_csv(rows):
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return buffer.getvalue()


def fmt_rate(r):
    if r["n"] == 0:
        return "0/0"
    low, high = r["wilson95"]
    return f"{r['successes']}/{r['n']} ({r['rate']:.0%}; 95% CI {low:.0%}–{high:.0%})"


def fmt_seconds(d):
    return "—" if d["median"] is None else f"{d['median']:.0f}s (n={d['n']}, {d['min']:.0f}–{d['max']:.0f})"


def to_markdown(report):
    lines = [f"# Experiment 002 report — cohort `{report['cohort']}`", "",
             f"Protocol `{report['protocol_version']}`, hash `{report['protocol_hash'][:16]}…`. "
             f"Scheduled attempts: {report['scheduled_attempts']}; recorded: {report['recorded_attempts']}; "
             f"replacements: {report['replacements']}.", ""]
    if report["reserved_without_summary"]:
        lines += [f"Reserved attempt directories without a summary: {', '.join(report['reserved_without_summary'])}.", ""]
    lines += ["## Outcomes by configuration and description", "",
              "| Config | Desc | Scheduled | Started | Primary (scheduled) | Primary (started) | Minimal (scheduled) | Terminations | Agent time (median) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for g in report["groups"]:
        terms = ", ".join(f"{k}: {v}" for k, v in sorted(g["terminations"].items())) or "—"
        lines.append(f"| {g['configuration']} | {g['description']} | {g['scheduled']} | {g['agent_started']} | "
                     f"{fmt_rate(g['primary_success_scheduled'])} | {fmt_rate(g['primary_success_agent_started'])} | "
                     f"{fmt_rate(g['minimal_outcome_scheduled'])} | {terms} | {fmt_seconds(g['timing']['agent_seconds'])} |")
    lines += ["", "## Criterion pass counts (evaluated attempts)", ""]
    ids = sorted({cid for g in report["groups"] for cid in g["criteria"]})
    if ids:
        lines += ["| Config | Desc | " + " | ".join(ids) + " |", "|---|---|" + "---|" * len(ids)]
        for g in report["groups"]:
            lines.append(f"| {g['configuration']} | {g['description']} | " + " | ".join(
                f"{g['criteria'][c]['passed']}/{g['criteria'][c]['evaluated']}" if c in g["criteria"] else "—"
                for c in ids) + " |")
    lines += ["", "## Failure classification", "",
              "Explicit violations fail a criterion the description stated; unspecified mismatches fail one it did not.", ""]
    for g in report["groups"]:
        c = g["classification"]
        if any(c.values()):
            lines.append(f"- **{g['configuration']} {g['description']}**: explicit {c['explicit_violations'] or '{}'}; "
                         f"partial {c['partially_specified_failures'] or '{}'}; "
                         f"unspecified {c['unspecified_mismatches'] or '{}'}")
    lines += ["", "## Usage (agent-started attempts; missing values are not zero)", "",
              "| Config | Desc | Input total (median) | Output (median) | Reasoning (median) | Tool calls (median) | Missing input |",
              "|---|---|---|---|---|---|---|"]
    for g in report["groups"]:
        u = g["usage"]
        def m(name):
            return "—" if u[name]["median"] is None else f"{u[name]['median']:.0f}"
        lines.append(f"| {g['configuration']} | {g['description']} | {m('input_tokens_total')} | {m('output_tokens')} | "
                     f"{m('reasoning_tokens')} | {m('tool_calls')} | {u['input_tokens_total']['missing']} |")
    lines += ["", "## Notes", ""] + [f"- {n}" for n in report["notes"]]
    return "\n".join(lines) + "\n"


def write(output, report, suffix=""):
    output = Path(output)
    stamp = suffix
    paths = dict(json=output / f"report{stamp}.json", csv=output / f"attempts{stamp}.csv",
                 markdown=output / f"report{stamp}.md")
    paths["json"].write_text(json.dumps(report, indent=2, default=str) + "\n")
    paths["csv"].write_text(to_csv(report["attempts"]))
    paths["markdown"].write_text(to_markdown(report))
    return paths
