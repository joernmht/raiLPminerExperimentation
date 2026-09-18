"""corpusbuilder.indices — index letters and families, found deterministically."""

from __future__ import annotations

from corpusbuilder import indices


def _b(latex: str) -> list[tuple]:
    return [
        (b.letters, b.family, b.kind, b.hi) for b in indices.binders_of(indices.normalise(latex))
    ]


def test_binder_pairs_in_every_spelling() -> None:
    assert _b(r"\sum_{i \in I} c_{i} x_{ij} \quad \forall j \in J") == [
        (("i",), "I", "member", None),
        (("j",), "J", "member", None),
    ]
    assert _b(r"\underset{i \in \mathcal{I}}{\sum} x_i") == [(("i",), "I", "member", None)]
    assert _b(r"\forall i, j \in I, i \neq j") == [(("i", "j"), "I", "member", None)]
    assert _b(r"\forall (i, j) \in A, k \in K") == [
        (("i", "j"), "A", "tuple", None),
        (("k",), "K", "member", None),
    ]
    assert _b(r"\sum_{e \in E_{i}} x_e") == [(("e",), "E", "member", None)]


def test_capped_ranges_become_range_bindings() -> None:
    assert _b(r"\sum_{t=1}^{T} x_t") == [(("t",), "T", "range", "T")]
    assert _b(r"\underset{t = 1}{\overset{T}{\sum}} x_{t}") == [(("t",), "T", "range", "T")]
    assert _b(r"\forall t \in \{1, 2, \ldots, T\}") == [(("t",), "T", "range", "T")]
    assert _b(r"\forall e \in E, 1 \le p \le P") == [
        (("e",), "E", "member", None),
        (("p",), "P", "range", "P"),
    ]
    assert _b(r"\forall i \in I, t = 1, \ldots, T") == [
        (("t",), "T", "range", "T"),
        (("i",), "I", "member", None),
    ]


def test_decorations_and_greek_normalise_to_lp2graph_names() -> None:
    n = indices.normalise(r"x_{k'} + x_{\hat{k}} + x_{\overset{\land}{k}} + \lambda_{i} + θ_{j}")
    assert "x_{kp}" in n and n.count("x_{k_hat}") == 2 and "lambda_{i}" in n and "theta_{j}" in n
    assert indices.base_letter("k_hat") == "k" and indices.base_letter("kp") == "k"
    assert indices.base_letter("theta") == "theta" and indices.base_letter("train") is None


def test_subscript_positions_and_juxtaposed_letters() -> None:
    uses = indices.subscript_uses(
        indices.normalise(r"c_{i} x_{i j} + y_{k'} + z^{k}_{i+1, t} + T_{max}")
    )
    got = {(u.base, u.position, u.letter, u.juxtaposed) for u in uses}
    assert ("c", "1", "i", False) in got and ("x", "2", "j", True) in got
    assert (
        ("y", "1", "kp", False) in got
        and ("z", "2", "t", False) in got
        and ("z", "sup", "k", False) in got
    )
    assert not any(u.base == "T" for u in uses)  # "max" is a label, not an index


def test_analyse_resolves_aliases_by_decoration_and_position() -> None:
    rows = [
        ("eq-0001", r"\sum_{k \in K} x_{k} \le 1"),
        ("eq-0002", r"x_{k'} + x_{k} \le 1 \quad \forall k \in K"),
        ("eq-0003", r"y_{s, t} \ge 0 \quad \forall s \in S, t = 1, \ldots, T"),
        ("eq-0004", r"y_{s, u} \le y_{s, t}"),
        ("eq-0005", r"z_{d} \le 1"),
        ("eq-0006", r"y_{s, u} \ge y_{s, t} - 1"),
        ("eq-0007", r"z_{w} \le z_{d}"),
        ("eq-0008", r"q_{w} \le q_{k}"),
    ]
    rec = indices.analyse(
        rows, None, {"index": {"K": "", "k": ""}, "param": {"T": "periods"}, "var": {}}
    )
    L = rec["letters"]
    assert L["k"]["verdict"] == "index" and L["k"]["family"] == "K" and L["k"]["rule"] == "binder"
    assert (
        L["kp"]["verdict"] == "alias"
        and L["kp"]["family"] == "K"
        and L["kp"]["rule"] == "decorated"
    )
    assert (
        L["t"]["verdict"] == "index" and L["t"]["rule"] == "capped" and L["t"]["capped"] == {"T": 1}
    )
    assert L["u"]["verdict"] == "alias" and L["u"]["family"] == "T" and L["u"]["rule"] == "position"
    assert L["d"]["verdict"] == "candidate" and L["d"]["family"] is None
    # one shared position is not enough for an alias: the vote is kept as evidence only
    assert L["w"]["verdict"] == "candidate" and L["w"]["alias_votes"] == {"K": 1}
    fams = rec["families"]
    assert (
        fams["K"]["declared_as"] == "index"
        and fams["T"]["declared_as"] == "param"
        and fams["T"]["cap"]
    )
    assert rec["declared_index_are_letters"] == ["k"]
    assert rec["counts"]["families_new"] == 2  # S and T
    lines = indices.proposed_index_lines(rec)
    assert any(line.startswith("%@ index T ") and "range 1..T" in line for line in lines)
    assert not any(line.startswith("%@ index K ") for line in lines)


def test_prose_and_table_evidence_bind_letters() -> None:
    discovery = {
        "maths": [
            {
                "id": "m-0001",
                "where": "inline",
                "cls": "statement",
                "ok": True,
                "latex": r"i \in \mathcal{I}",
            }
        ],
        "tables": [
            {
                "id": "t-0001",
                "notation": True,
                "rows": [
                    {"header": False, "symbol": "I", "desc": "Set of trains"},
                    {"header": False, "symbol": "j", "desc": "Generic train in ⟨I⟩"},
                ],
            }
        ],
        "deflists": [],
    }
    rec = indices.analyse([("eq-0001", r"x_{i} + x_{j} \le 1")], discovery)
    assert rec["letters"]["i"]["rule"] == "prose" and rec["letters"]["i"]["family"] == "I"
    assert rec["letters"]["j"]["rule"] == "table" and rec["letters"]["j"]["family"] == "I"
    assert rec["families"]["I"]["desc"] == "Set of trains"
    assert sorted(rec["families"]["I"]["letters"]) == ["i", "j"]


def test_numeric_and_arithmetic_caps_name_no_family_and_capitals_are_labels() -> None:
    rows = [
        ("eq-0001", r"\sum_{i=1}^{3} x_{i} \le 1"),
        ("eq-0002", r"\sum_{u=1}^{2N-1} y_{u} + \sum_{k=1}^{|K|} z_{k} \le 1"),
        ("eq-0003", r"t_{i, B} \le t_{i, E} \quad \forall i \in I"),
    ]
    rec = indices.analyse(rows, None)
    L = rec["letters"]
    assert L["i"]["verdict"] == "index" and L["i"]["family"] == "I" and "3" not in rec["families"]
    assert L["u"]["family"] is None and L["u"]["capped"] and "2N-1" not in rec["families"]
    assert L["k"]["family"] == "K" and rec["families"]["K"]["cap"]
    assert (
        L["B"]["verdict"] == "label" and L["E"]["verdict"] == "label" and L["B"]["family"] is None
    )
    assert rec["rows"]["eq-0003"]["letters"] == {"i": "binder", "B": "sub", "E": "sub"}
    assert rec["rows"]["eq-0002"]["binders"][1] == {
        "letters": ["k"],
        "family": "K",
        "kind": "range",
        "lo": "1",
        "hi": "|K|",
    }


def test_standard_labels_and_glued_words_are_not_indices() -> None:
    rows = [
        ("eq-0001", r"t_{end} - t_{start} \le T_{max} \quad \forall e \in E"),
        ("eq-0002", r"N_{st} + y_{e n d} + M_{qw} \le 1"),
        ("eq-0003", r"x_{ij} \le 1 \quad \forall i \in I, j \in J"),
        ("eq-0004", r"z_{ik} \le 1"),
    ]
    rec = indices.analyse(rows, None)
    L = rec["letters"]
    assert not {"n", "d", "s", "t"} & set(
        L
    )  # end/start/max/st are standard label words: skipped outright
    assert (
        L["q"]["verdict"] == "label"
        and L["q"]["rule"] == "glued word"
        and L["q"]["runs"] == {"qw": 1}
    )
    assert L["w"]["verdict"] == "label"  # an unlisted glued pair whose letters are bound nowhere
    assert L["i"]["verdict"] == "index" and L["j"]["verdict"] == "index"
    assert L["k"]["verdict"] == "juxtaposed" and L["k"]["runs"] == {
        "ik": 1
    }  # glued to the bound i: undecided


def test_two_letter_index_names_are_recognised_from_their_own_uses() -> None:
    # Joern's case: st is the station of an event, tr its train; N_{st} is indexed by the station
    rows = [
        (
            "eq-0001",
            r"\underset{e}{\sum} n_{e , e^{'}}^{p} \leq \left(N_{s t} - 1\right) \left(1 - p_{e^{'}}\right) + p_{e^{'}} \left(N_{s t}^{p} - 1\right) , "
            r"e , e^{'} \in E_{\text{ar}} , s t_{e} = s t_{e^{'}} , s t = s t_{e^{'}} , t r_{e} \neq t r_{e^{'}} .",
        ),
        ("eq-0002", r"x_{i j} \le 1 \quad \forall i \in I, j \in J"),
        ("eq-0003", r"t_{end} \le T_{max} \quad \forall e \in E"),
    ]
    rec = indices.analyse(rows, None)
    L = rec["letters"]
    assert L["st"]["verdict"] == "index" and L["st"]["rule"] == "multi-letter name"
    assert L["st"]["word_evidence"]["own_subscript"] >= 2 and L["st"]["word_evidence"]["bare"] >= 1
    assert "s" not in L and "t" not in L  # the pair is one name, not two letters or a label
    assert L["e"]["verdict"] == "index" and L["e"]["family"] == "E"
    assert L["i"]["verdict"] == "index" and L["j"]["verdict"] == "index"
    assert rec["rows"]["eq-0001"]["letters"]["st"] == "sub"


def test_a_bare_binder_dummy_counts_as_bound_without_a_family() -> None:
    rec = indices.analyse([("eq-0001", r"\sum_{q} c_{q} x_{q} \le 1")], None)
    assert rec["letters"]["q"]["verdict"] == "index" and rec["letters"]["q"]["family"] is None
