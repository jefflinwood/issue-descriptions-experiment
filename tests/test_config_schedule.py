import copy
import json
import tempfile
import unittest

from issue_specification.config import ConfigError, load_config, protocol_hash
from issue_specification.schedule import build_schedule

from tests.helpers import BENCHMARK, copy_benchmark


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(BENCHMARK / "config.json")

    def test_main_plan_has_80_attempts_in_balanced_complete_blocks(self):
        schedule = build_schedule(self.config)
        self.assertEqual(len(schedule), 80)
        cells = {(c["id"], d) for c in self.config["configurations"] for d in self.config["descriptions"]}
        self.assertEqual(len(cells), 16)
        for block in range(1, 6):
            rows = [r for r in schedule if r["block"] == block]
            self.assertEqual(len(rows), 16)
            self.assertEqual({(r["configuration"], r["description"]) for r in rows}, cells)
            self.assertEqual([r["position"] for r in rows], list(range(1, 17)))
        self.assertEqual([r["index"] for r in schedule], list(range(80)))
        self.assertEqual(len({r["attempt_id"] for r in schedule}), 80)

    def test_schedule_is_deterministic_and_seed_dependent(self):
        self.assertEqual(build_schedule(self.config), build_schedule(copy.deepcopy(self.config)))
        other = dict(self.config, seed=self.config["seed"] + 1)
        self.assertNotEqual([r["attempt_id"] for r in build_schedule(self.config)],
                            [r["attempt_id"] for r in build_schedule(other)])

    def test_blocks_are_randomized_not_identical(self):
        schedule = build_schedule(self.config)
        orders = {tuple((r["configuration"], r["description"]) for r in schedule if r["block"] == b)
                  for b in range(1, 6)}
        self.assertGreater(len(orders), 1)

    def test_requested_models_match_spec(self):
        models = {c["id"]: (c["harness"], c["model"]) for c in self.config["configurations"]}
        self.assertEqual(models, {"terra": ("codex", "gpt-6.1-terra"), "luna": ("codex", "gpt-6-luna"),
                                  "sonnet": ("claude_code", "claude-sonnet-5-5"),
                                  "haiku": ("claude_code", "claude-haiku-5-5")})


class ConfigValidationTests(unittest.TestCase):
    def mutate(self, change):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        benchmark = copy_benchmark(directory.name)
        raw = json.loads((benchmark / "config.json").read_text())
        change(raw)
        (benchmark / "config.json").write_text(json.dumps(raw))
        return benchmark / "config.json"

    def assertRejected(self, change, message):
        with self.assertRaisesRegex(ConfigError, message):
            load_config(self.mutate(change))

    def test_codex_cannot_run_anthropic_model(self):
        def change(raw):
            raw["configurations"][0]["model"] = "claude-sonnet-5-5"
        self.assertRejected(change, "Codex cannot")

    def test_claude_code_rejects_openai_provider(self):
        def change(raw):
            raw["configurations"][2]["provider"] = "openai"
        self.assertRejected(change, "cannot run provider")

    def test_floating_aliases_are_rejected(self):
        def change(raw):
            raw["configurations"][2]["model"] = "sonnet"
        self.assertRejected(change, "explicit claude")

    def test_model_selection_provenance_required(self):
        def change(raw):
            del raw["configurations"][1]["model_selection"]
        self.assertRejected(change, "model_selection")

    def test_short_revision_rejected(self):
        def change(raw):
            raw["source"]["revision"] = "353c3cc"
        self.assertRejected(change, "40-character")

    def test_duplicate_configuration_ids_rejected(self):
        def change(raw):
            raw["configurations"][1]["id"] = raw["configurations"][0]["id"]
        self.assertRejected(change, "unique")

    def test_unsandboxed_real_agents_rejected(self):
        def change(raw):
            raw["isolation"]["mode"] = "none"
        self.assertRejected(change, "only for fake")

    def test_invalid_effort_rejected(self):
        def change(raw):
            raw["configurations"][3]["effort"] = "turbo"
        self.assertRejected(change, "effort")

    def test_missing_python_fails_before_any_work(self):
        def change(raw):
            raw["python"] = "/nonexistent/python9"
        self.assertRejected(change, "not found")


class ProtocolHashTests(unittest.TestCase):
    def test_hash_is_portable_across_checkout_locations(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            a = load_config(copy_benchmark(first) / "config.json")
            b = load_config(copy_benchmark(second) / "config.json")
            self.assertEqual(protocol_hash(a), protocol_hash(b))
            self.assertEqual(protocol_hash(a), protocol_hash(load_config(BENCHMARK / "config.json")))

    def test_hash_changes_with_any_frozen_input(self):
        base = protocol_hash(load_config(BENCHMARK / "config.json"))
        for relative in ("descriptions/d1-vague.md", "descriptions/common.txt", "evaluation/acceptance.py",
                         "requirements.json", "constraints.txt", "reference/negative-controls/noop.patch"):
            with self.subTest(relative), tempfile.TemporaryDirectory() as directory:
                benchmark = copy_benchmark(directory)
                with (benchmark / relative).open("a") as stream:
                    stream.write("\n")
                self.assertNotEqual(protocol_hash(load_config(benchmark / "config.json")), base)

    def test_hash_changes_with_settings_and_source_kind(self):
        config = load_config(BENCHMARK / "config.json")
        base = protocol_hash(config)
        changed = copy.deepcopy(config)
        changed["limits"]["agent_timeout_seconds"] += 1
        self.assertNotEqual(protocol_hash(changed), base)
        local = copy.deepcopy(config)
        local["source"]["kind"] = "local-override"
        self.assertNotEqual(protocol_hash(local), base)


class TreatmentDisclosureTests(unittest.TestCase):
    """The vague treatments must not leak the richer contract."""

    def text(self, name):
        """Description text with line wrapping collapsed, so phrases match across lines."""
        return " ".join((BENCHMARK / "descriptions" / name).read_text().split())

    def test_common_wrapper_and_d1_do_not_reveal_contract(self):
        for name in ("common.txt", "d1-vague.md"):
            text = self.text(name).lower()
            for leak in ("prefill", "inactive", "no longer available", "today's prices", "current price", "due date",
                         "ship date", "shipping method", "staff", "added together", "preselect", "/orders/new"):
                self.assertNotIn(leak, text, f"{name} leaks {leak!r}")

    def test_d2_omits_examples_entry_points_and_verification(self):
        text = self.text("d2-clarified.md")
        for absent in ("## Acceptance examples", "## Repository entry points", "WS-TEST-001", "Retired Blend",
                       "new()", "product_id", "## Verification"):
            self.assertNotIn(absent, text)

    def test_d3_and_d4_share_examples_entry_points_and_constraints(self):
        d3, d4 = self.text("d3-agent-ready.md"), self.text("d4-expanded.md")
        self.assertGreater(len(d4), len(d3) * 1.2)
        for token in ("WS-TEST-001", "WS-TEST-002", "customer@example.test", "staff@example.test", "Test Blend",
                      "Retired Blend", "$100.00", "$120.00", "$90.00", "FedEx", "UPS", "403",
                      "Enter a valid delivery due date.", "/orders/new", "`new()`", "`detail()`", "`product_id`",
                      "`quantity`", "`customer_id`", "`shipping_method`", "`due_date`", "`requested_ship_date`",
                      "`Product.active`", "`OrderItem.unit_price`", "tests/test_reorder.py", "python -m pytest -q",
                      "app/templates/orders/detail.html", "Do not send prices through"):
            self.assertIn(token, d3, f"D3 missing {token!r}")
            self.assertIn(token, d4, f"D4 missing {token!r}")
        for i in range(1, 12):
            self.assertIn(f" {i}. ", d3)
            self.assertIn(f" {i}. ", d4)

    def test_d2_d3_d4_state_every_product_decision(self):
        facts = [("reorder",), ("prefill",), ("never places an order",),
                 ("added together",), ("preselected",), ("only their own orders",), ("refused",),
                 ("staff can reorder any",), ("no longer active",), ("no longer available",),
                 ("nothing is prefilled",), ("new order number",),
                 ("today's prices",), ("original order is not changed", "original order itself is not changed"),
                 ("due date",), ("ship date",), ("stays required",), ("shipping method",),
                 ("exactly as it does today",), ("schema",), ("dependencies",),
                 ("one route",), ("readme",)]
        for name in ("d2-clarified.md", "d3-agent-ready.md", "d4-expanded.md"):
            text = self.text(name).lower()
            for alternatives in facts:
                self.assertTrue(any(a in text for a in alternatives), f"{name} missing {alternatives!r}")

    def test_no_description_names_hidden_evaluation_fixtures(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("fx_names", BENCHMARK / "evaluation/fixtures.py")
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = module  # dataclasses resolve their module while executing
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        hidden = [p[1] for p in module.PRODUCTS] + [c[1] for c in module.CUSTOMERS]
        hidden += [o[0] for o in module.ORDERS.values()] + [p[1].split()[0] for p in module.PRODUCTS]
        for name in ("common.txt", "d1-vague.md", "d2-clarified.md", "d3-agent-ready.md", "d4-expanded.md"):
            text = self.text(name).lower()
            for secret in hidden:
                self.assertNotIn(secret.lower(), text, f"{name} names hidden fixture {secret!r}")

    def test_d4_noise_resolves_to_d3_final_state(self):
        d4 = self.text("d4-expanded.md")
        self.assertIn("supersedes my comment above", d4)
        self.assertIn("Reversing that: neither the delivery due date nor the requested ship date is copied", d4)
        self.assertIn("this comment is the spec", d4)
        self.assertIn("Not in this issue", d4)
        # The superseded positions appear only before their reversals.
        self.assertLess(d4.index("one click, places the order"), d4.index("supersedes my comment above"))
        self.assertLess(d4.index("carry the requested ship date over"), d4.index("Reversing that"))
        final = d4[d4.index("this comment is the spec"):]
        self.assertIn("never places an order by itself", final)
        self.assertNotIn("one click", final.lower())

    def test_archived_url_search_workload_reproduces_main_002_hash(self):
        archived = load_config(BENCHMARK / "archive/002.1-url-search/config.json")
        self.assertEqual(archived["protocol_version"], "002.1")
        self.assertTrue(protocol_hash(archived).startswith("205b9bdeb7fcbc00"))

if __name__ == "__main__":
    unittest.main()
