const fs = require('node:fs');
const path = require('node:path');
let playwrightModule = process.env.PLAYWRIGHT_MODULE;
if (!playwrightModule) {
  try { playwrightModule = require.resolve('playwright'); }
  catch (_) { playwrightModule = path.join(require('node:os').homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
}
const { chromium } = require(playwrightModule);
const url = process.env.LUMEN_QA_URL || 'http://127.0.0.1:8765/';
const out = process.env.LUMEN_QA_OUT || '/private/tmp/lumen-navigation-qa';
fs.mkdirSync(out, { recursive: true });
const report = { url, generatedAt: new Date().toISOString(), checks: [], errors: [] };
function check(name, pass, detail) {
  report.checks.push({ name, pass: !!pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'} ${name} ${JSON.stringify(detail || '')}`);
}
async function state(page) {
  return page.evaluate(() => {
    const audio = document.querySelector('#narration');
    const chapter = window.LUMEN_CHAPTER;
    return {
      time: audio.currentTime, paused: audio.paused, ready: audio.readyState,
      scene: document.querySelector('#scene-stage').dataset.scene,
      expectedScene: chapter.scenes.filter(scene => scene.start <= audio.currentTime).at(-1)?.id,
      active: document.querySelector('.sentence.active')?.id,
      expectedSentence: chapter.paragraphs.flatMap(p => p.sentences).filter(sentence => sentence.start <= audio.currentTime).at(-1)?.id,
      selection: window.__trustedSeekInput || null,
      elapsed: document.querySelector('#elapsed').textContent,
      status: document.querySelector('#play-status').textContent
    };
  });
}
async function press(page, selector, touch) {
  if (touch) await page.locator(selector).tap();
  else await page.locator(selector).click();
  await page.waitForTimeout(200);
  return state(page);
}
async function track(page, fraction, touch) {
  const box = await page.locator('#seek').boundingBox();
  const x = box.x + 6 + (box.width - 12) * fraction;
  const y = box.y + box.height / 2;
  if (touch) await page.touchscreen.tap(x, y);
  else await page.mouse.click(x, y);
  await page.waitForTimeout(300);
  return state(page);
}
function selectedCorrectly(result, fraction, playing) {
  return result.selection?.trusted && Math.abs(result.selection.fraction - fraction) < .015 &&
    Math.abs(result.time - result.selection.time) < (playing ? 1 : .2) &&
    result.paused === !playing && result.scene === result.expectedScene && result.active === result.expectedSentence;
}
(async () => {
  const launchOptions = { headless: true };
  if (process.env.CHROME_PATH) launchOptions.executablePath = process.env.CHROME_PATH;
  else if (process.platform === 'darwin' && fs.existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')) launchOptions.executablePath = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch(launchOptions);
  for (const config of [{ width: 1440, height: 1000, touch: false }, { width: 390, height: 844, touch: true }, { width: 320, height: 844, touch: true }]) {
    const context = await browser.newContext({ viewport: { width: config.width, height: config.height }, isMobile: config.touch, hasTouch: config.touch, deviceScaleFactor: 1 });
    const page = await context.newPage();
    page.on('pageerror', error => report.errors.push(String(error)));
    await page.addInitScript(() => {
      document.addEventListener('input', event => {
        if (event.target.id === 'seek') {
          window.__trustedSeekInput = {
            time: Number(event.target.value), fraction: Number(event.target.value) / Number(event.target.max),
            trusted: event.isTrusted
          };
        }
      }, true);
    });
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelector('#narration')?.readyState >= 1);
    const prefix = config.width + (config.touch ? ' touch' : ' mouse');
    const initial = await state(page);
    check(prefix + ' fresh load stays paused', initial.paused && initial.time === 0, initial);
    const forwardFresh = await press(page, '#forward', config.touch);
    check(prefix + ' forward immediately after metadata', Math.abs(forwardFresh.time - 15) < .2 && forwardFresh.paused, forwardFresh);
    const backFresh = await press(page, '#back', config.touch);
    check(prefix + ' rewind immediately after metadata', backFresh.time < .2 && backFresh.paused, backFresh);
    const pausedTrack = await track(page, .55, config.touch);
    check(prefix + ' paused timeline click or tap', selectedCorrectly(pausedTrack, .55, false), pausedTrack);
    await press(page, '#play', config.touch);
    await page.waitForTimeout(900);
    const playing = await state(page);
    const forwardPlaying = await press(page, '#forward', config.touch);
    check(prefix + ' forward while playing', !forwardPlaying.paused && Math.abs(forwardPlaying.time - playing.time - 15) < 1, { before: playing, after: forwardPlaying });
    const backPlaying = await press(page, '#back', config.touch);
    check(prefix + ' rewind while playing', !backPlaying.paused && Math.abs(backPlaying.time - forwardPlaying.time + 15) < 1, { before: forwardPlaying, after: backPlaying });
    const playingTrack = await track(page, .3, config.touch);
    check(prefix + ' playing timeline click or tap', selectedCorrectly(playingTrack, .3, true), playingTrack);
    if (!config.touch) {
      const box = await page.locator('#seek').boundingBox();
      const y = box.y + box.height / 2;
      await page.mouse.move(box.x + 6 + (box.width - 12) * .3, y);
      await page.mouse.down();
      await page.mouse.move(box.x + 6 + (box.width - 12) * .75, y, { steps: 20 });
      await page.mouse.up();
      await page.waitForTimeout(300);
      const dragged = await state(page);
      check('desktop timeline drag while playing', selectedCorrectly(dragged, .75, true), dragged);
      await press(page, '#play', false);
      await page.mouse.move(box.x + 6 + (box.width - 12) * .75, y);
      await page.mouse.down();
      await page.mouse.move(box.x + 6 + (box.width - 12) * .15, y, { steps: 20 });
      await page.mouse.up();
      await page.waitForTimeout(300);
      const pausedDragged = await state(page);
      check('desktop timeline drag while paused', selectedCorrectly(pausedDragged, .15, false), pausedDragged);
    } else {
      const cdp = await context.newCDPSession(page);
      const dragTouch = async (from, to) => {
        const box = await page.locator('#seek').boundingBox();
        const point = fraction => ({ x: box.x + 6 + (box.width - 12) * fraction, y: box.y + box.height / 2 });
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point(from)] });
        for (let step = 1; step <= 20; step++) {
          await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [point(from + (to - from) * step / 20)] });
          await page.waitForTimeout(15);
        }
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await page.waitForTimeout(300);
        return state(page);
      };
      const touchDragged = await dragTouch(.3, .75);
      check(prefix + ' timeline drag while playing', selectedCorrectly(touchDragged, .75, true), touchDragged);
      await press(page, '#play', true);
      const pausedTouchDragged = await dragTouch(.75, .15);
      check(prefix + ' timeline drag while paused', selectedCorrectly(pausedTouchDragged, .15, false), pausedTouchDragged);
      await cdp.detach();
    }
    await context.close();
  }
  check('no browser JavaScript errors', report.errors.length === 0, report.errors);
  report.passed = report.checks.filter(c => c.pass).length;
  report.failed = report.checks.filter(c => !c.pass).length;
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify({ passed: report.passed, failed: report.failed, report: path.join(out, 'report.json') }));
  process.exitCode = report.failed ? 1 : 0;
})().catch(error => {
  report.errors.push(String(error.stack));
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  console.error(error);
  process.exit(1);
});
