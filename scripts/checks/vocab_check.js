// Vocabulary-round page checks (jsdom): blind page is blind, decisions export per contract,
// confirm page pre-fills and records changes, import merges by id.
// Usage: NODE_PATH=<scratchpad>/node_modules node scripts/checks/vocab_check.js [blind.html] [confirm.html]
"use strict";
const fs = require("fs");
const { JSDOM } = require("jsdom");
const blindPath = process.argv[2] || "corpus/review/vocab_blind.html";
const confirmPath = process.argv[3] || "corpus/review/vocab_confirm.html";
let passed = 0, failed = 0;
function ok(cond, msg) { if (cond) { passed++; } else { failed++; console.log("FAIL: " + msg); } }
function boot(path) {
  const html = fs.readFileSync(path, "utf8");
  const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/" + path.split("/").pop() });
  const w = dom.window;
  w.__mjfail = 1; // no MathJax in jsdom: rows fall back to raw text, which is fine here
  w.URL.createObjectURL = () => "blob:x";
  return { w, d: w.document, html };
}
// ---------------- blind ----------------
{
  const { w, d, html } = boot(blindPath);
  const data = JSON.parse(d.getElementById("data").textContent);
  ok(data.mode === "blind" && data.items.length > 0, "blind: data block has items");
  ok(!/kind_guess/.test(html), "blind: no kind_guess anywhere in the page");
  ok(data.items.every(it => it.proposal === null), "blind: no proposals embedded");
  const first = data.items[0];
  ok(d.getElementById("symbol").textContent === first.name, "blind: first item shows the symbol name");
  ok(d.getElementById("paperKey").textContent === first.paper_key, "blind: first item shows the paper key");
  ok(d.querySelectorAll("#rows .row").length === first.rows.length && first.rows.length > 0, "blind: rows rendered");
  ok(d.querySelectorAll("#familyChips .chip").length === first.families.length, "blind: family chips rendered");
  ok(d.querySelectorAll("#kindBtns button.on").length === 0, "blind: no kind pre-selected");
  ok(d.getElementById("proposalBox").classList.contains("hidden"), "blind: proposal box hidden");
  ok(d.getElementById("confirmBtn").classList.contains("hidden"), "blind: confirm button hidden");
  // label: var + first family + a domain + Enter
  d.querySelector('#kindBtns button[data-k="var"]').click();
  ok(!d.getElementById("shapeBlock").classList.contains("hidden") && !d.getElementById("domainBlock").classList.contains("hidden"), "blind: shape + domain blocks appear for var");
  const chip = d.querySelector("#familyChips .chip");
  if (chip) chip.click();
  d.querySelector('#domainBtns button[data-d="continuous"]').click();
  d.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  const payload = w.__vocab.exportPayload();
  ok(payload.schema_version === "vocab-decisions-1" && payload.mode === "blind" && payload.labeller === "human", "blind: export header per contract");
  const dec = payload.decisions[0];
  ok(dec && dec.id === first.id && dec.verdict === "declare" && dec.kind === "var", "blind: decision verdict/kind");
  ok(JSON.stringify(dec.shape) === JSON.stringify(chip ? [first.families[0]] : []), "blind: shape = tapped family");
  ok(dec.domain === "continuous" && dec.pkind === null && dec.role === "primary", "blind: domain set, pkind null, role default");
  ok(dec.proposal === null && dec.changed === false, "blind: proposal null, changed false");
  ok(d.getElementById("symbol").textContent === data.items[1].name, "blind: Enter advanced to the second item");
  ok(d.getElementById("counter").textContent.indexOf("item 2 /") === 0, "blind: counter advanced");
  // persisted
  const stored = JSON.parse(w.localStorage.getItem("vocab:state:v1:blind"));
  ok(stored && stored.decisions[first.id] && stored.decisions[first.id].kind === "var", "blind: decision persisted in localStorage");
  // not-a-symbol path
  d.querySelector('#kindBtns button[data-k="not_a_symbol"]').click();
  d.getElementById("nextBtn").click();
  const p2 = w.__vocab.exportPayload();
  ok(p2.decisions.length === 2 && p2.decisions[1].verdict === "not_a_symbol" && p2.decisions[1].kind === null, "blind: not-a-symbol decision");
  // import merges by id (last wins)
  const n = w.__vocab.importJSON(JSON.stringify({ schema_version: "vocab-decisions-1", mode: "blind", decisions: [{ id: first.id, paper_key: first.paper_key, name: first.name, verdict: "declare", kind: "param", shape: [], domain: null, pkind: "scalar", role: null, desc: "", proposal: null, changed: false }] }));
  ok(n === 1 && w.__vocab.exportPayload().decisions.find(x => x.id === first.id).kind === "param", "blind: import merged by id, last wins");
}
// ---------------- confirm ----------------
{
  const { w, d } = boot(confirmPath);
  const data = JSON.parse(d.getElementById("data").textContent);
  ok(data.mode === "confirm" && data.items.length > 0, "confirm: data block has items");
  const first = data.items[0];
  ok(first.proposal && first.proposal.line.startsWith("%@"), "confirm: first item carries a proposal line");
  ok(!d.getElementById("proposalBox").classList.contains("hidden") && d.getElementById("proposalBox").textContent === first.proposal.line, "confirm: proposal shown");
  ok(d.querySelector("#kindBtns button.on") && d.querySelector("#kindBtns button.on").dataset.k === first.proposal.kind, "confirm: kind pre-selected from the proposal");
  // stage V proposals come before stage c within a paper
  const byPaper = {};
  for (const it of data.items) (byPaper[it.paper_key] = byPaper[it.paper_key] || []).push(it.proposal.source);
  ok(Object.values(byPaper).every(srcs => { let seenC = false; for (const s of srcs) { if (s === "assist-c") seenC = true; if (s === "assist-v" && seenC) return false; } return true; }), "confirm: assist-v before assist-c within every paper");
  d.getElementById("confirmBtn").click();
  let payload = w.__vocab.exportPayload();
  ok(payload.decisions.length === 1 && payload.decisions[0].changed === false && payload.decisions[0].proposal.line === first.proposal.line, "confirm: one-tap confirm exports changed=false with the proposal");
  // second item: edit the kind -> changed=true
  const second = data.items[1];
  const other = second.proposal.kind === "param" ? "var" : "param";
  d.querySelector('#kindBtns button[data-k="' + other + '"]').click();
  if (other === "var") d.querySelector('#domainBtns button[data-d="binary"]').click(); else d.querySelector('#pkindBtns button[data-p="scalar"]').click();
  d.getElementById("nextBtn").click();
  payload = w.__vocab.exportPayload();
  ok(payload.decisions.length === 2 && payload.decisions[1].changed === true && payload.decisions[1].kind === other, "confirm: an edit exports changed=true");
}
console.log(`[vocab] ${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
