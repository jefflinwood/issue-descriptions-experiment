# Reorder a past order

Let customers reorder a past order.

---

**Issue #142 · opened by Priya (operations)**

Wholesale customers keep emailing us to “send the same as last time,” and retail
customers ask how to repeat an order. Right now they have to rebuild the whole order on
the new-order form. Can we give them a way to reorder from an order they already
placed? Northside called about this twice last week.

---

**Comment · Sam (developer)**

Easiest version: a button on the order page that clones the order row and its items and
places it immediately. Ten lines of code.

---

**Comment · Dana (product)**

Two problems with cloning as-is. Prices change, so copying the old line prices would
charge the old price. And some blends get retired, so we'd be reordering coffee we no
longer sell. Let's keep it simple though: one click, places the order, today's prices,
skip retired products, and carry the requested ship date over from the old order.

---

**Comment · Priya (operations)**

One click worries me. People will tap it by accident and we'll roast and ship an order
nobody wanted. The delivery dates on old orders are also long past.

---

**Comment · Dana (product)**

Agreed, that changes my answer. **Decision (supersedes my comment above): Reorder never
places an order by itself. It prefills the existing new-order form, and the person
checks it and submits.** I'll write up the full set of decisions below so nobody has to
read this thread.

---

**Comment · Sam (developer)**

While we're in there, should reorder also go into the JSON API and the MCP tools? The
class demos use those.

---

**Comment · Dana (product)**

Not in this issue. Web pages only; the API and MCP stay as they are.

---

**Comment · Priya (operations)**

Unrelated, but the Render deploy took eleven minutes yesterday. Is that normal?

---

**Comment · Sam (developer)**

Probably the Postgres provisioning step. I'll look separately; not related to this issue.

---

**Comment · Dana (product)**

Earlier I said to copy the requested ship date along with everything else. **Reversing
that: neither the delivery due date nor the requested ship date is copied.** Old dates
are always wrong for a new order. Final decisions are below.

---

**Comment · Dana (product) — final decisions (this comment is the spec)**

Desired behavior: from an order's page, a person can start a new order that copies what
they ordered before, check it, and place it. Reordering prefills the existing new-order
form; it never places an order by itself.

Product decisions:

- Each order page shows a **Reorder** control to anyone who can view that order: the
  customer who owns it, and staff.
- Using it opens the new-order form prefilled with the original order's quantities. If
  the original order lists the same product on more than one line, the quantities are
  added together. For staff, the original account is preselected.
- Customers can reorder only their own orders. Trying to reorder another account's order
  is refused and shows none of that order's details. Staff can reorder any order.
- Products that are no longer active are left out, and the page names them as no longer
  available. If none of the products are still available, nothing is prefilled and the
  page says so.
- Placing the reordered order works like any new order. It gets a new order number, uses
  today's prices (not the prices on the original order), and belongs to the original
  account. The original order is not changed.
- The delivery due date and requested ship date are not copied; the person chooses them
  again. The due date stays required.
- The shipping method is preselected from the original order.
- Opening the new-order form normally, without reordering, behaves exactly as it does
  today.

---

**Comment · Sam (developer) — examples I'll test against**

These use the records in `tests/conftest.py`: staff user `staff@example.test`; customer
user `customer@example.test` for Northside Cafe; order `WS-TEST-001` for Northside Cafe
(2 cases of Test Blend at $100.00, UPS); and order `WS-TEST-002` for Southside Cafe (3
cases of Test Blend at $90.00, FedEx). Hypothetical records are marked as hypothetical.

1. The customer sees a Reorder control on the `WS-TEST-001` page. Staff see one on the
   `WS-TEST-002` page.
2. The customer's Reorder of `WS-TEST-001` shows the new-order form with Test Blend set
   to 2 and every other product at 0. No order exists until the form is submitted.
3. A hypothetical order with Test Blend on two lines (1 and 2 cases) prefills Test Blend
   as 3.
4. The customer trying to reorder `WS-TEST-002` is refused (for example, 403). The
   response shows none of that order's products or quantities, and no order is created.
5. Staff reordering `WS-TEST-002` get Southside Cafe preselected and Test Blend set to 3.
6. If `WS-TEST-001` also had a hypothetical inactive product, Retired Blend, the
   prefilled form leaves Retired Blend out, and the page says Retired Blend is no longer
   available.
7. Reordering a hypothetical order whose products are all inactive prefills nothing,
   says that none of its products are available, and creates no order.
8. If Test Blend's wholesale price is changed from $100.00 to $120.00, submitting the
   reorder of `WS-TEST-001` creates a new order of 2 cases at $120.00 with a new order
   number. `WS-TEST-001` still shows 2 cases at $100.00.
9. The prefilled form leaves the delivery due date and requested ship date empty.
   Submitting it without a due date shows the existing "Enter a valid delivery due
   date." error and creates no order.
10. Reordering `WS-TEST-002` preselects FedEx as the shipping method.
11. Opening `/orders/new` without reordering shows every quantity at 0, as it does today.

---

**Comment · Priya (operations)**

Example 6 is exactly what happened when we retired a blend last spring. 👍

---

**Comment · Sam (developer) — where the code is, and ground rules**

Repository entry points:

- `app/orders.py`: `new()` renders the new-order form on GET and creates the order on
  POST. It lists only active products and prices each line at the product's current
  price. `detail()` shows the rule for who may view an order.
- `app/templates/orders/new.html`: paired hidden `product_id` and `quantity` inputs per
  product, the `customer_id` select (staff) or hidden input (customers), the
  `shipping_method` select, and the `due_date` and `requested_ship_date` inputs.
- `app/templates/orders/detail.html`: the order page where the Reorder control goes.
- `app/models.py`: `Order.items`, `OrderItem.quantity`, `OrderItem.unit_price`,
  `Product.active`, and `Order.shipments` (the first shipment's `method`).
- `tests/conftest.py`: `app`, `client`, and `login` fixtures and the records above;
  `tests/test_orders.py` shows the existing order tests.
- `README.md`: add a short section explaining reordering.

Constraints:

- No database schema changes and no new dependencies. Keep existing routes; adding at
  most one route under `/orders/` for reordering is fine.
- Check that the person may view the source order (the same rule as `detail()`) before
  reading its items.
- Keep pricing in the existing order-creation path. Do not send prices through the form
  or trust prices from the browser.
- Keep the paired `product_id` and `quantity` fields so the existing POST handling still
  applies. Prefill only when showing the form; never submit it automatically.

Verification:

- Existing tests keep passing: `python -m pytest -q`.
- Add focused tests (for example, `tests/test_reorder.py`) covering examples 1–11 with
  the existing `client` and `login` fixtures. Check the prefilled `quantity` values and
  the created order's items and prices in the database, not only page text.
- Add a README section that explains reordering.

---

**Comment · Sam (developer)**

I won't get to this before Friday. Unassigning myself so whoever picks it up has a clean
start; everything needed is in Dana's decisions, my examples, and the notes above.
