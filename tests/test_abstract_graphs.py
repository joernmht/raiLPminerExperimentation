"""The abstracted graph carries structure and no words."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import export_abstract_graphs as X  # noqa: E402
from lp2graph import loads  # noqa: E402
from lp2graph.views.schema import schema  # noqa: E402


def _seed():
    text = (ROOT / "corpus" / "formulations" / "mip_2_1_big_m.json").read_text(encoding="utf-8")
    return loads(text, source="seed")


def test_same_structure_as_the_schema_graph():
    formulation = _seed()
    graph, abstract = schema(formulation), X.abstract_graph(formulation)
    assert len(abstract["nodes"]) == len(list(graph.nodes))
    assert len(abstract["edges"]) == len(list(graph.edges))
    ids = {n["id"] for n in abstract["nodes"]}
    assert all(e["src"] in ids and e["dst"] in ids for e in abstract["edges"])


def test_no_name_and_no_description_is_written():
    formulation = _seed()
    abstract = X.abstract_graph(formulation)
    words = {v.name for v in formulation.variables} | {p.name for p in formulation.parameters}
    words |= {c.name for c in formulation.constraints} | {i.name for i in formulation.indices}
    words |= {
        x.description for x in (*formulation.variables, *formulation.parameters) if x.description
    }
    text = json.dumps(abstract)
    assert all(
        set(n)
        <= {
            "id",
            "cls",
            "subtype",
            "shape",
            "comparator",
            "rhs_literal",
            "quantifiers",
            "role",
            "bounded",
            "over",
            "combination",
        }
        for n in abstract["nodes"]
    )
    for word in words:
        assert f'"{word}"' not in text, word
