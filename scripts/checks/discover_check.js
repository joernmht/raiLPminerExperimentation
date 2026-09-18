// Discovery-run page checks (jsdom), v3 (game layout): three fixed bands, rounds and queue, letter
// decisions with family chips and formula walking, role decisions with auto-advance, tapping in the
// text, citing a selection, export v2 / import v1+v2, worklist page.
// Usage: NODE_PATH=<scratchpad>/node_modules node scripts/checks/discover_check.js [paper.html] [index.html]
"use strict";
const fs = require("fs");
const { JSDOM } = require("jsdom");
const pagePath = process.argv[2] || "corpus/review/discover/10.1016_j.trb.2019.02.015.html";
const indexPath = process.argv[3] || "corpus/review/discover.html";
let passed = 0, failed = 0;
function ok(cond, msg) { if (cond) { passed++; } else { failed++; console.log("FAIL: " + msg); } }
const sleep = ms => new Promise(r => setTimeout(r, ms));
function boot(path) {
  const html = fs.readFileSync(path, "utf8");
  const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/" + path.split("/").pop() });
  const w = dom.window; w.URL.createObjectURL = () => "blob:x"; w.confirm = () => true; w.prompt = () => "ZZ";
  return { w, d: w.document, html };
}
(async () => {
  const { w, d } = boot(pagePath);
  const D = JSON.parse(d.getElementById("data").textContent);
  const X = w.__discover;
  ok(d.getElementById("top") && d.getElementById("mid") && d.getElementById("bottom"), "layout: three bands");
  ok(/grid-template-rows:auto 1fr auto/.test(d.querySelector("style").textContent), "layout: fixed top and bottom, scrolling middle");
  ok(d.querySelectorAll("#rounds button").length === 4, "rounds: four round tabs");
  // indices round first, candidates first
  ok(X.S().round === "indices" && X.current() === X.Q.indices[X.pos()], "queue: starts in the indices round at the first open letter");
  const v = l => D.indices.letters[l].verdict;
  const order = X.Q.indices.map(v);
  const firstIndex = order.indexOf("index"), lastCand = order.lastIndexOf("candidate");
  ok(firstIndex < 0 || lastCand < 0 || lastCand < firstIndex, "queue: candidates come before confirmed letters");
  const letter = X.current();
  ok(d.getElementById("item").textContent.includes("index letter") && d.querySelector("#item .big").textContent.includes(letter), "top: shows the letter and its position in the round");
  ok(d.querySelector('#decide button[data-v="index"]') && d.querySelector('#decide button[data-v="not"]') && d.querySelectorAll("#decide button.fam").length > 0, "bottom: ✓ ✗ ? and family chips");
  const carrying = X.rowsWith(letter);
  if (carrying.length) {
    ok(d.querySelector("#mid .cur") && carrying.includes(d.querySelector("#mid .cur").dataset.id), "text: a formula carrying the letter is lit");
    const before = d.querySelector("#mid .cur").dataset.id;
    ok(!d.getElementById("navs").classList.contains("hidden") && /formula 1 of \d+/.test(d.getElementById("walkbox").textContent), "walk: round navigators and the counter box are shown over the text");
    d.getElementById("navR").click();
    ok(carrying.length === 1 || d.querySelector("#mid .cur").dataset.id !== before, "walk: ▸ moves to the next formula carrying the letter");
    ok(carrying.length === 1 || /formula 2 of/.test(d.getElementById("walkbox").textContent), "walk: the counter box follows");
  }
  // decide with a family chip → auto-advance
  const chip = d.querySelector("#decide button.fam[data-fam]:not([data-fam='…'])");
  const fam = chip ? chip.dataset.fam : null;
  if (chip) chip.click(); else d.querySelector('#decide button[data-v="index"]').click();
  ok(X.S().indices[letter] && X.S().indices[letter].verdict === "index" && (!fam || X.S().indices[letter].family === fam), "decide: family chip records index + family");
  await sleep(200);
  ok(X.current() !== letter && X.isDone("indices", letter), "flow: auto-advances to the next open letter");
  const lbl = X.current();
  d.querySelector('#decide button[data-v="label"]').click();
  ok(X.S().indices[lbl] && X.S().indices[lbl].verdict === "label", "decide: label records that the letter is part of a name");
  await sleep(200);
  d.querySelector('#decide button[data-v="not"]').click();
  ok(X.S().indices[X.Q.indices.find(l => X.S().indices[l] && X.S().indices[l].verdict === "not")], "decide: ✗ records not-an-index");
  await sleep(200);
  ok(d.getElementById("rounds").textContent.includes("3/" + X.Q.indices.length), "rounds: counter shows three decided letters");
  // label proposals: a glued letter offers its run; the menu offers any word
  const glued = X.Q.indices.find(l => Object.keys(D.indices.letters[l].runs || {}).length);
  if (glued) { X.goTo(X.Q.indices.indexOf(glued)); const lb = d.querySelector("#decide button.lab"); ok(!!lb && d.getElementById("item").textContent.includes("written glued as"), "labels: glued letter shows its word and a proposal chip"); if (lb) { lb.click(); ok(X.S().labels[lb.dataset.lab] === true, "labels: chip records the proposal"); } }
  w.prompt = () => "foo"; d.getElementById("labelBtn").click(); ok(X.S().labels.foo === true, "labels: menu proposes any word");
  w.prompt = () => "ZZ";
  // definitions round
  X.setRound("defs");
  const defId = X.current();
  ok(defId && X.S().round === "defs" && d.getElementById("item").textContent.includes("definition candidate"), "defs: first open definition candidate on top");
  ok(d.querySelector("#item .span") || d.getElementById("item").textContent.includes("no defining sentence"), "defs: proposed span shown on top");
  ok(d.querySelector('#mid .chip.cur[data-id="' + defId + '"]'), "text: the candidate chip is lit");
  const sp = X.spanOf(defId);
  if (sp) ok(d.querySelector('p.para[data-i="' + sp.para + '"] mark.defspan'), "text: the defining sentence is marked");
  const rb = d.querySelectorAll("#decide button.dec[data-role]");
  ok(rb.length === 6 && d.querySelector('#decide button.dec.glow[data-role="definition"]'), "bottom: six role buttons, the proposal glows");
  d.querySelector('#decide button.dec[data-role="mention"]').click();
  ok(X.S().roles[defId] && X.S().roles[defId].role === "mention", "decide: role button records the role");
  await sleep(200);
  ok(X.current() !== defId, "flow: auto-advances after a role decision");
  // tap in the text (ad hoc)
  const disp = d.querySelector("#mid .disp[data-id]");
  disp.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  ok(d.getElementById("item").textContent.includes("tapped in the text") && d.querySelectorAll("#decide button.dec[data-role]").length === 6, "tap: a tapped element becomes the item with role buttons");
  d.querySelector('#decide button.dec[data-role="other"]').click();
  ok(X.S().roles[disp.dataset.id].role === "other" && !d.getElementById("item").textContent.includes("tapped in the text"), "tap: decision recorded and the queue resumes");
  // cite a selection for the current definition candidate
  const cur = X.current();
  const csp = X.spanOf(cur);
  const para = d.querySelector('p.para[data-i="' + (csp ? csp.para : D.maths[cur].block) + '"]');
  const tn = Array.from(para.childNodes).find(n => n.nodeType === 3 && n.nodeValue.trim().length > 12);
  if (tn) {
    const r = d.createRange(); r.setStart(tn, 1); r.setEnd(tn, Math.min(tn.nodeValue.length, 12));
    const sel = w.getSelection(); sel.removeAllRanges(); sel.addRange(r);
    const info = X.selectionInfo();
    ok(info && info.text === X.plainOf(D.paras.find(p => p.i === info.para)).slice(info.start, info.end), "selection: maps to plain offsets");
    d.getElementById("useSel").click();
    ok(X.S().roles[cur] && X.S().roles[cur].span && X.S().roles[cur].span.source === "human", "selection: cite records a human span");
  } else ok(false, "selection: no text node to select");
  // families round
  X.setRound("families");
  const famName = X.current();
  if (famName) { d.querySelector('#decide button[data-fv="family"]').click(); ok(X.S().families[famName] && X.S().families[famName].verdict === "family", "families: ✓ records the verdict"); await sleep(200); }
  // export / import
  const p = X.exportPayload();
  ok(p.schema_version === "discover-decisions-2" && p.roles[defId].role === "mention" && p.indices[letter].verdict === "index" && p.roles[cur].span.source === "human" && p.label_proposals.includes("foo"), "export: v2 carries roles, spans, letters, families, label proposals");
  ok(X.importJSON(JSON.stringify({ schema_version: "discover-decisions-1", paper_key: D.paper.key, formulas: { [disp.dataset.id]: "formula" }, spans: [{ para: 1, text: "x = 1" }], indices: {}, families: {} })) === 1 && X.roleOf(disp.dataset.id) === "formula" && X.S().marks.some(m => m.text === "x = 1"), "import: v1 accepted and converted");
  ok(X.importJSON(JSON.stringify({ schema_version: "discover-decisions-2", paper_key: "other" })) === 0, "import: refuses another paper's file");
  // keyboard: 1 = formula for the current definition candidate
  X.setRound("defs");
  const k = X.current();
  if (k) { d.dispatchEvent(new w.KeyboardEvent("keydown", { key: "1" })); ok(X.S().roles[k] && X.S().roles[k].role === "formula", "keys: 1 sets formula"); }
  {
    const { d: di } = boot(indexPath);
    ok(di.querySelectorAll("#papers tr").length > 200 && di.querySelector("#papers a").getAttribute("href").startsWith("discover/"), "index: worklist lists the papers with links");
  }
  console.log("discover_check: " + passed + " passed, " + failed + " failed");
  process.exit(failed ? 1 : 0);
})();
