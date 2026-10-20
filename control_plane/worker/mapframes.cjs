// Map compiled Next.js stack frames back to the app's source with the
// build's own source maps. stdin: JSON frames; stdout: JSON frames with
// "src" and "src_line" added where a mapping exists. Run with cwd = the app.
const fs = require("fs");
const path = require("path");
const { SourceMapConsumer } = require(path.join(process.cwd(), "node_modules/source-map-js"));
const frames = JSON.parse(fs.readFileSync(0, "utf8"));
const cache = {};
function consumer(file) {
  if (file in cache) return cache[file];
  let map = null;
  try {
    const mapFile = fs.existsSync(file + ".map") ? file + ".map" : null;
    if (mapFile) map = new SourceMapConsumer(JSON.parse(fs.readFileSync(mapFile, "utf8")));
  } catch (e) { map = null; }
  return (cache[file] = map);
}
for (const f of frames) {
  if (!f.file || !f.file.includes("/.next/")) continue;
  const c = consumer(f.file);
  if (!c) continue;
  let pos = c.originalPositionFor({ line: f.line, column: f.column || 0, bias: SourceMapConsumer.LEAST_UPPER_BOUND });
  if (!pos.source) pos = c.originalPositionFor({ line: f.line, column: f.column || 0 });
  if (pos.source) {
    let src = decodeURIComponent(pos.source);
    if (src.startsWith("file://")) src = path.relative(process.cwd(), src.slice(7));
    const m = src.match(/\[project\]\/(.+)$/);
    f.src = (m ? m[1] : src).replace(/^\.\//, "");
    f.src_line = pos.line;
  }
}
process.stdout.write(JSON.stringify(frames));
