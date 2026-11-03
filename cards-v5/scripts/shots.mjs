// node scripts/shots.mjs <catalog.html> <out-dir>  → Chromium screenshots of the catalog (dark/light × 390/1440) + console errors
// + overflow check (no horizontal page scroll at 390). Exit 1 on any error. Needs playwright-core (testenv/readme: npm ci).
import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { existsSync } from "node:fs";

const HERE = dirname(fileURLToPath(import.meta.url));
const pw = [resolve(HERE, "../../../testenv/readme/node_modules/playwright-core/index.mjs"), "/Users/ivv/dev/easyha/testenv/readme/node_modules/playwright-core/index.mjs"].find(existsSync);
const { chromium } = await import(pathToFileURL(pw).href);
const [file, out] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const b = await chromium.launch();
const problems = [];
for (const mode of ["dark", "light"]) {
  for (const w of [390, 1440]) {
    const p = await b.newPage({ viewport: { width: w, height: 900 } });
    p.on("console", (m) => m.type() === "error" && problems.push(`${mode}/${w} console: ${m.text()}`));
    p.on("pageerror", (e) => problems.push(`${mode}/${w} pageerror: ${e.message}`));
    await p.goto(pathToFileURL(resolve(file)).href + `?mode=${mode}`);
    const over = await p.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    if (over > 1) problems.push(`${mode}/${w}: horizontal overflow ${over}px`);
    await p.screenshot({ path: join(out, `catalog-${mode}-${w}.png`), fullPage: true });
    await p.close();
  }
}
await b.close();
console.log(problems.length ? problems.join("\n") : "catalog: no console errors, no overflow");
process.exit(problems.length ? 1 : 0);
