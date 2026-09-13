#!/usr/bin/env node
// TeX -> MathML for the review pages, so a page renders without any network
// (MathML is native in Chrome 109+, Safari and Firefox). stdin: JSON array of
// TeX strings; stdout: JSON array of {"mml": "<math ...>"} or {"error": "..."}.
// Needs mathjax-full on NODE_PATH (npm install --prefix scripts/render).
const { mathjax } = require('mathjax-full/js/mathjax.js');
const { TeX } = require('mathjax-full/js/input/tex.js');
const { liteAdaptor } = require('mathjax-full/js/adaptors/liteAdaptor.js');
const { RegisterHTMLHandler } = require('mathjax-full/js/handlers/html.js');
const { AllPackages } = require('mathjax-full/js/input/tex/AllPackages.js');
const { STATE } = require('mathjax-full/js/core/MathItem.js');
// bussproofs needs an output jax (getBBox); everything else works MathML-only
const packages = AllPackages.filter((p) => p !== 'bussproofs');
const { SerializedMmlVisitor } = require('mathjax-full/js/core/MmlTree/SerializedMmlVisitor.js');
const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const doc = mathjax.document('', { InputJax: new TeX({ packages }) });
const visitor = new SerializedMmlVisitor();
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  const items = JSON.parse(input || '[]');
  const out = items.map((tex) => {
    try {
      const node = doc.convert(tex, { display: true, end: STATE.CONVERT });
      const mml = visitor.visitTree(node);
      const err = mml.match(/data-mjx-error="([^"]*)"/) || mml.match(/<merror[^>]*>([^<]*)/);
      return err ? { error: err[1] } : { mml };
    } catch (e) {
      return { error: String(e && e.message || e).slice(0, 160) };
    }
  });
  process.stdout.write(JSON.stringify(out));
});
