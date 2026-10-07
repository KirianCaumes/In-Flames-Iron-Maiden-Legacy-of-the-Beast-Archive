// Render site/assets/social-preview.jpg, the 1200 x 630 image of the link previews (Discord, social networks):
// Jesterhead in battle 3's arena (Hell Gate, present), arms spread (Buff clip at 1.9 s), seen from the front.
//
// usage: node tools/social_preview.mjs <url of the site, served locally> [output.jpg]
//        e.g.  (cd site && python3 -m http.server 8000) &  node tools/social_preview.mjs http://localhost:8000/
// needs puppeteer-core (npm install --no-save puppeteer-core) and Chrome (CHROME=/path/to/chrome to override)
import puppeteer from 'puppeteer-core';

const [,, url, out = new URL('../site/assets/social-preview.jpg', import.meta.url).pathname] = process.argv;
if (!url) { console.error('usage: node tools/social_preview.mjs <url> [output.jpg]'); process.exit(1); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

const browser = await puppeteer.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'] });
const page = await browser.newPage();
await page.setViewport({ width: 1200, height: 630, deviceScaleFactor: 1 });
await page.goto(url, { waitUntil: 'networkidle0', timeout: 120000 });
await page.waitForFunction(() => window.__viewer, { timeout: 120000 });
// the viewer alone, at the size of the image
await page.addStyleTag({ content: '.masthead, .panel, .hint, .transport, .fs { display: none !important }'
  + ' .viewer { grid-template-columns: 1fr !important; height: 630px !important; min-height: 0 !important; border: 0 !important }' });
await page.evaluate(() => {
  document.getElementById('enemies').checked = false; document.getElementById('period').value = 'present';
  window.__viewer.setBattle('3');
});
await page.waitForFunction(() => window.__viewer.arena.key === 'road2', { timeout: 120000 });
await page.evaluate(() => {
  const v = window.__viewer;
  v.play('buff', true); document.getElementById('play').click();             // paused, without the effects
  for (const j of Object.values(v.jesters)) if (j.root.visible) j.scrub(1.9);
  v.camera.position.set(2.3, 2.7, 10.6); v.controls.target.set(-0.95, 2.4, 0); v.controls.update();
});
await sleep(2000);                                                             // textures
await page.screenshot({ path: out, type: 'jpeg', quality: 88, clip: { x: 0, y: 0, width: 1200, height: 630 } });
await browser.close();
console.log('wrote', out);
