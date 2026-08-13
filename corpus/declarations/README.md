# `corpus/declarations/` — per-paper symbol tables

A published equation carries algebra but no symbol table, and canonical LP2Graph
LaTeX needs one. Each paper being promoted therefore needs a sidecar here, named
after its dossier key:

    corpus/declarations/<paper_key>.tex

containing only `%@` declaration lines:

    %@ index I ordered=0 cyclic=0 :: Trains.
    %@ param h shape=- kind=scalar domain=- :: Minimum headway.
    %@ var t shape=I domain=non_negative role=primary drole=- lo=- hi=- :: Departure time.
    %@ obj sense=min name=total_time combination=sum :: Total departure time.
    %@ con eq_0003 kind=headway domain=- indicator=- :: Headway between consecutive trains.

Do **not** add `meta` / `name` / `desc` / `tags` / `prov` lines: those are generated
from the dossier by `corpusbuilder.promote`. (`%@ meta family=` is the exception —
it overrides the family otherwise derived from the declared variable domains.)

Running `python -m corpusbuilder.promote` writes a fill-in-the-blank
`<paper_key>.stub.tex` for every reviewed paper that has no sidecar yet, with the
symbols and constraint-row names already filled in. Edit it, rename it to
`<paper_key>.tex`, re-run promote. Stub files are git-ignored; the sidecars you
complete are corpus artifacts and belong in the repository.

See `docs/adr/0010-promotion-needs-a-declaration-sidecar.md`.
