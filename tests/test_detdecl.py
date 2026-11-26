"""Deterministic declarations: every change is undoable and rule-attributed."""

from __future__ import annotations

from lp2graph.mining.ingest.row_canon import scan_symbols

from corpusbuilder import detdecl as D

SIDECAR = """% Declaration sidecar for demo
%@ index I ordered=0 cyclic=0 :: trains
%@ param M shape=- kind=big_m domain=- :: big M
%@ var x shape=- domain=real role=variable drole=max lo=- hi=T-1 :: order
%@ var t shape=- domain=continuous role=primary drole=- lo=- hi=- :: time
%@ var y_ij shape=- domain=binary role=primary drole=- lo=- hi=- :: folded
"""


def _sidecar() -> D.Sidecar:
    return D.Sidecar(SIDECAR)


def test_metadata_defaults_and_exact_undo():
    sc = _sidecar()
    D.rule_metadata(sc)
    text = sc.text()
    assert "%@ var x shape=- domain=continuous role=primary drole=- lo=- hi=-" in text
    assert text.count(D.WAS) == 1
    assert D.restore(text) == SIDECAR
    assert D.restore(D.restore(text)) == SIDECAR


def test_letters_and_families_are_added_in_a_marked_block():
    sc = _sidecar()
    D.rule_letters(sc, {"i": "I", "s": "S", "t": "T"})
    text = sc.text()
    assert "%@ letter i over=I" in text and "%@ index S " in text
    assert "%@ letter t" not in text  # t is a declared symbol, never rebound
    assert text.index(D.BEGIN) < text.index("%@ letter i") < text.index(D.END)
    assert D.restore(text) == SIDECAR


def test_shape_is_the_majority_reading_with_unnamed_letter_families():
    uses = scan_symbols(r"& t_{i, s} - t_{ip, s} \ge M \cdot x_{i, ip} + t_{i}") + scan_symbols(
        r"& t_{i, s + 1} \le 1"
    )
    by_name: dict[str, list] = {}
    for u in uses:
        by_name.setdefault(u.name, []).append(u)
    shape, why = D.shape_of_uses(by_name["t"], {"i": "I", "ip": "I"})
    assert (shape, why) == (["I", "fam_s"], "")
    assert D.base_letter("ip") == "i" and D.letter_family("s") == "fam_s"


def test_index_letters_skip_weak_aliases_and_capitals():
    idx = {
        "letters": {
            "i": {"verdict": "index", "family": "I", "rule": "binder"},
            "ip": {"verdict": "alias", "family": "I", "rule": "decorated"},
            "x": {"verdict": "alias", "family": "N", "rule": "position"},
            "D": {"verdict": "index", "family": "E", "rule": "prose"},
            "q": {"verdict": "label", "family": None, "rule": "uppercase"},
        }
    }
    assert D.index_letters(idx) == {"i": "I", "ip": "I"}


def test_kind_of_words_reads_the_description_then_the_group():
    assert D.kind_of_words("binary decision variable, 1 if train i precedes j")[:1] == ("var",)
    assert D.kind_of_words("binary decision variable")[1]["domain"] == "binary"
    assert D.kind_of_words("set of trains") == ("index", {})
    assert D.kind_of_words("minimum headway")[0] == "param"
    assert not D.kind_of_words("minimum headway")[1]["strong"]
    assert D.kind_of_words("sufficiently large constant")[1] == {"kind": "big_m", "strong": "1"}
    assert D.kind_of_words("arrival time of train i") is None
    assert D.kind_of_words("arrival time of train i", "var")[0] == "var"


def test_variant_rule_follows_the_measured_cases_only():
    params = {"h": {}, "M": {}}
    variables = {"t": {"domain": "continuous"}, "x": {"domain": "binary"}}
    assert D.variant_kind("M_1", params, variables)[0] == "param"
    assert D.variant_kind("t_max", params, variables)[0] == "param"  # a bound of a variable
    assert D.variant_kind("t_arr", params, variables)[:2] == ("var", {"domain": "continuous"})
    assert D.variant_kind("x_1", params, variables)[:2] == ("var", {"domain": "binary"})
    assert D.variant_kind("t_bar", params, variables) is None  # accents split both ways
    assert D.variant_kind("t_recovery", params, variables) is None
    assert D.variant_kind("q_1", params, variables) is None  # no declared base
    for agree, total in D.VARIANT_AGREEMENT.values():
        assert agree / total >= 0.84


def test_non_vacuity_deduces_variables_and_nothing_else():
    sc = _sidecar()
    rows = {
        "": "",
        "c1": r"& u_{i} = t_{i} + M",  # set equal to an expression holding a variable
        "c2": r"& w_{i} \le M",  # the only undeclared name of an inequality without variables
        "c3": r"& v_{i} = M",  # an equation between a name and data decides nothing
        "c4": r"& p_{i} + q_{i} \le M",  # two undeclared names: undecided
    }
    out = D.deduce_variables(sc, rows, {"i": "I"})
    assert set(out) == {"u", "w"}


def test_name_keys_ignore_label_order_but_keep_case():
    assert D.name_key("t_arr_1") == D.name_key("t_1_arr") != D.name_key("T_arr_1")
