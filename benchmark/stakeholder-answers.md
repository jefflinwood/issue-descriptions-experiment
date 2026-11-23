# Stakeholder answer sheet — reorder a past order

Status: **approved** by the study owner on 2026-11-21 for the reorder workload.
Never agent-visible. Decisions first marked *draft* (proposed by Claude to complete
the contract) were reviewed and accepted unchanged by the owner on 2026-11-21. The
URL-search answer sheet used in protocol 002.1 is archived in
`archive/002.1-url-search/`.

Request: “Let customers reorder a past order.”

Baseline check (pinned `353c3ccd…`): there is no reorder control, route, or query
parameter. `/orders/new` always starts with every quantity at 0. The POST handler
lists only active products in the form, prices every line at the product's current
price, and requires a delivery due date. Nothing prevents posting an inactive
product's ID. The requested behavior is therefore missing, and Ridgeline has no
existing client-side feature that overlaps it.

| # | Question a developer would ask | Answer | Decided by | Criterion |
|---|---|---|---|---|
| 1 | Where does reordering start, and for whom? | A Reorder control on each order page, for anyone who may view it: the owning customer and staff. | Claude draft, approved | AC01 |
| 2 | One click, or prefill? | Prefill the existing new-order form; never place an order automatically. Repeated lines of one product are summed. Staff get the original account preselected. | owner (prefill); Claude draft, approved (summing, preselect) | AC02 |
| 3 | Whose orders can be reordered? | Customers: their own only; another account's order is refused and reveals nothing. Staff: any order. | owner | AC03 |
| 4 | What about products no longer sold? | Leave them out and name them as no longer available. If none remain, prefill nothing and say so. | owner (skip and say so); Claude draft, approved (all-inactive case) | AC04 |
| 5 | Which prices? | Today's prices, through the normal order creation. New order number, original account, original order unchanged. | owner | AC05 |
| 6 | Are dates copied? | No. Due date and requested ship date are chosen again; the due date stays required. | owner (due date required); Claude draft, approved (ship date) | AC06 |
| 7 | Is shipping copied? | The shipping method is preselected from the original order. | Claude draft, approved | AC07 |
| 8 | Does the normal new-order form change? | No. Without reordering it behaves exactly as today. | Claude draft, approved | AC08 |
| 9 | Allowed scope? | No schema or dependency changes; existing routes kept; at most one added route under `/orders/`. | Claude draft, approved | AC09 |
| 10 | Done means? | Existing tests pass, focused tests added, README section on reordering. | Claude draft, approved (mirrors 002.1 AC11) | AC10 |

Decisions deliberately **not** made (agents may choose; not scored):
- The control's wording and style, if it reads as reordering (“Reorder”, “Order again”,
  “Buy again”, “Repeat order”).
- Whether reordering uses a query parameter on `/orders/new` or one added route.
- Whether notes are copied.
- Truck-delivery details (warehouse and trucking service) on reordered truck orders.
- Whether cancelled orders can be reordered (no evaluated order is cancelled; the
  orders reordered are delivered, pending, or shipped).
- The HTTP status of a refused cross-account attempt (403, 404, or a redirect with a
  message), as long as nothing about the other order is shown or created.
