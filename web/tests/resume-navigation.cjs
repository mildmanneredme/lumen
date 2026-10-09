const fs = require('node:fs');
const path = require('node:path');
let playwrightModule = process.env.PLAYWRIGHT_MODULE;
if (!playwrightModule) {
  try { playwrightModule = require.resolve('playwright'); }
  catch (_) { playwrightModule = path.join(require('node:os').homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
}
const { chromium } = require(playwrightModule);
const url = process.env.LUMEN_QA_URL || 'http://127.0.0.1:8765/';
const out = process.env.LUMEN_QA_OUT || '/private/tmp/lumen-resume-qa';
const BOOKMARK_KEY = 'lumen-reader-v1';
const PREFERENCES_KEY = 'lumen-reader-preferences-v1';
fs.mkdirSync(out, { recursive: true });
const report = { url, generatedAt: new Date().toISOString(), checks: [], errors: [], screenshots: [] };
function check(name, pass, detail) {
  report.checks.push({ name, pass: !!pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'} ${name} ${JSON.stringify(detail || '')}`);
}
const formatTime = value => Math.floor(value / 60) + ':' + String(Math.floor(value % 60)).padStart(2, '0');
async function state(page) {
  return page.evaluate(({ bookmarkKey, preferencesKey }) => {
    const audio = document.querySelector('#narration');
    const chapter = window.LUMEN_CHAPTER;
    let bookmark = null, preferences = null;
    try { bookmark = JSON.parse(localStorage.getItem(bookmarkKey)); preferences = JSON.parse(localStorage.getItem(preferencesKey)); } catch (_) {}
    return {
      time: audio.currentTime, paused: audio.paused, ended: audio.ended, ready: audio.readyState,
      duration: audio.duration, chapterDuration: chapter.duration, rate: audio.playbackRate, seek: Number(document.querySelector('#seek').value),
      scene: document.querySelector('#scene-stage').dataset.scene,
      image: document.querySelector('#scene-image').getAttribute('src'),
      expectedScene: chapter.scenes.filter(scene => scene.start <= audio.currentTime).at(-1)?.id,
      expectedImage: chapter.scenes.filter(scene => scene.start <= audio.currentTime).at(-1)?.src,
      active: document.querySelector('.sentence.active')?.id,
      expectedSentence: chapter.paragraphs.flatMap(p => p.sentences).filter(sentence => sentence.start <= audio.currentTime).at(-1)?.id,
      resumeVisible: !document.querySelector('#resume-panel').hidden,
      continueText: document.querySelector('#resume-continue').textContent.trim(),
      status: document.querySelector('#play-status').textContent,
      playLabel: document.querySelector('#play').getAttribute('aria-label'),
      textSize: Number(document.querySelector('#text-size').value),
      bookmark, preferences, selection: window.__trustedSeekInput || null
    };
  }, { bookmarkKey: BOOKMARK_KEY, preferencesKey: PREFERENCES_KEY });
}
async function track(page, fraction) {
  const box = await page.locator('#seek').boundingBox();
  await page.mouse.click(box.x + 6 + (box.width - 12) * fraction, box.y + box.height / 2);
  await page.waitForTimeout(250);
  return state(page);
}
async function waitReady(page, expectedTime) {
  try {
    await page.waitForFunction(time => {
      const audio = document.querySelector('#narration');
      return audio?.readyState >= 1 && (time == null || Math.abs(audio.currentTime - time) < .2);
    }, expectedTime, { timeout: 15000 });
  } catch (error) {
    const diagnostic = await page.evaluate(() => {
      const a = document.querySelector('#narration');
      return {time:a?.currentTime,ready:a?.readyState,duration:a?.duration,network:a?.networkState,error:a?.error?.message,src:a?.src,
        seekable:a ? Array.from({length:a.seekable.length},(_,i)=>[a.seekable.start(i),a.seekable.end(i)]) : [],
        controller:navigator.serviceWorker?.controller?.scriptURL,status:document.querySelector('#play-status')?.textContent};
    });
    check('audio readiness failure diagnostic',false,{expectedTime,...diagnostic});
    throw error;
  }
  await page.waitForTimeout(200);
}
async function installSeed(context, bookmark, preferences = null) {
  await context.addInitScript(({ key, prefsKey, value, prefs }) => {
    localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value));
    if (prefs) localStorage.setItem(prefsKey, JSON.stringify(prefs));
  }, { key: BOOKMARK_KEY, prefsKey: PREFERENCES_KEY, value: bookmark, prefs: preferences });
}
async function delayedMetadata(page) {
  let release;
  const hold = new Promise(resolve => { release = resolve; });
  await page.route('**/assets/*.mp3*', async route => { await hold; await route.continue(); });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  return release;
}
async function screenshot(page, name) {
  const file = path.join(out, name + '.png');
  await page.screenshot({ path: file });
  report.screenshots.push(file);
}
(async () => {
  const launchOptions = { headless: true };
  if (process.env.CHROME_PATH) launchOptions.executablePath = process.env.CHROME_PATH;
  else if (process.platform === 'darwin' && fs.existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')) launchOptions.executablePath = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch(launchOptions);
  async function session(options = {}) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ...options });
    const page = await context.newPage();
    page.on('pageerror', error => report.errors.push(String(error)));
    await context.addInitScript(() => {
      document.addEventListener('input', event => {
        if (event.target.id === 'seek') window.__trustedSeekInput = { time: Number(event.target.value), trusted: event.isTrusted };
      }, true);
    });
    return { context, page };
  }

  const normal = await session();
  await normal.page.goto(url, { waitUntil: 'domcontentloaded' });
  await waitReady(normal.page, 0);
  const fresh = await state(normal.page);
  check('first visit starts paused without resume panel', fresh.paused && fresh.time === 0 && !fresh.resumeVisible, fresh);
  const selected = await track(normal.page, .55);
  check('native timeline establishes bookmark', selected.selection?.trusted && selected.paused && Math.abs(selected.time - selected.selection.time) < .2 && selected.bookmark?.audioTime === selected.time, selected);
  await normal.page.locator('#speed').selectOption('1.25');
  await normal.page.locator('#settings-open').click();
  await normal.page.locator('#text-size').focus();
  await normal.page.keyboard.press('Home');
  for (let i = 0; i < 6; i++) await normal.page.keyboard.press('ArrowRight');
  await normal.page.locator('#settings-close').click();
  const beforeReload = await state(normal.page);
  const bookmark = beforeReload.bookmark;
  await normal.page.reload({ waitUntil: 'domcontentloaded' });
  await waitReady(normal.page, beforeReload.time);
  const restored = await state(normal.page);
  check('reload restores precise paused audio, artwork, and sentence', restored.paused && Math.abs(restored.time - beforeReload.time) < .2 && restored.scene === restored.expectedScene && restored.image === restored.expectedImage && restored.active === restored.expectedSentence, restored);
  check('returning visitor sees Continue with retained time', restored.resumeVisible && restored.continueText === 'Continue from ' + formatTime(beforeReload.time), restored);
  check('reading preferences persist independently of audio bookmark', restored.rate === 1.25 && restored.textSize === 24 && !!restored.preferences && Object.keys(restored.bookmark).includes('audioTime'), restored);
  await screenshot(normal.page, 'desktop-returning-listener');
  await normal.page.locator('#resume-continue').click();
  await normal.page.waitForTimeout(400);
  const continued = await state(normal.page);
  check('Continue begins playback at retained position', !continued.paused && continued.time >= restored.time && continued.time < restored.time + 1.5 && !continued.resumeVisible, continued);
  await normal.page.locator('#play').click();
  await normal.page.reload({ waitUntil: 'domcontentloaded' });
  await normal.page.waitForFunction(() => !document.querySelector('#resume-panel').hidden);
  await normal.page.locator('#start-over').click();
  await normal.page.waitForTimeout(200);
  const restarted = await state(normal.page);
  check('Start over resets paused audio and saved progress', restarted.paused && restarted.time === 0 && restarted.bookmark?.audioTime === 0 && !restarted.bookmark?.completed && !restarted.resumeVisible, restarted);
  await normal.page.reload({ waitUntil: 'domcontentloaded' });
  await waitReady(normal.page, 0);
  check('Start over stays reset after reload', !(await state(normal.page)).resumeVisible && (await state(normal.page)).time === 0, await state(normal.page));

  await normal.page.locator('#seek').focus();
  await normal.page.keyboard.press('End');
  // Native range inputs round to the last valid 0.1-second step. Let the
  // remaining fraction play so this checks a real audio completion event.
  await normal.page.locator('#play').click();
  await normal.page.waitForFunction(() => document.querySelector('#narration').ended);
  await normal.page.waitForTimeout(150);
  const completed = await state(normal.page);
  check('reaching end records completion without resetting time', completed.paused && Math.abs(completed.time - completed.duration) < .2 && completed.bookmark?.completed && Math.abs(completed.bookmark.audioTime - completed.duration) < .2, completed);
  const completedBookmark = completed.bookmark;
  await normal.page.reload({ waitUntil: 'domcontentloaded' });
  await waitReady(normal.page, completed.time);
  const completedReload = await state(normal.page);
  check('completed excerpt reloads at end with Replay instead of zero', completedReload.paused && completedReload.bookmark?.completed && completedReload.resumeVisible && completedReload.playLabel === 'Replay audiobook' && completedReload.status === 'End of the pilot', completedReload);
  await normal.context.close();

  const endNavigation = await session();
  await endNavigation.page.goto(url, { waitUntil: 'domcontentloaded' });
  await waitReady(endNavigation.page, 0);
  await track(endNavigation.page, .98);
  await endNavigation.page.locator('#play').click();
  await endNavigation.page.waitForTimeout(350);
  await endNavigation.page.locator('#forward').click();
  await endNavigation.page.waitForTimeout(1000);
  const skippedEnd = await state(endNavigation.page);
  check('playing forward past the end stays completed and paused', skippedEnd.paused && skippedEnd.bookmark?.completed && skippedEnd.time >= skippedEnd.chapterDuration - .2 && skippedEnd.playLabel === 'Replay audiobook', skippedEnd);
  await track(endNavigation.page, .97);
  await endNavigation.page.locator('#play').click();
  await endNavigation.page.waitForTimeout(350);
  const beforeEndDrag = await state(endNavigation.page);
  const endBox = await endNavigation.page.locator('#seek').boundingBox();
  const endY = endBox.y + endBox.height / 2;
  await endNavigation.page.mouse.move(endBox.x + 6 + (endBox.width - 12) * beforeEndDrag.seek / beforeEndDrag.chapterDuration, endY);
  await endNavigation.page.mouse.down();
  await endNavigation.page.mouse.move(endBox.x + endBox.width - 1, endY, { steps: 8 });
  await endNavigation.page.mouse.up();
  await endNavigation.page.waitForTimeout(1200);
  const draggedEnd = await state(endNavigation.page);
  check('playing timeline drag to end stays completed and paused', draggedEnd.selection?.trusted && draggedEnd.paused && draggedEnd.bookmark?.completed && draggedEnd.time >= draggedEnd.chapterDuration - .2 && draggedEnd.playLabel === 'Replay audiobook', draggedEnd);
  await endNavigation.context.close();

  const delayed = await session();
  const releaseForward = await delayedMetadata(delayed.page);
  await delayed.page.locator('#forward').click();
  await delayed.page.locator('#forward').click();
  const pendingForward = await state(delayed.page);
  check('two forward clicks before metadata retain a 30-second intent', pendingForward.ready === 0 && Math.abs(pendingForward.seek - 30) < .2, pendingForward);
  releaseForward();
  await waitReady(delayed.page, 30);
  check('queued forward intent applies once audio metadata arrives', Math.abs((await state(delayed.page)).time - 30) < .2 && (await state(delayed.page)).paused, await state(delayed.page));
  await delayed.context.close();

  const earlyPlay = await session();
  const releasePlay = await delayedMetadata(earlyPlay.page);
  await earlyPlay.page.locator('#forward').click();
  await earlyPlay.page.locator('#forward').click();
  await earlyPlay.page.locator('#play').click();
  const requestedPlay = await state(earlyPlay.page);
  check('Play before metadata retains queued position and playback intent', requestedPlay.ready === 0 && Math.abs(requestedPlay.seek - 30) < .2 && requestedPlay.playLabel === 'Pause audiobook', requestedPlay);
  releasePlay();
  await earlyPlay.page.waitForFunction(() => {
    const audio = document.querySelector('#narration');
    return !audio.paused && audio.currentTime > 30.2 && audio.currentTime < 34;
  }, null, { timeout: 15000 });
  check('early Play advances from queued position after metadata loads', !(await state(earlyPlay.page)).paused && (await state(earlyPlay.page)).time > 30.2, await state(earlyPlay.page));
  await earlyPlay.context.close();

  const overridden = await session();
  await installSeed(overridden.context, bookmark);
  const releaseTimeline = await delayedMetadata(overridden.page);
  const pendingTimeline = await track(overridden.page, .3);
  check('timeline before metadata captures trusted user selection', pendingTimeline.ready === 0 && pendingTimeline.selection?.trusted && Math.abs(pendingTimeline.seek - pendingTimeline.selection.time) < .2, pendingTimeline);
  releaseTimeline();
  await waitReady(overridden.page, pendingTimeline.selection.time);
  const overrideReady = await state(overridden.page);
  check('later metadata never overwrites timeline with older saved position', Math.abs(overrideReady.time - pendingTimeline.selection.time) < .2 && Math.abs(overrideReady.time - bookmark.audioTime) > 20, overrideReady);
  await overridden.context.close();

  for (const fixture of ['corrupt', 'denied']) {
    const invalid = await session();
    if (fixture === 'corrupt') await installSeed(invalid.context, '{broken bookmark JSON');
    else await invalid.context.addInitScript(() => {
      Storage.prototype.getItem = function () { throw new DOMException('Storage disabled for test', 'SecurityError'); };
      Storage.prototype.setItem = function () { throw new DOMException('Storage disabled for test', 'SecurityError'); };
    });
    await invalid.page.goto(url, { waitUntil: 'domcontentloaded' });
    await waitReady(invalid.page, 0);
    await invalid.page.locator('#forward').click();
    await invalid.page.waitForTimeout(200);
    const invalidState = await state(invalid.page);
    check(fixture + ' storage keeps audio navigation usable', invalidState.paused && Math.abs(invalidState.time - 15) < .2, invalidState);
    await invalid.context.close();
  }

  const legacy = await session();
  const legacyTime = 211;
  await legacy.context.addInitScript(({ time }) => {
    localStorage.setItem('lumen-pilot-v0.1-e6b49db1dac5', JSON.stringify({ time, speed: 1.5, textSize: 26 }));
  }, { time: legacyTime });
  await legacy.page.goto(url, { waitUntil: 'domcontentloaded' });
  await waitReady(legacy.page, legacyTime);
  const migrated = await state(legacy.page);
  check('matching pilot-hash bookmark migrates to stable book storage', migrated.paused && migrated.bookmark?.bookId === 'lumen' && migrated.bookmark.chapterId === 'chapter-001' && Math.abs(migrated.bookmark.audioTime - legacyTime) < .2 && migrated.rate === 1.5 && migrated.textSize === 26 && migrated.resumeVisible, migrated);
  await legacy.context.close();

  const dataPath = path.resolve(__dirname, '../dist/data/chapter-001.js');
  const currentChapter = JSON.parse(fs.readFileSync(dataPath, 'utf8').replace(/^window\.LUMEN_CHAPTER\s*=\s*/, '').replace(/;\s*$/, ''));

  const oldSource = await session();
  await installSeed(oldSource.context, bookmark);
  const revisedChapter = structuredClone(currentChapter);
  revisedChapter.audio.sha256 = 'cached-source-race-test-hash';
  for (const paragraph of revisedChapter.paragraphs) {
    paragraph.start *= .75; paragraph.end *= .75;
    for (const sentence of paragraph.sentences) { sentence.start *= .75; sentence.end *= .75; }
  }
  for (const scene of revisedChapter.scenes) scene.start *= .75;
  const revisedSentence = revisedChapter.paragraphs.flatMap(p => p.sentences).find(s => s.id === bookmark.sentenceId);
  const revisedTime = revisedSentence.start + (revisedSentence.end - revisedSentence.start) * bookmark.sentenceFraction;
  let releaseApp;
  const appHold = new Promise(resolve => { releaseApp = resolve; });
  await oldSource.page.route('**/app.js*', async route => { await appHold; await route.continue(); });
  await oldSource.page.route('**/data/chapter-001.js*', route => route.fulfill({ contentType: 'text/javascript', body: 'window.LUMEN_CHAPTER = ' + JSON.stringify(revisedChapter) + ';' }));
  await oldSource.page.goto(url, { waitUntil: 'commit' });
  await oldSource.page.waitForFunction(() => document.querySelector('#narration')?.readyState >= 1);
  const sourceBeforeApp = await oldSource.page.locator('#narration').getAttribute('src');
  check('old HTML audio source loads before delayed application code', sourceBeforeApp.startsWith(currentChapter.audio.src) && !sourceBeforeApp.includes(revisedChapter.audio.sha256.slice(0, 12)), { sourceBeforeApp });
  releaseApp();
  await waitReady(oldSource.page, revisedTime);
  const sourceAfterApp = await oldSource.page.locator('#narration').getAttribute('src');
  const sourceResolved = await state(oldSource.page);
  check('recording change after old metadata restores without a stuck seek', sourceAfterApp.endsWith('?v=' + revisedChapter.audio.sha256.slice(0, 12)) && sourceResolved.paused && Math.abs(sourceResolved.time - revisedTime) < .2 && sourceResolved.active === bookmark.sentenceId && await oldSource.page.locator('#seek-feedback').evaluate(el => el.hidden), { sourceAfterApp, sourceResolved });
  await oldSource.context.close();

  for (const width of [390, 320]) {
    const mobile = await session({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
    await installSeed(mobile.context, bookmark);
    await mobile.page.goto(url, { waitUntil: 'domcontentloaded' });
    await waitReady(mobile.page, bookmark.audioTime);
    const layout = await mobile.page.evaluate(() => ({
      width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      panelVisible: !document.querySelector('#resume-panel').hidden,
      paneWidth: document.querySelector('#reading-pane').clientWidth,
      paneScrollWidth: document.querySelector('#reading-pane').scrollWidth,
      controls: ['resume-continue', 'start-over', 'back', 'play', 'forward', 'seek', 'speed'].map(id => {
        const box = document.getElementById(id).getBoundingClientRect();
        return { id, x: box.x, y: box.y, width: box.width, height: box.height, visible: box.x >= 0 && box.right <= innerWidth && box.y >= 0 && box.bottom <= innerHeight };
      })
    }));
    check('mobile ' + width + ' returning listener has visible controls without overflow', layout.panelVisible && layout.scrollWidth <= width && layout.paneScrollWidth <= layout.paneWidth && layout.controls.every(control => control.visible), layout);
    await screenshot(mobile.page, 'mobile-' + width + '-returning-listener');
    await mobile.context.close();
  }

  for (const fixture of ['replacement', 'extended excerpt']) {
    const changed = await session();
    const seed = fixture === 'replacement' ? bookmark : completedBookmark;
    await installSeed(changed.context, seed);
    const replacement = structuredClone(currentChapter);
    replacement.audio.sha256 = 'replacement-recording-test-hash';
    if (fixture === 'extended excerpt') replacement.id = 'chapter-001-full-test';
    for (const paragraph of replacement.paragraphs) {
      paragraph.start *= .75; paragraph.end *= .75;
      for (const sentence of paragraph.sentences) { sentence.start *= .75; sentence.end *= .75; }
    }
    for (const scene of replacement.scenes) scene.start *= .75;
    for (const intro of replacement.intro) { intro.start *= .75; intro.end *= .75; }
    const sentence = replacement.paragraphs.flatMap(p => p.sentences).find(s => s.id === seed.sentenceId);
    const semanticTime = sentence.start + (sentence.end - sentence.start) * seed.sentenceFraction;
    await changed.page.route('**/data/chapter-001.js*', route => route.fulfill({ contentType: 'text/javascript', body: 'window.LUMEN_CHAPTER = ' + JSON.stringify(replacement) + ';' }));
    await changed.page.goto(url, { waitUntil: 'domcontentloaded' });
    await waitReady(changed.page, semanticTime);
    const remapped = await state(changed.page);
    check(fixture + ' remaps by sentence fraction instead of obsolete seconds', remapped.paused && Math.abs(remapped.time - semanticTime) < .2 && Math.abs(remapped.time - seed.audioTime) > 20 && remapped.active === seed.sentenceId && remapped.resumeVisible, { semanticTime, oldTime: seed.audioTime, remapped });
    if (fixture === 'extended excerpt') check('completed pilot offers Continue in the extended book excerpt', remapped.playLabel === 'Play audiobook' && remapped.continueText === 'Continue from ' + formatTime(semanticTime) && remapped.status !== 'End of the pilot', remapped);
    await changed.page.locator('#resume-continue').click();
    await changed.page.waitForTimeout(250);
    await changed.page.locator('#play').click();
    await changed.page.waitForTimeout(150);
    const newlySaved = await state(changed.page);
    check(fixture + ' listening saves the current audio and extent as incomplete', newlySaved.paused && newlySaved.bookmark?.audioSha256 === replacement.audio.sha256 && newlySaved.bookmark.readingExtentId === replacement.id && newlySaved.bookmark.completed === false && Math.abs(newlySaved.bookmark.audioTime - newlySaved.time) < .2, newlySaved);
    await changed.context.close();
  }

  check('no browser JavaScript errors', report.errors.length === 0, report.errors);
  report.passed = report.checks.filter(c => c.pass).length;
  report.failed = report.checks.filter(c => !c.pass).length;
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify({ passed: report.passed, failed: report.failed, report: path.join(out, 'report.json'), screenshots: report.screenshots }));
  process.exitCode = report.failed ? 1 : 0;
})().catch(error => {
  report.errors.push(String(error.stack));
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  console.error(error);
  process.exit(1);
});
