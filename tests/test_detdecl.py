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


def test_prose_evidence_needs_the_kind_noun_next_to_the_symbol(monkeypatch):
    # invented sentences in the shapes publishers produce (symbols as plain characters)
    paras = [
        {
            "i": 1,
            "plain": "where α 1 is a binary variable, and the tolerance parameter ξ is small.",
        },
        {"i": 2, "plain": "The event times πi  and πj  lie within the given bounds."},
        {
            "i": 3,
            "plain": "the variable δij  and parameter ϕij  are introduced; ⟨\\rho_{k}⟩ is a variable.",
        },
        {"i": 4, "plain": "the dual variable λ of the relaxed constraint, and Zα is given."},
    ]
    monkeypatch.setattr(D, "load_record", lambda key, _dir=None: {"paras": paras})
    names = ["alpha", "alpha_1", "xi", "pi", "delta", "phi", "rho", "lambda", "x"]
    got = D.prose_evidence("demo", names)
    assert got["alpha_1"][:2] == ("var", {"domain": "binary"})
    assert got["xi"][0] == "param" and got["delta"][0] == "var" and got["phi"][0] == "param"
    # the bare letter is not the labelled one; a mention without a kind noun,
    # a symbol inside maths, a multiplier and a subscript decide nothing
    assert not {"alpha", "pi", "rho", "lambda", "x"} & set(got)


def _uses(*rows: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for row in rows:
        for u in scan_symbols(row):
            if not u.in_tail:
                out.setdefault(u.name, []).append(u)
    return out


def test_a_set_and_a_symbol_may_share_a_name():
    sc = D.Sidecar(
        "%@ index T ordered=1 cyclic=0 :: periods\n"
        "%@ index I ordered=0 cyclic=0 :: trains\n"
        "%@ index k ordered=0 cyclic=0 :: a family named by its letter\n"
        "%@ var x shape=I domain=continuous role=primary drole=- lo=- hi=- :: x\n"
    )
    uses = _uses(r"& x_{i} \le T", r"& x_{i} + T \ge 2 \cdot T", r"& x_{i} \le k")
    D.rule_missing(sc, uses, {"i": "I"}, {}, {})
    # the capital set written as a number: a parameter next to the family
    assert "T" in sc.names("index") and sc.names("param")["T"]["shape"] == "-"
    # a family named by a lower-case letter is an index letter's own set
    assert "k" not in sc.names("param") and sc.undeclared["k"] == "a set used as a value"
    # a capital some row binds as a letter (T \in \mathcal{T}) is an index, not a number
    sc2 = D.Sidecar("%@ index T ordered=1 cyclic=0 :: periods\n")
    D.rule_missing(sc2, _uses(r"& x \le T"), {}, {}, {}, bound=frozenset({"T"}))
    assert "T" not in sc2.names("param")


def test_linearity_reads_through_a_fraction():
    # w multiplies the numerator's variable; whatever divides is data
    sc = D.Sidecar("%@ var Z shape=- domain=continuous role=primary drole=- lo=- hi=- :: z\n")
    rows = (r"& y = w \cdot \frac{Z}{N} + q",)
    uses = _uses(*rows)
    D.rule_missing(sc, uses, {}, {}, {}, divisors=frozenset({"N"}))
    assert sc.names("param").keys() >= {"w", "N"}
    assert "q" not in sc.names("param") and sc.undeclared["q"] == "no evidence"


_TWO_FORMS = (
    "%@ index I ordered=0 cyclic=0 :: i\n"
    "%@ index J ordered=0 cyclic=0 :: j\n"
    "%@ index F ordered=0 cyclic=0 :: f\n"
    "%@ index T ordered=0 cyclic=0 :: t\n"
    "%@ var x shape=I,J domain=binary role=primary drole=- lo=- hi=- :: arc choice\n"
)
_FAMS = {"i": "I", "j": "J", "f": "F", "t": "T"}


def test_one_letter_written_over_different_sets_is_two_variables():
    # x over (I, J) and x over (F, T): neither tuple holds the other
    sc = D.Sidecar(_TWO_FORMS)
    rows = {
        "a": r"& \sum_{j \in \mathcal{J}} x_{i,j} = 1 \forall i \in \mathcal{I}",
        "b": r"& x_{i,j} \leq 1 \forall i \in \mathcal{I}, j \in \mathcal{J}",
        "c": r"& \sum_{t \in \mathcal{T}} x_{f,t,t} \leq 1 \forall f \in \mathcal{F}",
    }
    D.rule_homonym_variables(sc, rows, _FAMS)
    second = sc.names("var")["x_idx_F_T_T"]
    assert second["shape"] == "F,T,T" and second["domain"] == "binary"
    assert sc.rule_of[("symbol", "x_idx_F_T_T")] == D.HOMONYM_RULE
    assert "written over different sets" in sc.text()
    assert sc.together == {}


def test_one_row_that_writes_both_forms_relates_two_variables():
    # x_{i} next to x_{i,j} in one row: an aggregate and its parts
    sc = D.Sidecar(_TWO_FORMS)
    rows = {
        "a": r"& x_{i,j} \leq 1 \forall i \in \mathcal{I}, j \in \mathcal{J}",
        "b": r"& \sum_{j \in \mathcal{J}} x_{i,j} = x_{i} \forall i \in \mathcal{I}",
    }
    D.rule_homonym_variables(sc, rows, _FAMS)
    assert sc.names("var")["x_idx_I"]["shape"] == "I"
    assert "row b writes both forms" in sc.text()


def test_an_index_left_out_is_one_variable():
    # x_{i} in a row of its own, x declared over (I, J): the families of one
    # form are among those of the other, so nothing says they are two
    sc = D.Sidecar(_TWO_FORMS)
    rows = {
        "a": r"& x_{i,j} \leq 1 \forall i \in \mathcal{I}, j \in \mathcal{J}",
        "b": r"& x_{i} \leq 1 \forall i \in \mathcal{I}",
        "c": r"& x \geq 0",
    }
    D.rule_homonym_variables(sc, rows, _FAMS)
    assert set(sc.names("var")) == {"x"}
    assert list(sc.together) == ["x with 1 index"]
    assert "one variable" in sc.together["x with 1 index"]
