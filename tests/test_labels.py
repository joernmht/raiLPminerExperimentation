"""corpusbuilder.labels — standard label words and the scan for additions."""

from __future__ import annotations

from corpusbuilder import labels
from corpusbuilder.indices import normalise


def test_script_tokens_and_standard_membership() -> None:
    toks = labels.script_tokens(
        normalise(r"t_{end}^{max} + x_{i,j} + N_{s t} + c^{\text{arr}}_{k} + y_{foo}")
    )
    assert toks == ["end", "max", "st", "arr", "foo"]
    assert (
        labels.is_standard_label("End")
        and labels.is_standard_label("st")
        and not labels.is_standard_label("foo")
    )


def test_report_splits_standard_and_unlisted() -> None:
    report = {
        "standard_list_size": 3,
        "tokens_seen": 2,
        "standard": {"end": 5},
        "unlisted": {"foo": 2},
        "unlisted_papers": {"foo": 1},
    }
    md = labels.render_report_md(report)
    assert "end (5)" in md and "| foo | 2 | 1 |" in md
