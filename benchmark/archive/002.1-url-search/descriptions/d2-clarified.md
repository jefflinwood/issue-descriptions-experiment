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

## Scope

- No database schema changes, new dependencies, or unrelated endpoints.
- Keep the existing client-side table sorting, paging, and quick filter. That
  browser-side box filters only the rows already on the page; label or describe it
  so people do not confuse it with the URL search.
- Existing tests keep passing. Add focused tests for the new behavior and README
  usage examples that explain the URL search.
