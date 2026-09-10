// A 1200x750 capture of the top of a page, in a real Playwright viewport.
//
//   node site/capture.js <page.html> <out.jpg> [width] [height]
//
// `newContext({ viewport })`, not `--window-size`. Headless Chrome clamps the
// window on this Mac, and a capture taken that way is a narrow layout cropped
// to the frame rather than a render at the frame's width - which is how an
// earlier record in the index repo shipped a 500px layout labelled 390.
//
// Playwright is not a dependency of this repo. Install it wherever you like
// and point NODE_PATH at the node_modules:
//
//   npm i playwright && npx playwright install chromium
//   PLAYWRIGHT_NODE_PATH=$PWD/node_modules node site/capture.js ...
const [, , src, dest, w = '1200', h = '750'] = process.argv;
if (!src || !dest) {
  console.error('usage: node capture.js <page.html> <out.jpg> [w] [h]');
  process.exit(2);
}

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (err) {
  console.error('cannot require("playwright"): ' + err.message);
  process.exit(3);
}

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: Number(w), height: Number(h) },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();

  // The record must fetch nothing at view time, so the capture is taken under
  // that rule: the document through, everything after it aborted. A page that
  // needed a second request would render here exactly as it renders for a
  // reader with the network gone.
  let first = true;
  await page.route('**/*', (route) => {
    if (first) { first = false; return route.continue(); }
    return route.abort();
  });

  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));

  await page.goto('file://' + require('path').resolve(src),
                  { waitUntil: 'load' });
  await page.screenshot({
    path: dest,
    type: 'jpeg',
    quality: 90,
    clip: { x: 0, y: 0, width: Number(w), height: Number(h) },
  });
  await browser.close();

  if (errors.length) {
    console.error('console errors on the captured page:\n' + errors.join('\n'));
    process.exit(4);
  }
})().catch((err) => { console.error(err); process.exit(1); });
