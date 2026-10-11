/* Real Chrome exercises responsive private paintings against an in-memory book. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
let playwrightModule = process.env.PLAYWRIGHT_MODULE;
if (!playwrightModule) {
  try { playwrightModule = require.resolve('playwright'); }
  catch (_) { playwrightModule = path.join(require('node:os').homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
}
const {chromium} = require(playwrightModule);
const dist = path.resolve(__dirname, '../dist');
const production = 'https://lumen-phi-five.vercel.app';
const privateHeaders = {'Cache-Control':'private, no-store'};
const errors = [], checks = [];
function check(name, condition) {
  assert.ok(condition, name); checks.push(name); console.log('PASS ' + name);
}
function wav(seconds) {
  const rate = 8000, samples = seconds * rate, bytes = Buffer.alloc(44 + samples * 2);
  bytes.write('RIFF'); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write('WAVEfmt ', 8);
  bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(1, 22);
  bytes.writeUInt32LE(rate, 24); bytes.writeUInt32LE(rate * 2, 28);
  bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34);
  bytes.write('data', 36); bytes.writeUInt32LE(samples * 2, 40);
  return bytes;
}
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const artPath = (id, variant) => '/api/assets/illustration-fixture/images/' + digest(id + '-' + variant) + '.webp';
const manifest = {schemaVersion:1, bookId:'lumen', manuscriptVersion:'v6', releaseId:'illustration-fixture',
  defaultTrackId:'chapter-001', defaultNarratorId:'autonoe',
  narrators:[{id:'autonoe',label:'Female narrator'},{id:'charon',label:'Male narrator'}],
  tracks:[{id:'chapter-001',title:'Chapter 1',part:1,kind:'story',recordings:{}}], legacyAliases:{}};
const payloads = new Map(), media = new Map();
for (const [voice, duration] of [['autonoe',12], ['charon',8]]) {
  const bytes = wav(duration), hash = digest(bytes), src = '/fixture/' + voice + '.wav';
  const paragraphId = 'v6:chapter-001:p001', texts = ['An empty room.', 'A lamp appears.', 'A door opens.', 'A visitor arrives.'];
  let offset = 0;
  const sentences = texts.map((text,index) => {
    const sentence = {id:paragraphId + '-s0' + (index + 1), text, textStart:offset, textEnd:offset + text.length,
      start:index * duration / 4, end:(index + 1) * duration / 4};
    offset += text.length + 1; return sentence;
  });
  const payload = {schemaVersion:1,bookId:'lumen',manuscriptVersion:'v6',chapterId:'chapter-001',id:'chapter-001-full',
    readingExtent:'full',title:'Chapter 1',part:1,partTitle:'The surface',kind:'story',author:'Fixture',duration,
    audio:{src,sha256:hash,narratorId:voice},
    paragraphs:[{id:paragraphId,text:texts.join(' '),emphasis:[],start:0,end:duration,sentences}],
    scenes:['one','two','three'].map((id,index) => ({id, sentenceId:sentences[index + 1].id,
      start:sentences[index + 1].start,end:sentences[index + 1].end,
      src:production + artPath(id,'full'),phoneSrc:production + artPath(id,'phone'),
      title:'Painting ' + (index + 1),alt:'Fixture painting ' + (index + 1)}))};
  payloads.set('/fixture/' + voice + '.json', payload); media.set(src, bytes);
  manifest.tracks[0].recordings[voice] = {status:'ready',url:'/fixture/' + voice + '.json',audioSha256:hash};
}
const mime = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png',
  '.svg':'image/svg+xml','.webmanifest':'application/manifest+json'};
const server = http.createServer((req,res) => {
  const pathname = new URL(req.url,'http://localhost').pathname;
  const file = path.resolve(dist, '.' + (pathname === '/' ? '/index.html' : pathname));
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404); res.end(); return;
  }
  res.writeHead(200, {'Content-Type':mime[path.extname(file)] || 'application/octet-stream','Cache-Control':'no-store'});
  fs.createReadStream(file).pipe(res);
});
let browser;
(async () => {
  await new Promise((resolve,reject) => { server.once('error',reject); server.listen(0,'127.0.0.1',resolve); });
  const origin = 'http://127.0.0.1:' + server.address().port;
  const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  assert.ok(fs.existsSync(chrome), 'Installed Google Chrome is required for native image/audio acceptance.');
  browser = await chromium.launch({headless:true,executablePath:chrome});
  // Generate a genuine WebP in memory; no source artwork or remote services are used.
  const imagePage = await browser.newPage();
  const webp = Buffer.from((await imagePage.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 2;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = '#dfbd7d'; ctx.fillRect(0,0,2,2);
    return canvas.toDataURL('image/webp');
  })).split(',')[1], 'base64');
  await imagePage.close();
  const fixture = async ({width=390,height=900,dpr=3,saveData=false,reducedMotion='reduce',legacy=false,sceneOnly=false,invalidSceneTrigger,
    invalidPhone,failures=new Map()}={}) => {
    const context = await browser.newContext({viewport:{width,height},deviceScaleFactor:dpr,
      reducedMotion,serviceWorkers:'block'});
    await context.addInitScript(value => {
      Object.defineProperty(navigator,'connection',{configurable:true,value:{saveData:value}});
    }, saveData);
    const page = await context.newPage(), requests = [], attempts = new Map(), held = new Map();
    let authenticated = true;
    page.on('pageerror',error => errors.push(String(error)));
    await page.route('**/api/session',route => {
      if (route.request().method() === 'DELETE') authenticated = false;
      return route.fulfill({headers:privateHeaders,json:{authenticated}});
    });
    await page.route('**/api/book',route => route.fulfill({headers:privateHeaders,json:manifest}));
    await page.route('**/fixture/**',route => {
      const pathname = new URL(route.request().url()).pathname;
      if (payloads.has(pathname)) {
        const payload = JSON.parse(JSON.stringify(payloads.get(pathname)));
        if (legacy) for (const scene of payload.scenes) delete scene.phoneSrc;
        if (sceneOnly) {
          payload.schemaVersion = 2;
          for (const sentence of payload.paragraphs[0].sentences) sentence.syncStatus = 'measured';
          Object.assign(payload.paragraphs[0].sentences[1],{syncStatus:'unavailable',start:null,end:null});
          Object.assign(payload.scenes[0],{triggerSource:'measured-scene-cue',triggerAudioSha256:payload.audio.sha256});
          if (invalidSceneTrigger === 'wrong-voice-hash') {
            const otherVoice = payload.audio.narratorId === 'autonoe' ? 'charon' : 'autonoe';
            payload.scenes[0].triggerAudioSha256 = payloads.get('/fixture/' + otherVoice + '.json').audio.sha256;
          } else if (invalidSceneTrigger === 'unknown-trigger-source') {
            payload.scenes[0].triggerSource = 'estimated-scene-cue';
          } else if (invalidSceneTrigger === 'unknown-sentence') {
            payload.scenes[0].sentenceId = 'v6:chapter-001:p999-s01';
          } else if (invalidSceneTrigger === 'equal-starts') {
            payload.scenes[1].start = payload.scenes[0].start;
          } else if (invalidSceneTrigger === 'reversed-starts') {
            payload.scenes[1].start = payload.scenes[0].start - .1;
          }
        }
        if (invalidPhone !== undefined) payload.scenes[0].phoneSrc = invalidPhone;
        return route.fulfill({headers:privateHeaders,json:payload});
      }
      const bytes = media.get(pathname);
      if (!bytes) return route.fulfill({status:404});
      const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || '');
      const headers = {...privateHeaders,'Accept-Ranges':'bytes'};
      if (!range) return route.fulfill({contentType:'audio/wav',headers:{...headers,'Content-Length':String(bytes.length)},body:bytes});
      const start = Number(range[1]), end = Math.min(bytes.length - 1,range[2] ? Number(range[2]) : bytes.length - 1);
      if (start >= bytes.length || end < start) return route.fulfill({status:416,headers:{...headers,'Content-Range':'bytes */' + bytes.length}});
      return route.fulfill({status:206,contentType:'audio/wav',headers:{...headers,'Content-Length':String(end-start+1),
        'Content-Range':`bytes ${start}-${end}/${bytes.length}`},body:bytes.subarray(start,end+1)});
    });
    await page.route('**/api/assets/**',async route => {
      const url = route.request().url(), pathname = new URL(url).pathname;
      requests.push(url);
      attempts.set(pathname,(attempts.get(pathname) || 0) + 1);
      if (attempts.get(pathname) <= (failures.get(pathname) || 0)) {
        return route.fulfill({status:503,headers:privateHeaders,body:'temporarily unavailable'});
      }
      if (held.has(pathname)) await held.get(pathname).promise;
      await route.fulfill({contentType:'image/webp',headers:privateHeaders,body:webp});
    });
    const hold = pathname => {
      let release;
      const promise = new Promise(resolve => { release = resolve; });
      held.set(pathname,{promise});
      return () => { held.delete(pathname); release(); };
    };
    return {context,page,requests,attempts,failures,hold};
  };
  const ready = page => page.waitForFunction(() => window.LUMEN_CHAPTER && document.querySelector('#narration').readyState >= 1);
  const seek = (page,time) => page.locator('#seek').evaluate((el,time) => {
    el.value = String(time); el.dispatchEvent(new Event('input',{bubbles:true}));
  },time);
  const shown = (page,id,variant) => page.waitForFunction(({id,path}) => {
    const image = document.querySelector('#scene-image');
    return document.querySelector('#scene-stage').dataset.scene === id && image.getAttribute('src') &&
      new URL(image.src).pathname === path && image.complete && image.naturalWidth > 0;
  },{id,path:artPath(id,variant)});
  const cleared = page => page.evaluate(() => document.querySelector('#scene-stage').classList.contains('placeholder-art') &&
    !document.querySelector('#scene-title').textContent && !document.querySelector('#scene-image').getAttribute('src') &&
    !document.querySelector('#scene-incoming').getAttribute('src'));
  for (const [voice,trigger] of [['autonoe',3],['charon',2]]) {
    const f = await fixture();
    const releaseNext = f.hold(artPath('two','phone'));
    await f.page.goto(origin + '/?voice=' + voice,{waitUntil:'domcontentloaded'}); await ready(f.page);
    // Use a representable slider value: its 0.1-second step rounds .05 to the boundary.
    await seek(f.page,trigger - .2);
    const beforeTrigger = await f.page.evaluate(() => ({
      slider:Number(document.querySelector('#seek').value),audio:document.querySelector('#narration').currentTime
    }));
    check(voice + ': no painting or request precedes the measured narration trigger',
      beforeTrigger.slider < trigger && beforeTrigger.audio < trigger && await cleared(f.page) && f.requests.length === 0);
    const next = f.page.waitForRequest(request => new URL(request.url()).pathname === artPath('two','phone'));
    await seek(f.page,trigger); await shown(f.page,'one','phone'); await next;
    check(voice + ': exact voice-specific trigger displays the phone painting',await f.page.evaluate(trigger =>
      window.LUMEN_CHAPTER.scenes[0].start === trigger && document.querySelector('#scene-title').textContent === 'Painting 1',trigger));
    check(voice + ': preload, display and next use only their selected private preview URLs',
      f.requests.every(url => new URL(url).origin === origin && [artPath('one','phone'),artPath('two','phone')].includes(new URL(url).pathname)) &&
      (await f.page.locator('#scene-incoming').getAttribute('src')) === origin + artPath('one','phone'));
    await seek(f.page,trigger * 2);
    await f.page.waitForFunction(() => document.querySelector('#scene-title').textContent === 'Painting 2');
    await seek(f.page,0);
    check(voice + ': rewinding before the first trigger clears private paintings immediately',await cleared(f.page));
    const paintResponses = f.page.waitForResponse(response => new URL(response.url()).pathname === artPath('two','phone'));
    releaseNext(); await paintResponses; await f.page.waitForTimeout(100);
    check(voice + ': a delayed painting cannot reappear after rewind',await cleared(f.page));
    await f.context.close();
  }
  for (const [name,options,variant] of [
    ['large desktop',{width:1800,dpr:2},'full'],
    ['small render target',{width:1200,dpr:1},'phone'],
    ['save-data desktop',{width:1800,dpr:2,saveData:true},'phone'],
    ['legacy scene payload',{legacy:true},'full']
  ]) {
    const f = await fixture(options);
    await f.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(f.page);
    const expectedNext = options.saveData ? null : f.page.waitForRequest(request => new URL(request.url()).pathname === artPath('two',variant));
    await seek(f.page,3); await shown(f.page,'one',variant); if (expectedNext) await expectedNext;
    await f.page.waitForTimeout(100);
    const permitted = [artPath('one',variant),...(options.saveData ? [] : [artPath('two',variant)])];
    check(name + ': only the appropriate current and permitted next variant loads',
      f.requests.length > 0 && f.requests.every(url => new URL(url).origin === origin && permitted.includes(new URL(url).pathname)));
    check(name + ': painting requests create no private Cache Storage entries',await f.page.evaluate(async () => (await caches.keys()).length === 0));
    await f.context.close();
  }
  for (const [voice,trigger] of [['autonoe',3],['charon',2]]) {
    const f = await fixture({sceneOnly:true,saveData:true});
    await f.page.goto(origin + '/?voice=' + voice,{waitUntil:'domcontentloaded'}); await ready(f.page);
    await seek(f.page,trigger - .2);
    check(voice + ': scene-only cue requests no painting before its measured onset',
      await cleared(f.page) && f.requests.length === 0 && await f.page.evaluate(trigger =>
        document.querySelector('#narration').currentTime < trigger,trigger));
    await seek(f.page,trigger); await shown(f.page,'one','phone');
    check(voice + ': exact scene-only onset displays art while its canonical prose cue remains unmeasured',
      await f.page.evaluate(({voice,trigger}) => {
        const chapter = window.LUMEN_CHAPTER, scene = chapter.scenes[0];
        const cue = chapter.paragraphs[0].sentences.find(sentence => sentence.id === scene.sentenceId);
        return chapter.audio.narratorId === voice && scene.start === trigger &&
          scene.triggerSource === 'measured-scene-cue' && scene.triggerAudioSha256 === chapter.audio.sha256 &&
          cue.syncStatus === 'unavailable' && cue.start === null && cue.end === null;
      },{voice,trigger}));
    await seek(f.page,trigger + .3);
    const clockBeforeClick = await f.page.evaluate(() => document.querySelector('#narration').currentTime);
    await f.page.evaluate(() => document.getElementById(window.LUMEN_CHAPTER.scenes[0].sentenceId).click());
    check(voice + ': scene-only proof creates no sentence highlight, seek button or click-to-seek behavior',
      await f.page.evaluate(clock => {
        const sentence = document.getElementById(window.LUMEN_CHAPTER.scenes[0].sentenceId);
        return sentence.textContent === 'A lamp appears.' && sentence.classList.contains('unsynchronized') &&
          !sentence.classList.contains('active') && sentence.getAttribute('role') === null &&
          sentence.getAttribute('tabindex') === null && !document.querySelector('#prose .sentence.active') &&
          Math.abs(document.querySelector('#narration').currentTime - clock) < .01 && document.querySelector('#narration').paused;
      },clockBeforeClick));
    const releaseNext = f.hold(artPath('two','phone'));
    const nextRequest = f.page.waitForRequest(request => new URL(request.url()).pathname === artPath('two','phone'));
    await seek(f.page,trigger * 2); await nextRequest;
    await seek(f.page,0);
    check(voice + ': rewind removes scene-only art while a later painting is pending',await cleared(f.page));
    const nextResponse = f.page.waitForResponse(response => new URL(response.url()).pathname === artPath('two','phone'));
    releaseNext(); await nextResponse; await f.page.waitForTimeout(100);
    check(voice + ': delayed image responses cannot restore art after scene-only rewind',await cleared(f.page));
    await seek(f.page,trigger); await shown(f.page,'one','phone');
    const releaseThird = f.hold(artPath('three','phone'));
    const thirdRequest = f.page.waitForRequest(request => new URL(request.url()).pathname === artPath('three','phone'));
    await seek(f.page,trigger * 3); await thirdRequest;
    await f.page.locator('#settings-open').click(); await f.page.locator('#sign-out-settings').click();
    await f.page.waitForFunction(() => !document.body.classList.contains('authenticated'));
    const thirdResponse = f.page.waitForResponse(response => new URL(response.url()).pathname === artPath('three','phone'));
    releaseThird(); await thirdResponse;
    await f.page.evaluate(() => document.querySelector('#narration').dispatchEvent(new Event('timeupdate')));
    await f.page.waitForTimeout(100);
    check(voice + ': sign out clears scene-only art and rejects late image/audio callbacks',
      await cleared(f.page) && await f.page.evaluate(() => window.LUMEN_CHAPTER === undefined &&
        !document.querySelector('#narration').getAttribute('src')));
    await f.context.close();
  }
  for (const invalidSceneTrigger of ['wrong-voice-hash','unknown-trigger-source','unknown-sentence','equal-starts','reversed-starts']) {
    const f = await fixture({sceneOnly:true,invalidSceneTrigger});
    await f.page.goto(origin,{waitUntil:'domcontentloaded'});
    await f.page.locator('#transition-retry').waitFor({state:'visible'});
    check('invalid scene-only proof rejects the chapter before loading art (' + invalidSceneTrigger + ')',
      f.requests.length === 0 && await cleared(f.page));
    await f.context.close();
  }
  const resized = await fixture({width:1800,dpr:2});
  await resized.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(resized.page);
  await seek(resized.page,3); await shown(resized.page,'one','full');
  await resized.page.setViewportSize({width:390,height:900}); await shown(resized.page,'one','phone');
  check('a paused resize selects the phone variant without changing the narration clock',
    await resized.page.evaluate(() => Math.abs(document.querySelector('#narration').currentTime - 3) < .1));
  await resized.context.close();
  const transient = await fixture({saveData:true,failures:new Map([[artPath('one','phone'),1]])});
  await transient.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(transient.page);
  await seek(transient.page,3); await shown(transient.page,'one','phone');
  check('a transient first-painting failure recovers with one bounded retry at the same narration clock',
    transient.attempts.get(artPath('one','phone')) === 2 && await transient.page.evaluate(() =>
      Math.abs(document.querySelector('#narration').currentTime - 3) < .1 && document.querySelector('#play-status').textContent.includes('paused')));
  await transient.context.close();
  const variantRetry = await fixture({width:1800,dpr:2,saveData:false,failures:new Map([[artPath('one','phone'),1]])});
  await variantRetry.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(variantRetry.page);
  await seek(variantRetry.page,3); await shown(variantRetry.page,'one','full');
  await variantRetry.page.setViewportSize({width:390,height:900}); await shown(variantRetry.page,'one','phone');
  check('a responsive variant failure recovers with its selected phone URL and one retry',
    variantRetry.attempts.get(artPath('one','phone')) === 2 && await variantRetry.page.evaluate(() =>
      Math.abs(document.querySelector('#narration').currentTime - 3) < .1));
  await variantRetry.context.close();
  const failedVariant = await fixture({width:1800,dpr:2,failures:new Map([[artPath('one','phone'),100]])});
  await failedVariant.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(failedVariant.page);
  await seek(failedVariant.page,3); await shown(failedVariant.page,'one','full');
  const firstVariantFailure = failedVariant.page.waitForResponse(response =>
    new URL(response.url()).pathname === artPath('one','phone') && response.status() === 503);
  await failedVariant.page.setViewportSize({width:390,height:900}); await firstVariantFailure;
  await failedVariant.page.waitForResponse(response =>
    new URL(response.url()).pathname === artPath('one','phone') && response.status() === 503);
  await failedVariant.page.waitForTimeout(100);
  check('a terminal responsive replacement failure retains the decoded current painting and caption',
    failedVariant.attempts.get(artPath('one','phone')) === 2 && await failedVariant.page.evaluate(source =>
      document.querySelector('#scene-image').getAttribute('src')?.endsWith(source) &&
      document.querySelector('#scene-title').textContent === 'Painting 1' &&
      !document.querySelector('#scene-stage').classList.contains('placeholder-art') &&
      document.querySelector('#narration').paused && Math.abs(document.querySelector('#narration').currentTime - 3) < .1,
    artPath('one','full')));
  failedVariant.failures.set(artPath('one','phone'),2);
  await failedVariant.page.evaluate(() => window.dispatchEvent(new Event('online')));
  await shown(failedVariant.page,'one','phone');
  check('reconnection upgrades retained fallback art without changing the narration clock',
    failedVariant.attempts.get(artPath('one','phone')) === 3 && await failedVariant.page.evaluate(() =>
      Math.abs(document.querySelector('#narration').currentTime - 3) < .1));
  await seek(failedVariant.page,0);
  check('rewind clears retained variant fallback artwork',await cleared(failedVariant.page));
  await failedVariant.context.close();
  const hiddenArt = await fixture({width:390,dpr:2});
  await hiddenArt.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(hiddenArt.page);
  await seek(hiddenArt.page,3); await shown(hiddenArt.page,'one','phone');
  await hiddenArt.page.waitForTimeout(100);
  const hiddenRequestCount = hiddenArt.requests.length;
  await hiddenArt.page.locator('#art-toggle').click();
  await hiddenArt.page.setViewportSize({width:844,height:390});
  await seek(hiddenArt.page,6); await hiddenArt.page.waitForTimeout(100);
  check('hidden artwork neither switches variants on rotation nor loads a later painting',
    hiddenArt.requests.length === hiddenRequestCount && await hiddenArt.page.evaluate(() =>
      document.body.classList.contains('art-hidden')));
  await hiddenArt.page.locator('#art-toggle').click(); await shown(hiddenArt.page,'two','phone');
  check('showing artwork uses the current landscape render size and current narration beat',
    hiddenArt.requests.every(url => !new URL(url).pathname.endsWith(artPath('one','full')) &&
      !new URL(url).pathname.endsWith(artPath('two','full'))) && await hiddenArt.page.evaluate(() =>
      !document.body.classList.contains('art-hidden') && Math.abs(document.querySelector('#narration').currentTime - 6) < .1));
  await hiddenArt.context.close();
  const cssHidden = await fixture({width:390,height:500,dpr:2});
  await cssHidden.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(cssHidden.page);
  await seek(cssHidden.page,3); await cssHidden.page.waitForTimeout(100);
  check('a CSS-hidden short portrait stage loads no current or next painting while artwork is enabled',
    cssHidden.requests.length === 0 && await cleared(cssHidden.page) && await cssHidden.page.evaluate(() =>
      getComputedStyle(document.querySelector('#scene-stage')).display === 'none' &&
      document.querySelector('#art-toggle').getAttribute('aria-pressed') === 'true'));
  await cssHidden.page.setViewportSize({width:390,height:480});
  await seek(cssHidden.page,6); await cssHidden.page.waitForTimeout(100);
  check('resizing and seeking within a CSS-hidden stage start no painting requests',
    cssHidden.requests.length === 0 && await cleared(cssHidden.page));
  await cssHidden.page.setViewportSize({width:844,height:390}); await shown(cssHidden.page,'two','phone');
  await cssHidden.page.waitForTimeout(100);
  check('visible landscape resumes the current phone painting and next without changing narration',
    cssHidden.requests.length > 0 && cssHidden.requests.every(url =>
      [artPath('two','phone'),artPath('three','phone')].includes(new URL(url).pathname)) &&
      await cssHidden.page.evaluate(() => getComputedStyle(document.querySelector('#scene-stage')).display !== 'none' &&
        Math.abs(document.querySelector('#narration').currentTime - 6) < .1));
  await cssHidden.context.close();
  const cssPending = await fixture({width:390,dpr:2});
  const releaseCSSPainting = cssPending.hold(artPath('one','phone'));
  await cssPending.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(cssPending.page);
  const cssPaintingRequest = cssPending.page.waitForRequest(request =>
    new URL(request.url()).pathname === artPath('one','phone'));
  await seek(cssPending.page,3); await cssPaintingRequest;
  await cssPending.page.setViewportSize({width:390,height:500});
  const cssPaintingResponse = cssPending.page.waitForResponse(response =>
    new URL(response.url()).pathname === artPath('one','phone'));
  releaseCSSPainting(); await cssPaintingResponse; await cssPending.page.waitForTimeout(100);
  check('a pending image response cannot display art or preload the next painting after CSS hides the stage',
    cssPending.requests.length === 1 && await cssPending.page.evaluate(() =>
      !document.querySelector('#scene-image').getAttribute('src') &&
      !document.querySelector('#scene-incoming').getAttribute('src')));
  await cssPending.page.setViewportSize({width:844,height:390}); await shown(cssPending.page,'one','phone');
  check('a painting cancelled by CSS hiding resumes at the same current narration beat',
    await cssPending.page.evaluate(() => Math.abs(document.querySelector('#narration').currentTime - 3) < .1));
  await cssPending.context.close();
  const cssRetry = await fixture({width:390,dpr:2,saveData:true,failures:new Map([[artPath('one','phone'),1]])});
  await cssRetry.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(cssRetry.page);
  const cssFirstFailure = cssRetry.page.waitForResponse(response =>
    new URL(response.url()).pathname === artPath('one','phone') && response.status() === 503);
  await seek(cssRetry.page,3); await cssFirstFailure;
  await cssRetry.page.setViewportSize({width:390,height:500}); await cssRetry.page.waitForTimeout(1200);
  check('CSS hiding cancels an image retry before it starts',cssRetry.attempts.get(artPath('one','phone')) === 1);
  await cssRetry.page.setViewportSize({width:844,height:390}); await shown(cssRetry.page,'one','phone');
  check('making the stage visible retries its cancelled current painting without moving narration',
    cssRetry.attempts.get(artPath('one','phone')) === 2 && await cssRetry.page.evaluate(() =>
      Math.abs(document.querySelector('#narration').currentTime - 3) < .1));
  await cssRetry.context.close();
  const crossfadeVariant = await fixture({width:390,dpr:3,reducedMotion:'no-preference'});
  await crossfadeVariant.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(crossfadeVariant.page);
  await seek(crossfadeVariant.page,3); await shown(crossfadeVariant.page,'one','phone');
  await crossfadeVariant.page.locator('#play').click();
  await crossfadeVariant.page.waitForFunction(() => !document.querySelector('#narration').paused);
  await crossfadeVariant.page.setViewportSize({width:896,height:390});
  await crossfadeVariant.page.waitForFunction(source =>
    document.querySelector('#scene-incoming').classList.contains('visible') &&
    new URL(document.querySelector('#scene-incoming').src).pathname === source, artPath('one','full'));
  await crossfadeVariant.page.locator('#art-toggle').click();
  await crossfadeVariant.page.evaluate(() => document.querySelector('#narration').pause());
  await crossfadeVariant.page.locator('#art-toggle').click(); await shown(crossfadeVariant.page,'one','full');
  check('showing artwork completes a responsive crossfade interrupted by hiding the same scene',
    await crossfadeVariant.page.evaluate(() => document.querySelector('#scene-title').textContent === 'Painting 1' &&
      !document.querySelector('#scene-incoming').classList.contains('visible') &&
      document.querySelector('#narration').currentTime >= 3 && document.querySelector('#narration').currentTime < 6));
  await crossfadeVariant.context.close();
  const unavailable = await fixture({saveData:true,failures:new Map([[artPath('two','phone'),100]])});
  await unavailable.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(unavailable.page);
  await seek(unavailable.page,3); await shown(unavailable.page,'one','phone');
  await seek(unavailable.page,6);
  await unavailable.page.waitForFunction(() => document.querySelector('#scene-stage').classList.contains('placeholder-art') &&
    !document.querySelector('#scene-image').getAttribute('src') && !document.querySelector('#scene-title').textContent);
  await unavailable.page.evaluate(() => {
    for (let i=0;i<20;i++) document.querySelector('#narration').dispatchEvent(new Event('timeupdate'));
  });
  await unavailable.page.waitForTimeout(100);
  check('terminal image failure clears stale art and captions without endless timeupdate retries or narration-status changes',
    unavailable.attempts.get(artPath('two','phone')) === 2 && await cleared(unavailable.page) && await unavailable.page.evaluate(() =>
      document.querySelector('#play-status').textContent.includes('paused')));
  unavailable.failures.set(artPath('two','phone'),2);
  await unavailable.page.evaluate(() => window.dispatchEvent(new Event('online'))); await shown(unavailable.page,'two','phone');
  check('reconnection retries the failed current painting without restarting narration',
    unavailable.attempts.get(artPath('two','phone')) === 3 && await unavailable.page.evaluate(() =>
      Math.abs(document.querySelector('#narration').currentTime - 6) < .1));
  await unavailable.context.close();
  const backward = await fixture({saveData:true});
  await backward.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(backward.page);
  // Jump over the first painting so its earlier reveal has never been fetched.
  await seek(backward.page,6); await shown(backward.page,'two','phone');
  const releaseEarlier = backward.hold(artPath('one','phone'));
  const earlierRequest = backward.page.waitForRequest(request => new URL(request.url()).pathname === artPath('one','phone'));
  await seek(backward.page,3); await earlierRequest;
  check('rewinding between reveals removes the later painting while the earlier image loads',await backward.page.evaluate(() =>
    document.querySelector('#scene-stage').classList.contains('placeholder-art') &&
    !document.querySelector('#scene-image').getAttribute('src') && !document.querySelector('#scene-incoming').getAttribute('src')));
  releaseEarlier(); await shown(backward.page,'one','phone'); await backward.context.close();
  const signedOut = await fixture({saveData:true});
  await signedOut.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(signedOut.page);
  await seek(signedOut.page,3); await shown(signedOut.page,'one','phone');
  const releaseImage = signedOut.hold(artPath('two','phone'));
  const lateRequest = signedOut.page.waitForRequest(request => new URL(request.url()).pathname === artPath('two','phone'));
  await seek(signedOut.page,6); await lateRequest;
  await signedOut.page.locator('#settings-open').click(); await signedOut.page.locator('#sign-out-settings').click();
  await signedOut.page.waitForFunction(() => !document.body.classList.contains('authenticated'));
  check('sign out clears the old painting and caption while another private image is pending',await cleared(signedOut.page));
  const lateResponse = signedOut.page.waitForResponse(response => new URL(response.url()).pathname === artPath('two','phone'));
  releaseImage(); await lateResponse;
  await signedOut.page.evaluate(() => document.querySelector('#narration').dispatchEvent(new Event('timeupdate')));
  await signedOut.page.waitForTimeout(100);
  check('late image and audio events cannot reveal a painting after sign out',await cleared(signedOut.page) &&
    await signedOut.page.evaluate(() => window.LUMEN_CHAPTER === undefined && !document.querySelector('#narration').getAttribute('src')));
  await signedOut.context.close();
  const pendingRetry = await fixture({saveData:true,failures:new Map([[artPath('one','phone'),1]])});
  await pendingRetry.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(pendingRetry.page);
  const releaseRetry = pendingRetry.hold(artPath('one','phone'));
  const firstFailure = pendingRetry.page.waitForResponse(response =>
    new URL(response.url()).pathname === artPath('one','phone') && response.status() === 503);
  await seek(pendingRetry.page,3); await firstFailure;
  await pendingRetry.page.waitForRequest(request => new URL(request.url()).pathname === artPath('one','phone'));
  await pendingRetry.page.locator('#settings-open').click(); await pendingRetry.page.locator('#sign-out-settings').click();
  await pendingRetry.page.waitForFunction(() => !document.body.classList.contains('authenticated'));
  const retryResponse = pendingRetry.page.waitForResponse(response => new URL(response.url()).pathname === artPath('one','phone'));
  releaseRetry(); await retryResponse; await pendingRetry.page.waitForTimeout(100);
  check('a delayed image retry cannot restore private artwork after sign out',await cleared(pendingRetry.page) &&
    pendingRetry.attempts.get(artPath('one','phone')) === 2);
  await pendingRetry.context.close();
  const cancelledRetry = await fixture({saveData:true,failures:new Map([[artPath('one','phone'),1]])});
  await cancelledRetry.page.goto(origin,{waitUntil:'domcontentloaded'}); await ready(cancelledRetry.page);
  const cancelledFailure = cancelledRetry.page.waitForResponse(response =>
    new URL(response.url()).pathname === artPath('one','phone') && response.status() === 503);
  await seek(cancelledRetry.page,3); await cancelledFailure;
  await cancelledRetry.page.locator('#settings-open').click(); await cancelledRetry.page.locator('#sign-out-settings').click();
  await cancelledRetry.page.waitForFunction(() => !document.body.classList.contains('authenticated'));
  await cancelledRetry.page.waitForTimeout(1200);
  check('sign out cancels an image retry that has not started',cancelledRetry.attempts.get(artPath('one','phone')) === 1 &&
    await cleared(cancelledRetry.page));
  await cancelledRetry.context.close();
  for (const invalidPhone of ['javascript:alert(1)',null]) {
    const f = await fixture({invalidPhone});
    await f.page.goto(origin,{waitUntil:'domcontentloaded'});
    await f.page.locator('#transition-retry').waitFor({state:'visible'});
    check('invalid optional phone URL rejects the chapter before any painting request (' + typeof invalidPhone + ')',
      f.requests.length === 0 && await cleared(f.page));
    await f.context.close();
  }
  check('the illustrated reader has no uncaught browser errors',errors.length === 0);
  console.log('PASS ' + checks.length + ' illustrated-scene browser checks in Chrome ' + browser.version());
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
});
