#!/usr/bin/env node
// TeX -> self-contained SVG for the review pages: no network, no MathML
// support needed, identical in every viewer. Glyph paths are inlined
// once per page: fontCache 'global' collects them in one hidden <svg> the
// formulas reference by id. stdin: JSON array of TeX strings; stdout: JSON
// object {"items": [{"svg": ...} | {"error": ...}], "cache": "<svg ...>"}. Needs mathjax-full on
// NODE_PATH (npm install --prefix scripts/render).
const { mathjax } = require('mathjax-full/js/mathjax.js');
const { TeX } = require('mathjax-full/js/input/tex.js');
const { SVG } = require('mathjax-full/js/output/svg.js');
const { liteAdaptor } = require('mathjax-full/js/adaptors/liteAdaptor.js');
const { RegisterHTMLHandler } = require('mathjax-full/js/handlers/html.js');
const { AllPackages } = require('mathjax-full/js/input/tex/AllPackages.js');
const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const packages = AllPackages.filter((p) => p !== 'bussproofs');
const svgJax = new SVG({ fontCache: 'global' });
const doc = mathjax.document('', { InputJax: new TeX({ packages }), OutputJax: svgJax });
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  const items = JSON.parse(input || '[]');
  const out = items.map((tex) => {
    try {
      const html = adaptor.outerHTML(doc.convert(tex, { display: true }));
      const err = html.match(/data-mjx-error="([^"]*)"/);
      if (err) return { error: err[1] };
      const m = html.match(/<svg[\s\S]*<\/svg>/);
      return m ? { svg: m[0] } : { error: 'no svg produced' };
    } catch (e) {
      return { error: String((e && e.message) || e).slice(0, 160) };
    }
  });
  let cache = '';
  try { cache = adaptor.outerHTML(svgJax.fontCache.getCache()); } catch (e) { cache = ''; }
  process.stdout.write(JSON.stringify({ items: out, cache }));
});
