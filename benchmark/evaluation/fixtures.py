"""Deterministic evaluation data and browser-like helpers for the reorder workload.

Never mounted in an agent environment. Independent of the user's ``ridgeline.db``,
of Ridgeline's ``tests/conftest.py`` and of the current date. Helpers observe HTTP
responses and use links and forms the way a browser would; they never inspect
SQLAlchemy expressions, template names or private helpers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urljoin, urlsplit

PASSWORD = "eval-password"
STAFF = "staff@eval.test"
NORTH = "north@eval.test"     # wholesale customer user (Hilltop Roastery)
SOUTH = "south@eval.test"     # wholesale customer user (Lakeside Bistro)
ADA = "ada@eval.test"         # retail customer user (Ada Byron)

# key, name, origin, wholesale price now, retail price now, active
PRODUCTS = [
    ("summit", "Summit Espresso", "Brazil", Decimal("120.00"), Decimal("16.00"), True),
    ("valley", "Valley Decaf", "Mexico", Decimal("95.00"), Decimal("13.00"), True),
    ("harvest", "Harvest Reserve", "Kenya", Decimal("150.00"), Decimal("20.00"), False),
]
# Names deliberately absent from Ridgeline's repository and from every description.
CUSTOMERS = [("northside", "Hilltop Roastery", "wholesale"), ("southside", "Lakeside Bistro", "wholesale"),
             ("ada", "Ada Byron", "retail")]

# number, customer, status, shipping method, lines [(product, qty, historical unit price)]
ORDERS = {
    "N1": ("WS-N-001", "northside", "delivered", "fedex",
           [("summit", 2, Decimal("100.00")), ("harvest", 1, Decimal("150.00"))]),
    "N2": ("WS-N-002", "northside", "delivered", "ups", [("harvest", 3, Decimal("150.00"))]),
    "N3": ("WS-N-003", "northside", "delivered", "usps",
           [("summit", 1, Decimal("100.00")), ("summit", 2, Decimal("100.00")), ("valley", 4, Decimal("90.00"))]),
    "S1": ("WS-S-001", "southside", "pending", "fedex", [("valley", 5, Decimal("90.00"))]),
    "R1": ("RT-A-001", "ada", "shipped", "ups", [("summit", 4, Decimal("14.00"))]),
}
FUTURE_DUE = "2031-06-15"
BASELINE_SURFACE = json.loads(Path(__file__).with_name("baseline_surface.json").read_text())


class EvalConfig:
    TESTING = True
    SECRET_KEY = "evaluation-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AUTO_SEED = False
    MCP_CLASS_PASSWORD = "evaluation-class-password"


@dataclass
class World:
    app: object
    ids: dict        # order key -> id
    products: dict   # product key -> id
    customers: dict  # customer key -> id


def build_world():
    from app import create_app, db
    from app.models import Customer, Order, OrderDueDate, OrderItem, Product, Shipment, User

    app = create_app(EvalConfig)
    with app.app_context():
        db.create_all()
        products = {key: Product(sku=f"EVAL-{key.upper()}", name=name, origin=origin, roast_level="medium",
                                 wholesale_price_per_case=wholesale, retail_price_per_bag=retail,
                                 cases_in_stock=50, bags_in_stock=50, active=active)
                    for key, name, origin, wholesale, retail, active in PRODUCTS}
        customers = {key: Customer(name=name, type=kind, email=f"{key}@accounts.eval.test", address="1 Main St",
                                   city="Pittsburgh", state="PA") for key, name, kind in CUSTOMERS}
        db.session.add_all([*products.values(), *customers.values()])
        db.session.flush()
        users = [User(name="Eval Staff", email=STAFF, role="staff"),
                 User(name="North Buyer", email=NORTH, role="customer", customer=customers["northside"]),
                 User(name="South Buyer", email=SOUTH, role="customer", customer=customers["southside"]),
                 User(name="Ada Byron", email=ADA, role="customer", customer=customers["ada"])]
        for user in users:
            user.set_password(PASSWORD)
        db.session.add_all(users)
        orders = {}
        for day, (key, (number, customer, status, method, lines)) in enumerate(ORDERS.items(), start=1):
            orders[key] = Order(
                order_number=number, customer=customers[customer], order_type=customers[customer].type,
                status=status, order_date=datetime(2026, 1, day, 9, 0), requested_ship_date=date(2026, 1, 20),
                notes=f"Original note for {number}",
                delivery_deadline=OrderDueDate(due_date=date(2026, 2, day)),
                items=[OrderItem(product=products[p], quantity=q, unit_price=price) for p, q, price in lines],
                shipments=[Shipment(method=method, status="planned", load_number=1)])
        db.session.add_all(orders.values())
        db.session.commit()
        return World(app, {k: o.id for k, o in orders.items()}, {k: p.id for k, p in products.items()},
                     {k: c.id for k, c in customers.items()})


def client_for(world, email):
    client = world.app.test_client()
    response = client.post("/login", data={"email": email, "password": PASSWORD})
    assert response.status_code == 302, f"login failed for {email}: {response.status_code}"
    return client


def snapshot(world):
    """Every order with its items, for detecting creation and modification."""
    from app.models import Order
    with world.app.app_context():
        return {o.id: dict(number=o.order_number, customer_id=o.customer_id, status=o.status, notes=o.notes,
                           due=o.due_date, requested=o.requested_ship_date,
                           items=sorted((i.product_id, i.quantity, Decimal(str(i.unit_price))) for i in o.items))
                for o in Order.query.all()}


def summed(items):
    totals = {}
    for product_id, quantity, _ in items:
        totals[product_id] = totals.get(product_id, 0) + quantity
    return totals


def expected_quantities(world, key, active_only=True):
    active = {p[0] for p in PRODUCTS if p[5]}
    totals = {}
    for product, quantity, _ in ORDERS[key][4]:
        if product in active or not active_only:
            totals[world.products[product]] = totals.get(world.products[product], 0) + quantity
    return totals


# --------------------------------------------------------------------- HTML model

@dataclass
class Field:
    tag: str
    name: str | None
    type: str
    value: str
    checked: bool = False
    id: str | None = None
    attrs: dict = field(default_factory=dict)
    options: list = field(default_factory=list)  # (value, selected, label) for <select>


@dataclass
class Form:
    method: str
    action: str | None
    fields: list
    attrs: dict
    text: list = field(default_factory=list)

    def values(self, name):
        return [f for f in self.fields if f.name == name]


class Page(HTMLParser):
    """Dependency-free browser model: links, forms (with their visible text), visible text."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.links, self.forms, self.text_parts = [], [], []
        self._form = self._anchor = self._select = self._option = None
        self._skip = 0
        self._button = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag in ("script", "style", "template"):
            self._skip += 1
        elif tag == "a" and "href" in a:
            self._anchor = [a["href"], [a.get("aria-label", ""), a.get("title", "")]]
        elif tag == "form":
            self._form = Form((a.get("method") or "get").lower(), a.get("action"), [], a)
            self.forms.append(self._form)
        elif tag in ("input", "button", "textarea") and self._form is not None:
            kind = (a.get("type") or ("submit" if tag == "button" else "text")).lower()
            if tag == "textarea":
                kind = "textarea"
            f = Field(tag, a.get("name"), kind, a.get("value", ""), "checked" in a, a.get("id"), a)
            self._form.fields.append(f)
            if tag == "button":
                self._button = f
            if kind in ("submit", "button") and tag == "input":
                self._form.text.append(a.get("value", ""))
            for key in ("aria-label", "title"):
                if a.get(key):
                    self._form.text.append(a[key])
        elif tag == "select" and self._form is not None:
            self._select = Field("select", a.get("name"), "select", "", id=a.get("id"), attrs=a)
            self._form.fields.append(self._select)
        elif tag == "option" and self._select is not None:
            self._option = [a.get("value"), "selected" in a, []]

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._anchor is not None:
            self.links.append((self._anchor[0], " ".join(t for t in self._anchor[1] if t).strip()))
            self._anchor = None
        elif tag == "form":
            self._form = None
        elif tag == "button":
            self._button = None
        elif tag == "option" and self._option is not None and self._select is not None:
            label = "".join(self._option[2]).strip()
            value = self._option[0] if self._option[0] is not None else label
            self._select.options.append((value, self._option[1], label))
            self._option = None
        elif tag == "select":
            self._select = None

    def handle_data(self, data):
        if self._skip:
            return
        self.text_parts.append(data)
        if self._anchor is not None:
            self._anchor[1].append(data)
        if self._option is not None:
            self._option[2].append(data)
        if self._form is not None and self._button is not None:
            self._form.text.append(data)

    @property
    def visible_text(self):
        return re.sub(r"\s+", " ", " ".join(self.text_parts)).strip()


REORDER_WORDS = re.compile(r"\b(re-?order|order (this )?again|buy again|repeat (this )?order|duplicate order|copy order)\b",
                           re.IGNORECASE)
UNAVAILABLE_WORDS = re.compile(r"no longer|unavailable|not available|discontinued|inactive|skipped|left out|removed|"
                               r"not included|omitted|can(no|')t be (re)?ordered", re.IGNORECASE)


@dataclass
class Control:
    kind: str           # "link" or "form"
    method: str
    url: str
    data: list          # name/value pairs for a form control
    label: str


def reorder_controls(html, page_url):
    page = Page(html)
    found = []
    for href, text in page.links:
        if REORDER_WORDS.search(text or ""):
            found.append(Control("link", "get", _absolute(href, page_url), [], text))
    for form in page.forms:
        label = " ".join(form.text)
        if REORDER_WORDS.search(label):
            found.append(Control("form", form.method, _absolute(form.action or page_url, page_url),
                                 form_pairs(form, {}), label.strip()))
    return found


def _absolute(href, page_url):
    parts = urlsplit(urljoin("http://localhost" + page_url, href))
    return parts.path + ("?" + parts.query if parts.query else "")


def activate(client, control):
    if control.method == "post":
        return client.post(control.url, data=_multi(control.data), follow_redirects=True)
    if control.kind == "form" and control.data:
        base = control.url.split("?")[0]
        return client.get(base + "?" + urlencode(control.data), follow_redirects=True)
    return client.get(control.url, follow_redirects=True)


def retarget(control, old_id, new_id, old_number, new_number):
    """The same control pointed at another order (by id and by order number)."""
    def swap(text):
        text = re.sub(rf"(?<![0-9]){old_id}(?![0-9])", str(new_id), text)
        return text.replace(old_number, new_number)
    return Control(control.kind, control.method, swap(control.url),
                   [(n, swap(v)) for n, v in control.data], control.label)


def _multi(pairs):
    from werkzeug.datastructures import MultiDict
    return MultiDict(pairs)


def form_pairs(form, overrides):
    pairs = []
    for f in form.fields:
        if not f.name or "disabled" in f.attrs:
            continue
        if f.type in ("submit", "button", "reset", "image", "file"):
            continue
        if f.type in ("checkbox", "radio"):
            if f.checked:
                pairs.append((f.name, f.value or "on"))
            continue
        if f.tag == "select":
            chosen = [v for v, s, _ in f.options if s] or [v for v, _, _ in f.options[:1]]
            pairs.extend((f.name, v) for v in chosen[:1])
            continue
        pairs.append((f.name, f.value))
    for name, value in overrides.items():
        if any(n == name for n, _ in pairs):
            pairs = [(n, value if n == name else v) for n, v in pairs]
        else:
            pairs.append((name, value))
    return pairs


def order_form(html):
    """The new-order style form: one carrying product_id and quantity fields."""
    for form in Page(html).forms:
        if form.values("product_id") and form.values("quantity"):
            return form
    return None


def prefilled_quantities(form):
    """Product id → quantity exactly as the server would read the submitted form."""
    pairs = form_pairs(form, {})
    ids = [v for n, v in pairs if n == "product_id"]
    quantities = [v for n, v in pairs if n == "quantity"]
    result = {}
    for product_id, quantity in zip(ids, quantities):
        try:
            q = int(quantity or 0)
        except ValueError:
            continue
        if q > 0:
            result[int(product_id)] = result.get(int(product_id), 0) + q
    return result


def selected(form, name):
    field = next(iter(form.values(name)), None)
    if field is None:
        return None
    if field.tag == "select":
        chosen = [v for v, s, _ in field.options if s]
        return chosen[0] if chosen else (field.options[0][0] if field.options else None)
    return field.value


def submit(client, form, page_url, overrides):
    target = _absolute(form.action or page_url, page_url)
    pairs = form_pairs(form, overrides)
    if form.method == "get":
        return client.get(target.split("?")[0] + "?" + urlencode(pairs), follow_redirects=False)
    return client.post(target, data=_multi(pairs), follow_redirects=False)


def landed_url(response):
    """Path and query of the page a (redirect-following) response ended on."""
    request = response.request
    query = request.query_string.decode() if request.query_string else ""
    return request.path + ("?" + query if query else "")
