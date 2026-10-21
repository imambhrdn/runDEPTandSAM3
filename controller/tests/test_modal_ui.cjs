// NODE_PATH=<playwright-core parent> CHROMIUM_PATH=<chromium> TEST_VIDEO_PATH=<finite-duration WebM> node controller/tests/test_modal_ui.cjs
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright-core');

(async () => {
  const root = path.resolve(__dirname, '../static');
  const files = {'/modal': ['modal.html', 'text/html'], '/static/modal.css': ['modal.css', 'text/css'], '/static/modal.js': ['modal.js', 'text/javascript']};
  const server = http.createServer(async (request, response) => {
    const file = files[new URL(request.url, 'http://localhost').pathname];
    if (!file) { response.writeHead(404).end(); return; }
    try {
      const content = await fs.readFile(path.join(root, file[0]));
      response.writeHead(200, {'Content-Type': file[1]}).end(content);
    } catch { response.writeHead(500).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox']});
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
    const errors = [], apiCalls = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/worker/') || request.method() !== 'GET') {
        apiCalls.push(request.url()); await route.abort(); return;
      }
      if (url.hostname !== '127.0.0.1') { external.push(request.url()); await route.abort(); return; }
      await route.continue();
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/modal`);
    assert(await page.locator('#process').isDisabled());
    assert(await page.locator('#source-timeline').isDisabled());
    assert.equal(await page.locator('.balance strong').innerText(), 'US$ —');
    assert.equal(await page.locator('.vast-link').getAttribute('href'), '/');
    await page.locator('#compare-slider').fill('75');
    assert.equal(await page.locator('#comparison').evaluate(el => el.style.getPropertyValue('--split')), '75%');
    await page.locator('#tab-sam').click();
    assert.equal(await page.locator('#comparison').getAttribute('data-mode'), 'sam');
    await page.keyboard.press('End');
    assert.equal(await page.locator('#tab-face').getAttribute('aria-selected'), 'true');
    await page.locator('#tab-depth').click();
    await page.locator('#compare-slider').fill('50');
    if (process.env.SCREENSHOT_PATH) await page.screenshot({path: process.env.SCREENSHOT_PATH, fullPage: true});
    await page.locator('#source-file').setInputFiles({name: 'invalid.txt', mimeType: 'text/plain', buffer: Buffer.from('not video')});
    assert((await page.locator('#error').innerText()).includes('Format tidak didukung'));
    const fixture = await fs.readFile(process.env.TEST_VIDEO_PATH);
    await page.locator('#source-file').setInputFiles({name: 'sample.webm', mimeType: 'video/webm', buffer: fixture});
    await page.locator('#selection-stage').waitFor();
    assert(await page.locator('#source-timeline').isEnabled());
    const canvasBox = await page.locator('#source-canvas').boundingBox();
    const stageBox = await page.locator('#selection-stage').boundingBox();
    assert(Math.abs(canvasBox.width - stageBox.width) < 1 && Math.abs(canvasBox.height - stageBox.height) < 1, 'Selection marker and frame must share coordinates');
    await page.locator('#selection-stage').click();
    assert((await page.locator('#selection-info').innerText()).match(/Objek: (79|80), (59|60)/));
    await page.locator('#source-timeline').fill('1');
    await page.waitForFunction(() => document.querySelector('#source-time').textContent.startsWith('1.00 /'));
    await page.locator('#selection-stage').focus();
    await page.keyboard.press('Enter');
    assert((await page.locator('#selection-info').innerText()).includes('80, 60'));
    assert((await page.locator('#selection-info').innerText()).includes('1.00 dtk'));
    assert(await page.locator('#selection-mark').isVisible());
    await page.keyboard.press('ArrowRight');
    assert((await page.locator('#selection-info').innerText()).includes('81, 60'));
    await page.locator('#source-timeline').fill('1.5');
    assert(await page.locator('#selection-mark').isHidden());
    await page.waitForFunction(() => document.querySelector('#source-time').textContent.startsWith('1.50 /'));
    await page.locator('#reset-frame').click();
    await page.waitForFunction(() => document.querySelector('#source-time').textContent.startsWith('0.00 /'));
    assert(await page.locator('#process').isDisabled());
    for (const width of [1440, 1024, 390]) {
      await page.setViewportSize({width, height: 1000});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Overflow at ${width}px`);
    }
    await page.locator('#source-file').setInputFiles({name: 'broken.webm', mimeType: 'video/webm', buffer: Buffer.from('broken')});
    await page.waitForFunction(() => document.querySelector('#error').textContent.includes('Browser tidak bisa'));
    assert(await page.locator('#source-timeline').isDisabled());
    assert(await page.locator('#selection-stage').isHidden());
    assert(await page.locator('#compare-canvas').isHidden());
    assert.deepEqual(errors, []);
    assert.deepEqual(apiCalls, [], 'No upload, rental, or worker calls allowed');
    assert.deepEqual(external, [], 'No third-party requests allowed');
    console.log('Modal UI: layout, tabs, comparison, local seek/selection, invalid video, disabled processing, zero API calls: OK');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
