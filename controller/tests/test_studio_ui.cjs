// NODE_PATH=<playwright-core parent> CHROMIUM_PATH=<chromium> TEST_VIDEO_PATH=<video> node controller/tests/test_studio_ui.cjs
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright-core');

(async () => {
  const root = path.resolve(__dirname, '../static');
  const server = http.createServer(async (req, res) => {
    const file = {'/': 'index.html', '/static/app.js': 'app.js', '/static/style.css': 'style.css'}[req.url];
    if (!file) return res.writeHead(404).end();
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(await fs.readFile(path.join(root, file)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH, headless: true, args: ['--no-sandbox']});
    const page = await browser.newPage();
    const errors = [], jobs = [], starts = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/api/**', async route => {
      const req = route.request(), url = new URL(req.url());
      let data;
      if (url.pathname === '/api/config') data = {max_upload_mb: 250, max_minutes: 45, auto_destroy: false, balance: 1.72};
      else if (url.pathname === '/api/jobs') data = jobs;
      else if (url.pathname === '/api/upload') {
        const id = String(jobs.length + 1).padStart(24, '0');
        data = {id, state: 'ready', stage: 'Pilih subjek', width: 160, height: 120, artifacts: {}, frame_url: '/frame.svg'};
        jobs.push(data);
      } else if (url.pathname === '/api/batches/start') {
        const body = req.postDataJSON(); starts.push(body);
        assert.equal(req.headers()['x-controller'], '1');
        for (const [i, j] of jobs.entries()) Object.assign(j, {state: 'queued', batch_owner: jobs[0].id, batch_index: i, stage: 'Antrean'});
        data = jobs;
      } else throw new Error(`Unexpected API: ${url.pathname}`);
      await route.fulfill({json: data});
    });
    await page.route('**/frame.svg', route => route.fulfill({contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120"><rect width="160" height="120" fill="green"/></svg>'}));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(() => !document.querySelector('#file').disabled);
    assert.equal(await page.locator('main > section').count(), 2);
    assert.equal(await page.locator('#demo,.sidebar,.pipeline').count(), 0);
    const buffer = await fs.readFile(process.env.TEST_VIDEO_PATH);
    await page.locator('#file').setInputFiles([{name: 'first.webm', mimeType: 'video/webm', buffer}, {name: 'second.webm', mimeType: 'video/webm', buffer}]);
    await page.waitForFunction(() => document.querySelectorAll('.queue-row').length === 2 && !document.querySelector('#file').disabled);
    await page.waitForFunction(() => !document.querySelector('#source-video').hidden);
    await page.locator('#stage').focus(); await page.keyboard.press('Enter');
    assert(await page.locator('#start').isDisabled());
    await page.locator('.queue-row').nth(1).locator('button').first().click();
    await page.waitForFunction(() => !document.querySelector('#source-video').hidden);
    await page.locator('#stage').focus(); await page.keyboard.press('Enter');
    assert(await page.locator('#start').isEnabled());
    await page.locator('#timeline').evaluate(el => { el.value = '0.5'; el.dispatchEvent(new Event('input')); });
    await page.waitForFunction(() => !document.querySelector('#source-video').seeking);
    await page.locator('#stage').focus(); await page.keyboard.press('Enter');
    assert(await page.locator('#start').isDisabled());
    await page.locator('#first').click();
    await page.waitForFunction(() => !document.querySelector('#source-video').seeking && document.querySelector('#source-video').currentTime === 0);
    await page.locator('#stage').focus(); await page.keyboard.press('Enter');
    await page.locator('#start').click();
    await page.waitForFunction(() => document.querySelectorAll('#results article').length === 2);
    assert.equal(starts.length, 1); assert.equal(starts[0].jobs.length, 2);
    assert(starts[0].jobs.every(j => j.params.face_guidance === false && j.params.x === 80 && j.params.y === 60));
    jobs[0].artifacts = {'composite.mp4': '/result.mp4'};
    await page.waitForFunction(() => document.querySelector('#results video'));
    assert.equal(await page.locator('#results a').count(), 1);
    assert.equal(await page.locator('[data-artifact="depth_preview.mp4"]').count(), 0);
    for (const width of [1440, 1024, 390]) {
      await page.setViewportSize({width, height: 1000});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Overflow at ${width}`);
    }
    assert.deepEqual(errors, []);
    console.log('Studio: multiple uploads, per-video selection, first-frame guard, one batch request, real artifacts, mobile OK. No rentals.');
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(e => {console.error(e); process.exitCode = 1;});
