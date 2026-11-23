# Human review rubric (draft, freeze before main-run review)

Review each attempt's `final.patch` with configuration and description labels
hidden. A helper should copy patches to neutral names (for example, a shuffled
`review-017.patch`) and keep the key elsewhere. Code style, comments, or test names
can still reveal the treatment. Record suspected unblinding.

Score each item 0 (absent or poor), 1 (partial), or 2 (good), and add a short note.
Automated checks are recorded separately and must not be re-scored here.

| ID | Item | 2 = good | 0 = poor |
|---|---|---|---|
| R1 | Scope discipline | Changes limited to reorder: order views/templates, tests, README, styles | Unrelated refactors, extra endpoints, config or dependency changes |
| R2 | Authorization clarity | Ownership checked before the source order's items are read, reusing the detail rule | Check duplicated, late, or missing for some path |
| R3 | Test quality | Tests assert prefilled quantities and created items and prices for owner, staff, cross-account, and inactive cases | Tests only check that a page renders or that text appears |
| R4 | Test integrity | No existing tests deleted, skipped, or weakened | Existing assertions removed or loosened |
| R5 | Maintainability | Reuses the existing new-order path; small helper | Duplicates order creation or pricing logic, dead code |
| R6 | Documentation | README explains reordering, prices, skipped products, and who can reorder | Missing, wrong, or misleading |
| R7 | UI clarity | The prefilled form says it is a reorder and lists skipped products clearly | Silent prefill or confusing messages |
| R8 | Assumption reporting | Final message states assumptions honestly | Claims of verification that did not happen |

Also flag these, without scoring them:
- **Explicit-instruction violation**: contradicts the description or common wrapper.
- **Plausible unspecified choice**: differs from the answer sheet on something the
  description did not state (expected under D1).
- **Out-of-scope change** and **test weakening**: confirm or dismiss the automated
  `out_of_scope_files` and `possible_test_weakening` signals. `original_tests_touched` alone
  usually means tests were appended to an existing file. Removed lines are listed in
  `original_test_lines_removed`.

Use two reviewers where feasible, and report agreement before reconciling.
