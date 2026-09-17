// Discovery-round page checks (jsdom): text view marks every element, chip cycling, display reject,
// index verdicts + family edits, hand-marked spans, export contract, import merge, index page rows.
// Usage: NODE_PATH=<scratchpad>/node_modules node scripts/checks/discover_check.js [paper.html] [index.html]
"use strict";
const fs = require("fs");
const { JSDOM } = require("jsdom");
const pagePath = process.argv[2] || "corpus/review/discover/10.1016_j.apm.2017.07.030.html";
const indexPath = process.argv[3] || "corpus/review/discover.html";
let passed = 0, failed = 0;
function ok(cond, msg) { if (cond) { passed++; } else { failed++; console.log("FAIL: " + msg); } }
function boot(path) {
  const html = fs.readFileSync(path, "utf8");
  const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/" + path.split("/").pop() });
  const w = dom.window;
  w.URL.createObjectURL = () => "blob:x";
  w.confirm = () => true;
  return { w, d: w.document, html };
}
{
  const { w, d } = boot(pagePath);
  const D = JSON.parse(d.getElementById("data").textContent);
  ok(D.schema_version === "discover-page-1" && D.paper.key, "page: data block");
  const chips = d.querySelectorAll("#text .chip[data-id]");
  const inline = Object.values(D.maths).filter(m => m.where === "inline").length;
  ok(chips.length === inline, "text: one chip per inline element (" + chips.length + " vs " + inline + ")");
  const disps = d.querySelectorAll("#text .disp[data-key]");
  const display = Object.values(D.maths).filter(m => m.where === "display").length;
  ok(disps.length === display, "text: one block per display element (" + disps.length + " vs " + display + ")");
  ok(d.querySelectorAll("#text h4.sec").length > 0, "text: section headings present");
  ok(!/⟨m-\d{4}⟩/.test(d.getElementById("tables").innerHTML), "tables: no unresolved placeholders");
  // chip cycling on a statement
  const stmt = Array.from(chips).find(c => c.classList.contains("c-statement"));
  ok(!!stmt, "text: a statement chip exists");
  if (stmt) {
    stmt.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
    ok(w.__discover.S().formulas[stmt.dataset.id] === "formula" && stmt.classList.contains("f-formula"), "chip: first click marks formula");
    stmt.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
    ok(w.__discover.S().formulas[stmt.dataset.id] === "not" && stmt.classList.contains("f-not"), "chip: second click marks not");
    stmt.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
    ok(w.__discover.S().formulas[stmt.dataset.id] === undefined, "chip: third click clears");
    // statements tab mirrors the chip
    const row = d.querySelector('#stmts .stmt[data-id="' + stmt.dataset.id + '"]');
    ok(!!row, "statements: the chip has a row");
    if (row) { row.querySelector('button[data-v="formula"]').click(); ok(w.__discover.S().formulas[stmt.dataset.id] === "formula" && stmt.classList.contains("f-formula"), "statements: button marks the chip too"); }
  }
  // symbol chips are inert
  const sym = Array.from(chips).find(c => c.classList.contains("c-symbol"));
  if (sym) { sym.dispatchEvent(new w.MouseEvent("click", { bubbles: true })); ok(w.__discover.S().formulas[sym.dataset.id] === undefined, "chip: symbol mention is inert"); }
  // display reject
  const disp = disps[0];
  disp.querySelector("button.dtoggle").click();
  ok(w.__discover.S().formulas[disp.dataset.key] === "not" && disp.classList.contains("f-not"), "display: reject marks not");
  disp.querySelector("button.dtoggle").click();
  ok(w.__discover.S().formulas[disp.dataset.key] === undefined, "display: reject again clears");
  // index verdicts
  const letterRows = d.querySelectorAll("#letters tr[data-n]");
  ok(letterRows.length === Object.keys(D.indices.letters).length && letterRows.length > 0, "indices: one row per letter");
  const first = letterRows[0];
  first.querySelector('button[data-v="index"]').click();
  const n0 = first.dataset.n;
  ok(w.__discover.S().indices[n0] && w.__discover.S().indices[n0].verdict === "index", "indices: confirm records the verdict");
  const inp = d.querySelector('#letters tr[data-n="' + n0 + '"] input[data-fam]');
  inp.value = "ZZ"; inp.dispatchEvent(new w.Event("change", { bubbles: true }));
  ok(w.__discover.S().indices[n0].family === "ZZ", "indices: editing the family is recorded");
  const famRows = d.querySelectorAll("#families tr[data-f]");
  ok(famRows.length === Object.keys(D.indices.families).length, "indices: one row per family");
  if (famRows.length) { famRows[0].querySelector('button[data-v="not"]').click(); ok(w.__discover.S().families[famRows[0].dataset.f].verdict === "not", "families: reject recorded"); }
  // rows jump to the text
  const ref = d.querySelector("#letters .rowref");
  if (ref) { ref.click(); ok(!d.getElementById("tab-text").classList.contains("hidden"), "indices: row reference jumps to the text tab"); }
  // hand-marked span
  w.__discover.S().spans.push({ para: 3, text: "x(i,j) = 1", latex: "x_{ij} = 1" });
  // export contract
  const p = w.__discover.exportPayload();
  ok(p.schema_version === "discover-decisions-1" && p.paper_key === D.paper.key && p.labeller === "human", "export: schema, key, labeller");
  ok(p.indices[n0].family === "ZZ" && p.spans.length === 1 && p.formulas[stmt ? stmt.dataset.id : ""] === "formula", "export: carries formulas, spans, indices");
  // import merges (last wins)
  const r = w.__discover.importJSON(JSON.stringify({ schema_version: "discover-decisions-1", paper_key: D.paper.key, formulas: { [stmt ? stmt.dataset.id : "m-0001"]: "not" }, spans: [{ para: 3, text: "x(i,j) = 1", latex: "x_{ij} = 1" }, { para: 5, text: "y = 2" }], indices: { [n0]: { verdict: "not", family: "" } }, families: {} }));
  ok(r === 1 && w.__discover.S().formulas[stmt ? stmt.dataset.id : "m-0001"] === "not" && w.__discover.S().indices[n0].verdict === "not", "import: last wins on formulas and indices");
  ok(w.__discover.S().spans.length === 2, "import: spans dedupe by (para, text)");
  ok(w.__discover.importJSON(JSON.stringify({ schema_version: "discover-decisions-1", paper_key: "other" })) === 0, "import: refuses another paper's file");
  // persistence
  const { w: w2 } = boot(pagePath);
  ok(Object.keys(w2.__discover.S().formulas).length === 0, "fresh window: no bleed without storage (jsdom storage is per window)");
}
{
  const { d } = boot(indexPath);
  const rows = d.querySelectorAll("#papers tr");
  ok(rows.length > 200, "index: lists the papers (" + (rows.length - 1) + ")");
  ok(d.querySelector("#papers a").getAttribute("href").startsWith("discover/"), "index: links into the pages folder");
}
console.log("discover_check: " + passed + " passed, " + failed + " failed");
process.exit(failed ? 1 : 0);
