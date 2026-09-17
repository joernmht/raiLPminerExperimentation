# ADR-0020: Every mathematical element has a role; definitions are verbatim, citable spans

**Status:** accepted (2026-09-17)

## Context

ADR-0019 made every `<mml:math>` element visible. Reviewing the pages showed
that inline mathematics in the prose mostly does not yield formulas: it yields
*definitions* ("Let x_ij be a binary variable ...", "E is the set of ...").
Those are what the vocabulary needs, and what a Details pane should cite. A
paraphrase is not citable; a verbatim passage with its position is.

## Decision

1. **A role for every element**, proposed deterministically
   (`corpusbuilder.roles`): `formula`, `definition`, `index` (membership
   statement), `domain` (domain statement), `mention`, `other`. Display
   equations are formulas; inline relations that carry an inequality or a
   matrix are formulas; symbol mentions whose sentence defines them are
   definitions; notation-table rows and definition-list items are
   definitions. The human decides on the page; the proposal and its rule stay
   recorded.
2. **A definition carries a span**: paragraph index and character offsets into
   the paragraph's plain text (maths as `⟨LaTeX⟩`), the verbatim text, and
   whether the defining words come before, after or around the element. The
   sentence rules propose it; a small model (`corpusbuilder.definitions`,
   assist stage d) may refine the boundaries, but **only an exact substring of
   the paragraph that contains the element is ever kept** (source `model`);
   the human can cite any selection (source `human`). Every span carries its
   source.
3. **The page is two panes**: the text with every element marked by role, and
   a side pane (details of the selected element, the index table with letter
   walking, open items). On narrow screens the side pane is a bottom sheet.
4. **Exports are `discover-decisions-2`** (roles with resolved spans, marks,
   letters, families); `discover-decisions-1` files still import.

## Consequences

- `corpus/roles.{json,md}` carry counts only; the model's spans live under
  `corpus/assist/definitions/` (gitignored TDM) and are cached per paragraph.
- Downstream, the vocabulary can quote a symbol's definition by
  `(paper, paragraph, start, end)` and never by a model's words.
