# Search orders with a shareable URL

Let people search the orders list using a shareable URL.

## Desired behavior

Staff and customers can search the Orders page (`/orders/`) and bookmark or share
the resulting URL. The server applies the search from the URL before rendering the
page, so opening a shared link shows the same filtered list.

## Product decisions

- The search term is the `q` query parameter of the orders list URL.
- A missing, empty, or whitespace-only `q` shows the same orders the page shows today.
- Leading and trailing whitespace in the term is ignored. Matching is a
  case-insensitive "contains" match.
- For staff, an order matches when its order number or its account (customer) name
  contains the term.
- For customers, search covers only their own orders and matches order numbers only.
  Search never shows a customer an order they could not already see.
- The term is literal text. Characters such as `%` and `_` match themselves; they
  are not wildcards.
- The staff status and type filters keep working and combine with search: an order
  must match the search and the active filters. Existing newest-first ordering and
  access behavior stay the same.
- The page has a labeled search form that submits with GET, shows the current
  search term (safely escaped), and keeps the active status/type filters when
  submitted. The status/type filter links keep the current search term.
- Clearing the search (for example, submitting an empty search) keeps the active
  status/type filters. A search with no matching orders shows a clear message that
  no orders match.

## Acceptance examples

The examples use the records in `tests/conftest.py` (staff user
`staff@example.test`; customer user `customer@example.test` for Northside Cafe;
order `WS-TEST-001` for Northside Cafe, pending; order `WS-TEST-002` for Southside
Cafe, confirmed) plus hypothetical records where noted.

1. Staff `/orders/?q=ws-test-001` lists only `WS-TEST-001`.
2. Staff `/orders/?q=%20%20WS-TEST-001%20` (surrounding spaces) lists only `WS-TEST-001`.
3. Staff `/orders/?q=southside` lists only `WS-TEST-002` (account-name match).
4. Staff `/orders/?q=cafe` lists both orders, because each account name contains "Cafe".
5. Staff `/orders/`, `/orders/?q=`, and `/orders/?q=%20%20` each list both orders.
6. Customer `/orders/?q=WS-TEST-002` and `/orders/?q=southside` list no orders;
   customer `/orders/?q=001` lists `WS-TEST-001`; customer `/orders/?q=northside`
   lists no orders, because customers search order numbers only.
7. Hypothetical orders `RT-50%-OFF` and `RT-50X-OFF`: staff `/orders/?q=50%25-` lists
   only `RT-50%-OFF`. A hypothetical order `RT-PROMO_1` is matched by `q=PROMO_1`, but
   `RT-PROMOX1` is not. A query of only `%` or only `_` lists only orders whose number
   or (for staff) account name contains that character.
8. Staff `/orders/?status=confirmed&q=cafe` lists only `WS-TEST-002`;
   `/orders/?status=pending&q=southside` lists no orders.
9. On `/orders/?status=pending`, submitting the search form with `north` requests a
   URL containing both `status=pending` and `q=north`. On `/orders/?q=north`, the
   Pending and Retail filter links include `q=north`.
10. On `/orders/?status=pending&q=north`, submitting an empty search shows all
    pending orders. `/orders/?q=no-such-order` shows a visible "no orders match"
    message instead of an empty table with no explanation.
11. Visiting `/orders/?q=%22%3E%3Cscript%3E` shows the text in the search box and does
    not inject markup into the page.

## Repository entry points

- `app/orders.py`: `_visible_orders_query()` applies the staff `status`/`type` filters
  and the customer scope; `index()` renders the list.
- `app/templates/orders/index.html`: staff filter links (`.filter-row`), the orders
  table, and the DataTables initialization with its browser-side search box.
- `app/models.py`: `Order.order_number`, `Order.order_date`, and the `Order.customer`
  relationship to `Customer.name`.
- `tests/conftest.py`: `app`, `client`, and `login` fixtures and the records above;
  `tests/test_orders.py` shows the existing order tests.
- `README.md`: add a short section with URL search examples.

## Constraints

- No database schema changes, new dependencies, or unrelated endpoints.
- Keep the existing client-side table sorting, paging, and quick filter. That
  browser-side box filters only the rows already on the page; label or describe it
  so people do not confuse it with the URL search.
- Keep authorization in the server-side query: customers stay scoped to their own
  account before any search term is applied.
- If you use SQL `LIKE`/`ILIKE`, escape `%`, `_`, and the escape character itself so
  user input cannot act as a wildcard.
- Render the search term through normal template escaping.

## Verification

- Existing tests keep passing: `python -m pytest -q`.
- Add focused tests (for example, `tests/test_order_search.py`) covering examples
  1–11 with the existing `client` and `login` fixtures. Check which orders are
  listed in the table rather than whether text appears anywhere in the page, since
  the search term itself is echoed into the page.
- Add README usage examples that explain the URL search.
