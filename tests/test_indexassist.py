"""corpusbuilder.indexassist — the model is a gated second annotator; the router decides who sees a letter."""

from __future__ import annotations

import json
from pathlib import Path

from corpusbuilder import assist, discover, indexassist, indices
from tests.test_discover import _XML, _fake_convert


def _paper(tmp_path: Path, key: str = "p") -> tuple[Path, Path, dict]:
    rec = discover.discover_paper(_XML, key=key, convert=_fake_convert)
    rows = [(m["eq"], m["latex"]) for m in rec["maths"] if m["where"] == "display"]
    idx = indices.analyse(rows, rec, {"index": {}, "param": {}, "var": {}})
    idx["paper_key"] = key
    disc, ind = tmp_path / "disc", tmp_path / "ind"
    disc.mkdir(exist_ok=True)
    ind.mkdir(exist_ok=True)
    (disc / f"{key}.json").write_text(json.dumps(rec), encoding="utf-8", newline="\n")
    (ind / f"{key}.json").write_text(json.dumps(idx), encoding="utf-8", newline="\n")
    return disc, ind, idx


def test_validate_reply_gates_verdicts_and_families() -> None:
    reply = {
        "i": {"verdict": "index", "family": "I", "why": "bound by the sum"},
        "j": {"verdict": "index", "family": "Zeta", "why": "made up"},
        "k": {"verdict": "maybe", "family": "", "why": ""},
        "arr": {"verdict": "label", "family": "I", "why": "arrival"},
    }
    got, problems = indexassist.validate_reply(reply, ["i", "j", "k", "arr", "z"], {"I", "J"})
    assert got["i"] == {
        "verdict": "index",
        "family": "I",
        "why": "bound by the sum",
        "source": "model",
        "gated": False,
    }
    assert got["j"]["verdict"] == "index" and got["j"]["family"] == "" and got["j"]["gated"]
    assert got["k"]["verdict"] == "unsure" and got["k"]["gated"]
    assert got["arr"]["verdict"] == "label" and got["arr"]["family"] == ""  # a label has no family
    assert got["z"]["verdict"] == "unsure" and got["z"]["gated"]
    assert problems == [
        "j: family 'Zeta' is not a set the paper names",
        "k: verdict 'maybe'",
        "z: missing",
    ]


def test_route_accepts_only_agreement() -> None:
    rule_index = {"verdict": "index", "family": "I"}
    rule_alias = {"verdict": "alias", "family": "I"}
    rule_nofam = {"verdict": "index", "family": None}
    rule_cand = {"verdict": "candidate", "family": None}
    rule_label = {"verdict": "label", "family": None}

    def m(v: str, f: str = "") -> dict:
        return {"verdict": v, "family": f}

    assert indexassist.route(rule_index, m("index", "I"))[0] == "auto"
    assert indexassist.route(rule_alias, m("index", ""))[0] == "auto"
    assert indexassist.route(rule_alias, m("index", ""))[2] == {
        "verdict": "index",
        "family": "I",
        "source": "assist",
    }
    assert indexassist.route(rule_index, m("index", "J"))[:2] == ("human", "family disagreement")
    assert indexassist.route(rule_index, m("label"))[:2] == ("human", "bind disagreement")
    assert indexassist.route(rule_index, m("unsure"))[:2] == ("human", "model unsure")
    assert indexassist.route(rule_nofam, m("index", "I"))[:2] == ("human", "rule has no family")
    assert indexassist.route(rule_cand, m("label")) == (
        "auto",
        "both do not bind",
        {"verdict": "label", "family": "", "source": "assist"},
    )
    assert indexassist.route(rule_label, m("not"))[2]["verdict"] == "not"
    assert indexassist.route(rule_cand, m("index", "I"))[:2] == ("human", "bind disagreement")


def test_load_labels_reads_exports_and_page_state_later_wins(tmp_path: Path) -> None:
    export = {
        "schema_version": "discover-decisions-2",
        "paper_key": "p",
        "roles": {},
        "marks": [],
        "indices": {
            "i": {"verdict": "index", "family": "I"},
            "q": {"verdict": "unsure", "family": ""},
        },
        "families": {},
        "label_proposals": [],
    }
    state = {
        "roles": {},
        "marks": [],
        "indices": {"i": {"verdict": "not", "family": ""}, "j": {"verdict": "label", "family": ""}},
        "saved": "20260919T195439Z",
    }
    (tmp_path / "a.json").write_text(json.dumps(export), encoding="utf-8", newline="\n")
    (tmp_path / "p.json").write_text(json.dumps(state), encoding="utf-8", newline="\n")
    (tmp_path / "junk.json").write_text("not json", encoding="utf-8", newline="\n")
    labels = indexassist.load_labels(
        [tmp_path / "a.json", tmp_path / "p.json", tmp_path / "junk.json"]
    )
    assert labels == {
        "p": {
            "i": {"verdict": "not", "family": ""},
            "q": {"verdict": "unsure", "family": ""},
            "j": {"verdict": "label", "family": ""},
        }
    }


def test_run_paper_hides_the_rule_from_the_model_and_routes(tmp_path: Path) -> None:
    disc, ind, idx = _paper(tmp_path)
    letters = idx["letters"]
    assert letters, "the fixture paper must carry index letters"
    seen: list[dict] = []

    def chat(payload: dict) -> str:
        seen.append(payload)
        user = json.loads(payload["messages"][1]["content"])
        out = {}
        for ev in user["paper"]["letters"]:
            assert "proposed" not in ev and "rule" not in ev  # independence by construction
            fam = next(iter(ev["bound_to"]), "")
            out[ev["letter"]] = (
                {"verdict": "index", "family": fam, "why": "bound"}
                if ev["bound_to"]
                else {"verdict": "label", "family": "", "why": "a word"}
            )
        return json.dumps(out)

    ws = assist.Workspace(assist=tmp_path / "assist")
    out = indexassist.run_paper(
        ws, "p", [], discovery_dir=disc, indices_dir=ind, out_dir=tmp_path / "out", chat=chat
    )
    assert out["schema_version"] == "indexassist-1" and len(seen) == 1
    assert set(out["letters"]) == set(letters)
    assert (tmp_path / "out" / "p.json").exists()
    for r in out["letters"].values():
        assert r["route"] in ("auto", "human") and r["model"]["source"] == "model"
        if r["route"] == "auto":
            assert r["accepted"]["source"] == "assist"
    assert out["summary"]["auto"] + out["summary"]["human"] == len(letters)


def test_evaluate_leave_one_out_tallies_against_the_human(tmp_path: Path) -> None:
    disc, ind, idx = _paper(tmp_path, "p")
    _paper(tmp_path, "q")
    names = sorted(idx["letters"])
    bound = [n for n in names if idx["letters"][n]["verdict"] in indexassist.RULE_BINDS]
    assert bound, "the fixture must bind at least one letter"
    first = bound[0]
    labels = {
        "p": {
            n: {
                "verdict": "index" if n in bound else "label",
                "family": idx["letters"][n].get("family") or "",
            }
            for n in names
        },
        "q": {
            n: {
                "verdict": "index" if n in bound else "label",
                "family": idx["letters"][n].get("family") or "",
            }
            for n in names
        },
    }
    labels["p"][first] = {"verdict": "not", "family": ""}  # the human overrules the rule once
    calls: list[list[str]] = []

    def chat(payload: dict) -> str:
        user = json.loads(payload["messages"][1]["content"])
        calls.append([ex["paper"] for ex in user["examples"]])
        assert all(ex["paper"] != user["paper"]["paper"] for ex in user["examples"])
        assert all("expert" in ev for ex in user["examples"] for ev in ex["letters"])
        return json.dumps(
            {
                ev["letter"]: {"verdict": "unsure", "family": "", "why": ""}
                if ev["letter"] == first
                else {
                    "verdict": "index" if ev["bound_to"] else "label",
                    "family": next(iter(ev["bound_to"]), ""),
                    "why": "",
                }
                for ev in user["paper"]["letters"]
            }
        )

    ws = assist.Workspace(assist=tmp_path / "assist")
    report = indexassist.evaluate(
        ws, labels, discovery_dir=disc, indices_dir=ind, out_dir=tmp_path / "loo", chat=chat
    )
    assert report["papers"] == ["p", "q"] and calls == [["q"], ["p"]]
    t = report["totals"]
    assert t["n"] == 2 * len(names)
    assert (
        t["rule_wrong"] == 1 and t["rule_wrong_caught"] == 1
    )  # the overruled letter went to the human
    assert t["auto"] + t["human"] == t["n"] and t["auto_ok"] <= t["auto"]
    assert set(report["per_class"]) == {x["class"] for x in report["per_letter"]}
    md = indexassist.report_markdown(report)
    assert "leave-one-paper-out" in md and f"`{first}`" in md
