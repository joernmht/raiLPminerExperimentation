// MathML -> LaTeX bridge (deterministic; no model).
// Reads a JSON array of MathML strings on stdin, writes a JSON array of
// {ok:true, latex} | {ok:false, error} on stdout, preserving order.
"use strict";
const { MathMLToLaTeX } = require("mathml-to-latex");

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (buf += c));
process.stdin.on("end", () => {
  let arr;
  try {
    arr = JSON.parse(buf);
  } catch (e) {
    process.stderr.write("convert.js: invalid JSON input\n");
    process.exit(2);
  }
  const out = arr.map((m) => {
    try {
      return { ok: true, latex: MathMLToLaTeX.convert(m) };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  });
  process.stdout.write(JSON.stringify(out));
});
