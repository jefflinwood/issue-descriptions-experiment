"""Frozen acceptance checks for the reorder workload.

Test names start with a criterion ID from ``benchmark/requirements.json``:
``test_acNN_*`` for the behavioral contract and ``test_min_*`` for the minimal
common outcome. Run from an evaluation directory outside the candidate checkout,
with the candidate repository first on ``PYTHONPATH``.
"""
from decimal import Decimal
import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest

_spec = importlib.util.spec_from_file_location("reorder_eval_fixtures", Path(__file__).with_name("fixtures.py"))
fx = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fx
_spec.loader.exec_module(fx)


@pytest.fixture
def world():
    w = fx.build_world()
    yield w
    from app import db
    with w.app.app_context():
        db.session.remove()
        db.drop_all()


def user(world, email):
    return fx.client_for(world, email)


def number(key):
    return fx.ORDERS[key][0]


def controls_on_detail(client, world, key):
    url = f"/orders/{world.ids[key]}"
    response = client.get(url)
    assert response.status_code == 200, f"{url} returned {response.status_code}"
    return fx.reorder_controls(response.get_data(as_text=True), url)


def reorder_page(client, world, key):
    """Use the reorder control on an order's page; return (response, order form or None, landed url)."""
    controls = controls_on_detail(client, world, key)
    assert controls, f"no reorder control on the page for {number(key)}"
    response = fx.activate(client, controls[0])
    html = response.get_data(as_text=True)
    return response, fx.order_form(html), fx.landed_url(response)


def new_orders(before, after):
    return {k: v for k, v in after.items() if k not in before}


# ------------------------------------------------------------- AC01: reorder entry point
def test_ac01_order_page_offers_reorder_to_owner_and_staff(world):
    assert controls_on_detail(user(world, fx.NORTH), world, "N1"), "owner sees no reorder control"
    assert controls_on_detail(user(world, fx.ADA), world, "R1"), "retail owner sees no reorder control"
    assert controls_on_detail(user(world, fx.STAFF), world, "S1"), "staff see no reorder control"


# ----------------------------------- AC02: prefilled new-order form, nothing created by itself
def test_ac02_reorder_prefills_active_quantities_without_creating(world):
    client = user(world, fx.NORTH)
    before = fx.snapshot(world)
    response, form, _ = reorder_page(client, world, "N1")
    assert response.status_code == 200
    assert form is not None, "reorder did not show the new-order form"
    assert fx.prefilled_quantities(form) == fx.expected_quantities(world, "N1")
    assert fx.snapshot(world) == before, "using the reorder control created or changed an order"


def test_ac02_repeated_lines_are_summed(world):
    _, form, _ = reorder_page(user(world, fx.NORTH), world, "N3")
    assert form is not None
    assert fx.prefilled_quantities(form) == fx.expected_quantities(world, "N3")


def test_ac02_staff_reorder_preselects_original_account(world):
    client = user(world, fx.STAFF)
    before = fx.snapshot(world)
    _, form, _ = reorder_page(client, world, "S1")
    assert form is not None
    assert fx.prefilled_quantities(form) == fx.expected_quantities(world, "S1")
    assert fx.selected(form, "customer_id") == str(world.customers["southside"])
    assert fx.snapshot(world) == before


# ------------------------------------------- AC03: customers reorder only their own orders
def test_ac03_customer_cannot_reorder_another_customers_order(world):
    client = user(world, fx.NORTH)
    own = controls_on_detail(client, world, "N1")
    assert own, "no reorder control to probe"
    for other in ("S1", "R1"):
        before = fx.snapshot(world)
        probe = fx.retarget(own[0], world.ids["N1"], world.ids[other], number("N1"), number(other))
        response = fx.activate(client, probe)
        html = response.get_data(as_text=True)
        form = fx.order_form(html)
        leaked = form is not None and fx.prefilled_quantities(form) == fx.expected_quantities(world, other)
        assert not leaked, f"customer got a form prefilled from {number(other)}"
        assert number(other) not in fx.Page(html).visible_text, f"{number(other)} shown to another customer"
        assert fx.snapshot(world) == before, f"an order was created or changed from {number(other)}"


def test_ac03_staff_can_reorder_any_account(world):
    _, form, _ = reorder_page(user(world, fx.STAFF), world, "R1")
    assert form is not None
    assert fx.prefilled_quantities(form) == fx.expected_quantities(world, "R1")
    assert fx.selected(form, "customer_id") == str(world.customers["ada"])


# --------------------------------------------------- AC04: inactive products skipped and named
def test_ac04_inactive_products_are_left_out_and_named(world):
    response, form, _ = reorder_page(user(world, fx.NORTH), world, "N1")
    assert form is not None
    assert world.products["harvest"] not in fx.prefilled_quantities(form)
    text = fx.Page(response.get_data(as_text=True)).visible_text
    assert "Harvest Reserve" in text, "the skipped product is not named"
    assert fx.UNAVAILABLE_WORDS.search(text), "no message says the product is unavailable"


def test_ac04_order_with_only_inactive_products(world):
    client = user(world, fx.NORTH)
    before = fx.snapshot(world)
    controls = controls_on_detail(client, world, "N2")
    assert controls
    response = fx.activate(client, controls[0])
    assert response.status_code < 500
    html = response.get_data(as_text=True)
    form = fx.order_form(html)
    assert form is None or fx.prefilled_quantities(form) == {}
    text = fx.Page(html).visible_text
    assert "Harvest Reserve" in text and fx.UNAVAILABLE_WORDS.search(text)
    assert fx.snapshot(world) == before


# ------------------------------- AC05: submission uses current prices; original unchanged
def _submit_reorder(world, email, key, **overrides):
    client = user(world, email)
    _, form, landed = reorder_page(client, world, key)
    assert form is not None
    before = fx.snapshot(world)
    response = fx.submit(client, form, landed, {"due_date": fx.FUTURE_DUE, **overrides})
    return before, fx.snapshot(world), response


def test_ac05_submitted_reorder_uses_current_prices_and_keeps_original(world):
    before, after, response = _submit_reorder(world, fx.NORTH, "N1")
    assert response.status_code in (302, 303), f"submission returned {response.status_code}"
    created = new_orders(before, after)
    assert len(created) == 1, "submitting the reorder did not create exactly one order"
    order = next(iter(created.values()))
    assert order["customer_id"] == world.customers["northside"]
    assert order["number"] != number("N1")
    assert order["items"] == [(world.products["summit"], 2, Decimal("120.00"))]
    assert after[world.ids["N1"]] == before[world.ids["N1"]], "the original order changed"


def test_ac05_retail_reorder_uses_current_bag_price(world):
    before, after, _ = _submit_reorder(world, fx.ADA, "R1")
    created = list(new_orders(before, after).values())
    assert len(created) == 1
    assert created[0]["items"] == [(world.products["summit"], 4, Decimal("16.00"))]


# ---------------------------------------------- AC06: dates are chosen again, not copied
def test_ac06_dates_are_not_copied(world):
    _, form, _ = reorder_page(user(world, fx.NORTH), world, "N1")
    assert form is not None
    assert (fx.selected(form, "due_date") or "") == "", "the original due date was copied"
    assert (fx.selected(form, "requested_ship_date") or "") == "", "the original ship date was copied"


def test_ac06_due_date_still_required(world):
    before, after, _ = _submit_reorder(world, fx.NORTH, "N1", due_date="")
    assert new_orders(before, after) == {}, "an order was created without a delivery due date"


# -------------------------------------------------- AC07: shipping method carried over
def test_ac07_shipping_method_is_preselected(world):
    client = user(world, fx.NORTH)
    for key in ("N1", "N3"):
        _, form, _ = reorder_page(client, world, key)
        assert form is not None
        assert fx.selected(form, "shipping_method") == fx.ORDERS[key][3], key


# ------------------------------------------------ AC08: the plain new-order form is unchanged
def test_ac08_plain_new_order_form_is_unchanged(world):
    for email in (fx.STAFF, fx.NORTH):
        response = user(world, email).get("/orders/new")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        form = fx.order_form(html)
        assert form is not None
        assert fx.prefilled_quantities(form) == {}
        assert not fx.UNAVAILABLE_WORDS.search(fx.Page(html).visible_text)
        assert (fx.selected(form, "due_date") or "") == ""


def test_ac08_existing_access_rules_unchanged(world):
    anonymous = world.app.test_client()
    response = anonymous.get(f"/orders/{world.ids['N1']}")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    assert user(world, fx.NORTH).get(f"/orders/{world.ids['S1']}").status_code == 403


# ---------------------------------------- AC09: no schema/dependency changes; one route at most
def test_ac09_no_schema_or_dependency_changes(world):
    from app import db
    import app as package
    schema = {t.name: sorted(c.name for c in t.columns) for t in db.metadata.sorted_tables}
    assert schema == fx.BASELINE_SURFACE["schema"]
    root = Path(package.__file__).resolve().parent.parent
    for name, expected in fx.BASELINE_SURFACE["requirements_sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name


def test_ac09_existing_routes_kept_and_at_most_one_order_route_added(world):
    routes = {f"{r.rule} {','.join(sorted(r.methods - {'HEAD', 'OPTIONS'}))}" for r in world.app.url_map.iter_rules()}
    baseline = set(fx.BASELINE_SURFACE["routes"])
    assert baseline <= routes, f"routes removed or changed: {sorted(baseline - routes)}"
    added = sorted(routes - baseline)
    assert len(added) <= 1, f"more than one route added: {added}"
    assert all(a.startswith("/orders/") for a in added), f"route added outside /orders/: {added}"


# ------------------------------------------------------------- Minimal common outcome
# Justified for every description: D1 says only "reorder a past order", so either a
# prefilled form (submitted here with a due date) or a direct one-click order counts.
def _reorder_controls_anywhere(client, world, key):
    controls = controls_on_detail(client, world, key)
    if controls:
        return controls
    listing = client.get("/orders/").get_data(as_text=True)
    return [c for c in fx.reorder_controls(listing, "/orders/") if str(world.ids[key]) in c.url
            or number(key) in c.url or any(str(world.ids[key]) == v for _, v in c.data)]


def test_min_reorder_recreates_active_items(world):
    client = user(world, fx.NORTH)
    controls = _reorder_controls_anywhere(client, world, "N3")
    assert controls, "no reorder control found"
    expected = fx.expected_quantities(world, "N3")
    before = fx.snapshot(world)
    response = fx.activate(client, controls[0])
    created = new_orders(before, fx.snapshot(world))
    if not created:
        form = fx.order_form(response.get_data(as_text=True))
        assert form is not None, "reorder neither created an order nor showed an order form"
        fx.submit(client, form, fx.landed_url(response), {"due_date": fx.FUTURE_DUE})
        created = new_orders(before, fx.snapshot(world))
    assert len(created) == 1, f"expected one new order, got {len(created)}"
    order = next(iter(created.values()))
    assert order["customer_id"] == world.customers["northside"]
    assert fx.summed(order["items"]) == expected


def test_min_reorder_never_crosses_accounts(world):
    client = user(world, fx.NORTH)
    controls = _reorder_controls_anywhere(client, world, "N1")
    probes = []
    for other in ("S1", "R1"):
        probes += [fx.retarget(c, world.ids["N1"], world.ids[other], number("N1"), number(other)) for c in controls]
        oid = world.ids[other]
        probes += [fx.Control("link", "get", f"/orders/{oid}/reorder", [], "guess"),
                   fx.Control("form", "post", f"/orders/{oid}/reorder", [], "guess"),
                   fx.Control("link", "get", f"/orders/new?reorder={oid}", [], "guess"),
                   fx.Control("link", "get", f"/orders/new?from={oid}", [], "guess"),
                   fx.Control("link", "get", f"/orders/new?order_id={oid}", [], "guess")]
    others = {world.customers["southside"], world.customers["ada"]}
    for probe in probes:
        before = fx.snapshot(world)
        response = fx.activate(client, probe)
        after = fx.snapshot(world)
        assert not [o for o in new_orders(before, after).values() if o["customer_id"] in others], probe
        assert all(after[k] == v for k, v in before.items()), f"{probe.url} changed an existing order"
        form = fx.order_form(response.get_data(as_text=True))
        for other in ("S1", "R1"):
            if form is not None:
                assert fx.prefilled_quantities(form) != fx.expected_quantities(world, other), probe
