import json
from pathlib import Path
import tempfile
import unittest

from issue_specification import evaluation, reporting
from issue_specification.config import load_config
from issue_specification.schedule import build_schedule

from tests.helpers import BENCHMARK

# Machinery tests use the archived 002.1 (URL search) inventory, whose IDs they name explicitly.
REQUIREMENTS = json.loads((BENCHMARK / "archive/002.1-url-search/requirements.json").read_text())
CURRENT = json.loads((BENCHMARK / "requirements.json").read_text())

JUNIT = """<?xml version="1.0"?><testsuites><testsuite>
<testcase classname="acceptance" name="test_ac01_q_filters_server_rendered_rows"/>
<testcase classname="acceptance" name="test_ac01_shared_url"><failure message="boom"/></testcase>
<testcase classname="acceptance" name="test_ac02_missing"/>
<testcase classname="acceptance" name="test_ac03_x"><skipped message="skip"/></testcase>
<testcase classname="acceptance" name="test_min_url_search_filters_visible_orders"/>
</testsuite></testsuites>"""


class EvaluationTests(unittest.TestCase):
    def test_junit_maps_to_criteria_and_missing_tests_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.xml"
            path.write_text(JUNIT)
            tests = evaluation.parse_junit(path)
            results = evaluation.criterion_results(REQUIREMENTS, {"tests": tests})
            self.assertFalse(results["AC01"]["passed"])
            self.assertEqual(results["AC01"]["failed"], ["test_ac01_shared_url"])
            self.assertTrue(results["AC02"]["passed"])
            self.assertFalse(results["AC03"]["passed"])          # skipped is not passed
            self.assertFalse(results["AC04"]["passed"])          # no tests ran
            self.assertIn("no tests", results["AC04"]["reason"])
            self.assertTrue(results["MIN-SEARCH"]["passed"])
            self.assertFalse(results["MIN-AUTH"]["passed"])
            self.assertIsNone(evaluation.parse_junit(Path(directory) / "missing.xml"))

    def test_primary_requires_completed_session(self):
        tests = [{"name": f"{c['tests']}x", "outcome": "passed", "message": None}
                 for c in REQUIREMENTS["criteria"] if c.get("tests")]
        tests += [{"name": m["tests"], "outcome": "passed", "message": None} for m in REQUIREMENTS["minimal_outcome"]]
        passing = {"passed": True}
        phases = {"acceptance": {"tests": tests}, "regression": passing, "candidate": passing}
        static = {"tests_changed": True, "readme_documents_query": True}
        done = evaluation.summarize(REQUIREMENTS, phases, static, agent_completed=True)
        self.assertTrue(done["primary_success"])
        timed_out = evaluation.summarize(REQUIREMENTS, phases, static, agent_completed=False)
        self.assertFalse(timed_out["primary_success"])
        self.assertTrue(timed_out["artifact_checks_passed"])
        no_docs = evaluation.summarize(REQUIREMENTS, phases, dict(static, readme_documents_query=False), True)
        self.assertFalse(no_docs["criteria"]["AC11"]["passed"])
        self.assertFalse(no_docs["primary_success"])

    def test_current_workload_composite_and_readme_pattern_are_data_driven(self):
        self.assertEqual(evaluation.composite_criterion(REQUIREMENTS)["id"], "AC11")
        self.assertEqual(evaluation.readme_pattern(REQUIREMENTS), evaluation.README_QUERY)
        composite = evaluation.composite_criterion(CURRENT)
        self.assertEqual(composite["id"], CURRENT["criteria"][-1]["id"])
        pattern = evaluation.readme_pattern(CURRENT)
        self.assertTrue(pattern.search("Use the **Reorder** button on an order page."))
        self.assertTrue(pattern.search("You can order again from any past order."))
        self.assertFalse(pattern.search("Orders are listed newest first."))
        tests = [{"name": f"{c['tests']}x", "outcome": "passed", "message": None}
                 for c in CURRENT["criteria"] if c.get("tests")]
        tests += [{"name": m["tests"], "outcome": "passed", "message": None} for m in CURRENT["minimal_outcome"]]
        passing = {"passed": True}
        outcome = evaluation.summarize(CURRENT, {"acceptance": {"tests": tests}, "regression": passing,
                                                 "candidate": passing},
                                       {"tests_changed": True, "readme_documents_query": True}, True)
        self.assertTrue(outcome["primary_success"])
        self.assertIn(composite["id"], outcome["criteria"])
        self.assertNotIn("AC11", outcome["criteria"])

    def test_disclosure_classification_distinguishes_d1_choices(self):
        criteria = {c["id"]: {"passed": False} for c in REQUIREMENTS["criteria"]}
        d1 = evaluation.disclosure_classification(REQUIREMENTS, "D1", criteria)
        d3 = evaluation.disclosure_classification(REQUIREMENTS, "D3", criteria)
        self.assertIn("AC04", d1["unspecified_mismatches"])
        self.assertNotIn("AC04", d1["explicit_violations"])
        self.assertEqual(len(d3["explicit_violations"]), len(REQUIREMENTS["criteria"]))
        self.assertEqual(d3["unspecified_mismatches"], [])

    def test_static_checks_detect_docs_tests_and_scope(self):
        patch = ("diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n # R\n"
                 "+Use `/orders/?q=WS` to search.\n"
                 "diff --git a/app/api.py b/app/api.py\n+++ b/app/api.py\n+x = 1\n")
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            (checkout / "README.md").write_text("# R\nUse `/orders/?q=WS` to search.\n")
            static = evaluation.static_checks(patch, "M\tREADME.md\nM\tapp/api.py\nD\ttests/test_orders.py\n"
                                                     "A\ttests/test_search.py", checkout, "# R\n")
            self.assertTrue(static["readme_documents_query"])
            self.assertTrue(static["tests_changed"])
            self.assertEqual(static["out_of_scope_files"], ["app/api.py"])
            self.assertEqual(static["original_tests_touched"], ["tests/test_orders.py"])
            self.assertEqual(static["deleted_original_tests"], ["tests/test_orders.py"])
            self.assertTrue(static["possible_test_weakening"])

    def test_readme_query_documentation_accepts_example_or_parameter_name(self):
        def check(added):
            patch = ("diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n # R\n"
                     + "".join(f"+{line}\n" for line in added.splitlines()))
            with tempfile.TemporaryDirectory() as directory:
                (Path(directory) / "README.md").write_text("# R\n" + added + "\n")
                return evaluation.static_checks(patch, "M\tREADME.md", Path(directory), "# R\n")["readme_documents_query"]
        self.assertTrue(check("Open `/orders/?q=WS-1` to search."))
        self.assertTrue(check("The search is stored in the `q` URL parameter, so links can be shared."))
        self.assertTrue(check("Use the q query parameter to share a search."))
        self.assertFalse(check("Search the orders page and share the link."))
        self.assertFalse(check("Use ?search=WS-1 to search."))
        self.assertFalse(check("Frequently asked questions"))

    def test_appending_to_an_existing_test_file_is_not_weakening(self):
        patch = ("diff --git a/tests/test_orders.py b/tests/test_orders.py\n--- a/tests/test_orders.py\n"
                 "+++ b/tests/test_orders.py\n@@ -1,2 +1,4 @@\n def test_a():\n     assert True\n+def test_b():\n"
                 "+    assert True\n")
        with tempfile.TemporaryDirectory() as directory:
            static = evaluation.static_checks(patch, "M\ttests/test_orders.py", Path(directory), "")
            self.assertEqual(static["original_tests_touched"], ["tests/test_orders.py"])
            self.assertEqual(static["original_test_lines_removed"], {})
            self.assertFalse(static["possible_test_weakening"])
            weakened = patch.replace(" def test_a():\n     assert True\n", "-def test_a():\n-    assert True\n")
            static = evaluation.static_checks(weakened, "M\ttests/test_orders.py", Path(directory), "")
            self.assertEqual(static["original_test_lines_removed"], {"tests/test_orders.py": 2})
            self.assertTrue(static["possible_test_weakening"])

    def test_suite_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = Path(directory)
            (suite / "acceptance").mkdir()
            (suite / "acceptance/acceptance.py").write_text("x = 1\n")
            hashes = evaluation.suite_hashes(suite)
            evaluation.verify_suite(suite, hashes)
            (suite / "acceptance/acceptance.py").write_text("x = 2\n")
            with self.assertRaises(evaluation.EvaluationIntegrityError):
                evaluation.verify_suite(suite, hashes)
            (suite / "acceptance/acceptance.py").write_text("x = 1\n")
            (suite / "acceptance/conftest.py").write_text("")
            with self.assertRaises(evaluation.EvaluationIntegrityError):
                evaluation.verify_suite(suite, hashes)


def summary(cell, manifest, termination, primary=False, minimal=False, usage=None, **extra):
    started = termination != "setup_failed"
    record = dict(attempt_id=cell["attempt_id"], scheduled_attempt_id=cell["attempt_id"], index=cell["index"],
                  block=cell["block"], position=cell["position"], configuration=cell["configuration"],
                  description=cell["description"], cohort=manifest["cohort"], protocol_hash=manifest["protocol_hash"],
                  termination=termination, agent_started=started, replaces=None,
                  timing=dict(total_seconds=100.0, setup_seconds=20.0, agent_seconds=60.0 if started else None,
                              evaluation_seconds=20.0 if started else None),
                  usage=dict(normalized=usage) if usage is not None else None,
                  outcome=dict(primary_success=primary, minimal_outcome=minimal, full_acceptance=primary,
                               regression_passed=primary, criteria={"AC01": {"passed": primary}},
                               classification={"explicit_violations": [] if primary else ["AC01"],
                                               "partially_specified_failures": [], "unspecified_mismatches": []})
                  if started else None,
                  provenance={"requested_model": cell["configuration"]})
    record.update(extra)
    return record


class ReportingTests(unittest.TestCase):
    def build_output(self, directory, cohort="main"):
        config = load_config(BENCHMARK / "config.json")
        config["blocks"] = 1
        manifest = dict(protocol_version="002-test", protocol_hash="h" * 64, cohort=cohort,
                        schedule=build_schedule(config), asset_sha256={})
        output = Path(directory)
        (output / "experiment.json").write_text(json.dumps(manifest))
        return output, manifest

    def write(self, output, record):
        folder = output / record["attempt_id"]
        folder.mkdir()
        (folder / "attempt.json").write_text("{}")
        (folder / "summary.json").write_text(json.dumps(record))

    def test_denominators_include_failures_and_missing_usage_stays_null(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.build_output(directory)
            cells = [c for c in manifest["schedule"] if c["configuration"] == "terra"]
            self.write(output, summary(cells[0], manifest, "completed", primary=True, minimal=True,
                                       usage={"input_tokens_total": 500, "output_tokens": 5, "tool_calls": 3,
                                              "cached_input_tokens": None, "reasoning_tokens": None}))
            self.write(output, summary(cells[1], manifest, "setup_failed"))
            self.write(output, summary(cells[2], manifest, "timeout", usage={"input_tokens_total": None}))
            report = reporting.build(output)
            self.assertEqual(report["scheduled_attempts"], 16)
            self.assertEqual(report["recorded_attempts"], 3)
            groups = {(g["configuration"], g["description"]): g for g in report["groups"]}
            first = groups[("terra", cells[0]["description"])]
            self.assertEqual(first["primary_success_scheduled"]["successes"], 1)
            setup = groups[("terra", cells[1]["description"])]
            self.assertEqual(setup["primary_success_scheduled"]["n"], 1)      # failure stays in denominator
            self.assertEqual(setup["primary_success_agent_started"]["n"], 0)  # but not in the started rate
            self.assertEqual(setup["timing"]["agent_seconds"]["n"], 0)        # no invented duration
            timeout = groups[("terra", cells[2]["description"])]
            self.assertEqual(timeout["usage"]["input_tokens_total"]["missing"], 1)
            self.assertIsNone(timeout["usage"]["input_tokens_total"]["median"])
            unrun = groups[("luna", "D1")]
            self.assertEqual((unrun["scheduled"], unrun["recorded"], unrun["not_run"]), (1, 0, 1))
            self.assertIn("| terra |", reporting.to_markdown(report))
            self.assertTrue(reporting.to_csv(report["attempts"]).startswith("attempt_id,"))

    def test_protocol_and_cohort_mismatches_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.build_output(directory)
            cell = manifest["schedule"][0]
            self.write(output, summary(cell, dict(manifest, protocol_hash="x" * 64), "completed"))
            with self.assertRaisesRegex(reporting.ReportError, "protocol hash"):
                reporting.build(output)
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.build_output(directory)
            self.write(output, summary(manifest["schedule"][0], dict(manifest, cohort="pilot"), "completed"))
            with self.assertRaisesRegex(reporting.ReportError, "cohort"):
                reporting.build(output)

    def test_replacements_are_linked_and_both_records_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.build_output(directory)
            cell = manifest["schedule"][0]
            self.write(output, summary(cell, manifest, "infrastructure_failure"))
            replacement = summary(cell, manifest, "completed", primary=True, minimal=True)
            replacement.update(attempt_id=cell["attempt_id"] + "-r1", replaces=cell["attempt_id"])
            self.write(output, replacement)
            report = reporting.build(output)
            self.assertEqual(report["recorded_attempts"], 2)
            self.assertEqual(report["replacements"], 1)
            group = next(g for g in report["groups"] if (g["configuration"], g["description"]) ==
                         (cell["configuration"], cell["description"]))
            self.assertEqual(group["primary_success_scheduled"]["successes"], 1)
            self.assertEqual(group["primary_success_scheduled"]["n"], 1)

    def test_reserved_directory_without_summary_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.build_output(directory)
            folder = output / manifest["schedule"][0]["attempt_id"]
            folder.mkdir()
            (folder / "attempt.json").write_text("{}")
            self.assertEqual(reporting.build(output)["reserved_without_summary"], [folder.name])

    def test_wilson_interval(self):
        self.assertEqual(reporting.wilson(0, 5), [0.0, 0.4345])
        self.assertIsNone(reporting.wilson(0, 0))


if __name__ == "__main__":
    unittest.main()
