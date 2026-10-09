"""The labelling guide: one worked example per decision on the discovery pages.

Colleagues who label a few papers each (Nikola's crowdsourcing idea, meeting
2026-10-08, lab issue #15) meet the pages without the conventions the corpus
lab worked out with Joern. This page shows, for every decision case, a small
invented example in the shape the pages present it, the tap that is right, and
the one-sentence reason. The examples are made up on purpose: the guide quotes
no publisher text, so it can be handed to anyone.

Written to ``corpus/review/guide.html`` and linked from the worklist and from
every paper page. Deterministic: the same code writes the same page.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.discoverguide
"""

from __future__ import annotations

import html

from corpusbuilder.discovergame import _INDEX_STYLE, REVIEW
from corpusbuilder.vocabgame import LOGO

GUIDE_PAGE = REVIEW / "guide.html"

#: (case, what the page shows, the right tap, why, a trap to avoid or "")
#: Formulas between \( \) or \[ \] are typeset by MathJax.
CASES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "A letter bound by a sum or a quantifier",
        r"\[ \sum_{i \in I} x_{i,t} = 1 \qquad \forall t \in T \]"
        r"The page asks about \(i\) (and later about \(t\)).",
        "✓ index → <b>I</b> for \\(i\\); ✓ index → <b>T</b> for \\(t\\)",
        "The binder says which set the letter runs over. A ⏱ next to \\(t\\) marks the time index.",
        "",
    ),
    (
        "A letter whose set the notation table names",
        "Notation table: <i>“\\(f\\): train index, \\(f \\in F\\), \\(F\\) is the set of trains.”</i> "
        "A formula elsewhere writes \\( \\sum_{(e,f) \\in E} y_{e,f} \\).",
        "✓ index → <b>F</b>",
        "The set the paper states for the letter is its family, even when a binder pairs it with another set.",
        "Do not pick \\(E\\) because \\(f\\) appears inside \\((e,f) \\in E\\): that says the pair lives in \\(E\\).",
    ),
    (
        "A primed or decorated letter",
        r"\( x_{f'} \), \( \hat{f} \) in a paper where \(f \in F\).",
        "✓ index, then the chip <b>same as f → F</b>",
        "\\(f'\\) and \\(\\hat f\\) are other elements of the same set as \\(f\\).",
        "",
    ),
    (
        "A two-letter index name",
        r"\( t^{\mathrm{arr}}_{e} = t^{\mathrm{dep}}_{e'} \quad \text{if } st_e = st_{e'} \) "
        r"(\(st_e\): the station of event \(e\)); the page shows the name <b>st</b>.",
        "✓ index → its set (here: the set of stations)",
        "The page already joined the two letters because they are used together as one name.",
        "",
    ),
    (
        "Letters glued together",
        r"\( x_{ij} \) where both \(i\) and \(j\) are bound elsewhere; and \( t_{end} \) where no letter is.",
        "\\(x_{ij}\\): decide \\(i\\) and \\(j\\) each as ✓ index. \\(t_{end}\\): <b>label</b>",
        "\\(ij\\) is two indices without a comma; <i>end</i> is a word that names which \\(t\\).",
        "If the page offers “＋ end is a label word”, tap it: it teaches the page the word.",
    ),
    (
        "Superscripts that are part of a name",
        r"\( d^{+}_{i} \ge t_i - \bar t_i, \quad d^{-}_{i} \ge \bar t_i - t_i \) "
        r"and \( t^{\mathrm{arr}}_{i} \).",
        "<b>label</b> for any letter the page shows inside such a superscript",
        "\\(d^{+}\\) and \\(d^{-}\\) are two different variables (late, early); <i>arr</i> says which time.",
        "Only \\(i\\) is an index here.",
    ),
    (
        "Capital letters",
        r"\( t_{i,B} \), \( \psi_{r,P} \); and \( u_{j,|A|} \).",
        "\\(B\\), \\(P\\): <b>label</b>. \\(A\\) inside \\(|A|\\): <b>✗ not an index</b>",
        "A capital in a subscript usually names a variant. \\(|A|\\) is the size of the set \\(A\\), a fixed position.",
        "A capital is ✓ index only if the paper really runs it over a set.",
    ),
    (
        "A variable",
        r"Domain row: \( x_{ij} \in \{0,1\} \). The page asks about \(x\) (it sat in a subscript once).",
        "<b>✗ not an index</b> for \\(x\\). In the Formulas round the row itself is <b>domain</b>",
        "A decision variable is never an index; the domain row says what kind of variable it is.",
        "",
    ),
    (
        "A parameter",
        r"\( \sum_{i \in I} c_i x_i \le M \), with \(c_i\) a cost and \(M\) a large constant.",
        "<b>✗ not an index</b> for \\(c\\) and \\(M\\)",
        "Data the model is given (costs, durations, big-M) is a parameter.",
        "",
    ),
    (
        "A number set",
        r"\( Q \in \mathbb{R} \), \( z_k \in \mathbb{Z}_{+} \).",
        "<b>✗ not an index</b> (or <b>label</b> if the letter names a variant); never pick ℝ, ℕ or ℤ as a family",
        "A membership in ℝ, ℕ or ℤ says what values a quantity takes, not what it runs over.",
        "\\(\\mathcal{R}\\) (calligraphic) is different: that is a named set, e.g. of routes.",
    ),
    (
        "A row that is not a declaration",
        r"\( t_e - \bar t_e \ge 0 \qquad \forall e \in E \)",
        "In the Formulas round: <b>formula</b>, not <b>domain</b>",
        "Its left side is an expression (a difference), so it is a constraint. "
        "A domain row lists symbols on the left: \\(t_e \\ge 0\\).",
        "Same for \\(x_i \\ge b_i\\): a bound by another symbol is a constraint.",
    ),
    (
        "When you cannot tell",
        "The letter appears once, the paper never explains it.",
        "<b>? unsure</b>",
        "Unsure is a valid answer and is counted as such; a guess is worse than none.",
        "",
    ),
)

HOW = (
    ("Bands", "The item sits on top, the paper text in the middle, the buttons at the bottom."),
    (
        "Rounds",
        "Indices, Definitions, Formulas, Families: start with Indices, the others are optional.",
    ),
    (
        "◂ ▸ over the text",
        "Walk through the formulas that carry the letter, or switch to "
        "“◂▸ family …” to see every formula that uses its set.",
    ),
    ("◂ back", "Returns to the item before, also a decided one; your earlier verdict is lit."),
    (
        "Saving",
        "Every tap is stored on the server; you can stop at any time and continue on another device.",
    ),
)


def guide_html() -> str:
    cases = "\n".join(
        f'<section class="case"><h2>{i}. {html.escape(t)}</h2>'
        f'<div class="see"><span class="k">on the page</span>{see}</div>'
        f'<div class="tap"><span class="k">tap</span>{tap}</div>'
        f'<div class="why"><span class="k">why</span>{why}</div>'
        + (f'<div class="trap"><span class="k">careful</span>{trap}</div>' if trap else "")
        + "</section>"
        for i, (t, see, tap, why, trap) in enumerate(CASES, 1)
    )
    how = "\n".join(f"<li><b>{html.escape(a)}</b>: {html.escape(b)}</li>" for a, b in HOW)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Labelling guide</title>
<style>{_INDEX_STYLE}
.case{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:12px 0;box-shadow:var(--shadow)}}
.case h2{{font-size:17px;margin:0 0 8px}}.case div{{margin:6px 0;line-height:1.45}}
.k{{display:inline-block;min-width:84px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}}
.tap{{color:var(--accent)}}.trap{{color:var(--warn)}}
ul.how li{{margin:6px 0;line-height:1.4}}
</style>
<script>window.MathJax = {{tex: {{inlineMath: [["\\\\(", "\\\\)"]], displayMath: [["\\\\[", "\\\\]"]]}}}};</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js"></script>
</head><body><main>
<div class="brandlogo">{LOGO}</div>
<p><a href="discover.html">← worklist</a></p>
<h1>How to decide: one example per case</h1>
<p>You decide, for each letter the page found in a subscript or superscript, whether it is an
<b>index</b> (it runs over a set), a <b>label</b> (it is part of a name), or <b>not an index</b>
(a variable, a parameter, a set, a number). Pick the set for every index. The page proposes an answer;
it is right most of the time, and your job is to catch where it is not.</p>
<h2>The page</h2><ul class="how">{how}</ul>
{cases}
<p class="muted">Examples are invented for this guide. Questions: Joern Maurischat.</p>
</main></body></html>
"""


def main() -> int:
    GUIDE_PAGE.parent.mkdir(parents=True, exist_ok=True)
    GUIDE_PAGE.write_text(guide_html(), encoding="utf-8", newline="\n")
    print(f"wrote {GUIDE_PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
