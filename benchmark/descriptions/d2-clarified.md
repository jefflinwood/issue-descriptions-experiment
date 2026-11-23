# Reorder a past order

Let customers reorder a past order.

## Desired behavior

From an order's page, a person can start a new order that copies what they ordered
before, check it, and place it. Reordering prefills the existing new-order form; it
never places an order by itself.

## Product decisions

- Each order page shows a **Reorder** control to anyone who can view that order:
  the customer who owns it, and staff.
- Using it opens the new-order form prefilled with the original order's quantities.
  If the original order lists the same product on more than one line, the quantities
  are added together. For staff, the original account is preselected.
- Customers can reorder only their own orders. Trying to reorder another account's
  order is refused and shows none of that order's details. Staff can reorder any
  order.
- Products that are no longer active are left out, and the page names them as no
  longer available. If none of the products are still available, nothing is
  prefilled and the page says so.
- Placing the reordered order works like any new order. It gets a new order number,
  uses today's prices (not the prices on the original order), and belongs to the
  original account. The original order is not changed.
- The delivery due date and requested ship date are not copied; the person chooses
  them again. The due date stays required.
- The shipping method is preselected from the original order.
- Opening the new-order form normally, without reordering, behaves exactly as it
  does today.

## Scope

- No database schema changes and no new dependencies. Keep existing routes; adding
  at most one route under `/orders/` for reordering is fine.
- Existing tests keep passing. Add focused tests for the new behavior and a README
  section that explains reordering.
