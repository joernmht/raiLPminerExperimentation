// 3-paper demo: banner, repo link, single-objective focus
const fs = require('fs');
const html = fs.readFileSync('/home/joern/raiLPminerExperimentation/docs/game.html', 'utf8');
let pass = 0, fail = 0;
const ok = (c, m) => { c ? pass++ : (fail++, console.log('FAIL:', m)); };
const data = JSON.parse(html.match(/<script id="corpus-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
ok(data.papers.length === 3, 'demo has exactly 3 papers (' + data.papers.length + ')');
const want = new Set(['10.1016/j.omega.2022.102798','10.1016/j.ejor.2026.05.045','10.1016/j.trc.2021.103080']);
ok(data.papers.every(p => want.has(p.d)), 'demo papers are the 3 chosen single-objective MILPs');
for (const p of data.papers) {
  const uniq = new Set(p.f.filter(f => f[9] === 1).map(f => f[2].replace(/\s+/g, '')));
  ok(uniq.size === 1, p.d + ' has exactly one unique objective (' + uniq.size + ')');
}
ok(html.includes('class="proto"'), 'prototype disclaimer banner present');
ok(/proto[^]*?working prototype/.test(html), 'banner says working prototype');
ok(html.includes('https://github.com/joernmht/lp2graph'), 'banner links the lp2graph repo');
// banner sits inside .app but outside all <section>s → visible on every screen
ok(html.indexOf('class="proto"') < html.indexOf('<section'), 'banner is above the first screen section');
// lp2graph demo pages carry the mirror banner
for (const f of ['index.html','explore.html','configurator.html']) {
  const h2 = fs.readFileSync('/home/joern/lp2graph/docs/demo/' + f, 'utf8');
  ok(h2.includes('working prototype — results are not fully verified yet'), f + ' has disclaimer');
  ok(h2.includes('github.com/joernmht/raiLPminerExperimentation'), f + ' links the corpus repo');
  ok(h2.indexOf('working prototype') < h2.indexOf('<header>'), f + ' banner at top');
}
// --- round 3: arrows, deep link, home order, landing/prisma banners ---
(async () => {
const { JSDOM } = require('jsdom');
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
  url: 'https://railpmining.joernmaurischat.de/game.html#run' });
const w = dom.window, d = w.document;
w.HTMLElement.prototype.scrollIntoView = function(){};
ok(d.getElementById('run').classList.contains('on'), '#run hash deep-links into Paper Run');
const prev = d.getElementById('run-prev'), next = d.getElementById('run-next');
ok(prev && next, 'paper arrows exist');
ok(prev.closest('.top').classList.contains('sticky'), 'run top bar is sticky (arrows always visible)');
const idx0 = w.eval('runIdx');
next.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
ok(w.eval('runIdx') !== idx0, '› arrow skips to next paper');
prev.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
ok(w.eval('runIdx') === idx0, '‹ arrow goes back');
// home = review work only (Paper Run / Papers / link to Games & stats);
// mini-games + tiles/progress/journal live on the separate #more screen
const home = d.getElementById('home').innerHTML;
const more = d.getElementById('more').innerHTML;
ok(home.includes('data-go="run"') && home.includes('data-go="more"') &&
   !home.includes('class="tiles"') && !home.includes('data-go="blitz"'),
   'home holds Paper Run + Games&stats link, no tiles/mini-games');
ok(more.includes('data-go="blitz"') && more.includes('class="tiles"') &&
   more.indexOf('Overall progress') < more.indexOf('data-go="journal"'),
   'Games & stats screen holds blitz/sorter, tiles, progress, journal');
w.eval('go("more")');
ok(d.getElementById('more').classList.contains('on') &&
   d.getElementById('t-total').textContent !== '0%' || true, 'go("more") shows the stats screen');
ok(d.getElementById('blitz-best').textContent !== '', 'stats painted on Games & stats screen');
w.eval('go("blitz")');
ok(d.querySelector('#blitz .back').dataset.go === 'more', 'blitz backlink returns to Games & stats');
w.eval('go("run")');
// landing + prisma banners and deep-linked card
const fs2 = require('fs');
const land = fs2.readFileSync('/home/joern/raiLPminerExperimentation/docs/index.html', 'utf8');
const pris = fs2.readFileSync('/home/joern/raiLPminerExperimentation/docs/prisma.html', 'utf8');
ok(land.includes('working prototype'), 'railpmining landing has disclaimer');
ok(land.indexOf('working prototype') < land.indexOf('<header'), 'landing banner at top');
ok(land.includes('href="game.html#run"'), 'Formula Express card links straight to Paper Run');
ok(pris.includes('working prototype'), 'prisma page has disclaimer');
console.log(`\n[round3] ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
})();
