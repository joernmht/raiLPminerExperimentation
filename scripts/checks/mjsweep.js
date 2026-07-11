const { mathjax } = require('mathjax-full/js/mathjax.js');
const { TeX } = require('mathjax-full/js/input/tex.js');
const { SVG } = require('mathjax-full/js/output/svg.js');
const { liteAdaptor } = require('mathjax-full/js/adaptors/liteAdaptor.js');
const { RegisterHTMLHandler } = require('mathjax-full/js/handlers/html.js');
const { AllPackages } = require('mathjax-full/js/input/tex/AllPackages.js');
const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const doc = mathjax.document('', { InputJax: new TeX({ packages: AllPackages }), OutputJax: new SVG({ fontCache: 'none' }) });
const items = require('./demo_tex.json');
let bad = 0;
for (const it of items) {
  let msg = null;
  try {
    const out = adaptor.outerHTML(doc.convert(it.tex, { display: true }));
    const err = out.match(/data-mjx-error="([^"]*)"/);
    if (err) msg = err[1];
  } catch (e) { msg = 'THROW: ' + e.message.slice(0, 80); }
  if (msg) { bad++; console.log(it.doi, it.id, '::', msg, '::', it.tex.slice(0, 110)); }
}
console.log('errors:', bad, '/', items.length);
