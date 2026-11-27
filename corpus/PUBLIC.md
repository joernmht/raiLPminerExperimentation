# Public example dataset

The papers below are published by their journals under **CC BY 4.0** (publisher version,
confirmed by OpenAlex and Crossref on 2026-11-27). Their per-paper corpus files are public:
extracted formulas (`corpus/dossiers`), review decisions, declaration sidecars, the promoted
canonical LaTeX, the canonical formulation and its provenance record. Reuse requires
attribution to the original article (the DOI below); the derived files in this repository
are offered under the repository licence.

All other papers of the corpus are not openly licensed. What is derived from them (their
formulas, symbol tables, canonical models) is kept in a private repository and is **not**
part of this one; corpus-wide reports here hold counts and bibliographic identifiers only.
The public repository is a filtered export, produced by `scripts/publish_public.py` from the
key list `corpus/public_keys.txt`.

| paper | year | journal | candidate rows | canonical rows | objective |
| --- | ---: | --- | ---: | ---: | --- |
| Polinder et al., [10.1016/j.cor.2022.105740](https://doi.org/10.1016/j.cor.2022.105740) | 2022 | Computers & Operations Research | 14 | 2 | outside_grammar |
| Hartleb et al., [10.1016/j.ejor.2021.06.025](https://doi.org/10.1016/j.ejor.2021.06.025) | 2019 | European Journal of Operational Research | not reviewed | - | - |
| Wagenaar et al., [10.1016/j.ejor.2026.05.045](https://doi.org/10.1016/j.ejor.2026.05.045) | 2026 | European Journal of Operational Research | 19 | 10 | canonical |
| Cats et al., [10.1016/j.ejtl.2025.100160](https://doi.org/10.1016/j.ejtl.2025.100160) | 2025 | EURO Journal on Transportation and Logistics | not reviewed | - | - |
| Fuchs et al., [10.1016/j.jrtpm.2025.100555](https://doi.org/10.1016/j.jrtpm.2025.100555) | 2025 | Journal of Rail Transport Planning & Management | 21 | 12 | canonical |
| Mannino et al., [10.1016/j.omega.2022.102796](https://doi.org/10.1016/j.omega.2022.102796) | 2022 | Omega | 27 | 9 | canonical |
| Sartor et al., [10.1016/j.omega.2022.102798](https://doi.org/10.1016/j.omega.2022.102798) | 2022 | Omega | 21 | 11 | outside_grammar |
| Szymula et al., [10.1016/j.trb.2020.03.008](https://doi.org/10.1016/j.trb.2020.03.008) | 2020 | Transportation Research Part B Methodological | 33 | 19 | outside_grammar |
| Polinder et al., [10.1016/j.trb.2021.02.006](https://doi.org/10.1016/j.trb.2021.02.006) | 2020 | Transportation Research Part B Methodological | 28 | 14 | outside_grammar |
| Zhang et al., [10.1016/j.trb.2022.02.002](https://doi.org/10.1016/j.trb.2022.02.002) | 2022 | Transportation Research Part B Methodological | 21 | 15 | canonical |
| Zhu et al., [10.1016/j.trb.2025.103189](https://doi.org/10.1016/j.trb.2025.103189) | 2025 | Transportation Research Part B Methodological | 27 | 18 | outside_grammar |
| Zhu et al., [10.1016/j.trc.2021.103080](https://doi.org/10.1016/j.trc.2021.103080) | 2021 | Transportation Research Part C Emerging Technologies | 48 | 31 | canonical |
| Binder et al., [10.1016/j.trc.2021.103368](https://doi.org/10.1016/j.trc.2021.103368) | 2021 | Transportation Research Part C Emerging Technologies | 1 | 0 | outside_grammar |
| Fuchs et al., [10.1016/j.trc.2022.103805](https://doi.org/10.1016/j.trc.2022.103805) | 2022 | Transportation Research Part C Emerging Technologies | 16 | 9 | canonical |
| Xiu et al., [10.1016/j.trc.2025.105076](https://doi.org/10.1016/j.trc.2025.105076) | 2025 | Transportation Research Part C Emerging Technologies | 58 | 11 | canonical |
| Liu et al., [10.1016/j.tre.2023.103339](https://doi.org/10.1016/j.tre.2023.103339) | 2023 | Transportation Research Part E Logistics and Transportation Review | 54 | 7 | several |

## Abstracted graphs for all promoted papers

`corpus/graphs/<paper key>.json` holds, for every promoted paper whatever its license, the
structure of its canonical model without its words: the schema graph with anonymous node ids
(`var:3`, `con:12`, `index:2`), node classes and subtypes, shapes, comparators, quantifiers,
signs and operators, the objective feature, candidate and canonical row counts, the named
patterns found, and the DOI. No symbol name, description or formula is in these files
(`scripts/export_abstract_graphs.py`).
