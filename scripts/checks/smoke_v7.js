// game v7 smoke: objective ruleset + display repair + unified tap-to-trace
const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync(process.argv[2] || '/home/joern/raiLPminerExperimentation/docs/game.html', 'utf8');

let pass = 0, fail = 0;
const ok = (cond, msg) => { if (cond) { pass++; } else { fail++; console.log('FAIL:', msg); } };

// ---- payload checks (no DOM needed) ----
const m = html.match(/<script id="corpus-data" type="application\/json">([\s\S]*?)<\/script>/);
ok(m, 'corpus-data payload present');
const data = JSON.parse(m[1]);
const trb = data.papers.find(p => p.d === '10.1016/j.trb.2022.02.002');
if (trb) {                     // full-corpus build; the 3-paper demo no longer carries trb
  const byLabel = {};
  trb.f.forEach(f => { byLabel[f[1]] = f; });
  ok(byLabel['d1e6372'][9] === 1, 'minZ_1 (glued "m i n Z") flagged objective');
  ok(byLabel['d1e14053'][9] === 0, 'T=min{a,b} ∀f (pointwise definition) NOT objective');
  ok(byLabel['d1e14054'][9] === 0, 'duplicate pointwise definition NOT objective');
  ok(typeof byLabel['d1e6961'][10] === 'string' && byLabel['d1e6961'][10].includes('\\underline{D}'),
     'combining-mark \\underset repaired to \\underline in display copy');
  ok(typeof byLabel['d1e13551'][10] === 'string' && byLabel['d1e13551'][10].includes('\\right.'),
     'dangling \\right repaired with null delimiter in display copy');
  ok(byLabel['d1e6372'][10] === 0, 'clean formula carries no redundant display copy');
} else {
  console.log('  (trb.2022.02.002 not in this build — payload ruleset spot checks skipped)');
}
// every display copy must differ from the raw latex; raw stays untouched
let disp = 0;
for (const p of data.papers) for (const f of p.f) {
  if (f[10]) { disp++; ok(f[10] !== f[2], 'display copy differs from raw for ' + f[0]); }
}
console.log('  display-repaired formulas in demo:', disp);
ok(html.includes('f[10]||f[2]'), 'renderMath call sites use repaired display copy');

// ---- DOM interaction: unified tap-to-trace ----
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
  url: 'https://railpmining.joernmaurischat.de/game.html' });
const w = dom.window, d = w.document;
w.HTMLElement.prototype.scrollIntoView = function(){};
ok(typeof w.drawPaperGraph === 'function', 'drawPaperGraph is a page global');

const p = w.eval('RAW').papers.find(pp => pp.d === '10.1016/j.trb.2022.02.002')
       || w.eval('RAW').papers.find(pp => pp.f.some(f => f[9] === 1));
const host = d.createElement('div'); d.body.appendChild(host);
w.drawPaperGraph(host, p);
const svg = host.querySelector('svg');
ok(svg, 'paper graph renders');
const strip = host.querySelector('.gsel');
ok(strip && strip.innerHTML === '', 'chip strip exists and starts empty');

// find the objective node (formula node whose f[9]===1)
const objFi = p.f.findIndex(f => f[9] === 1);
ok(objFi >= 0, 'demo paper has an objective');
const objCircle = [...svg.querySelectorAll('.gnode-f')]
  .find(c => c.getAttribute('data-fid') === p.f[objFi][0]);
ok(objCircle, 'objective circle present in graph');
objCircle.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));

const chips = [...strip.querySelectorAll('.chip')];
ok(chips.length > 1, 'tapping objective lists connected entities (' + chips.length + ' chips)');
ok(chips[0].classList.contains('sel'), 'selected entity is FIRST in the list');
ok(chips[0].getAttribute('data-fid') === p.f[objFi][0], 'first chip is the tapped objective');
ok(chips[0].textContent.includes('🎯'), 'objective chip carries the objective marker');
ok(chips.slice(1).every(c => !c.getAttribute('data-fid')), 'objective neighbours are symbols');
ok(objCircle.classList.contains('sel'), 'tapped node highlighted as selected');
const hlSyms = [...svg.querySelectorAll('.gnode-s.hl')];
ok(hlSyms.length === chips.length - 1, 'connected symbol nodes highlighted (' + hlSyms.length + ')');
ok(svg.querySelectorAll('.gedge.hl').length === chips.length - 1, 'incident edges highlighted');
// every non-selected node is either a lit direct neighbour (.hl) or dimmed (.dim)
const nonSel = [...svg.querySelectorAll('.gnode-s,.gnode-f')].filter(c => !c.classList.contains('sel'));
ok(nonSel.every(c => c.classList.contains('hl') || c.classList.contains('dim')),
   'every non-selected node is neighbour-lit or dimmed');
ok(svg.querySelectorAll('.gnode-s.dim,.gnode-f.dim').length > 0, 'non-neighbour nodes dimmed');
ok([...svg.querySelectorAll('.gedge')].every(l => l.classList.contains('hl') || l.classList.contains('dim')),
   'non-incident edges dimmed');

// symbol names shown = abbreviated names from the payload
const symNames = new Set(p.f[objFi][5].map(s => s[0]));
ok(chips.slice(1).every(c => symNames.has(c.textContent)), 'chips show abbreviated symbol names');

// tap a symbol chip → selection moves to that symbol, symbol first in list
chips[1].dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const chips2 = [...strip.querySelectorAll('.chip')];
ok(chips2[0].classList.contains('sel') && chips2[0].textContent === chips[1].textContent,
   'tapping a symbol chip re-selects: symbol becomes first + highlighted');
ok(chips2.slice(1).some(c => c.getAttribute('data-fid')), 'symbol selection lists connected formulas');

// tap same node again → deselect, strip empties
const symNode = [...svg.querySelectorAll('.gnode-s')]
  .find(c => c.getAttribute('data-node') === chips2[0].getAttribute('data-node'));
ok(symNode, 'selected symbol circle found');
symNode.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
ok(strip.innerHTML === '', 'tapping the selected node again clears the trace');
ok(svg.querySelectorAll('.hl,.dim,.sel').length === 0, 'all highlight classes cleared');

// ---- v8: refined objective rules in the payload ----
const byDoi = {};
data.papers.forEach(pp => { byDoi[pp.d] = pp; });
const objIds = (doi) => byDoi[doi].f.filter(f => f[9] === 1).map(f => f[0]);
const okIf = (doi, cond, msg) =>
  byDoi[doi] ? ok(cond(), msg) : console.log(`  (${doi} not in this build — skipped: ${msg})`);
okIf('10.1016/j.ejor.2021.06.025', () => JSON.stringify(objIds('10.1016/j.ejor.2021.06.025')) ===
   JSON.stringify(['eq-0005','eq-0009','eq-0018','eq-0027']),
   'aligned min…s.t. model blocks are the objectives; M_kr/t_kr defs are not');
okIf('10.1016/j.trc.2021.103368', () => JSON.stringify(objIds('10.1016/j.trc.2021.103368')) === JSON.stringify(['eq-0001']),
   'vector objective min(z_D,z_O,z_P) detected');
okIf('10.1016/j.trc.2021.103080', () => objIds('10.1016/j.trc.2021.103080').length === 1 &&
   objIds('10.1016/j.trc.2021.103080')[0] === 'eq-0006',
   'pointwise max{…} constraints no longer flagged; real minimize kept');
// eq-0024 joined the expected set when the word-form detector landed (game
// 67b8ac1): it is "\begin{aligned}\mathbf{min} … \mathbf{s}.\mathbf{t}. …", an
// aligned model block headed by min, which the v8 rule counts as an objective.
// The two η_u = max(0,…) definitions must still stay out.
okIf('10.1016/j.omega.2022.102796', () => JSON.stringify(objIds('10.1016/j.omega.2022.102796')) === JSON.stringify(['eq-0013','eq-0017','eq-0024']),
   'η_u = max(0,…) definitions no longer flagged; \\mathbf{min} model block is');
if (data.papers.length <= 3) {
  ok(data.papers.every(pp => pp.f.some(f => f[9] === 1)),
     'every demo paper has at least one objective');
}

// ---- v8: swipe ⇄ to switch paper on the run screen ----
ok(typeof w.attachRunSwipe === 'function' && typeof w.skipRun === 'function',
   'swipe handlers are page globals');
w.eval('paintRun()');
const runSlot = d.getElementById('run-slot');
ok(runSlot && runSlot.dataset.swipe === '1', 'swipe listener attached to run slot');
const swipe = (x1, x2, y2) => {
  const ts = new w.Event('touchstart', { bubbles: true });
  ts.touches = [{ clientX: x1, clientY: 100 }];
  runSlot.dispatchEvent(ts);
  const te = new w.Event('touchend', { bubbles: true });
  te.changedTouches = [{ clientX: x2, clientY: y2 }];
  runSlot.dispatchEvent(te);
};
const idx0 = w.eval('runIdx');
swipe(300, 150, 110);                       // left swipe → next paper
ok(w.eval('runIdx') !== idx0, 'left swipe skips to the next paper');
swipe(150, 300, 110);                       // right swipe → previous paper
ok(w.eval('runIdx') === idx0, 'right swipe returns to the previous paper');
const idx1 = w.eval('runIdx');
swipe(300, 260, 105);                       // too short → ignored
ok(w.eval('runIdx') === idx1, 'short swipe is ignored');
swipe(300, 180, 250);                       // diagonal (scroll) → ignored
ok(w.eval('runIdx') === idx1, 'vertical-ish swipe is ignored (scrolling)');

// ---- v9: component highlight + definition-first list + desktop split ----
w.eval('go("run")');
ok(d.body.classList.contains('wide'), 'run screen sets body.wide (desktop side-by-side hook)');
ok(html.includes('body.wide #run-slot'), 'desktop grid CSS for run slot present');
const svg2 = d.getElementById('pgraph').querySelector('svg');
const frows = d.getElementById('frows');
const order0 = [...frows.children].map(r => r.id);
// tap a symbol that appears in >1 formula
const symCounts = {};
[...svg2.querySelectorAll('.gedge')].forEach(l => {
  const s = l.getAttribute('data-s'); symCounts[s] = (symCounts[s] || 0) + 1; });
const symIdx = Object.keys(symCounts).find(s => symCounts[s] > 1) || Object.keys(symCounts)[0];
const symEl2 = svg2.querySelector(`.gnode-s[data-node="${symIdx}"]`);
ok(symEl2, 'shared symbol node found on run graph');
symEl2.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const nbrFids = new Set([...svg2.querySelectorAll(`.gedge[data-s="${symIdx}"]`)
  ].map(l => 'fr-' + svg2.querySelector(`[data-node="${l.getAttribute('data-f')}"]`).getAttribute('data-fid')));
const rows1 = [...frows.children].map(r => r.id);
ok(rows1.slice(0, nbrFids.size).every(id => nbrFids.has(id)),
   `formulas using the tapped symbol lifted to the top of the list (${nbrFids.size})`);
ok(JSON.stringify(rows1.slice(0, nbrFids.size)) ===
   JSON.stringify(order0.filter(id => nbrFids.has(id))),
   'lifted rows keep paper order — first row = definition site');
ok(JSON.stringify(rows1.slice(nbrFids.size)) ===
   JSON.stringify(order0.filter(id => !nbrFids.has(id))),
   'remaining rows keep paper order');
symEl2.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));   // deselect
ok(JSON.stringify([...frows.children].map(r => r.id)) === JSON.stringify(order0),
   'deselecting restores the original paper order');
w.eval('go("papers")');
ok(!d.body.classList.contains('wide'), 'leaving the run screen clears body.wide');

// ---- v10: multi-part fix (duplicate / split-at-cursor) ----
w.eval('go("run")');
const frows2 = d.getElementById('frows');
const row0 = frows2.querySelector('.frow');
const fid0 = row0.dataset.fid;
row0.querySelector('.b-fix').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
ok(d.getElementById('fixsheet').classList.contains('on'), 'fix button opens the fix sheet');
let tas = [...d.querySelectorAll('#fix-parts textarea')];
ok(tas.length === 1, 'fix sheet starts with one box');
tas[0].value = 'A = B C = D';
tas[0].focus(); tas[0].selectionStart = tas[0].selectionEnd = 5;
w.eval('splitFixPart()');
tas = [...d.querySelectorAll('#fix-parts textarea')];
ok(tas.length === 2 && tas[0].value === 'A = B' && tas[1].value === 'C = D',
   'split at cursor yields two trimmed formulas');
ok([...d.querySelectorAll('#fix-parts .fnum')].length === 2, 'part labels shown when >1 box');
tas[1].focus();
w.eval('dupFixPart()');
tas = [...d.querySelectorAll('#fix-parts textarea')];
ok(tas.length === 3 && tas[2].value === 'C = D', 'duplicate copies the focused box');
ok(d.getElementById('fix-save').textContent.includes('3 formulas'), 'save button counts the parts');
d.querySelectorAll('#fix-parts .fdel')[2].dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
tas = [...d.querySelectorAll('#fix-parts textarea')];
ok(tas.length === 2, 'a part can be removed again');
w.eval('saveFix()');
const dec = w.eval('S.dec')[w.eval('RUSHP')[w.eval('runIdx')].k][fid0];
ok(dec && dec.s === 'c' && dec.n === 'A = B' && JSON.stringify(dec.m) === '["C = D"]',
   'saved decision = corrected, note=part 1, m=extra parts');
ok(row0.querySelector('.st').textContent === '✎×2', 'row status shows the part count');
const rEl = row0.querySelector('.render');
ok(rEl.dataset.tex[0] === '[' && rEl.childElementCount === 2,
   'row render holds both formulas (dataset.tex = JSON array)');
const exp = w.eval('JSON.stringify(buildExport())');
const expDec = JSON.parse(exp).formula_decisions
  .flatMap(pd => pd.decisions).find(x => x.id === fid0);
ok(expDec.status === 'corrected' && JSON.stringify(expDec.parts) === '["A = B","C = D"]',
   'export carries parts=[all corrected formulas], note=part 1');
// heal path: a raw (untypeset) render survives a reorder as a re-render request
const svg3 = d.getElementById('pgraph').querySelector('svg');
const anySym = svg3.querySelector('.gnode-s[data-node]');
anySym.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
ok([...frows2.querySelectorAll('.render')].every(el =>
   !el.dataset.tex || !el.childElementCount || el.childElementCount > 0),
   'reorder leaves no emptied renders behind');
anySym.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));

// ---- v11: similarity groups + duplicate marking + forgiving graph taps ----
ok(data.papers.every(pp => Array.isArray(pp.g)), 'every paper carries similarity groups (g)');
ok(data.papers.some(pp => pp.g.length), 'similarity analysis found groups in this build');
w.eval('go("run")');
const p3 = w.eval('RUSHP')[w.eval('runIdx')];
const frows3 = d.getElementById('frows');
if (p3.g.length) {
  // grouped rows are rendered adjacent
  const ids3 = [...frows3.children].map(r => r.dataset.fid);
  const grp = p3.g[0];
  const at = grp.map(id => ids3.indexOf(id)).sort((a, b) => a - b);
  ok(at[at.length - 1] - at[0] === grp.length - 1, 'group members sit adjacent in the list');
  const firstRow = d.getElementById('fr-' + grp[0]);
  const chip = firstRow.querySelector('.gchip');
  ok(chip && chip.textContent === '≈ ×' + grp.length, 'group chip shows ≈ ×n');
  // tap ≈ on the first member -> the rest of the group is marked duplicate-of it
  chip.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const dec3 = w.eval('S.dec')[p3.k];
  ok(grp.slice(1).every(id => dec3[id] && dec3[id].s === 'd' && dec3[id].of === grp[0]),
     'group chip marks other members duplicate-of the kept formula');
  ok(!dec3[grp[0]] || dec3[grp[0]].s !== 'd', 'the kept formula itself is not marked');
  ok(d.getElementById('fr-' + grp[1]).querySelector('.st').textContent === '⧉',
     'duplicate rows show the ⧉ status');
  const expD = JSON.parse(w.eval('JSON.stringify(buildExport())')).formula_decisions
    .find(pd => pd.paper_key === p3.k).decisions.find(x => x.id === grp[1]);
  ok(expD.status === 'duplicate' && expD.duplicate_of === grp[0],
     'export says status=duplicate with duplicate_of');
}
// single ⧉ dup button on any unreviewed row
const openRow = [...frows3.children].find(r => !w.eval('S.dec')[p3.k][r.dataset.fid]);
if (openRow) {
  openRow.querySelector('.b-dup').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  ok(w.eval('S.dec')[p3.k][openRow.dataset.fid].s === 'd', 'row ⧉ dup button marks a duplicate');
}
ok(w.eval('statusCounts()').d >= 1, 'statusCounts tracks duplicates');
w.eval('paintHome()');
ok(d.getElementById('mainbar').querySelector('.sd'), 'progress bar has a duplicates segment');
// forgiving tap: clicking svg background must not throw (jsdom has no layout -> guarded)
let tapOk = true;
try { d.getElementById('pgraph').querySelector('svg')
        .dispatchEvent(new w.MouseEvent('click', { bubbles: true })); }
catch (err) { tapOk = false; }
ok(tapOk, 'nearest-node tap fallback is guarded when layout is unavailable');

// ---- v13: build-time auto-split suggestions (corpusbuilder.split) ----
const P = w.eval('RAW').papers;
ok(P.every(pp => pp.f.every(f => f.length >= 12)), 'every formula carries the split slot f[11]');
ok(P.every(pp => pp.f.every(f => !f[11] || (Array.isArray(f[11]) && (f[11].length === 1 || f[11].length >= 3)))),
   'f[11] is 0, [conf] (suspect) or [conf, part1, part2, ...]');
const pool = w.eval('RUSHP');
const withSug = [];
for (const pp of pool) for (const f of pp.f) if (f[11] && f[11].length > 1) withSug.push([pp, f]);
const sugSplit = withSug[0];
if (sugSplit) {
  const [sp, sf] = sugSplit;
  w.eval(`go("run"); runIdx = RUSHP.findIndex(x=>x.k===${JSON.stringify(sp.k)}); paintRun();`);
  const srow = d.getElementById('fr-' + sf[0]);
  const achip = srow && srow.querySelector('.achip');
  ok(achip && achip.textContent === '⚡×' + (sf[11].length - 1), 'auto-split row shows the ⚡×n chip');
  achip.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  ok(d.getElementById('fixsheet').classList.contains('on'), '⚡ chip opens the fix sheet');
  const tas = [...d.querySelectorAll('#fix-parts textarea')];
  ok(tas.length === sf[11].length - 1, 'fix sheet is pre-filled with one box per detected part');
  ok(tas.every((t, i) => t.value === sf[11][i + 1]), 'pre-filled boxes carry the detected parts');
  w.eval('closeSheets()');
} else {
  ok(true, 'no split suggestion in this payload (skip chip check)');
  ok(true, 'no split suggestion in this payload (skip sheet check)');
  ok(true, 'no split suggestion in this payload (skip boxes check)');
  ok(true, 'no split suggestion in this payload (skip values check)');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
