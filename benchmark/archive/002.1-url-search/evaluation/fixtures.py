"""Deterministic evaluation data and HTTP/form helpers for Experiment 002.

Never mounted in an agent environment. The data is independent of the user's
``ridgeline.db``, of Ridgeline's own ``tests/conftest.py``, and of the current
date. Helpers observe HTTP responses and submit forms the way a browser would;
they never inspect SQLAlchemy expressions or private helper names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

PASSWORD = "eval-password"
STAFF = "staff@eval.test"
NORTH = "north@eval.test"          # customer user for Northside Cafe
CATERING = "catering@eval.test"    # customer user for Northside Catering

# key, display name, account type. Names overlap ("Northside ..."), mix case,
# and contain literal SQL wildcard characters.
CUSTOMERS = [
    ("northside", "Northside Cafe", "wholesale"),
    ("catering", "Northside Catering", "wholesale"),
    ("southside", "Southside Cafe", "wholesale"),
    ("underscore", "Bean_Counter Coffee", "retail"),
    ("xcounter", "BeanXCounter Coffee", "retail"),
    ("percent", "Ridge 100% Club", "retail"),
]

# order number, customer key, type, status, order day in March 2026.
ORDERS = [
    ("WS-1001", "northside", "wholesale", "pending", 1),
    ("WS-1002", "northside", "wholesale", "shipped", 2),
    ("Ws-NoRtH-1003", "catering", "wholesale", "pending", 3),
    ("WS-2001", "southside", "wholesale", "shipped", 4),
    ("RT-PROMO_1", "underscore", "retail", "pending", 5),
    ("RT-PROMOX1", "xcounter", "retail", "shipped", 6),
    ("RT-100%-A", "percent", "retail", "pending", 7),
    ("RT-100X-B", "percent", "retail", "delivered", 8),
    ("RT-3001", "underscore", "retail", "confirmed", 9),
    ("WS-NORTH-1004", "northside", "wholesale", "pending", 10),
    ("WS-2002", "southside", "wholesale", "pending", 11),
]

ALL_NUMBERS = frozenset(o[0] for o in ORDERS)
NEWEST_FIRST = [o[0] for o in sorted(ORDERS, key=lambda o: o[4], reverse=True)]
OWN = {
    NORTH: frozenset(o[0] for o in ORDERS if o[1] == "northside"),
    CATERING: frozenset(o[0] for o in ORDERS if o[1] == "catering"),
}
BASELINE_SURFACE = json.loads((Path(__file__).with_name("baseline_surface.json")).read_text())


class EvalConfig:
    TESTING = True
    SECRET_KEY = "evaluation-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AUTO_SEED = False
    MCP_CLASS_PASSWORD = "evaluation-class-password"


def build_app():
    """Create a candidate app with only the deterministic evaluation records."""
    from app import create_app, db
    from app.models import Customer, Order, OrderDueDate, OrderItem, Product, Shipment, User

    app = create_app(EvalConfig)
    with app.app_context():
        db.create_all()
        product = Product(sku="EVAL-1", name="Evaluation Blend", origin="Peru", roast_level="medium",
                          wholesale_price_per_case=Decimal("100.00"),
                          retail_price_per_bag=Decimal("12.00"), cases_in_stock=50, bags_in_stock=50)
        customers = {key: Customer(name=name, type=kind, email=f"{key}@accounts.eval.test",
                                   city="Pittsburgh", state="PA")
                     for key, name, kind in CUSTOMERS}
        db.session.add_all([product, *customers.values()])
        db.session.flush()
        users = [User(name="Eval Staff", email=STAFF, role="staff"),
                 User(name="North Buyer", email=NORTH, role="customer", customer=customers["northside"]),
                 User(name="Catering Buyer", email=CATERING, role="customer", customer=customers["catering"])]
        for user in users:
            user.set_password(PASSWORD)
        db.session.add_all(users)
        for number, key, kind, status, day in ORDERS:
            price = product.wholesale_price_per_case if kind == "wholesale" else product.retail_price_per_bag
            db.session.add(Order(
                order_number=number, customer=customers[key], order_type=kind, status=status,
                order_date=datetime(2026, 3, day, 9, 30),
                delivery_deadline=OrderDueDate(due_date=date(2026, 4, day)),
                items=[OrderItem(product=product, quantity=1, unit_price=price)],
                shipments=[Shipment(method="ups", status="planned", load_number=1)]))
        db.session.commit()
    return app


def client_for(app, email):
    client = app.test_client()
    response = client.post("/login", data={"email": email, "password": PASSWORD})
    assert response.status_code == 302, f"login failed for {email}: {response.status_code}"
    return client


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
    options: list = field(default_factory=list)  # (value, selected) for <select>
    in_label: bool = False


@dataclass
class Form:
    method: str
    action: str | None
    fields: list
    attrs: dict

    def field(self, name):
        return next((f for f in self.fields if f.name == name), None)


class Page(HTMLParser):
    """Small, dependency-free browser model: links, forms, labels, visible text."""

    VOID = {"input", "br", "img", "meta", "link", "hr", "source", "wbr", "area", "col", "base"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.links, self.forms, self.label_for, self.ids = [], [], set(), {}
        self.text_parts = []
        self._form = None
        self._anchor = None
        self._label_depth = 0
        self._skip = 0
        self._select = None
        self._option = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if a.get("id"):
            self.ids[a["id"]] = (tag, a)
        if tag in ("script", "style", "template"):
            self._skip += 1
        elif tag == "a" and "href" in a:
            self._anchor = [a["href"], []]
        elif tag == "label":
            self._label_depth += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        elif tag == "form":
            self._form = Form((a.get("method") or "get").lower(), a.get("action"), [], a)
            self.forms.append(self._form)
        elif tag in ("input", "button", "textarea") and self._form is not None:
            kind = (a.get("type") or ("submit" if tag == "button" else "text")).lower()
            if tag == "textarea":
                kind = "textarea"
            self._form.fields.append(Field(tag, a.get("name"), kind, a.get("value", ""),
                                           "checked" in a, a.get("id"), a,
                                           in_label=self._label_depth > 0))
        elif tag == "select" and self._form is not None:
            self._select = Field("select", a.get("name"), "select", "", id=a.get("id"), attrs=a,
                                 in_label=self._label_depth > 0)
            self._form.fields.append(self._select)
        elif tag == "option" and self._select is not None:
            self._option = [a.get("value"), "selected" in a, []]

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._anchor is not None:
            self.links.append((self._anchor[0], "".join(self._anchor[1]).strip()))
            self._anchor = None
        elif tag == "label":
            self._label_depth = max(0, self._label_depth - 1)
        elif tag == "form":
            self._form = None
        elif tag == "option" and self._option is not None and self._select is not None:
            value = self._option[0] if self._option[0] is not None else "".join(self._option[2]).strip()
            self._select.options.append((value, self._option[1]))
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

    @property
    def visible_text(self):
        return re.sub(r"\s+", " ", " ".join(self.text_parts)).strip()


ORDER_LINK = re.compile(r"^(?:https?://[^/]+)?/orders/(\d+)/?$")


def listed_orders(response):
    """Order numbers linked as order-detail rows, in document order."""
    page = Page(response.get_data(as_text=True))
    return [text for href, text in page.links if ORDER_LINK.match(href.split("?")[0].split("#")[0])]


def listed_set(response):
    return frozenset(listed_orders(response))


def is_labeled(page, f):
    if f.in_label or f.attrs.get("aria-label", "").strip() or f.attrs.get("title", "").strip():
        return True
    if f.id and f.id in page.label_for:
        return True
    labelled_by = f.attrs.get("aria-labelledby", "").split()
    return any(i in page.ids for i in labelled_by)


TEXT_TYPES = {"text", "search", "textarea"}


def search_forms(page, name="q"):
    """Forms that contain a text-like control with the given name (any name if None)."""
    found = []
    for form in page.forms:
        for f in form.fields:
            if f.type in TEXT_TYPES and (name is None or f.name == name) and f.name:
                found.append((form, f))
                break
    return found


def form_pairs(form, overrides):
    """Name/value pairs a browser submits for this form (no submitter button)."""
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
            chosen = [v for v, s in f.options if s] or [v for v, _ in f.options[:1]]
            pairs.extend((f.name, v) for v in chosen[:1])
            continue
        pairs.append((f.name, f.value))
    for name, value in overrides.items():
        if any(n == name for n, _ in pairs):
            pairs = [(n, value if n == name else v) for n, v in pairs]
        else:
            pairs.append((name, value))
    return pairs


def submit(client, page_url, form, overrides):
    """Submit ``form`` like a browser: GET replaces the action URL's query string."""
    target = urljoin("http://localhost" + page_url, form.action if form.action else page_url)
    parts = urlsplit(target)
    pairs = form_pairs(form, overrides)
    if form.method != "get":
        return client.post(parts.path, data=dict(pairs)), None
    url = parts.path + ("?" + urlencode(pairs) if pairs else "")
    return client.get(url), url


def follow(client, href, page_url):
    parts = urlsplit(urljoin("http://localhost" + page_url, href))
    return client.get(parts.path + ("?" + parts.query if parts.query else "")), parts


def link_with(page, **params):
    """First orders-list link whose query contains every given parameter value."""
    for href, _ in page.links:
        parts = urlsplit(href)
        if parts.path.rstrip("/").endswith("/orders") or parts.path in ("", "/orders/"):
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            if parts.path and all(query.get(k) == v for k, v in params.items()):
                return href
    return None


EMPTY_STATE = re.compile(
    r"\b(no|0|zero)\b[^.!?]{0,60}?\b(orders?|results?|match\w*|found)\b|nothing (matched|found)",
    re.IGNORECASE)
