"""corpusbuilder.definitions — model-refined definition boundaries stay verbatim and citable."""

from __future__ import annotations

import json
from pathlib import Path

from corpusbuilder import assist, definitions, discover
from tests.test_discover import _XML, _fake_convert


def test_validate_reply_keeps_only_verbatim_passages_containing_the_element() -> None:
    plain = "Trains run. Let ⟨x⟩ be a binary variable that equals one if the train runs. Here ⟨y⟩ is a cost."
    cands = [
        {"id": "m-0001", "latex": "x"},
        {"id": "m-0002", "latex": "y"},
        {"id": "m-0003", "latex": "z"},
    ]
    reply = {
        "m-0001": {
            "defines": True,
            "passage": "Let ⟨x⟩ be a binary variable that equals one if the train runs.",
            "position": "around",
        },
        "m-0002": {
            "defines": True,
            "passage": "Here y is a cost.",
            "position": "after",
        },  # paraphrased: refused
        "m-0003": {"defines": False},
    }
    spans, problems = definitions.validate_reply(reply, plain, cands)
    assert (
        spans["m-0001"]["text"].startswith("Let ⟨x⟩")
        and plain[spans["m-0001"]["start"] : spans["m-0001"]["end"]] == spans["m-0001"]["text"]
    )
    assert spans["m-0001"]["source"] == "model" and spans["m-0003"] == {
        "defines": False,
        "source": "model",
    }
    assert "m-0002" not in spans and problems == [
        "m-0002: passage is not a verbatim substring containing the element"
    ]


def test_refine_paper_offline_writes_spans_and_flags_differences(tmp_path: Path) -> None:
    rec = discover.discover_paper(_XML, key="p", convert=_fake_convert)
    (tmp_path / "disc").mkdir()
    (tmp_path / "disc" / "p.json").write_text(json.dumps(rec), encoding="utf-8", newline="\n")
    seen: list[dict] = []

    def chat(payload: dict) -> str:
        seen.append(payload)
        user = json.loads(payload["messages"][1]["content"])
        out = {}
        for c in user["candidates"]:
            marker = f"⟨{c['latex']}⟩"
            i = user["paragraph"].index(marker)
            out[c["id"]] = {
                "defines": True,
                "passage": user["paragraph"][i : i + len(marker) + 21],
                "position": "after",
            }
        return json.dumps(out)

    ws = assist.Workspace(assist=tmp_path / "assist")
    out = definitions.refine_paper(
        ws, "p", discovery_dir=tmp_path / "disc", out_dir=tmp_path / "defs", chat=chat
    )
    assert out["candidates"] == 1 and out["paragraphs"] == 1 and len(seen) == 1
    span = out["spans"]["m-0001"]
    assert (
        span["defines"]
        and span["source"] == "model"
        and span["text"].startswith("⟨xij⟩")
        and span["differs_from_rule"]
    )
    assert seen[0]["messages"][0]["content"].startswith("You mark") and seen[0]["temperature"] == 0
    written = json.loads((tmp_path / "defs" / "p.json").read_text(encoding="utf-8"))
    assert written["schema_version"] == "definitions-1" and written["summary"]["refined"] == 1
