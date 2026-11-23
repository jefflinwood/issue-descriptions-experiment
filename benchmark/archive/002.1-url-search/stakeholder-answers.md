# Stakeholder answer sheet — URL order search

Status: **draft**, prepared 2026-11-20 for protocol `002-draft.1`. These are
proposed stakeholder choices, not facts implied by the vague request. They become
binding only when the protocol is frozen after review. Never agent-visible.

Request: “Let people search the orders list using a shareable URL.”

Baseline check (pinned `353c3ccd…`): `/orders/?q=…` is ignored by the server. Every
visible order is still rendered. The only search is DataTables' browser-side box,
which filters rows already on the page and is not reflected in the URL. The
requested behavior is therefore missing.

| # | Question a developer would ask | Answer | Criterion |
|---|---|---|---|
| 1 | What carries the search in the URL? | GET parameter `q` on `/orders/`, applied before server rendering. | AC01 |
| 2 | What happens with no / empty / blank `q`? | Same orders as today. | AC02 |
| 3 | How are terms matched? | Trim; case-insensitive substring of the order number. | AC03 |
| 4 | Which fields can staff search? | Order number **or** customer (account) name. | AC04 |
| 5 | What can customers search? | Only their own orders, by order number only. Never broaden access. | AC05 |
| 6 | Are `%` / `_` wildcards? | No. All input is literal text, including the escape character. | AC06 |
| 7 | How does search interact with status/type filters? | AND. Keep newest-first ordering and existing access rules. | AC07 |
| 8 | What UI submits the search? | Labeled GET form showing the current value (escaped), keeping active filters; filter links keep `q`. | AC08 |
| 9 | Clearing and no results? | Clearing keeps status/type filters; an understandable “no orders match” state. | AC09 |
| 10 | Allowed scope? | No schema, dependency, or unrelated endpoint changes; keep DataTables sorting/paging/quick filter, distinguished from URL search. | AC10 |
| 11 | Done means? | Existing tests pass, focused tests added, README examples of the query search - `?q=` or `q` mentioned. | AC11 |

Decisions deliberately **not** made (agents may choose; not scored):
- The exact form layout, label wording, button text, and empty-state wording
  (the empty state must say no orders match, results, or matches).
- Whether the “All” chip keeps `q`.
- Whether customers' status/type parameters are honored (the baseline ignores them;
  the evaluation does not test that combination).
- Whether matching is implemented in SQL or Python, and helper names.
