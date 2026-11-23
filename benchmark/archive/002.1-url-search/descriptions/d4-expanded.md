# Search orders with a shareable URL

Let people search the orders list using a shareable URL.

## Background and desired behavior

The Orders page lives at `/orders/`. Both staff members and customers use it, and
today there is no way to hand somebody a link that opens the list already narrowed
to the orders being discussed. We want people to be able to search this page and
then bookmark the result, paste it into a message, or otherwise share the URL with
a colleague. For that to work, the search has to be carried in the URL and applied
by the server: when someone opens a shared link, the server reads the search from
the URL and filters the orders before it renders the page, so the recipient sees
the same filtered list that the sender saw.

## Product decisions

These are the decisions the stakeholders have already made. Please follow them
rather than choosing alternatives.

- **Parameter name.** The search term is passed in the `q` query parameter of the
  orders list URL.
- **No search means no change.** When `q` is missing, when it is present but empty,
  or when it contains only whitespace, the page shows exactly the same orders that
  it shows today.
- **How matching works.** Any leading and trailing whitespace in the term is
  ignored. Matching is a "contains" match, and it does not care about upper or
  lower case.
- **What staff search.** For staff users, an order matches when its order number
  contains the term, or when the name of its account (the customer) contains the
  term. Either one is enough.
- **What customers search.** For customer users, the search covers only that
  customer's own orders, and it matches against order numbers only. Searching must
  never show a customer any order that they could not already see without searching.
- **Literal text.** The term is treated as literal text. Characters such as `%` and
  `_` match themselves; they do not behave as wildcards.
- **Existing filters.** The staff status and type filters continue to work, and they
  combine with the search: to be listed, an order has to match the search and also
  match the active filters. The existing ordering of the list (newest first) and the
  existing access behavior stay the same.
- **The search form.** The page has a search form with a label. The form submits
  with GET, so that the search ends up in the URL. It shows the current search term
  (escaped safely), and when it is submitted it keeps whatever status/type filters
  are active. In the other direction, the status/type filter links keep the current
  search term.
- **Clearing and empty results.** Clearing the search, for example by submitting an
  empty search, keeps the active status/type filters. When a search matches no
  orders, the page shows a clear message saying that no orders match.

## Acceptance examples

The following examples describe expected results concretely. They use the records
that already exist in `tests/conftest.py`: a staff user, `staff@example.test`; a
customer user, `customer@example.test`, who belongs to the Northside Cafe account;
order `WS-TEST-001`, which belongs to Northside Cafe and is pending; and order
`WS-TEST-002`, which belongs to Southside Cafe and is confirmed. Some examples also
use hypothetical records, and those are marked as hypothetical.

1. When a staff user opens `/orders/?q=ws-test-001`, the list shows only
   `WS-TEST-001`.
2. When a staff user opens `/orders/?q=%20%20WS-TEST-001%20`, where the term has
   spaces around it, the list again shows only `WS-TEST-001`.
3. When a staff user opens `/orders/?q=southside`, the list shows only `WS-TEST-002`.
   This one is matched through the account name.
4. When a staff user opens `/orders/?q=cafe`, the list shows both orders, because
   each of the two account names contains "Cafe".
5. When a staff user opens `/orders/`, `/orders/?q=`, or `/orders/?q=%20%20`, each of
   these pages lists both orders.
6. When the customer user opens `/orders/?q=WS-TEST-002` or `/orders/?q=southside`,
   no orders are listed. When the customer opens `/orders/?q=001`, the list shows
   `WS-TEST-001`. When the customer opens `/orders/?q=northside`, no orders are
   listed, because customers search order numbers only.
7. Suppose there are hypothetical orders `RT-50%-OFF` and `RT-50X-OFF`. A staff user
   who opens `/orders/?q=50%25-` sees only `RT-50%-OFF`. Likewise, a hypothetical
   order `RT-PROMO_1` is matched by `q=PROMO_1`, while a hypothetical `RT-PROMOX1` is
   not. A query consisting of only `%`, or only `_`, lists only the orders whose
   number, or (for staff) whose account name, contains that character.
8. When a staff user opens `/orders/?status=confirmed&q=cafe`, the list shows only
   `WS-TEST-002`. When a staff user opens `/orders/?status=pending&q=southside`, no
   orders are listed.
9. Starting from `/orders/?status=pending`, submitting the search form with `north`
   requests a URL that contains both `status=pending` and `q=north`. Starting from
   `/orders/?q=north`, the Pending filter link and the Retail filter link both
   include `q=north`.
10. Starting from `/orders/?status=pending&q=north`, submitting an empty search shows
    all of the pending orders. Opening `/orders/?q=no-such-order` shows a visible
    message that no orders match, rather than an empty table with no explanation.
11. Opening `/orders/?q=%22%3E%3Cscript%3E` shows that text inside the search box, and
    it does not inject any markup into the page.

## Repository entry points

These are the places in the repository that relate to this issue.

- `app/orders.py`: the function `_visible_orders_query()` is where the staff `status`
  and `type` filters are applied and where customers are scoped to their own orders;
  the `index()` view renders the list.
- `app/templates/orders/index.html`: this template contains the staff filter links
  (the `.filter-row` element), the orders table itself, and the DataTables
  initialization, which includes DataTables' own browser-side search box.
- `app/models.py`: the relevant fields are `Order.order_number` and
  `Order.order_date`, and the `Order.customer` relationship, which leads to
  `Customer.name`.
- `tests/conftest.py`: this file provides the `app`, `client`, and `login` fixtures
  and the records described above. `tests/test_orders.py` shows the existing tests
  for orders.
- `README.md`: add a short section to the README with examples of URL searches.

## Constraints

- Do not make database schema changes, do not add new dependencies, and do not add
  unrelated endpoints.
- Keep the existing client-side table features: sorting, paging, and the quick
  filter. That browser-side box filters only the rows that are already on the page,
  so label it or describe it in a way that keeps people from confusing it with the
  URL search.
- Keep authorization in the server-side query. Customers must stay scoped to their
  own account before any search term is applied.
- If you use SQL `LIKE` or `ILIKE`, escape `%`, `_`, and the escape character itself,
  so that user input cannot act as a wildcard.
- Render the search term through the normal template escaping.

## Verification

- The existing tests must keep passing. Run them with `python -m pytest -q`.
- Add focused tests, for example in a file named `tests/test_order_search.py`, that
  cover examples 1 through 11 using the existing `client` and `login` fixtures. When
  checking results, look at which orders are listed in the table rather than at
  whether some text appears anywhere in the page, because the search term itself is
  echoed back into the page.
- Add README usage examples that explain the URL search.
