# game.html check suites (jsdom / MathJax)

Node-based smoke tests for the Formula Express game builds. Setup (once, any dir):
`npm install jsdom mathjax-full`, then run from a dir whose node_modules has them
(or set NODE_PATH).

- `smoke_v7.js [path]` — 900+ checks vs a game build (default docs/game.html;
  pass corpus/review/game.html for the full corpus): payload objective labels,
  display-repair copies (f[10]), tap-to-trace graph interaction, swipe/skip.
- `demo_check.js` — 29 checks specific to the public 3-paper demo + banners on
  landing/prisma + lp2graph demo pages + #run deep link + run arrows.
- `mjsweep.js` — renders every formula in ./demo_tex.json through MathJax
  (export that JSON with corpusbuilder.game.render_latex over dossiers) and
  lists render errors; `mjtest.js '<latex>'` for one-off probes.
