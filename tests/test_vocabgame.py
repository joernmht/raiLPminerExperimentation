"""corpusbuilder.vocabgame — the vocabulary round (blind gold labels, proposal confirmation)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from corpusbuilder import vocabgame

_HEADER = """%@ meta id=p1 family=milp schema=0.1.0
%@ name :: Probe
% ASSISTED RESOLUTION — rung (c) of staged symbol resolution (sec:resolution).
%@ index I ordered=0 cyclic=0 :: items
%@ param c shape=I kind=vector domain=- :: cost
%@ var x shape=I domain=binary role=primary drole=- lo=- hi=- :: choice
% --- vocabulary fill (stage v, corpusbuilder.assist --vocab, model m, 2026-09-10) ---
%@ var t_arr shape=I domain=continuous role=primary drole=- lo=- hi=- :: arrival time
%@ obj sense=min name=objective combination=sum :: cost
"""


def _gold() -> dict:
    return {
        "schema_version": "vocab-gold-1",
        "seed": 1,
        "n": 2,
        "pool": 5,
        "items": [
            {
                "id": "p1::t_arr",
                "paper_key": "p1",
                "name": "t_arr",
                "evidence": "position only",
                "kind_guess_hidden": "var",
                "arities": {"1": 2},
                "rows": [
                    {"name": "eq_0001", "latex": r"t_arr_{i} \le c_{i} \forall i \in \mathcal{I}"}
                ],
                "families": ["I"],
                "abstract": "An abstract.",
            },
            {
                "id": "p1::h_min",
                "paper_key": "p1",
                "name": "h_min",
                "evidence": "position only",
                "kind_guess_hidden": "param",
                "arities": {"0": 1},
                "rows": [{"name": "eq_0002", "latex": r"x_{i} \ge h_min"}],
                "families": ["I"],
                "abstract": "",
            },
        ],
    }


def _workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    promoted = tmp_path / "promoted"
    decl = tmp_path / "declarations"
    promoted.mkdir()
    decl.mkdir()
    body = (
        "\\begin{align}\n"
        r"  \min\quad & \sum_{i \in \mathcal{I}} c_{i} \cdot x_{i} \tag{eq\_0001} \\" + "\n"
        r"  & t_arr_{i} \le c_{i} \forall i \in \mathcal{I} \tag{eq\_0002} \\" + "\n"
        "\\end{align}\n"
    )
    (promoted / "p1.tex").write_text(_HEADER + body, encoding="utf-8")
    (decl / "p1.tex").write_text(_HEADER, encoding="utf-8")
    promotion = tmp_path / "promotion.json"
    promotion.write_text(
        json.dumps(
            {
                "papers": [
                    {
                        "paper_key": "p1",
                        "promoted": False,
                        "coverage": {"rows_probed": 1, "rows_ok": 1},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return promoted, decl, promotion


def test_display_latex_marks_the_symbol_and_protects_underscored_names():
    out = vocabgame.display_latex(r"t_arr_{i} \le c_{i} + \mathit{t_arr} + h_min", "t_arr")
    assert r"\mathbf{t\_arr}_{i}" in out
    assert r"\mathbf{t\_arr}" in out and out.count(r"\mathbf{") == 2
    assert r"\mathit{h\_min}" in out


def test_blind_build_is_deterministic_and_hides_every_model_hint(tmp_path: Path):
    items = vocabgame.blind_items(_gold())
    out = tmp_path / "blind.html"
    vocabgame.build("blind", items, out)
    first = out.read_bytes()
    vocabgame.build("blind", items, out)
    assert out.read_bytes() == first
    html = out.read_text(encoding="utf-8")
    assert "kind_guess" not in html
    data = json.loads(re.search(r'id="data">(.*?)</script>', html, re.S).group(1))
    assert data["mode"] == "blind" and [it["id"] for it in data["items"]] == [
        "p1::t_arr",
        "p1::h_min",
    ]
    assert all(it["proposal"] is None for it in data["items"])
    assert data["items"][0]["families"] == ["I"] and data["items"][0]["abstract"] == "An abstract."
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    assert style.count("{") == style.count("}") and "prefers-color-scheme:dark" in style


def test_confirm_items_order_proposals_and_attach_rows(tmp_path: Path):
    promoted, decl, promotion = _workspace(tmp_path)
    items = vocabgame.confirm_items(
        promoted_dir=promoted,
        declarations_dir=decl,
        promotion_path=promotion,
        prose_dir=tmp_path / "none",
    )
    assert [it["name"] for it in items] == ["t_arr", "I", "c", "x"]  # stage V first, then rung c
    assert items[0]["proposal"]["source"] == "assist-v" and items[0]["proposal"]["kind"] == "var"
    assert (
        items[0]["rows"][0]["name"] == "eq_0002"
        and r"\mathbf{t\_arr}" in items[0]["rows"][0]["latex"]
    )
    assert items[0]["families"] == ["I"] and items[0]["id"] == "p1::t_arr"
    assert [r["name"] for r in items[2]["rows"]] == ["eq_0001", "eq_0002"]  # c occurs in both rows
    limited = vocabgame.confirm_items(
        promoted_dir=promoted,
        declarations_dir=decl,
        promotion_path=promotion,
        prose_dir=tmp_path / "none",
        limit=0,
    )
    assert limited == []
    out = tmp_path / "confirm.html"
    vocabgame.build("confirm", items, out)
    data = json.loads(
        re.search(r'id="data">(.*?)</script>', out.read_text(encoding="utf-8"), re.S).group(1)
    )
    assert data["mode"] == "confirm" and data["items"][0]["proposal"]["line"].startswith(
        "%@ var t_arr"
    )
    assert data["var_domains"] == ["binary", "integer", "non_negative", "continuous"]


def test_export_contract_keys_are_the_ones_apply_decisions_reads(tmp_path: Path):
    # The page's decision object must carry exactly these keys (locked here so
    # corpusbuilder.vocab --apply-decisions and the page never drift apart).
    html = vocabgame.TEMPLATE
    for key in (
        "id",
        "paper_key",
        "name",
        "verdict",
        "kind",
        "shape",
        "domain",
        "pkind",
        "role",
        "desc",
        "proposal",
        "changed",
    ):
        assert f"{key}:" in html or f"{key} =" in html, key
    assert '"vocab-decisions-1"' in html and 'labeller: "human"' in html
