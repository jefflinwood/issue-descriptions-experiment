"""Frozen acceptance checks for Experiment 002 (URL-addressable order search).

Test names start with a criterion ID from ``benchmark/requirements.json``:
``test_acNN_*`` for the full behavioral contract and ``test_min_*`` for the
minimal common outcome. Run from an evaluation directory outside the candidate
checkout, with the candidate repository first on ``PYTHONPATH``.
"""
import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest

_spec = importlib.util.spec_from_file_location("issue002_eval_fixtures", Path(__file__).with_name("fixtures.py"))
fx = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fx
_spec.loader.exec_module(fx)

STAFF, NORTH, CATERING = fx.STAFF, fx.NORTH, fx.CATERING
ALL = fx.ALL_NUMBERS


@pytest.fixture
def app():
    application = fx.build_app()
    yield application
    from app import db
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def staff(app):
    return fx.client_for(app, STAFF)


@pytest.fixture
def north(app):
    return fx.client_for(app, NORTH)


@pytest.fixture
def catering(app):
    return fx.client_for(app, CATERING)


def visible(client, url):
    response = client.get(url)
    assert response.status_code == 200, f"{url} returned {response.status_code}"
    return fx.listed_set(response)


def q(value):
    from urllib.parse import quote
    return "/orders/?q=" + quote(value, safe="")


def S(*numbers):
    return frozenset(numbers)


# ------------------------------------------------------------------ AC01: GET q filters
def test_ac01_q_filters_server_rendered_rows(staff):
    assert visible(staff, q("WS-2001")) == S("WS-2001")


def test_ac01_shared_url_reproduces_results_in_new_session(app, staff):
    first = visible(staff, q("northside cafe"))
    second = visible(fx.client_for(app, STAFF), q("northside cafe"))
    assert first == second == S("WS-1001", "WS-1002", "WS-NORTH-1004")


# ------------------------------------------------- AC02: missing/empty/blank q is baseline
def test_ac02_missing_empty_and_blank_query_preserve_staff_list(staff):
    for url in ("/orders/", "/orders/?q=", "/orders/?q=%20%20%20", "/orders/?q=%09"):
        assert visible(staff, url) == ALL, url


def test_ac02_missing_and_blank_query_preserve_customer_list(north):
    for url in ("/orders/", "/orders/?q=", "/orders/?q=%20%20"):
        assert visible(north, url) == fx.OWN[NORTH], url


# -------------------------------------- AC03: trimmed, case-insensitive order-number substring
def test_ac03_order_number_match_is_case_insensitive_substring(staff):
    assert visible(staff, q("ws-1001")) == S("WS-1001")
    assert visible(staff, q("WS-NORTH-1003")) == S("Ws-NoRtH-1003")
    assert visible(staff, q("2001")) == S("WS-2001")


def test_ac03_query_is_trimmed(staff):
    assert visible(staff, q("  WS-1001  ")) == S("WS-1001")
    assert visible(staff, q("\tRT-3001 ")) == S("RT-3001")


# ------------------------------------------------- AC04: staff OR across number and name
def test_ac04_staff_matches_customer_names(staff):
    assert visible(staff, q("SOUTHSIDE")) == S("WS-2001", "WS-2002")
    assert visible(staff, q("northside")) == S("WS-1001", "WS-1002", "Ws-NoRtH-1003", "WS-NORTH-1004")
    assert visible(staff, q("catering")) == S("Ws-NoRtH-1003")


def test_ac04_staff_number_or_name_union(staff):
    assert visible(staff, q("north")) == S("WS-1001", "WS-1002", "Ws-NoRtH-1003", "WS-NORTH-1004")
    assert visible(staff, q("1003")) == S("Ws-NoRtH-1003")
    assert visible(staff, q("cafe")) == S("WS-1001", "WS-1002", "WS-2001", "WS-NORTH-1004", "WS-2002")


# ------------------------------------------- AC05: customers search only own order numbers
def test_ac05_customer_search_never_exposes_other_accounts(north, catering):
    assert visible(north, q("1003")) == S()
    assert visible(north, q("WS-2001")) == S()
    assert visible(catering, q("WS-1001")) == S()
    assert visible(catering, q("north")) == S("Ws-NoRtH-1003")


def test_ac05_customer_search_matches_own_order_numbers_only(north):
    assert visible(north, q("north")) == S("WS-NORTH-1004")
    assert visible(north, q("ws-100")) == S("WS-1001", "WS-1002")
    assert visible(north, q("100")) == S("WS-1001", "WS-1002", "WS-NORTH-1004")
    assert visible(north, q("Northside Cafe")) == S()


# ------------------------------------------------------- AC06: wildcard characters are literal
def test_ac06_percent_is_literal(staff, north):
    assert visible(staff, q("%")) == S("RT-100%-A", "RT-100X-B")
    assert visible(staff, q("100%-")) == S("RT-100%-A")
    assert visible(north, q("%")) == S()


def test_ac06_underscore_is_literal(staff):
    assert visible(staff, q("_")) == S("RT-PROMO_1", "RT-3001")
    assert visible(staff, q("PROMO_1")) == S("RT-PROMO_1")
    assert visible(staff, q("Bean_C")) == S("RT-PROMO_1", "RT-3001")


def test_ac06_backslash_is_literal(staff):
    assert visible(staff, q("\\")) == S()
    assert visible(staff, q("\\%")) == S()


# ---------------------------------------- AC07: AND with staff filters; ordering and access kept
def test_ac07_status_and_type_filters_combine_with_and(staff):
    assert visible(staff, "/orders/?status=pending&q=north") == S("WS-1001", "Ws-NoRtH-1003", "WS-NORTH-1004")
    assert visible(staff, "/orders/?type=retail&q=100") == S("RT-100%-A", "RT-100X-B")
    assert visible(staff, "/orders/?type=wholesale&q=100") == S(
        "WS-1001", "WS-1002", "Ws-NoRtH-1003", "WS-NORTH-1004")
    assert visible(staff, "/orders/?status=shipped&type=wholesale&q=ws") == S("WS-1002", "WS-2001")
    assert visible(staff, "/orders/?status=delivered&q=north") == S()


def test_ac07_newest_first_ordering_is_preserved(staff):
    rows = fx.listed_orders(staff.get(q("ws")))
    expected = [n for n in fx.NEWEST_FIRST if n.lower().startswith("ws")]
    assert rows == expected


def test_ac07_existing_access_rules_unchanged(app):
    anonymous = app.test_client()
    response = anonymous.get(q("WS-1001"))
    assert response.status_code == 302 and "/login" in response.headers["Location"]


# ----------------------------------------------- AC08: labeled GET form, safe echo, retained state
def _form(client, url):
    response = client.get(url)
    page = fx.Page(response.get_data(as_text=True))
    forms = fx.search_forms(page, "q")
    assert forms, f"no form with a text/search control named q on {url}"
    return page, forms[0]


def test_ac08_labeled_get_form_submits_query(staff, north):
    page, (form, control) = _form(staff, "/orders/")
    assert form.method == "get"
    assert fx.is_labeled(page, control), "search control has no accessible label"
    response, _ = fx.submit(staff, "/orders/", form, {"q": "northside"})
    assert fx.listed_set(response) == S("WS-1001", "WS-1002", "Ws-NoRtH-1003", "WS-NORTH-1004")
    _, (form, _) = _form(north, "/orders/")
    response, _ = fx.submit(north, "/orders/", form, {"q": "north"})
    assert fx.listed_set(response) == S("WS-NORTH-1004")


def test_ac08_current_query_is_shown_and_escaped(staff):
    payload = '"><script>window.__eval_xss=1</script>'
    response = staff.get(q(payload))
    assert response.status_code == 200
    assert "<script>window.__eval_xss=1</script>" not in response.get_data(as_text=True)
    page = fx.Page(response.get_data(as_text=True))
    forms = fx.search_forms(page, "q")
    assert forms and forms[0][1].value.strip() == payload.strip()
    _, (_, control) = _form(staff, q("Northside"))
    assert control.value == "Northside"


def test_ac08_form_retains_active_filters(staff):
    url = "/orders/?status=pending"
    _, (form, _) = _form(staff, url)
    response, _ = fx.submit(staff, url, form, {"q": "north"})
    assert fx.listed_set(response) == S("WS-1001", "Ws-NoRtH-1003", "WS-NORTH-1004")
    url = "/orders/?type=retail"
    _, (form, _) = _form(staff, url)
    response, _ = fx.submit(staff, url, form, {"q": "100"})
    assert fx.listed_set(response) == S("RT-100%-A", "RT-100X-B")


def test_ac08_filter_links_retain_query(staff):
    url = q("north")
    page = fx.Page(staff.get(url).get_data(as_text=True))
    pending = fx.link_with(page, status="pending", q="north")
    wholesale = fx.link_with(page, type="wholesale", q="north")
    assert pending and wholesale, "status/type filter links do not carry q"
    response, _ = fx.follow(staff, pending, url)
    assert fx.listed_set(response) == S("WS-1001", "Ws-NoRtH-1003", "WS-NORTH-1004")
    response, _ = fx.follow(staff, wholesale, url)
    assert fx.listed_set(response) == S("WS-1001", "WS-1002", "Ws-NoRtH-1003", "WS-NORTH-1004")


# ------------------------------------------------- AC09: clearing keeps filters; empty state
def test_ac09_clearing_search_keeps_filters(staff):
    url = "/orders/?status=pending&q=north"
    _, (form, _) = _form(staff, url)
    response, _ = fx.submit(staff, url, form, {"q": ""})
    pending = frozenset(o[0] for o in fx.ORDERS if o[3] == "pending")
    assert fx.listed_set(response) == pending


def test_ac09_no_results_shows_understandable_empty_state(staff, north):
    for client in (staff, north):
        response = client.get(q("no-such-order-zz"))
        assert response.status_code == 200
        assert fx.listed_set(response) == S()
        assert fx.EMPTY_STATE.search(fx.Page(response.get_data(as_text=True)).visible_text), \
            "no visible empty-state message outside scripts"


# ------------------------------------- AC10: no schema/dependency/endpoint changes; DataTables kept
def test_ac10_no_new_schema_or_endpoints(app):
    from app import db
    routes = sorted(f"{r.rule} {','.join(sorted(r.methods - {'HEAD', 'OPTIONS'}))}"
                    for r in app.url_map.iter_rules())
    assert routes == fx.BASELINE_SURFACE["routes"]
    schema = {t.name: sorted(c.name for c in t.columns) for t in db.metadata.sorted_tables}
    assert schema == fx.BASELINE_SURFACE["schema"]


def test_ac10_no_new_dependencies():
    import app as package
    root = Path(package.__file__).resolve().parent.parent
    for name, expected in fx.BASELINE_SURFACE["requirements_sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name


def test_ac10_client_side_table_is_kept(staff):
    html = staff.get(q("ws")).get_data(as_text=True)
    assert 'id="orders-table"' in html
    assert "datatables" in html.lower() and "DataTable(" in html


# ------------------------------------------------------------- Minimal common outcome
# Justified for every description: D1 does not name the parameter, so the URL is
# discovered from a GET search form when one exists, then common names are tried.
CANDIDATE_PARAMS = ("q", "search", "query", "term", "s", "keyword", "filter")


def _discover_search_url(client, value):
    page = fx.Page(client.get("/orders/").get_data(as_text=True))
    for form, control in fx.search_forms(page, None):
        if form.method == "get":
            response, url = fx.submit(client, "/orders/", form, {control.name: value})
            if response.status_code == 200 and fx.listed_set(response) < ALL:
                return url
    from urllib.parse import quote
    for name in CANDIDATE_PARAMS:
        url = f"/orders/?{name}={quote(value, safe='')}"
        response = client.get(url)
        if response.status_code == 200 and fx.listed_set(response) < ALL:
            return url
    return None


def test_min_url_search_filters_visible_orders(app, staff):
    url = _discover_search_url(staff, "WS-2001")
    assert url, "no URL-addressable search narrowed the order list"
    rows = visible(fx.client_for(app, STAFF), url)
    assert "WS-2001" in rows and rows < ALL


def test_min_search_never_broadens_customer_visibility(app, north, catering):
    queries = ["", "north", "1003", "WS-2001", "Southside", "catering", "%", "_", "*", "' OR '1'='1"]
    names = set(CANDIDATE_PARAMS)
    for client in (north, catering):
        page = fx.Page(client.get("/orders/").get_data(as_text=True))
        names.update(c.name for _, c in fx.search_forms(page, None))
    from urllib.parse import quote
    for client, user in ((north, NORTH), (catering, CATERING)):
        for name in sorted(names):
            for value in queries:
                rows = visible(client, f"/orders/?{name}={quote(value, safe='')}")
                assert rows <= fx.OWN[user], (user, name, value, sorted(rows - fx.OWN[user]))
