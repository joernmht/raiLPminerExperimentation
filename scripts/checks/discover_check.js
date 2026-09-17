// Discovery-round page checks (jsdom), v2: two panes, a role for every element, citable definition
// spans (proposed + human via selection), letter walking, open items, export v2 / import v1+v2.
// Usage: NODE_PATH=<scratchpad>/node_modules node scripts/checks/discover_check.js [paper.html] [index.html]
"use strict";
const fs = require("fs");
const { JSDOM } = require("jsdom");
const pagePath = process.argv[2] || "corpus/review/discover/10.1016_j.trb.2019.02.015.html";
const indexPath = process.argv[3] || "corpus/review/discover.html";
let passed = 0, failed = 0;
function ok(cond, msg) { if (cond) { passed++; } else { failed++; console.log("FAIL: " + msg); } }
function boot(path) {
  const html = fs.readFileSync(path, "utf8");
  const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/" + path.split("/").pop() });
  const w = dom.window; w.URL.createObjectURL = () => "blob:x"; w.confirm = () => true; w.prompt = () => "ZZ";
  return { w, d: w.document, html };
}
{
  const { w, d } = boot(pagePath);
  const D = JSON.parse(d.getElementById("data").textContent);
  const X = w.__discover;
  ok(D.schema_version === "discover-page-2" && D.roles && Object.keys(D.roles).length === Object.keys(D.maths).length, "page: a proposed role for every element");
  ok(d.getElementById("textPane") && d.getElementById("sidePane"), "layout: text pane and side pane");
  const chips = d.querySelectorAll("#text .chip[data-id]");
  const inline = Object.values(D.maths).filter(m => m.where === "inline").length;
  ok(chips.length === inline, "text: one chip per inline element (" + chips.length + " vs " + inline + ")");
  ok(Array.from(chips).every(c => /\br-(formula|definition|index|domain|mention|other)\b/.test(c.className)), "text: every chip carries a role class");
  const disps = d.querySelectorAll("#text .disp[data-id]");
  ok(disps.length === Object.values(D.maths).filter(m => m.where === "display").length, "text: one block per display element");
  ok(!/⟨m-\d{4}⟩/.test(d.getElementById("tables").innerHTML), "tables: no unresolved placeholders");
  // select a definition chip: details shows the citable span and the paragraph is marked
  const defId = Object.keys(D.roles).sort().find(id => D.roles[id].role === "definition" && D.maths[id].where === "inline" && D.roles[id].span);
  ok(!!defId, "roles: a definition with a proposed span exists");
  X.select(defId, false);
  ok(X.S().sel === defId && d.querySelector('#text .chip[data-id="' + defId + '"]').classList.contains("sel"), "select: chip marked selected");
  const detail = d.getElementById("detail").textContent;
  ok(detail.includes(D.roles[defId].span.text.slice(0, 30)) && /paragraph \d+ · chars \d+–\d+/.test(detail), "details: verbatim span with a citation line");
  const para = d.querySelector('p.para[data-i="' + D.roles[defId].span.para + '"]');
  ok(para && para.querySelector("mark.defspan"), "text: the defining sentence is marked in the paragraph");
  // plain text of a paragraph matches the span offsets
  const plain = X.plainOf(D.paras.find(p => p.i === D.roles[defId].span.para));
  ok(plain.slice(D.roles[defId].span.start, D.roles[defId].span.end) === D.roles[defId].span.text, "spans: offsets address the verbatim text");
  // role decisions
  X.setRole(defId, "mention");
  ok(X.S().roles[defId].role === "mention" && d.querySelector('#text .chip[data-id="' + defId + '"]').classList.contains("r-mention"), "role: decision repaints the chip");
  X.setRole(defId, "mention");
  ok(!X.S().roles[defId], "role: same button again clears");
  X.setRole(defId, "definition");
  // human citation via selection (re-query: role decisions re-render the paragraph)
  const para2 = d.querySelector('p.para[data-i="' + D.roles[defId].span.para + '"]');
  const tn = Array.from(para2.childNodes).find(n => n.nodeType === 3 && n.nodeValue.trim().length > 12);
  if (tn && w.getSelection) {
    const r = d.createRange(); r.setStart(tn, 1); r.setEnd(tn, Math.min(tn.nodeValue.length, 12));
    const sel = w.getSelection(); sel.removeAllRanges(); sel.addRange(r);
    const info = X.selectionInfo();
    ok(info && info.text === plain.slice(info.start, info.end) && info.text.length >= 2, "selection: maps DOM selection to plain offsets");
    d.getElementById("useSel").click();
    const sp = X.spanOf(defId);
    ok(sp && sp.source === "human" && sp.text === info.text, "selection: citing records a human span");
    ok(d.getElementById("detail").textContent.includes("source human"), "details: shows the human source");
  } else ok(false, "selection: jsdom Selection unavailable");
  // display formula role + letters in details
  const dispId = disps[0].dataset.id;
  X.select(dispId, false);
  ok(d.getElementById("detail").querySelector("button.rb.on.r-formula"), "details: display formula proposed as formula");
  const lchip = d.querySelector("#detail button.lchip");
  if (lchip) { const l = lchip.dataset.l; delete X.S().indices[l]; lchip.click(); ok(X.S().indices[l] && X.S().indices[l].verdict === "index", "details: letter chip decides index"); }
  // letter walk from the indices pane
  const walk = d.querySelector('#letters button.walk[data-d="1"]');
  if (walk) { walk.click(); ok(X.S().sel !== null && d.querySelectorAll("#textPane .hasL").length > 0, "indices: ▸ walks to a formula carrying the letter and highlights all of them"); }
  const fam = d.querySelector("#detail button.fam, #letters input[data-fam]");
  ok(!!fam, "indices: family editing available");
  // open items
  const open = X.openItems();
  ok(d.getElementById("open").textContent.includes("Definition candidates (" + open.defs.length + ")"), "open items: counts definition candidates");
  // export v2
  const p = X.exportPayload();
  ok(p.schema_version === "discover-decisions-2" && p.paper_key === D.paper.key && p.roles[defId] && p.roles[defId].role === "definition" && p.roles[defId].span && p.roles[defId].span.source === "human", "export: v2 with resolved spans and sources");
  // import v1 (formulas) and v2
  ok(X.importJSON(JSON.stringify({ schema_version: "discover-decisions-1", paper_key: D.paper.key, formulas: { [dispId]: "not" }, spans: [{ para: 1, text: "x = 1" }], indices: {}, families: {} })) === 1, "import: v1 accepted");
  ok(X.roleOf(dispId) === "other" && X.S().marks.some(m => m.text === "x = 1"), "import: v1 formulas/not become roles, spans become marks");
  ok(X.importJSON(JSON.stringify({ schema_version: "discover-decisions-2", paper_key: D.paper.key, roles: { [dispId]: { role: "formula", span: null, source: "human" } }, marks: [], indices: {}, families: {} })) === 1 && X.roleOf(dispId) === "formula", "import: v2 last wins");
  ok(X.importJSON(JSON.stringify({ schema_version: "discover-decisions-2", paper_key: "other" })) === 0, "import: refuses another paper's file");
  // keyboard: j/k step
  d.dispatchEvent(new w.KeyboardEvent("keydown", { key: "j" }));
  ok(X.S().sel !== null, "keys: j steps to an element");
}
{
  const { d } = boot(indexPath);
  const rows = d.querySelectorAll("#papers tr");
  ok(rows.length > 200, "index: lists the papers (" + (rows.length - 1) + ")");
  ok(d.querySelector("#papers a").getAttribute("href").startsWith("discover/"), "index: links into the pages folder");
}
console.log("discover_check: " + passed + " passed, " + failed + " failed");
process.exit(failed ? 1 : 0);
