// symbol-table classifier: tap a symbol -> parameter/variable/index, applied
// paper-wide, surfaced in the header chip, and carried into the export that
// corpusbuilder.promote consumes.
const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync(process.argv[2] || '/home/joern/raiLPminerExperimentation/docs/game.html', 'utf8');

let pass = 0, fail = 0;
const ok = (cond, msg) => { if (cond) { pass++; } else { fail++; console.log('FAIL:', msg); } };

const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
  url: 'https://railpmining.joernmaurischat.de/game.html' });
const w = dom.window, d = w.document;
w.HTMLElement.prototype.scrollIntoView = function(){};
const click = el => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));

const p = w.eval('RAW').papers.find(pp => pp.f.length > 3);
ok(!!p, 'a paper with formulas exists');
w.eval('S').sym = {};                                   // start from a clean table

const host = d.createElement('div'); d.body.appendChild(host);
w.drawPaperGraph(host, p);
const svg = host.querySelector('svg'), strip = host.querySelector('.gsel');
ok(!!svg && !!strip, 'paper graph and chip strip render');

// ---- tapping a SYMBOL exposes the classifier; tapping a formula does not ----
const symCircle = [...svg.querySelectorAll('.gnode-s')].find(c => c.getAttribute('data-sym'));
ok(!!symCircle, 'graph has symbol nodes carrying data-sym');
const name = symCircle.getAttribute('data-sym');
click(symCircle);
let btns = [...strip.querySelectorAll('.kbtn')];
ok(btns.length === 3, 'selecting a symbol offers 3 kinds, got ' + btns.length);
ok(btns.map(b => b.getAttribute('data-kind')).join(',') === 'p,v,i',
   'kinds are parameter/variable/index, in that order');

const fCircle = svg.querySelector('.gnode-f');
if (fCircle) { click(fCircle);
  ok(strip.querySelectorAll('.kbtn').length === 0, 'a formula node offers no symbol classifier'); }

// ---- the occurrence count is the propagation, shown before it is applied ----
if (!strip.querySelector('.kbtn')) click(symCircle);
const uses = p.f.filter(f => (f[5] || []).some(s => s[0] === name)).length;
const kn = strip.querySelector('.kn');
ok(!!kn && kn.textContent.trim().startsWith(String(uses)),
   `classifier shows the true occurrence count (${kn && kn.textContent.trim()} vs ${uses})`);
ok(uses >= 1, 'the symbol occurs in at least one formula');

// ---- one tap classifies the symbol for the whole paper ----
btns = [...strip.querySelectorAll('.kbtn')];
click(btns.find(b => b.getAttribute('data-kind') === 'v'));
const S = w.eval('S');
ok(S.sym[p.k] && S.sym[p.k][name] === 'v', 'variable verdict stored, keyed by paper+symbol');
ok(Object.keys(S.sym[p.k]).length === 1, 'exactly one entry written, not one per formula');
ok(symCircle.classList.contains('kv'), 'symbol node recoloured as a variable');
ok(!!strip.querySelector('.kbtn.on'), 'active kind is highlighted');

// every OTHER symbol node stays untyped: propagation follows the symbol, not the graph
const otherTyped = [...svg.querySelectorAll('.gnode-s')]
  .filter(c => c.getAttribute('data-sym') !== name)
  .filter(c => c.classList.contains('kp') || c.classList.contains('kv') || c.classList.contains('ki'));
ok(otherTyped.length === 0, 'classifying one symbol does not type the others');

// ---- toggling the active kind clears it ----
click([...strip.querySelectorAll('.kbtn')].find(b => b.getAttribute('data-kind') === 'v'));
ok(!(S.sym[p.k] || {})[name], 'clicking the active kind clears the verdict');
ok(!symCircle.classList.contains('kv'), 'node colour cleared with it');

// ---- export ----
// re-clicking a selected node deselects it, so only click when the strip is empty
if (!strip.querySelector('.kbtn')) click(symCircle);
click([...strip.querySelectorAll('.kbtn')].find(b => b.getAttribute('data-kind') === 'p'));
const exp = w.buildExport();
ok(exp.schema_version === 'game-decisions-2', 'export schema bumped for symbol tables');
ok(Array.isArray(exp.symbol_tables), 'export carries symbol_tables');
const st = exp.symbol_tables.find(t => t.paper_key === p.k);
ok(!!st, 'the classified paper appears in symbol_tables');
ok(st.symbols[name] === 'parameter', 'export spells the kind out for promote, not the code');
ok(st.classified >= 1 && st.total_symbols >= st.classified, 'export reports classification progress');
const keys = Object.keys(st.symbols);
ok(JSON.stringify(keys) === JSON.stringify([...keys].sort()), 'symbol keys sorted (deterministic export)');
ok(exp.totals.papers_with_symbols >= 1, 'totals count papers with a symbol table');

// papers with no classified symbol must not appear at all
const empty = w.eval('RAW').papers.find(pp => pp.k !== p.k);
if (empty) ok(!exp.symbol_tables.find(t => t.paper_key === empty.k),
  'an unclassified paper is omitted from symbol_tables');

// ---- header chip reports what is still owed ----
const chip = w.chkChips(p);
ok(/symbols typed/.test(chip), 'run header reports symbol-table progress');
ok(/<b>1\/\d+<\/b>/.test(chip), 'chip shows done/total with the one we classified');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
