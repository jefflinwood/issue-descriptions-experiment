"""Post-hoc corrected evaluation for protocol 002.2 (reorder), decided after collection.

Three frozen checks were found to measure analysis choices rather than the
behavior the descriptions asked for. This script re-checks only the attempts those
checks failed, by rebuilding each candidate from its saved ``final.patch`` and
running a corrected version of the check. Every other criterion keeps its frozen
result. Saved run records are never modified.

Corrections:
  C1  AC04, all-inactive order: pass if nothing is prefilled, no order is created,
      and the page says the products are unavailable. Naming them is not required
      (the descriptions say only "the page says so" for that case). Besides the frozen
      unavailable-word list, "none of ... available" counts (for example "None of the
      products on this order are still available"), which the frozen list missed.
  C2  AC10, README: pass if a human-verified README section documents reordering
      without using the word "reorder" (listed in README_VERIFIED below).
  C3  MIN-REORDER: repeated lines of one product need not be summed. Pass if one new
      order is created for the right account with exactly the original active
      products, each quantity equal to one of that product's original lines.
      (D1 never mentions repeated lines, and Ridgeline's own form cannot create them.)
      Summing stays required for AC02, which D2-D4 state explicitly.

Usage:  python findings/002.2-reorder/posthoc.py --python <interpreter with Ridgeline deps>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "results" / "main-003"
REVISION = "353c3ccdf3acdaae9acae78b5132241061b5decc"
README_VERIFIED = {
    "029-b2-luna-d2": "From an order page, customers and staff can reopen the new-order form with available "
                      "products and quantities prefilled.",
    "035-b3-luna-d1": "customers can start a new order from a past order, with its active products, "
                      "quantities, and shipping method prefilled.",
}

PROBE = r'''
import json, re, sys
from decimal import Decimal
NONE_AVAILABLE = re.compile(r"\bnone of\b.{0,80}?\bavailable\b", re.IGNORECASE)
sys.path.insert(0, sys.argv[1])
import fixtures as fx
check = sys.argv[2]
result = {}
if check == "C1":
    w = fx.build_world(); client = fx.client_for(w, fx.NORTH)
    before = fx.snapshot(w)
    url = f"/orders/{w.ids['N2']}"
    controls = fx.reorder_controls(client.get(url).get_data(as_text=True), url)
    response = fx.activate(client, controls[0])
    html = response.get_data(as_text=True)
    form = fx.order_form(html)
    text = fx.Page(html).visible_text
    result = dict(status=response.status_code, prefilled=(fx.prefilled_quantities(form) if form else {}),
                  says_unavailable=bool(fx.UNAVAILABLE_WORDS.search(text) or NONE_AVAILABLE.search(text)),
                  names_product="Harvest Reserve" in text, created=len(fx.snapshot(w)) - len(before))
    found = fx.UNAVAILABLE_WORDS.search(text) or NONE_AVAILABLE.search(text)
    result["message"] = text[max(0, found.start() - 30):found.end() + 60] if found else None
    result["passed"] = result["status"] < 500 and not result["prefilled"] and result["says_unavailable"] and not result["created"]
elif check == "C3":
    w = fx.build_world(); client = fx.client_for(w, fx.NORTH)
    url = f"/orders/{w.ids['N3']}"
    controls = fx.reorder_controls(client.get(url).get_data(as_text=True), url)
    before = fx.snapshot(w)
    response = fx.activate(client, controls[0])
    created = {k: v for k, v in fx.snapshot(w).items() if k not in before}
    if not created:
        form = fx.order_form(response.get_data(as_text=True))
        if form is not None:
            fx.submit(client, form, fx.landed_url(response), {"due_date": fx.FUTURE_DUE})
            created = {k: v for k, v in fx.snapshot(w).items() if k not in before}
    lines = {}
    for product, quantity, _ in fx.ORDERS["N3"][4]:
        if next(p for p in fx.PRODUCTS if p[0] == product)[5]:
            lines.setdefault(w.products[product], set()).add(quantity)
    result = dict(created=len(created))
    if len(created) == 1:
        order = next(iter(created.values()))
        got = fx.summed(order["items"])
        result.update(customer_ok=order["customer_id"] == w.customers["northside"], quantities=got,
                      allowed={k: sorted(v | {sum(v)}) for k, v in lines.items()})
        result["passed"] = (result["customer_ok"] and set(got) == set(lines)
                            and all(got[p] in (lines[p] | {sum(lines[p])}) for p in lines))
    else:
        result["passed"] = False
print(json.dumps(result, default=str))
'''


def rebuild(attempt, workdir):
    repo = workdir / attempt
    subprocess.run(["git", "clone", "--quiet", str(RUN / attempt / "baseline.git"), str(repo)], check=True)
    subprocess.run(["git", "checkout", "--quiet", REVISION], cwd=repo, check=True)
    patch = RUN / attempt / "final.patch"
    if patch.read_text().strip():
        subprocess.run(["git", "apply", "--binary", str(patch)], cwd=repo, check=True)
    return repo


def probe(python, repo, check, fixtures_dir):
    script = fixtures_dir / "probe.py"
    script.write_text(PROBE)
    done = subprocess.run([python, "-W", "ignore", str(script), str(fixtures_dir), check], cwd=repo,
                          env={"PYTHONPATH": str(repo), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                          capture_output=True, text=True, timeout=300)
    if done.returncode:
        return dict(passed=False, error=done.stderr[-800:])
    return json.loads(done.stdout.strip().splitlines()[-1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    manifest = json.loads((RUN / "experiment.json").read_text())
    summaries = {p.parent.name: json.loads(p.read_text()) for p in sorted(RUN.glob("*/summary.json"))}
    corrections = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fixtures_dir = tmp / "fixtures"
        fixtures_dir.mkdir()
        for name in ("fixtures.py", "baseline_surface.json"):
            # The frozen evaluator files exactly as used in the run.
            source = next(RUN.glob(f"*/evaluation/suite/acceptance/{name}"))
            (fixtures_dir / name).write_bytes(source.read_bytes())
        for attempt, s in summaries.items():
            criteria = s["outcome"]["criteria"]
            needed = []
            if not criteria["AC04"]["passed"] and criteria["AC04"]["failed"] == ["test_ac04_order_with_only_inactive_products"]:
                needed.append("C1")
            if not criteria["AC10"]["passed"] and [k for k, v in criteria["AC10"]["components"].items() if not v] == ["readme_documents_query"]:
                needed.append("C2")
            if not criteria["MIN-REORDER"]["passed"]:
                needed.append("C3")
            if not needed:
                continue
            repo = rebuild(attempt, tmp / "repos") if {"C1", "C3"} & set(needed) else None
            corrections[attempt] = {}
            for check in needed:
                if check == "C2":
                    corrections[attempt]["C2"] = dict(passed=attempt in README_VERIFIED,
                                                      evidence=README_VERIFIED.get(attempt))
                else:
                    corrections[attempt][check] = probe(args.python, repo, check, fixtures_dir)
    cells, rows = {}, []
    for attempt, s in summaries.items():
        o, c = s["outcome"], s["outcome"]["criteria"]
        fix = corrections.get(attempt, {})
        passed = {k: v["passed"] for k, v in c.items()}
        if fix.get("C1", {}).get("passed"):
            passed["AC04"] = True
        if fix.get("C2", {}).get("passed"):
            passed["AC10"] = True
        if fix.get("C3", {}).get("passed"):
            passed["MIN-REORDER"] = True
        completed = s["termination"] == "completed"
        contract = all(passed[f"AC{n:02d}"] for n in range(1, 11))
        primary = completed and contract and o["regression_passed"]
        minimal = completed and passed["MIN-REORDER"] and passed["MIN-AUTH"] and o["regression_passed"]
        key = f"{s['configuration']} {s['description']}"
        cell = cells.setdefault(key, dict(frozen_primary=0, posthoc_primary=0, frozen_minimal=0, posthoc_minimal=0, n=0))
        cell["n"] += 1
        cell["frozen_primary"] += bool(o["primary_success"])
        cell["posthoc_primary"] += bool(primary)
        cell["frozen_minimal"] += bool(o["minimal_outcome"])
        cell["posthoc_minimal"] += bool(minimal)
        rows.append(dict(attempt=attempt, frozen_primary=o["primary_success"], posthoc_primary=primary,
                         frozen_minimal=o["minimal_outcome"], posthoc_minimal=minimal, corrections=fix))
    out = dict(protocol_hash=manifest["protocol_hash"], corrections=corrections, cells=cells, attempts=rows)
    target = Path(__file__).with_name("posthoc.json")
    target.write_text(json.dumps(out, indent=2, default=str) + "\n")
    for key, cell in sorted(cells.items()):
        print(f"{key:<10} primary {cell['frozen_primary']}/5 -> {cell['posthoc_primary']}/5 | "
              f"minimal {cell['frozen_minimal']}/5 -> {cell['posthoc_minimal']}/5")
    for attempt, fix in sorted(corrections.items()):
        print(attempt, {k: (v.get("passed"), v.get("message") or v.get("quantities") or v.get("error", "")[:120])
                        for k, v in fix.items()})


if __name__ == "__main__":
    sys.exit(main())
