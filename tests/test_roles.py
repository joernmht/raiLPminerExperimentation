"""corpusbuilder.roles — a role for every element and citable definition spans."""

from __future__ import annotations

from corpusbuilder import discover, roles
from tests.test_discover import _XML, _fake_convert


def test_sentences_respect_abbreviations_and_placeholders() -> None:
    plain = (
        "Let ⟨x⟩ be the flow (see Fig. 3). It equals 2.5 units. Here ⟨y⟩ is the cost; e.g. large."
    )
    spans = [plain[a:b] for a, b in roles.sentences(plain)]
    assert spans == [
        "Let ⟨x⟩ be the flow (see Fig. 3).",
        "It equals 2.5 units.",
        "Here ⟨y⟩ is the cost; e.g. large.",
    ]


def test_definition_span_positions() -> None:
    plain = "Trains are indexed. Let ⟨x_{ij}⟩ be a binary variable. The set of trains is denoted by ⟨T⟩. Here ⟨E⟩ is the set of events. This uses ⟨n⟩ again."
    a = plain.index("⟨x_{ij}⟩")
    span = roles.definition_span(plain, a, a + len("⟨x_{ij}⟩"))
    assert (
        span["text"] == "Let ⟨x_{ij}⟩ be a binary variable."
        and span["position"] == "around"
        and span["source"] == "rule"
    )
    a = plain.index("⟨T⟩")
    assert roles.definition_span(plain, a, a + 3)["position"] == "before"
    a = plain.index("⟨E⟩")
    assert roles.definition_span(plain, a, a + 3)["position"] == "after"
    a = plain.index("⟨n⟩")
    assert roles.definition_span(plain, a, a + 3) is None
    aside = "The stop is skipped (i.e. ⟨s_a = 1⟩)."
    a = aside.index("⟨")
    assert roles.definition_span(aside, a, aside.index("⟩") + 1) is None


def test_propose_roles_covers_every_element() -> None:
    rec = discover.discover_paper(_XML, key="p", convert=_fake_convert)
    r = roles.propose_roles(rec)
    assert set(r) == {m["id"] for m in rec["maths"]}
    by_latex = {m["latex"]: r[m["id"]] for m in rec["maths"]}
    assert by_latex["min∑ci"]["role"] == "formula"
    assert by_latex["xij"]["role"] == "definition" and by_latex["xij"]["span"]["text"].startswith(
        "Let ⟨xij⟩ be a binary variable"
    )
    assert by_latex["i∈I"]["role"] == "index"
    assert by_latex["n+1"]["role"] == "other"
    table = [v for k, v in r.items() if rec["maths"][int(k[2:]) - 1]["where"] == "table"]
    assert table[0]["role"] == "definition" and table[0]["span"]["text"].startswith("⟨I⟩ | Set of trains")
    c = roles.counts(r)
    assert c["elements"] == len(rec["maths"]) and c["definition_with_span"] == c["definition"]


def test_inline_constraints_are_formulas_not_definitions() -> None:
    plain = "The headway constraints are: ⟨x_{e'} - x_{e} \\geq L_{a}⟩ for all pairs."
    rec = {"cls": "statement", "latex": "x_{e'} - x_{e} \\geq L_{a}"}
    a = plain.index("⟨")
    assert roles._role_inline(rec, plain, a, plain.index("⟩") + 1)[0] == "formula"
    rec = {"cls": "statement", "latex": "c_{e} = 1"}
    plain2 = "Here ⟨c_{e} = 1⟩ indicates that the event is cancelled."
    assert (
        roles._role_inline(rec, plain2, plain2.index("⟨"), plain2.index("⟩") + 1)[0] == "definition"
    )
