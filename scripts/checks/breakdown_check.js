// Breakdown-oriented review: objective first, then least broken down, with the
// whole analysis re-run after every step. Run:
//   NODE_PATH=<scratchpad>/node_modules node scripts/checks/breakdown_check.js [game.html]
const fs = require('fs');
const { JSDOM } = require('jsdom');
const path = process.argv[2] || '/home/joern/raiLPminerExperimentation/docs/game.html';
const html = fs.readFileSync(path, 'utf8');

let pass = 0, fail = 0;
const ok = (cond, msg) => { if (cond) { pass++; } else { fail++; console.log('FAIL:', msg); } };

// ---- payload ----------------------------------------------------------------
const m = html.match(/<script id="corpus-data" type="application\/json">([\s\S]*?)<\/script>/);
ok(m, 'corpus-data payload present');
const data = JSON.parse(m[1]);

let withTail = 0, withEv = 0;
for (const p of data.papers) {
  if (p.ev && Object.keys(p.ev).length) withEv++;
  for (const f of p.f) if (f[12]) {
    withTail++;
    ok(Array.isArray(f[12]), 'slot 12 is a symbol-name list for ' + f[0]);
    const shown = new Set((f[5] || []).map(s => s[0]));
    ok(f[12].every(nm => !shown.has(nm)), 'slot 12 holds only symbols beyond the display list');
  }
}
ok(withEv > 0, 'at least one paper carries deterministic evidence (p.ev)');
console.log('  papers with evidence:', withEv, '· formulas with a symbol tail:', withTail);
for (const p of data.papers)
  for (const nm in (p.ev || {}))
    ok('pvi'.includes(p.ev[nm]), 'evidence code is p/v/i for ' + nm);

// ---- DOM --------------------------------------------------------------------
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
  url: 'https://railpmining.joernmaurischat.de/game.html' });
const w = dom.window, d = w.document;
w.HTMLElement.prototype.scrollIntoView = function () {};

for (const fn of ['betaOf', 'baseOrder', 'applyRowOrder', 'logStep', 'symsOf', 'kindOf', 'paperBeta'])
  ok(typeof w[fn] === 'function', fn + ' is a page global');

const RAW = w.eval('RAW');
const p = RAW.papers.find(pp => pp.f.some(f => f[9] === 1) && pp.f.length > 3) || RAW.papers[0];

// beta counts the tail, not just the displayed twelve
const long = RAW.papers.flatMap(pp => pp.f.filter(f => f[12])).sort((a, b) => b[12].length - a[12].length)[0];
if (long) {
  const owner = RAW.papers.find(pp => pp.f.includes(long));
  ok(w.symsOf(long).length === (long[5] || []).length + long[12].length,
     'symsOf unions the display list and the tail');
  ok(w.symsOf(long).length > 12, 'a long formula really does exceed the display list');
  ok(w.betaOf(owner, long) < 1 || w.symsOf(long).every(nm => w.kindOf(owner, nm)),
     'beta below 1 unless every symbol including the tail is typed');
} else {
  console.log('  (no formula in this build exceeds 12 symbols — tail checks skipped)');
}

// ---- ordering ---------------------------------------------------------------
w.go('run');
const order = w.baseOrder(p);
ok(order.length === p.f.length, 'baseOrder returns every formula exactly once');
ok(new Set(order).size === p.f.length, 'baseOrder has no duplicates');

const byId = {}; p.f.forEach(f => { byId[f[0]] = f; });
const objIds = p.f.filter(f => f[9] === 1).map(f => f[0]);
ok(objIds.includes(order[0]), 'the objective leads the review order');

// After the objective, beta is non-decreasing. A similarity group travels as
// one block positioned by its leading member, so the run is over block heads:
// inside a block the members are judged together, not sorted against the world.
const gOf = {}; (p.g || []).forEach((ids, gi) => ids.forEach(id => { gOf[id] = gi; }));
const undecided = order.filter(id => !((w.eval('S').dec[p.k] || {})[id]));
const heads = [];
let lastG = null;
for (const id of undecided.filter(x => !objIds.includes(x))) {
  const g = gOf[id];
  if (g === undefined || g !== lastG) heads.push(id);
  lastG = g;
}
let monotone = true;
for (let i = 1; i < heads.length; i++)
  if (w.betaOf(p, byId[heads[i]]) < w.betaOf(p, byId[heads[i - 1]]) - 1e-9) monotone = false;
ok(monotone, 'undecided rows run least-broken-down first');
ok(heads.length > 1, 'the ordering check saw more than one block');

// a group's members really are adjacent
let adjacent = true;
for (let gi = 0; gi < (p.g || []).length; gi++) {
  const at = p.g[gi].map(id => order.indexOf(id)).sort((a, b) => a - b);
  for (let i = 1; i < at.length; i++) if (at[i] !== at[i - 1] + 1) adjacent = false;
}
ok(adjacent, 'similarity groups stay adjacent under the new order');

// ---- a decision sinks the row and re-runs the analysis ----------------------
w.paintRun();
const runP = w.eval('nextRunPaper()');
const firstId = w.baseOrder(runP)[0];
const rowsBefore = [...d.querySelectorAll('#frows .frow')].map(r => r.dataset.fid);
ok(rowsBefore[0] === firstId, 'the DOM matches the base order on first paint');
ok(d.querySelector('#fr-' + firstId + ' .bchip').textContent.length > 0,
   'each row shows its breakdown chip');

const accept = d.querySelector('#fr-' + firstId + ' [data-a="a"]');
accept.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const rowsAfter = [...d.querySelectorAll('#frows .frow')].map(r => r.dataset.fid);
ok(rowsAfter.indexOf(firstId) > rowsBefore.indexOf(firstId),
   'a decided row sinks below the open ones');
ok(rowsAfter.length === rowsBefore.length, 'no row is lost when the list restacks');

// ---- typing a symbol lifts beta and is recorded -----------------------------
const S0 = w.eval('S');
const stepsBefore = (S0.steps || []).length;
const open = w.symsOf(byId[w.baseOrder(runP).find(id => !((S0.dec[runP.k] || {})[id]))] || runP.f[0])
  .filter(nm => !w.kindOf(runP, nm));
if (open.length) {
  const before = w.paperBeta(runP).mean;
  w.setSymKind(runP, open[0], 'v');
  ok(w.paperBeta(runP).mean > before, 'typing a symbol raises the paper mean beta');
  const steps = w.eval('S').steps;
  ok(steps.length === stepsBefore + 1, 'the tap appended exactly one step');
  const last = steps[steps.length - 1];
  ok(last.a === 'type:' + open[0] + '=v', 'the step records what was typed');
  ok(Array.isArray(last.b) && last.b.length === 2, 'the step records broken-down before and after');
  ok(last.m[1] >= last.m[0], 'the step records mean beta before and after');
  // untyping is a step too, and puts the symbol back
  w.setSymKind(runP, open[0], 'v');
  ok(!w.kindOf(runP, open[0]) || (runP.ev || {})[open[0]],
     'tapping the same kind again clears the reviewer verdict');
  ok(w.eval('S').steps.length === stepsBefore + 2, 'clearing is recorded as a step too');
} else {
  console.log('  (every symbol of the lead paper is already typed — tap checks skipped)');
}

// ---- evidence is used, and a reviewer verdict outranks it -------------------
const evPaper = RAW.papers.find(pp => Object.keys(pp.ev || {}).length);
if (evPaper) {
  const nm = Object.keys(evPaper.ev)[0];
  ok(w.kindOf(evPaper, nm) === evPaper.ev[nm], 'evidence types a symbol with no reviewer verdict');
  ok(w.eval('fromEvidence')(evPaper, nm), 'the symbol is marked as coming from evidence');
  w.setSymKind(evPaper, nm, evPaper.ev[nm] === 'p' ? 'v' : 'p');
  ok(w.kindOf(evPaper, nm) !== evPaper.ev[nm], 'a reviewer verdict overrides the evidence');
}

// ---- trace lift sits on top of the order, and deselect restores it ----------
w.paintRun();
const p2 = w.eval('nextRunPaper()');
const base = w.baseOrder(p2);
const svg = d.querySelector('#pgraph svg');
if (svg) {
  const node = svg.querySelector('.gnode-f[data-fid]');
  const fid = node.getAttribute('data-fid');
  node.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  ok([...d.querySelectorAll('#frows .frow')][0].dataset.fid === fid,
     'tapping a formula node lifts its row to the top');
  node.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));   // deselect
  ok([...d.querySelectorAll('#frows .frow')].map(r => r.dataset.fid).join() === base.join(),
     'deselecting drops back to the standing order');
}

// ---- export / import --------------------------------------------------------
const exp = w.buildExport();
ok(exp.schema_version === 'game-decisions-3', 'export declares game-decisions-3');
ok(Array.isArray(exp.steps) && exp.steps.length > 0, 'export carries the step history');
const st = exp.steps[0];
ok(st.at && st.paper_key && st.action, 'a step carries when, which paper and what happened');
ok(st.broken_down && 'before' in st.broken_down && 'after' in st.broken_down,
   'a step carries the breakdown before and after');
ok(typeof exp.totals.steps === 'number', 'totals report the step count');
ok(exp.symbol_tables !== undefined, 'the symbol-table contract is unchanged');
ok(exp.formula_decisions !== undefined, 'the decision contract is unchanged');

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
