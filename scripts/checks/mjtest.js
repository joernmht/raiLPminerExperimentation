// Reproduce the MathJax errors on the two broken corpus formulas.
const { mathjax } = require('mathjax-full/js/mathjax.js');
const { TeX } = require('mathjax-full/js/input/tex.js');
const { SVG } = require('mathjax-full/js/output/svg.js');
const { liteAdaptor } = require('mathjax-full/js/adaptors/liteAdaptor.js');
const { RegisterHTMLHandler } = require('mathjax-full/js/handlers/html.js');
const { AllPackages } = require('mathjax-full/js/input/tex/AllPackages.js');

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const tex = new TeX({ packages: AllPackages });
const svg = new SVG({ fontCache: 'none' });
const doc = mathjax.document('', { InputJax: tex, OutputJax: svg });

const cases = process.argv[2] ? [process.argv[2]] : [
  '\\underset{g \\in A_{k}^{s} \\cap A_{f}}{\\sum} x_{g}^{f} \\geq \\left(1 - \\underset{g \\in A_{f}^{v i}}{\\sum} x_{g}^{f}\\right) \\cdot \\underset{̲}{D}_{k}^{f} \\forall f \\in F , \\forall k \\in K_{f}',
  'T_{f k}^{i n i} = \\left\\{\\begin{matrix} \\alpha_{i n i} \\cdot \\bar{T}_{f}^{k} , & \\text{if}\\textrm{ } \\gamma_{f}^{M} = 1 ,\\textrm{ } k \\in \\left[0 , \\lceil \\frac{n_{f}^{K}}{2} \\rceil\\right] \\\\ \\delta_{i n i}^{K} , & \\text{if}\\textrm{ } \\gamma_{f}^{M} = 0 ,\\textrm{ } k \\in \\left[0 , \\lceil \\frac{n_{f}^{K}}{2} \\rceil\\right] \\\\ 0 , & \\text{otherwise} \\end{matrix}\\right',
];
for (const c of cases) {
  const node = doc.convert(c, { display: true });
  const out = adaptor.outerHTML(node);
  const err = out.match(/data-mjx-error="([^"]*)"/);
  console.log(err ? 'ERROR: ' + err[1] : 'OK');
}
