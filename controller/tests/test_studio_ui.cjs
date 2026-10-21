// Run with NODE_PATH pointing to playwright-core and CHROMIUM_PATH to a browser.
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright-core');

(async () => {
  const root = path.resolve(__dirname, '../static');
  const files = {'/': ['index.html', 'text/html'], '/static/style.css': ['style.css', 'text/css'], '/static/app.js': ['app.js', 'text/javascript']};
  const server = http.createServer(async (request, response) => {
    const file = files[new URL(request.url, 'http://localhost').pathname];
    if (!file) { response.writeHead(404).end(); return; }
    try {
      response.writeHead(200, {'Content-Type': file[1]});
      response.end(await fs.readFile(path.join(root, file[0])));
    } catch {
      response.writeHead(500).end();
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox']});
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}, reducedMotion: 'reduce'});
    const errors = [], unexpected = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (endpoint === '/api/upload') {
        assert.equal(request.method(), 'POST');
        await route.fulfill({json: {id: 'upload-test', state: 'ready', stage: 'Pilih subjek', width: 160, height: 120, seconds: 2, artifacts: {}, frame_url: ''}});
        return;
      }
      assert.equal(request.method(), 'GET', 'UI preview must never start a paid job');
      const data = endpoint === '/api/config' ? {machine_id: 0, max_price: .099, min_vram: 12, max_minutes: 45, max_upload_mb: 250, auto_destroy: false} : [];
      if (!['/api/config', '/api/jobs'].includes(endpoint)) unexpected.push(endpoint);
      await route.fulfill({json: data});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.getByText('Belum ada proses. Unggah klip pertama Anda untuk mulai.').waitFor();
    assert.equal(await page.locator('.output-card').count(), 4);
    assert.equal(await page.locator('.balance-placeholder strong').innerText(), 'US$ —');
    assert(await page.locator('#face').isVisible());
    assert(await page.locator('#face').isDisabled());
    const balance = await page.locator('.balance-placeholder').boundingBox();
    assert(balance.x > 1000, 'Balance belongs at the top right');
    assert(!(await page.locator('.gpu-panel').innerText()).includes('US$'));
    assert((await page.locator('#session-note').innerText()).includes('Penghapusan GPU manual'));
    await page.locator('[data-preview="depth"]').click();
    assert(await page.locator('#preview-dialog').evaluate(dialog => dialog.open));
    assert.equal(await page.locator('#preview-dialog input[type="range"]').count(), 0);
    await page.keyboard.press('Escape');
    assert(!(await page.locator('#preview-dialog').evaluate(dialog => dialog.open)));
    await page.locator('[data-preview="face"]').click();
    assert.equal(await page.locator('#preview-title').innerText(), 'Face Guidance');
    assert(await page.locator('#preview-content .face-mesh').isVisible());
    await page.keyboard.press('Escape');
    await page.locator('#demo').click();
    assert.equal(await page.locator('.pipeline-step.active').count(), 1);
    await page.waitForFunction(() => document.querySelector('#demo-counter').textContent === '4 / 4 tahap');
    assert.equal(await page.locator('.pipeline-step.done').count(), 4);
    await page.evaluate(() => render({id: 'test', state: 'ready', stage: 'Pilih subjek', width: 960, height: 540, artifacts: {}, frame_url: ''}));
    assert(await page.locator('#face').isEnabled());
    assert(await page.locator('#selection-timeline').isDisabled());
    assert.equal(await page.locator('#pick #selection-timeline').count(), 1);
    await page.locator('#stage').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#start').isEnabled(), true);
    assert((await page.locator('#point').innerText()).includes('480, 270'));
    // Generate a finite-duration fixture: ffmpeg -f lavfi -i color=c=blue:s=160x120:r=24:d=2 -c:v libvpx test.webm
    const fixture = await fs.readFile(process.env.TEST_VIDEO_PATH);
    await page.locator('#file').setInputFiles({name: 'test.webm', mimeType: 'video/webm', buffer: fixture});
    await page.locator('#source-video').waitFor();
    assert(await page.locator('#selection-timeline').isEnabled());
    await page.locator('#selection-timeline').fill('1');
    await page.waitForFunction(() => !document.querySelector('#source-video').seeking);
    assert.equal(await page.locator('#source-video').evaluate(video => video.currentTime), 1);
    await page.locator('#stage').click({position: {x: 80, y: 60}});
    const selected = await page.evaluate(() => point);
    assert(Math.abs(selected.x - 80) <= 1 && Math.abs(selected.y - 60) <= 1, 'Click maps to displayed video coordinates');
    assert((await page.locator('#point').innerText()).includes('belum bisa dikirim ke worker'));
    assert(await page.locator('#start').isDisabled());
    await page.evaluate(() => render(current));
    assert(await page.locator('#start').isDisabled(), 'Polling must not enable starting on a later frame');
    await page.locator('#first-frame').click();
    await page.waitForFunction(() => !document.querySelector('#source-video').seeking);
    assert(await page.locator('#mark').isHidden());
    assert(await page.locator('#start').isDisabled());
    await page.locator('#stage').click({position: {x: 80, y: 60}});
    assert(await page.locator('#start').isEnabled());
    await page.locator('#selection-timeline').fill('1.5');
    assert(await page.locator('#mark').isHidden());
    assert(await page.locator('#start').isDisabled());
    await page.evaluate(() => render({id: 'test', state: 'failed', stage: 'Proses gagal', gpu_name: 'RTX A4000', error: 'Contoh error', artifacts: {}, instance_ids: [123]}));
    assert.equal(await page.locator('#gpu-name').innerText(), 'RTX A4000');
    assert.equal(await page.locator('#job-error').innerText(), 'Contoh error');
    assert.equal(await page.locator('#cancel').isVisible(), false);
    assert(await page.locator('#face').isDisabled());
    await page.evaluate(() => { document.querySelector('#result').hidden = true; });
    if (process.env.SCREENSHOT_PATH) await page.screenshot({path: process.env.SCREENSHOT_PATH, fullPage: true});
    for (const width of [1440, 1024, 390]) {
      await page.setViewportSize({width, height: 1000});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Horizontal overflow at ${width}px`);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    console.log('Studio UI: desktop/mobile, placeholders, selection timeline, seek/click, first-frame guard, keyboard, status: OK. No rental calls.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
