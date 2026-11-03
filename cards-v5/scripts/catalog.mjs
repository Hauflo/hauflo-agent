// node scripts/catalog.mjs <out-dir>  → <out-dir>/catalog.html, generated from the real source (esbuild → node).
import { mkdirSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import { generate } from "./gen.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export async function catalog(outDir) {
  generate();
  const { build } = createRequire(import.meta.url)(join(resolve(ROOT, "../../backend"), "node_modules/esbuild"));
  const tmp = mkdtempSync(join(tmpdir(), "hf5-cat-"));
  const file = join(tmp, "catalog.mjs");
  await build({ entryPoints: [join(ROOT, "src/catalog/catalog.js")], bundle: true, format: "esm", platform: "node", outfile: file, loader: { ".json": "json" }, logLevel: "error" });
  const { catalogHtml } = await import(pathToFileURL(file).href);
  mkdirSync(outDir, { recursive: true });
  writeFileSync(join(outDir, "catalog.html"), catalogHtml());
  rmSync(tmp, { recursive: true, force: true });
  return join(outDir, "catalog.html");
}
if (import.meta.url === `file://${process.argv[1]}`) console.log(await catalog(resolve(process.argv[2] || "catalog-out")));
