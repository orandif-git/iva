// Draws PNGs from SVG files with Chromium. Used by make_logo.py. Input: a JSON list of {svg, out, w, h, bg, dsf}.
// w and h are the SVG's size in CSS pixels; dsf is the device scale factor (2 gives a file twice as wide and tall).
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const b = await chromium.launch();
  for (const j of jobs) {
    const pg = await b.newPage({ viewport: { width: j.w, height: j.h }, deviceScaleFactor: j.dsf || 1 });
    const data = Buffer.from(fs.readFileSync(j.svg)).toString('base64');
    await pg.setContent(`<html><body style="margin:0;background:${j.bg || 'transparent'}"><div style="width:${j.w}px;height:${j.h}px;display:grid;place-items:center"><img style="width:100%;height:100%;object-fit:contain" src="data:image/svg+xml;base64,${data}"></div></body></html>`);
    await pg.waitForFunction(() => document.images[0].complete);
    await pg.screenshot({ path: j.out, omitBackground: !j.bg });
    await pg.close();
  }
  await b.close();
})();
