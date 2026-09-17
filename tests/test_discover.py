"""corpusbuilder.discover — every mathematical element, classified, with its prose window."""

from __future__ import annotations

import json
from pathlib import Path

from corpusbuilder import discover
from corpusbuilder.mathml import Converted

_XML = """<?xml version="1.0" encoding="utf-8"?>
<full-text-retrieval-response xmlns="http://www.elsevier.com/xml/svapi/article/dtd"
  xmlns:ce="http://www.elsevier.com/xml/common/dtd" xmlns:mml="http://www.w3.org/1998/Math/MathML"
  xmlns:cals="http://www.elsevier.com/xml/common/cals/dtd">
<originalText><xocs:doc xmlns:xocs="http://www.elsevier.com/xml/xocs/dtd"><xocs:serial-item><article>
<head><ce:abstract class="author"><ce:abstract-sec><ce:simple-para>We reschedule trains.</ce:simple-para></ce:abstract-sec></ce:abstract></head>
<body><ce:sections>
<ce:section id="s1"><ce:label>2</ce:label><ce:section-title>Model</ce:section-title>
<ce:para id="p1">Let <mml:math><mml:msub><mml:mi>x</mml:mi><mml:mrow><mml:mi>i</mml:mi><mml:mi>j</mml:mi></mml:mrow></mml:msub></mml:math>
be a binary variable for all <mml:math><mml:mi>i</mml:mi><mml:mo>∈</mml:mo><mml:mi>I</mml:mi></mml:math>. The objective
<ce:formula id="e1"><ce:label>(1)</ce:label><mml:math><mml:mo>min</mml:mo><mml:mo>∑</mml:mo><mml:msub><mml:mi>c</mml:mi><mml:mi>i</mml:mi></mml:msub></mml:math></ce:formula>
holds where <mml:math><mml:mi>n</mml:mi><mml:mo>+</mml:mo><mml:mn>1</mml:mn></mml:math> is used.</ce:para>
<ce:para id="p2">Nested: <ce:para id="p2a">inner text <mml:math><mml:mi>y</mml:mi></mml:math></ce:para> tail.</ce:para>
</ce:section></ce:sections></body>
<ce:floats><ce:table id="t1"><ce:label>Table 1</ce:label><ce:caption><ce:simple-para>Notation.</ce:simple-para></ce:caption>
<cals:tgroup cols="2"><cals:thead><cals:row><cals:entry>Symbol</cals:entry><cals:entry>Meaning</cals:entry></cals:row></cals:thead>
<cals:tbody><cals:row><cals:entry><mml:math><mml:mi>I</mml:mi></mml:math></cals:entry><cals:entry>Set of trains</cals:entry></cals:row>
<cals:row><cals:entry>i</cals:entry><cals:entry>Generic train in <mml:math><mml:mi>I</mml:mi></mml:math></cals:entry></cals:row>
<cals:row><cals:entry>3.5</cals:entry><cals:entry>a number</cals:entry></cals:row></cals:tbody></cals:tgroup></ce:table>
<ce:table id="t2"><ce:label>Table 2</ce:label><ce:caption><ce:simple-para>Results.</ce:simple-para></ce:caption>
<cals:tgroup cols="2"><cals:tbody><cals:row><cals:entry>A</cals:entry><cals:entry>12</cals:entry></cals:row>
<cals:row><cals:entry>B</cals:entry><cals:entry>13</cals:entry></cals:row></cals:tbody></cals:tgroup></ce:table></ce:floats>
</article></xocs:serial-item></xocs:doc></originalText></full-text-retrieval-response>
"""


def _fake_convert(mathml_list: list[str]) -> list[Converted]:
    """Deterministic stand-in for the node bridge: the element's text, tagged."""
    out = []
    for m in mathml_list:
        text = "".join(t for t in __import__("re").sub(r"<[^>]+>", " ", m).split())
        out.append(Converted(ok=True, latex=text))
    return out


def test_discover_paper_records_every_element_with_class_and_context() -> None:
    rec = discover.discover_paper(_XML, key="p", convert=_fake_convert)
    where = {m["id"]: m["where"] for m in rec["maths"]}
    assert list(where.values()).count("display") == 1
    assert list(where.values()).count("inline") == 4
    assert list(where.values()).count("table") == 2
    disp = next(m for m in rec["maths"] if m["where"] == "display")
    assert disp["eq"] == "eq-0001" and disp["tag"] == "(1)" and disp["label"] == "e1"
    assert disp["cls"] == "operator"
    by_latex = {m["latex"]: m for m in rec["maths"]}
    assert by_latex["xij"]["cls"] == "symbol"
    assert by_latex["i∈I"]["cls"] == "statement"
    assert by_latex["n+1"]["cls"] == "expr"
    assert "variable" in by_latex["xij"]["near"] and "binary" in by_latex["xij"]["near"]
    assert "foreach" in by_latex["i∈I"]["near"]
    assert by_latex["xij"]["context"].startswith("Let ⟨xij⟩")


def test_paragraph_segments_keep_order_and_nesting() -> None:
    rec = discover.discover_paper(_XML, key="p", convert=_fake_convert)
    kinds = [s[0] for s in rec["paras"][1]["segments"]]
    assert kinds == ["t", "m", "t", "m", "t", "d", "t", "m", "t"]
    assert rec["paras"][1]["section"] == "2 Model"
    assert rec["paras"][0]["region"] == "abstract"
    nested = rec["paras"][2]
    assert "¶" in nested["plain"] and "tail." in nested["plain"]
    assert rec["counts"]["display_in_dossier"] == 1 and rec["counts"]["dossier_formulas"] == 1


def test_notation_table_is_recognised_and_data_table_is_not() -> None:
    rec = discover.discover_paper(_XML, key="p", convert=_fake_convert)
    t1, t2 = rec["tables"]
    assert t1["notation"] and not t2["notation"]
    rows = [r for r in t1["rows"] if not r["header"]]
    assert [r["symbol"] for r in rows] == ["I", "i", None]
    assert rows[1]["desc"] == "Generic train in ⟨I⟩"
    assert rec["counts"]["notation_tables"] == 1 and rec["counts"]["notation_rows"] == 2
    # the row rule alone (no telling caption) needs wordy descriptions
    assert not discover.is_notation_table(
        {"caption": "", "rows": [{"header": False, "symbol": "A", "desc": "12"}]}
    )
    assert discover.is_notation_table(
        {
            "caption": "",
            "rows": [
                {"header": False, "symbol": "A", "desc": "set of trains"},
                {"header": False, "symbol": "B", "desc": "set of tracks"},
            ],
        }
    )


def test_discovery_is_deterministic_and_report_counts_only(tmp_path: Path) -> None:
    a = discover.discover_paper(_XML, key="p", convert=_fake_convert)
    b = discover.discover_paper(_XML, key="p", convert=_fake_convert)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    report = discover.build_report({"p": a["counts"]})
    md = discover.render_report_md(report)
    assert "| p |" in md and "Set of trains" not in md and "Let" not in json.dumps(report)


def test_kind_words_cover_the_vocabulary_of_declarations() -> None:
    found = discover.kind_words(
        "let x be a non-negative continuous decision variable, a big-M constant"
    )
    assert {"let", "nonnegative", "continuous", "decision", "variable", "big_m", "constant"} <= set(
        found
    )
    assert discover.kind_words("the train arrives") == []
