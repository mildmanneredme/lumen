const fs = require('node:fs');
const path = require('node:path');
let playwrightModule = process.env.PLAYWRIGHT_MODULE;
if (!playwrightModule) {
  try { playwrightModule = require.resolve('playwright'); }
  catch (_) { playwrightModule = path.join(require('node:os').homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
}
const { chromium } = require(playwrightModule);

const url = process.env.LUMEN_QA_URL || 'http://127.0.0.1:8765/';
const out = process.env.LUMEN_QA_OUT || '/private/tmp/lumen-qa';
fs.mkdirSync(out, { recursive: true });
const report = { url, generatedAt: new Date().toISOString(), checks: [], errors: [], screenshots: [] };
function check(name, pass, detail) {
  report.checks.push({ name, pass: !!pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'} ${name} ${JSON.stringify(detail || '')}`);
}
async function state(page) {
  return page.evaluate(() => {
    const a = document.querySelector('#narration');
    const chapter = window.LUMEN_CHAPTER;
    const current = chapter.paragraphs.flatMap(p => p.sentences).filter(s => s.start <= a.currentTime).at(-1);
    const expectedScene = chapter.scenes.filter(s => s.start <= a.currentTime).at(-1);
    return {
      time: a.currentTime, paused: a.paused, ended: a.ended, duration: a.duration, rate: a.playbackRate,
      active: document.querySelector('.sentence.active')?.id || null, expectedSentence: current?.id || null,
      activeCount: document.querySelectorAll('.sentence.active').length,
      scene: document.querySelector('#scene-stage').dataset.scene, expectedScene: expectedScene?.id,
      image: document.querySelector('#scene-image').getAttribute('src'),
      sceneCounter: document.querySelector('#scene-number').textContent,
      expectedCounter: String(chapter.scenes.indexOf(expectedScene) + 1).padStart(2, '0') + ' / ' + String(chapter.scenes.length).padStart(2, '0'),
      elapsed: document.querySelector('#elapsed').textContent,
      status: document.querySelector('#play-status').textContent,
      playLabel: document.querySelector('#play').getAttribute('aria-label'),
      follow: document.querySelector('#follow').getAttribute('aria-pressed'),
      scrollTop: document.querySelector('#reading-pane').scrollTop,
      total: document.querySelector('#total').textContent
    };
  });
}
async function seekTo(page, seconds) {
  await page.locator('#seek').evaluate((el, value) => {
    el.value = String(value); el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }, seconds);
  await page.waitForTimeout(250);
  return state(page);
}
async function assertPausedCue(page, seconds, label) {
  await seekTo(page, seconds);
  await page.waitForFunction(() => {
    const a = document.querySelector('#narration');
    const scene = window.LUMEN_CHAPTER.scenes.filter(s => s.start <= a.currentTime).at(-1);
    return scene && document.querySelector('#scene-image').getAttribute('src') === 'assets/' + scene.id + '.png';
  }, null, { timeout: 5000 });
  const result = await state(page);
  check(label, result.paused && result.active === result.expectedSentence && result.scene === result.expectedScene && result.image === 'assets/' + result.expectedScene + '.png' && result.sceneCounter === result.expectedCounter, result);
}
async function screenshot(page, name) {
  const file = path.join(out, name + '.png');
  await page.screenshot({ path: file }); report.screenshots.push(file);
}

(async () => {
  const launchOptions = { headless: true };
  if (process.env.CHROME_PATH) launchOptions.executablePath = process.env.CHROME_PATH;
  else if (process.platform === 'darwin' && fs.existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')) launchOptions.executablePath = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.on('pageerror', e => report.errors.push(String(e)));
  page.on('console', e => { if (e.type() === 'error') report.errors.push(e.text()); });
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.querySelector('#narration').readyState >= 1);
  const ranges = await page.request.get(url + 'assets/chapter-001-pilot.mp3', { headers: { Range: 'bytes=100000-100099' } });
  check('audio server supports byte ranges', ranges.status() === 206 && ranges.headers()['content-range'] === 'bytes 100000-100099/5124278', { status: ranges.status(), range: ranges.headers()['content-range'], acceptRanges: ranges.headers()['accept-ranges'] });
  check('fresh load ready without autoplay', (await state(page)).paused && (await state(page)).time === 0, await state(page));
  await screenshot(page, 'desktop-opening');

  await page.locator('#play').click();
  await page.waitForTimeout(10000);
  const played = await state(page);
  check('real ten-second playback advances and highlights', !played.paused && played.time >= 8 && played.time < 13 && played.active === played.expectedSentence && played.activeCount === 1, played);
  await page.locator('#play').click();
  const pausedAt = (await state(page)).time;
  await page.waitForTimeout(700);
  const paused = await state(page);
  check('pause freezes audio time', paused.paused && Math.abs(paused.time - pausedAt) < .02, paused);

  for (const time of [161.42, 161.62, 206.36, 206.56, 161.4, 14]) {
    const result = await seekTo(page, time);
    check(`seek ${time} synchronizes sentence and scene`, Math.abs(result.time - time) < .11 && result.active === result.expectedSentence && result.scene === result.expectedScene, result);
  }
  const cues = await page.evaluate(() => window.LUMEN_CHAPTER.scenes.map(s => ({ id: s.id, start: s.start })));
  for (const cue of cues) {
    if (cue.start > 0) await assertPausedCue(page, cue.start - .2, `cue ${cue.id} before boundary`);
    await assertPausedCue(page, cue.start + .2, `cue ${cue.id} after boundary`);
  }
  for (const cue of [...cues].reverse()) {
    await assertPausedCue(page, cue.start + .2, `reverse cue ${cue.id}`);
    if (cue.start > 0) await assertPausedCue(page, cue.start - .2, `reverse cue ${cue.id} before boundary`);
  }
  await seekTo(page, 90);
  await page.locator('#forward').click();
  await page.waitForTimeout(100);
  const forward = await state(page);
  check('forward fifteen seconds', Math.abs(forward.time - 105) < .2, forward);
  await page.locator('#back').click();
  await page.waitForTimeout(100);
  const back = await state(page);
  check('rewind fifteen seconds', Math.abs(back.time - 90) < .2, back);

  await page.locator('#speed').selectOption('2');
  await page.locator('#play').click();
  const speedStart = (await state(page)).time;
  await page.waitForTimeout(1100);
  const sped = await state(page);
  check('double speed advances at selected rate', sped.rate === 2 && sped.time - speedStart > 1.6 && sped.time - speedStart < 3, sped);
  await page.locator('#play').click();
  await seekTo(page, 211);
  await screenshot(page, 'desktop-personal-connection');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(350);
  const resumed = await state(page);
  check('reload resumes timestamp rate scene while paused', Math.abs(resumed.time - 211) < .2 && resumed.rate === 2 && resumed.paused && resumed.scene === resumed.expectedScene, resumed);

  await page.locator('#reading-pane').hover();
  await page.mouse.wheel(0, 180);
  await page.waitForTimeout(150);
  check('wheel suspends narration follow', (await state(page)).follow === 'false', await state(page));
  await page.locator('#follow').click();
  check('follow control restores narration follow', (await state(page)).follow === 'true', await state(page));

  await page.locator('#settings-open').click();
  check('reading settings modal opens', await page.locator('#settings').evaluate(el => el.open), await page.locator('#settings').getAttribute('aria-labelledby'));
  await page.locator('#text-size').evaluate(el => { el.value = '28'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  check('text size changes', await page.locator('#prose').evaluate(el => getComputedStyle(el).fontSize === '28px'), await page.locator('#text-size-value').textContent());
  await page.keyboard.press('Escape');
  check('escape closes settings', !(await page.locator('#settings').evaluate(el => el.open)));
  await page.locator('#text-size').evaluate(el => { el.value = '20'; el.dispatchEvent(new Event('input', { bubbles: true })); });

  const finalCue = cues.at(-1);
  await page.locator('#speed').selectOption('1');
  await assertPausedCue(page, finalCue.start - .6, 'position immediately before final cue');
  await page.locator('#play').click();
  await page.waitForFunction(id => document.querySelector('#scene-stage').dataset.scene === id && document.querySelector('#scene-incoming').classList.contains('visible'), finalCue.id, { timeout: 5000 });
  const fading = await page.evaluate(() => ({ duration: getComputedStyle(document.querySelector('#scene-incoming')).transitionDuration, visible: document.querySelector('#scene-incoming').classList.contains('visible'), incoming: document.querySelector('#scene-incoming').getAttribute('src'), base: document.querySelector('#scene-image').getAttribute('src'), paused: document.querySelector('#narration').paused }));
  check('final cue crossfades for 1.25 seconds during playback', !fading.paused && fading.visible && fading.duration === '1.25s' && fading.incoming === 'assets/' + finalCue.id + '.png' && fading.base !== fading.incoming, fading);
  await page.waitForFunction(id => document.querySelector('#scene-image').getAttribute('src') === 'assets/' + id + '.png' && !document.querySelector('#scene-incoming').classList.contains('visible'), finalCue.id, { timeout: 3000 });
  check('final cue crossfade completes without stale art', (await state(page)).image === 'assets/' + finalCue.id + '.png', await state(page));
  await screenshot(page, 'desktop-final-cue');
  await page.locator('#play').click();

  const duration = await page.evaluate(() => window.LUMEN_CHAPTER.duration);
  await seekTo(page, duration - .3);
  await page.locator('#play').click();
  await page.waitForFunction(() => document.querySelector('#narration').ended, null, { timeout: 5000 });
  const ended = await state(page);
  check('end exposes replay', ended.paused && ended.ended && ended.playLabel === 'Replay audiobook' && ended.status === 'End of the pilot', ended);
  await page.locator('#replay').click();
  await page.waitForTimeout(1500);
  const replayed = await state(page);
  check('end replay restarts first scene and artwork', replayed.time < 3 && !replayed.paused && replayed.scene === cues[0].id && replayed.image === 'assets/' + cues[0].id + '.png' && replayed.sceneCounter === replayed.expectedCounter, replayed);
  await page.locator('#play').click();

  for (const width of [390, 320]) {
    const mobile = await browser.newContext({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
    const mp = await mobile.newPage();
    await mp.goto(url, { waitUntil: 'networkidle' });
    await mp.waitForTimeout(200);
    const layout = await mp.evaluate(() => ({ width: window.innerWidth, scrollWidth: document.documentElement.scrollWidth, controls: ['back', 'play', 'forward', 'seek', 'speed', 'follow', 'settings-open'].map(id => { const el = document.getElementById(id), r = el.getBoundingClientRect(); return { id, label: el.getAttribute('aria-label') || el.textContent.trim(), x: r.x, y: r.y, width: r.width, height: r.height, inViewport: r.x >= 0 && r.right <= innerWidth && r.y >= 0 && r.bottom <= innerHeight }; }) }));
    check(`mobile ${width} has no horizontal overflow and visible controls`, layout.scrollWidth <= width && layout.controls.every(c => c.inViewport), layout);
    const small = layout.controls.filter(c => ['back', 'play', 'forward', 'speed'].includes(c.id) && (c.width < 44 || c.height < 44));
    check(`mobile ${width} transport targets at least 44px`, small.length === 0, small);
    await screenshot(mp, `mobile-${width}-opening`);
    await mp.locator('#settings-open').click();
    await mp.locator('#text-size').evaluate(el => { el.value = '28'; el.dispatchEvent(new Event('input', { bubbles: true })); });
    await mp.locator('#settings-close').click();
    check(`mobile ${width} maximum text size no overflow`, await mp.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.querySelector('#reading-pane').scrollWidth <= document.querySelector('#reading-pane').clientWidth));
    await screenshot(mp, `mobile-${width}-large-type`);
    await mobile.close();
  }

  const reducedContext = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const rp = await reducedContext.newPage();
  await rp.goto(url, { waitUntil: 'networkidle' });
  await seekTo(rp, 161.7);
  const reducedResult = await rp.locator('#scene-incoming').evaluate(el => ({ reduced: matchMedia('(prefers-reduced-motion: reduce)').matches, transition: getComputedStyle(el).transitionDuration, incomingOpacity: getComputedStyle(el).opacity, image: document.querySelector('#scene-image').getAttribute('src') }));
  check('reduced motion swaps scene without transition', reducedResult.reduced && reducedResult.transition === '0s' && reducedResult.image.endsWith('unnamed-suspect.png'), reducedResult);
  await reducedContext.close();
  check('no browser JavaScript or resource errors', report.errors.length === 0, report.errors);
  report.passed = report.checks.filter(c => c.pass).length;
  report.failed = report.checks.filter(c => !c.pass).length;
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify({ passed: report.passed, failed: report.failed, report: path.join(out, 'report.json'), screenshots: report.screenshots }));
  process.exitCode = report.failed ? 1 : 0;
})().catch(error => { report.errors.push(String(error.stack)); fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2)); console.error(error); process.exit(1); });
