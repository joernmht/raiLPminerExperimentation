// Factory floor (public navigator + local page): vertical lanes by default, rotate toggle,
// swimlane buttons, station links, banner, no corpus paper keys. Run with
// NODE_PATH=<scratchpad>/node_modules node scripts/checks/factory_check.js [docs/factory.html]
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const ROOT = '/home/joern/raiLPminerExperimentation';
const target = process.argv[2] || 'docs/factory.html';
const file = path.isAbsolute(target) ? target : path.join(ROOT, target);
const isPublic = file.startsWith(path.join(ROOT, 'docs'));
const html = fs.readFileSync(file, 'utf8');
let pass = 0, fail = 0;
const ok = (c, m) => { c ? pass++ : (fail++, console.log('FAIL:', m)); };
// URLs that answered 200 to curl on 2026-09-10 (the Zenodo DOI: 302 via doi.org, zenodo refuses bots)
const VERIFIED = new Set([
  'https://lp2graph.joernmaurischat.de/', 'https://lp2graph.joernmaurischat.de/docs/',
  'https://lp2graph.joernmaurischat.de/explore.html', 'https://lp2graph.joernmaurischat.de/configurator.html',
  'https://lp2graph.joernmaurischat.de/docs/validation/',
  'https://railpmining.joernmaurischat.de/', 'https://railpmining.joernmaurischat.de/prisma.html',
  'https://railpmining.joernmaurischat.de/game.html',
  'https://github.com/joernmht/raiLPminerExperimentation', 'https://github.com/joernmht/lp2graph',
  'https://arxiv.org/abs/2607.11980', 'https://doi.org/10.5281/zenodo.19165428',
]);
const data = JSON.parse(html.match(/<script type="application\/json" id="data">([\s\S]*?)<\/script>/)[1]);
ok(data.public === isPublic, 'data.public matches the build (' + data.public + ')');
ok(!/10\.1016|10\.\d{4,9}_j\./.test(html), 'no corpus paper key anywhere in the page');
ok(data.lanes.length === 4 && data.lanes.every(l => (l.links || []).length >= 1), 'every swimlane carries ≥1 subpage button');
ok(data.stations.filter(s => s.href).length >= 5, 'stations with a fitting subpage are links (' + data.stations.filter(s => s.href).length + ')');
if (isPublic) {
  ok(html.includes('class="proto"') && html.includes('working prototype'), 'prototype banner present');
  ok(html.indexOf('class="proto"') < html.indexOf('<header>'), 'banner sits above the header');
  ok(html.includes('href="./"') && html.includes('href="prisma.html"') && html.includes('href="game.html#run"'), 'footer links hub, PRISMA and the game');
  ok(html.includes('https://lp2graph.joernmaurischat.de/'), 'sister-site link present');
  ok(!html.includes('factory_data.json"') || !html.includes('DATA.public && false'), 'public page carries the polling gate');
} else {
  ok(!html.includes('class="proto"'), 'local page has no public banner');
}
(async () => {
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
    url: isPublic ? 'https://railpmining.joernmaurischat.de/factory.html' : 'http://127.0.0.1:8765/factory.html' });
  const w = dom.window, d = w.document;
  await new Promise(r => setTimeout(r, 50));
  const wrap = d.getElementById('floorwrap');
  ok(wrap.getAttribute('data-orient') === 'v', 'vertical orientation by default');
  ok(d.querySelector('.lanes.v') && d.querySelectorAll('.lanes.v .lane').length === 4, 'lane headers are a row of 4 columns');
  ok(Array.from(d.querySelectorAll('.lane')).every(l => l.querySelectorAll('a.lbtn').length >= 1), 'every lane header has buttons');
  const svg = d.querySelector('svg.fl');
  ok(svg && Number(svg.getAttribute('height')) > Number(svg.getAttribute('width')), 'vertical floor is taller than wide');
  ok(d.querySelectorAll('svg.fl .st').length === data.stations.length, 'every station drawn (' + d.querySelectorAll('svg.fl .st').length + ')');
  ok(d.querySelector('svg.fl .st.kind-gate') && d.querySelector('svg.fl .st[data-id="vocab"]'), 'gate and vocab station drawn (planned or live)');
  ok(d.querySelectorAll('svg.fl a.stl').length === data.stations.filter(s => s.href).length, 'linked stations are <a> elements');
  ok(d.querySelectorAll('svg.fl path.edge').length === data.edges.length, 'every conveyor drawn');
  const rot = d.getElementById('rotBtn');
  rot.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  ok(wrap.getAttribute('data-orient') === 'h' && !d.querySelector('.lanes.v'), 'rotate flips to horizontal lanes');
  const svg2 = d.querySelector('svg.fl');
  ok(Number(svg2.getAttribute('width')) > Number(svg2.getAttribute('height')), 'horizontal floor is wider than tall');
  ok(d.querySelectorAll('svg.fl .st').length === data.stations.length, 'all stations survive the rotation');
  rot.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  ok(wrap.getAttribute('data-orient') === 'v', 'rotate flips back to vertical');
  ok(w.localStorage.getItem('factory:orient') === 'v', 'orientation remembered per device');
  // every href resolves: relative -> a file in docs/ (hash stripped), absolute -> verified list
  const hrefs = Array.from(d.querySelectorAll('a[href]')).map(a => a.getAttribute('href'));
  const bad = hrefs.filter(h => {
    if (/^https?:/.test(h)) return !VERIFIED.has(h.replace(/#.*$/, ''));
    const f = h.replace(/#.*$/, '') || 'index.html';
    return !fs.existsSync(path.join(ROOT, 'docs', f === './' ? 'index.html' : f));
  });
  ok(bad.length === 0, 'all links resolve (bad: ' + bad.join(', ') + ')');
  ok(d.getElementById('bins').textContent.includes('papers pass the gate'), 'failure bins rendered');
  ok(d.getElementById('legend').textContent.includes('the difference is the fill-in list'), 'vocabulary-check card rendered');
  console.log(`\n[factory ${isPublic ? 'public' : 'local'}] ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
