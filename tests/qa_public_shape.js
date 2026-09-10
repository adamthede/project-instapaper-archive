// The QA harness behind docs/qa/2026-09-10-public-shape/README.md.
//
//   npm i playwright pngjs pixelmatch && npx playwright install chromium
//   node tests/qa_public_shape.js <scratch-dir> <out-dir>
//
// <scratch-dir> holds `record-reading/` (a `--public` build) and
// `private-before/` (an ordinary build of the same archive). Both are outputs,
// not fixtures - build them first, from the same index and the same day, or
// the "generated" stamp alone will show as a diff.
//
// Playwright is not a dependency of this repo and this is not part of the
// pytest suite: the suite has no browser and the repo has no CI to run one in.
// It is committed so the measurements in that README can be reproduced rather
// than believed.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { PNG } = require('pngjs');
const pixelmatch = require('pixelmatch').default || require('pixelmatch');

const SP = process.argv[2];
const OUT = process.argv[3];
const PUBLIC = path.join(SP, 'record-reading', 'index.html');
const PRIVATE = path.join(SP, 'private-before', 'index.html');

const report = [];
function say(s) { report.push(s); console.log(s); }

async function shot(browser, file, width, dest,
                    { block = false, hideNav = false } = {}) {
  const context = await browser.newContext({
    viewport: { width, height: 900 }, deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  const requests = [];
  const errors = [];
  page.on('request', (r) => requests.push(r.url()));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  if (block) {
    let first = true;
    await page.route('**/*', (route) => {
      if (first) { first = false; return route.continue(); }
      return route.abort();
    });
  }
  await page.goto('file://' + file, { waitUntil: 'load' });
  // The sticky bar is the one element the two pages are MEANT to differ in,
  // and at 390 the private row wraps to a second line, which shifts every
  // pixel below it. Removing it lets the two bodies be compared where they
  // actually sit, rather than through a whole-pixel offset that cannot cancel
  // a fractional one.
  if (hideNav) await page.evaluate(() => document.querySelector('.stickynav').remove());
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.body.scrollWidth,
    innerWidth: window.innerWidth,
    height: document.documentElement.scrollHeight,
    navPosition: document.querySelector('.stickynav')
      ? getComputedStyle(document.querySelector('.stickynav')).position : 'removed',
    navHeight: document.querySelector('.stickynav')
      ? Math.round(document.querySelector('.stickynav').getBoundingClientRect().height) : 0,
    mastheadTop: Math.round(document.querySelector('header.masthead').getBoundingClientRect().top + window.scrollY),
    pageLinks: [...document.querySelectorAll('.pagelinks > *')]
      .map((e) => e.tagName + ':' + e.textContent),
    siteLinks: [...document.querySelectorAll('.sitelinks > *')]
      .map((e) => e.tagName + ':' + e.textContent + ':' + (e.getAttribute('href') || '')),
    anchors: [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href')),
    footer: document.querySelector('footer').textContent.trim().replace(/\s+/g, ' '),
  }));
  if (dest) await page.screenshot({ path: dest, fullPage: true });
  await context.close();
  return { requests, errors, metrics };
}

function diff(a, b, dest, { offsetB = 0, skipTop = 0 } = {}) {
  const A = PNG.sync.read(fs.readFileSync(a));
  const B = PNG.sync.read(fs.readFileSync(b));
  const w = Math.min(A.width, B.width);
  const h = Math.min(A.height - skipTop, B.height - skipTop - offsetB);
  const crop = (img, top) => {
    const out = new PNG({ width: w, height: h });
    PNG.bitblt(img, out, 0, top, w, h, 0, 0);
    return out;
  };
  const ca = crop(A, skipTop); const cb = crop(B, skipTop + offsetB);
  const out = new PNG({ width: w, height: h });
  const n = pixelmatch(ca.data, cb.data, out.data, w, h,
                       { threshold: 0.1, diffMask: true });
  fs.writeFileSync(dest, PNG.sync.write(out));
  // Which horizontal bands differ, so the report can name them.
  const bands = [];
  let run = null;
  for (let y = 0; y < h; y++) {
    let any = false;
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (out.data[i + 3] !== 0) { any = true; break; }
    }
    if (any && !run) run = { from: y, to: y };
    else if (any) run.to = y;
    else if (run) { bands.push(run); run = null; }
  }
  if (run) bands.push(run);
  return { pixels: n, w, h, heightA: A.height, heightB: B.height, bands };
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();

  say('## The press, clause one: nothing is fetched at view time\n');
  const blocked = await shot(browser, PUBLIC, 1400, null, { block: true });
  say('Loaded from `file://` with a route handler that lets the document');
  say('through and aborts every subsequent request, whatever origin it names.\n');
  say('| | |');
  say('|---|---|');
  say(`| requests | ${blocked.requests.length} |`);
  say(`| the request | \`${path.basename(blocked.requests[0] || '')}\` |`);
  say(`| console errors | ${blocked.errors.length} |`);
  say(`| rendered height | ${blocked.metrics.height} px |`);
  say(`| sticky bar position | ${blocked.metrics.navPosition} |`);
  if (blocked.errors.length) say('\n' + blocked.errors.join('\n'));
  say('');

  say('## The chrome\n');
  say('```');
  say('page row  : ' + JSON.stringify(blocked.metrics.pageLinks));
  say('sibling   : ' + JSON.stringify(blocked.metrics.siteLinks, null, 0));
  say('anchors   : ' + JSON.stringify(blocked.metrics.anchors));
  say('footer    : ' + blocked.metrics.footer);
  say('```\n');

  say('## The viewports\n');
  say('| page | width | body.scrollWidth | window.innerWidth | full height |');
  say('|---|---|---|---|---|');
  const shots = {};
  for (const [label, file] of [['public', PUBLIC], ['private', PRIVATE]]) {
    for (const width of [1400, 390]) {
      const dest = path.join(OUT, `2026-09-10-${label}-cover-${width}.png`);
      const r = await shot(browser, file, width, dest);
      shots[`${label}-${width}`] = { dest, ...r };
      say(`| ${label} cover | ${width} | ${r.metrics.scrollWidth} | `
        + `${r.metrics.innerWidth} | ${r.metrics.height} |`);
    }
  }
  say('');

  say('## The fidelity pair\n');
  say('Public against private, same build, same day. `diffMask` on, so a');
  say('pixel is listed only where the two actually differ.\n');
  say('| width | comparison | differing pixels | share | bands (y) |');
  say('|---|---|---|---|---|');
  for (const width of [1400, 390]) {
    const pub = shots[`public-${width}`].metrics;
    const prv = shots[`private-${width}`].metrics;
    const offset = prv.navHeight - pub.navHeight;
    const rows = [['whole page', {}, `2026-09-10-cover-diff-${width}.png`]];
    if (offset) {
      rows.push(['below the bar, private shifted up ' + offset + 'px',
                 { offsetB: offset, skipTop: pub.navHeight },
                 `2026-09-10-cover-diff-${width}-below-bar.png`]);
    }
    rows.length = 1;
    for (const [label, opts, name] of rows) {
      const d = diff(shots[`public-${width}`].dest,
                     shots[`private-${width}`].dest, path.join(OUT, name), opts);
      const share = (d.pixels / (d.w * d.h) * 100).toFixed(3);
      const bands = d.bands.map((b) => `${b.from}-${b.to}`).join(', ') || 'none';
      say(`| ${width} | ${label} | ${d.pixels} | ${share}% | ${bands} |`);
    }
  }
  say('');
  say('### The body, with the sticky bar removed\n');
  say('The bar is the one element the two are meant to differ in, and at 390 the');
  say("private page row wraps to a second line, which moves every pixel below it.");
  say('Removed from both, the bodies are compared where they sit.\n');
  say('| width | differing pixels | share | bands (y) |');
  say('|---|---|---|---|');
  for (const width of [1400, 390]) {
    const files = {};
    for (const [label, file] of [['public', PUBLIC], ['private', PRIVATE]]) {
      const dest = path.join(OUT, `2026-09-10-${label}-body-${width}.png`);
      await shot(browser, file, width, dest, { hideNav: true });
      files[label] = dest;
    }
    const dest = path.join(OUT, `2026-09-10-body-diff-${width}.png`);
    const d = diff(files.public, files.private, dest);
    const share = (d.pixels / (d.w * d.h) * 100).toFixed(3);
    const bands = d.bands.map((b) => `${b.from}-${b.to}`).join(', ') || 'none';
    say(`| ${width} | ${d.pixels} | ${share}% | ${bands} |`);
  }
  say('');
  say('| width | page | sticky bar height | masthead top | full height |');
  say('|---|---|---|---|---|');
  for (const width of [1400, 390]) {
    for (const label of ['public', 'private']) {
      const m = shots[`${label}-${width}`].metrics;
      say(`| ${width} | ${label} | ${m.navHeight} | ${m.mastheadTop} | ${m.height} |`);
    }
  }

  await browser.close();
  fs.writeFileSync(path.join(OUT, 'measurements.md'), report.join('\n') + '\n');
})().catch((e) => { console.error(e); process.exit(1); });
