/* Owner flag workflow with native audio, a bounded fixture API, and phone layout. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
let playwright;
try { playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright'); }
catch (_) { playwright = require(path.join(require('node:os').homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const dist = path.resolve(__dirname,'../dist'), errors = [], checks = [];
const output = process.env.LUMEN_QA_OUT || '/private/tmp/lumen-audio-flags-qa';
fs.mkdirSync(output,{recursive:true});
const check = (name, condition) => { assert.ok(condition,name); checks.push(name); console.log('PASS ' + name); };
function wav(seconds) {
  const samples = seconds * 8000, bytes = Buffer.alloc(44 + samples * 2);
  bytes.write('RIFF'); bytes.writeUInt32LE(bytes.length-8,4); bytes.write('WAVEfmt ',8);
  bytes.writeUInt32LE(16,16); bytes.writeUInt16LE(1,20); bytes.writeUInt16LE(1,22);
  bytes.writeUInt32LE(8000,24); bytes.writeUInt32LE(16000,28); bytes.writeUInt16LE(2,32); bytes.writeUInt16LE(16,34);
  bytes.write('data',36); bytes.writeUInt32LE(samples*2,40);
  return bytes;
}
const fixtureDuration = 20.123375;
const secondStart = 5.123456789, secondEnd = 10.234567891;
const bytes = wav(fixtureDuration), hash = crypto.createHash('sha256').update(bytes).digest('hex');
const maleBytes = Buffer.from(bytes); maleBytes.writeInt16LE(1,44);
const maleHash = crypto.createHash('sha256').update(maleBytes).digest('hex');
const manifest = {schemaVersion:1,bookId:'lumen',manuscriptVersion:'v6',releaseId:'flag-fixture',defaultTrackId:'chapter-001',defaultNarratorId:'autonoe',
  narrators:[{id:'autonoe',label:'Female narrator'},{id:'charon',label:'Male narrator'}],tracks:[],legacyAliases:{}};
const payloads = new Map();
for (const number of [1,2]) {
  const id = 'chapter-' + String(number).padStart(3,'0'), recordings = {};
  for (const voice of ['autonoe','charon']) {
    const paragraphId = 'v6:' + id + ':p001', text = 'The first sentence. The second sentence. Uncertain words. The fourth sentence.';
    const sentences = [
      {id:paragraphId+'-s01',text:'The first sentence.',textStart:0,textEnd:19,start:0,end:secondStart,syncStatus:'measured'},
      {id:paragraphId+'-s02',text:'The second sentence.',textStart:20,textEnd:40,start:secondStart,end:secondEnd,syncStatus:'measured'},
      {id:paragraphId+'-s03',text:'Uncertain words.',textStart:41,textEnd:57,start:null,end:null,syncStatus:'unavailable'},
      {id:paragraphId+'-s04',text:'The fourth sentence.',textStart:58,textEnd:text.length,start:15,end:fixtureDuration,syncStatus:'measured'}
    ];
    const url = '/fixture/' + id + '-' + voice + '.json';
    const audioHash = voice === 'autonoe' ? hash : maleHash;
    recordings[voice] = {status:'ready',url,audioSha256:audioHash};
    payloads.set(url,{schemaVersion:2,bookId:'lumen',manuscriptVersion:'v6',chapterId:id,id:id+'-full',readingExtent:'full',title:'Chapter '+number,
      part:1,kind:'story',author:'Rob Xie',duration:fixtureDuration,audio:{src:'/fixture/audio-'+voice+'.wav',sha256:audioHash,narratorId:voice},
      paragraphs:[{id:paragraphId,text,emphasis:[],start:0,end:fixtureDuration,sentences}],scenes:[]});
  }
  manifest.tracks.push({id,title:'Chapter '+number,part:1,kind:'story',recordings});
}
const mime = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.png':'image/png','.webmanifest':'application/manifest+json'};
const server = http.createServer((request,response) => {
  const pathname = new URL(request.url,'http://localhost').pathname;
  const file = path.resolve(dist,'.' + (pathname === '/' ? '/index.html' : pathname));
  if (!file.startsWith(dist+path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404); response.end(); return; }
  response.writeHead(200,{'Content-Type':mime[path.extname(file)] || 'application/octet-stream','Cache-Control':'no-store'});
  fs.createReadStream(file).pipe(response);
});
let browser;
(async () => {
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  browser = await playwright.chromium.launch({headless:true,...(fs.existsSync(chrome)?{executablePath:chrome}:{})});
  async function fixture(role = 'owner', width = 390) {
    const context = await browser.newContext({viewport:{width,height:844},isMobile:true,hasTouch:true,serviceWorkers:'block',acceptDownloads:true});
    const page = await context.newPage(); page.on('pageerror',error => errors.push(String(error)));
    const state = {role,posts:[],gets:0,records:[],failSave:false,status:200,holdSave:null};
    await page.route('**/api/session',route => {
      if (route.request().method() === 'DELETE') state.role = 'guest';
      return route.fulfill({json:{authenticated:state.role !== 'guest',canFlagAudio:state.role === 'owner'}});
    });
    await page.route('**/api/book',route => route.fulfill({json:manifest}));
    await page.route('**/fixture/**',route => {
      const pathname = new URL(route.request().url()).pathname;
      if (payloads.has(pathname)) return route.fulfill({json:payloads.get(pathname)});
      const audioBytes = pathname.endsWith('charon.wav') ? maleBytes : bytes;
      const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || '');
      if (!range) return route.fulfill({contentType:'audio/wav',headers:{'Accept-Ranges':'bytes','Content-Length':String(audioBytes.length)},body:audioBytes});
      const start = Number(range[1]),end = Math.min(audioBytes.length-1,range[2]?Number(range[2]):audioBytes.length-1);
      return route.fulfill({status:206,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes','Content-Length':String(end-start+1),'Content-Range':`bytes ${start}-${end}/${audioBytes.length}`},body:audioBytes.subarray(start,end+1)});
    });
    await page.route('**/api/audio-flags',async route => {
      if (route.request().method() === 'GET') { state.gets++; return route.fulfill({json:{flags:state.records}}); }
      const payload = route.request().postDataJSON(); state.posts.push(payload);
      if (state.holdSave) await new Promise(resolve => { state.releaseSave = resolve; });
      if (state.failSave) { state.failSave = false; return route.abort('internetdisconnected'); }
      if (state.status !== 200) return route.fulfill({status:state.status,json:{error:'Fixture rejection'}});
      const record = {...payload,bookId:'lumen',manuscriptVersion:'v6',sentenceIds:['v6:chapter-001:p001-s02'],text:'The second sentence.',createdAt:new Date().toISOString(),status:'regenerate'};
      if (!state.records.some(saved => saved.id === record.id)) state.records.unshift(record);
      return route.fulfill({json:{flag:record}});
    });
    await page.goto(origin,{waitUntil:'domcontentloaded'});
    if (role !== 'guest') await page.waitForFunction(() => window.LUMEN_BOOK && document.querySelector('#narration').readyState >= 2);
    else await page.waitForFunction(() => document.querySelector('#access-status').textContent.includes('Use your access code'));
    return {page,context,state};
  }
  const guest = await fixture('guest');
  check('guest has no owner controls or flag requests',await guest.page.locator('#audio-flag-open').isHidden() && await guest.page.locator('#audio-flag-settings').isHidden() && guest.state.gets === 0);
  await guest.context.close();
  const reader = await fixture('reader');
  await reader.page.locator('#settings-open').click();
  check('ordinary reader has no flag controls or list requests',await reader.page.locator('#audio-flag-open').isHidden() && await reader.page.locator('#audio-flags-open').isHidden() && reader.state.gets === 0);
  await reader.context.close();
  const {page,context,state} = await fixture();
  check('owner alone sees the flag control',await page.locator('#audio-flag-open').isVisible());
  const geometry = await page.evaluate(() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,
    flag:document.querySelector('#audio-flag-open').getBoundingClientRect().toJSON(),seek:document.querySelector('#seek').getBoundingClientRect().toJSON()}));
  check('phone flag is at least 44px and timeline keeps full width',geometry.flag.width >= 44 && geometry.flag.height >= 44 && geometry.seek.width >= geometry.width-40 && geometry.scroll <= geometry.width);
  const tiny = await fixture('owner',320);
  check('small phone keeps flag and full-width timeline inside its viewport',await tiny.page.evaluate(() => {
    const flag = document.querySelector('#audio-flag-open').getBoundingClientRect(), seek = document.querySelector('#seek').getBoundingClientRect();
    return flag.height >= 44 && seek.width >= innerWidth-40 && document.documentElement.scrollWidth <= innerWidth;
  }));
  await tiny.context.close();
  const ownerLayout = await page.evaluate(() => ({width:innerWidth,height:innerHeight,mastheadCount:document.querySelectorAll('.masthead').length,
    masthead:document.querySelector('.masthead').getBoundingClientRect().toJSON(),player:document.querySelector('.player').getBoundingClientRect().toJSON(),
    flag:document.querySelector('#audio-flag-open').getBoundingClientRect().toJSON()}));
  check('owner footer and flag remain completely inside the viewport',ownerLayout.player.bottom <= ownerLayout.height && ownerLayout.flag.bottom <= ownerLayout.height && ownerLayout.mastheadCount === 1);
  fs.writeFileSync(path.join(output,'owner-phone-layout.json'),JSON.stringify(ownerLayout,null,2)+'\n');
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.screenshot({path:path.join(output,'owner-phone-reader.png')});
  await page.locator('#seek').evaluate(element => { element.value = '7'; element.dispatchEvent(new Event('input',{bubbles:true})); });
  await page.waitForFunction(() => Math.abs(document.querySelector('#narration').currentTime-7) < .2 && !document.querySelector('#narration').seeking);
  await page.locator('#play').click();
  await page.waitForFunction(() => !document.querySelector('#narration').paused);
  await page.locator('#audio-flag-open').click();
  check('flag pauses and selects the current measured sentence',await page.locator('#audio-flag-start').inputValue() === '0:05.123' && await page.locator('#audio-flag-end').inputValue() === '0:10.234' &&
    await page.evaluate(() => document.querySelector('#narration').paused));
  check('dialog displays faithful sentence and narrator',await page.locator('#audio-flag-excerpt').textContent() === 'The second sentence.' && (await page.locator('#audio-flag-context').textContent()).includes('Female narrator'));
  await page.screenshot({path:path.join(output,'owner-phone-flag-dialog.png')});
  await page.locator('#audio-flag-start').fill('5'); await page.locator('#audio-flag-end').fill('0:05.4');
  const beforePreview = await page.evaluate(() => ({time:document.querySelector('#narration').currentTime,bookmark:localStorage.getItem('lumen-book-v2')}));
  await page.locator('#audio-flag-preview').click();
  await page.waitForFunction(() => document.querySelector('#audio-flag-player').currentTime >= 5.35 && document.querySelector('#audio-flag-player').paused);
  const previewResult = await page.evaluate(before => {
    const preview = document.querySelector('#audio-flag-player');
    return {previewTime:preview.currentTime,mainTime:document.querySelector('#narration').currentTime,beforeTime:before.time,bookmark:localStorage.getItem('lumen-book-v2'),beforeBookmark:before.bookmark};
  },beforePreview);
  check('preview stops at selected end and preserves main bookmark/time',previewResult.previewTime < 5.6 && previewResult.mainTime === previewResult.beforeTime && previewResult.bookmark === previewResult.beforeBookmark);
  await page.locator('#audio-flag-start').fill('19'); await page.locator('#audio-flag-end').fill('21');
  check('out-of-recording range cannot save',await page.locator('#audio-flag-save').isDisabled());
  await page.locator('#audio-flag-start').fill('5'); await page.locator('#audio-flag-end').fill('10'); await page.locator('#audio-flag-note').fill('Correct the pause.');
  state.failSave = true; await page.locator('#audio-flag-save').click();
  await page.waitForFunction(() => document.querySelector('#audio-flag-status').textContent.includes('not been confirmed saved'));
  await page.locator('#audio-flag-save').click();
  await page.waitForFunction(() => document.querySelector('#audio-flag-status').textContent.startsWith('Saved for regeneration'));
  check('retry reuses the UUID and saves original hash/voice/range/note',state.posts.length === 2 && state.posts[0].id === state.posts[1].id && state.posts[1].audioSha256 === hash &&
    state.posts[1].narratorId === 'autonoe' && state.posts[1].chapterId === 'chapter-001' && state.posts[1].releaseId === manifest.releaseId && state.posts[1].start === 5 && state.posts[1].end === 10 && state.posts[1].note === 'Correct the pause.');
  await page.locator('#audio-flag-close').click();
  await page.locator('#settings-open').click(); await page.locator('#audio-flags-open').click();
  await page.waitForFunction(() => document.querySelector('#audio-flags-list').children.length === 1);
  check('owner can review saved source context', (await page.locator('#audio-flags-list').textContent()).includes('Chapter 1 · Female narrator · 0:05.000–0:10.000') && state.gets === 1);
  const downloaded = page.waitForEvent('download'); await page.locator('#audio-flags-export').click();
  const download = await downloaded, exported = JSON.parse(fs.readFileSync(await download.path(),'utf8'));
  check('JSON export retains recording hash and canonical sentence IDs',exported.flags[0].audioSha256 === hash && exported.flags[0].sentenceIds.length === 1);
  state.records = Array.from({length:110},(_,index) => ({...state.records[0],id:crypto.randomUUID(),note:'Saved note ' + index}));
  await page.locator('#audio-flags-refresh').click();
  await page.waitForFunction(() => document.querySelector('#audio-flags-list').children.length === 100);
  check('saved flags list is bounded and makes its export scope explicit',(await page.locator('#audio-flags-status').textContent()).includes('latest 100 shown') &&
    await page.locator('#audio-flags-export').textContent() === 'Download shown flags');
  await page.locator('#audio-flags-close').click();
  await page.locator('#seek').evaluate(element => { element.value = '12'; element.dispatchEvent(new Event('input',{bubbles:true})); });
  await page.waitForFunction(() => Math.abs(document.querySelector('#narration').currentTime-12) < .2);
  await page.locator('#audio-flag-open').click();
  check('uncertain timing defaults to bounded last ten seconds',await page.locator('#audio-flag-start').inputValue() === '0:02.000' && await page.locator('#audio-flag-end').inputValue() === '0:12.000');
  await page.locator('#audio-flag-close').click();
  await page.locator('#settings-open').click(); await page.locator('#narrator').selectOption('charon'); await page.locator('#settings-close').click();
  // The reader correctly refuses a voice map from the uncertain gap. Select a measured passage first.
  await page.locator('#transition-retry').waitFor({state:'visible'});
  await page.locator('[id="v6:chapter-001:p001-s02"]').click();
  await page.locator('#settings-open').click(); await page.locator('#narrator').selectOption('charon'); await page.locator('#settings-close').click();
  await page.waitForFunction(() => window.LUMEN_CHAPTER.audio.narratorId === 'charon' && document.querySelector('#narration').readyState >= 2);
  await page.locator('#audio-flag-open').click(); await page.locator('#audio-flag-save').click();
  await page.waitForFunction(() => document.querySelector('#audio-flag-status').textContent.startsWith('Saved for regeneration'));
  check('male narrator flags capture committed audible voice and its recording hash',state.posts.at(-1).narratorId === 'charon' && state.posts.at(-1).audioSha256 === maleHash);
  await page.locator('#audio-flag-close').click();
  await page.locator('#seek').press('End');
  await page.waitForFunction(() => !document.querySelector('#narration').seeking);
  await page.locator('#narration').evaluate(element => element.play());
  await page.waitForFunction(() => document.querySelector('#narration').ended);
  await page.locator('#audio-flag-open').click();
  check('fractional recording endpoint stays valid for a completed chapter',await page.locator('#audio-flag-end').inputValue() === '0:20.123' && !(await page.locator('#audio-flag-save').isDisabled()));
  const completedBefore = await page.evaluate(() => JSON.parse(localStorage.getItem('lumen-book-v2')).lastPosition);
  await page.locator('#audio-flag-start').fill('19.8'); await page.locator('#audio-flag-preview').click();
  await page.waitForFunction(() => document.querySelector('#audio-flag-player').currentTime >= 20.1 && document.querySelector('#audio-flag-player').paused);
  check('completed chapter preview preserves completion and exact main endpoint',await page.evaluate(before => {
    const saved = JSON.parse(localStorage.getItem('lumen-book-v2')).lastPosition;
    return saved.completed && before.completed && saved.audioTime === before.audioTime && document.querySelector('#narration').paused;
  },completedBefore));
  await page.locator('#audio-flag-save').click();
  await page.waitForFunction(() => document.querySelector('#audio-flag-status').textContent.startsWith('Saved for regeneration'));
  check('unchanged fractional EOF field saves the exact decoded endpoint',state.posts.at(-1).end === fixtureDuration);
  await page.locator('#audio-flag-close').click();
  await page.locator('#settings-open').click(); await page.locator('#sign-out-settings').click();
  await page.waitForFunction(() => !document.body.classList.contains('authenticated'));
  check('sign-out hides and purges draft/list/preview data',await page.evaluate(() => document.querySelector('#audio-flag-open').hidden && document.querySelector('#audio-flag-settings').hidden &&
    !document.querySelector('#audio-flags-list').textContent && !document.querySelector('#audio-flag-note').value && !document.querySelector('#audio-flag-excerpt').textContent && !document.querySelector('#audio-flag-player').getAttribute('src')));
  await context.close();
  const precise = await fixture();
  await precise.page.locator('#seek').evaluate(element => {element.value = '7'; element.dispatchEvent(new Event('input',{bubbles:true}));});
  await precise.page.waitForFunction(() => Math.abs(document.querySelector('#narration').currentTime-7) < .001 && !document.querySelector('#narration').seeking);
  await precise.page.locator('#audio-flag-open').click();
  check('fractional default starts exactly at the current canonical sentence',await precise.page.locator('#audio-flag-start').inputValue() === '0:05.123' &&
    await precise.page.locator('#audio-flag-excerpt').textContent() === 'The second sentence.');
  await precise.page.locator('#audio-flag-note').fill('Fractional cue check');
  await precise.page.locator('#audio-flag-save').click();
  await precise.page.waitForFunction(() => document.querySelector('#audio-flag-status').textContent.startsWith('Saved for regeneration'));
  check('unchanged displayed fields save both full-precision measured boundaries',precise.state.posts[0].start === secondStart && precise.state.posts[0].end === secondEnd);
  await precise.context.close();
  const denied = await fixture(); denied.state.status = 403;
  await denied.page.locator('#audio-flag-open').click(); await denied.page.locator('#audio-flag-save').click();
  await denied.page.waitForFunction(() => document.querySelector('#audio-flag-open').hidden);
  check('forbidden response revokes controls while reading continues',await denied.page.locator('#audio-flag-settings').isHidden() && await denied.page.evaluate(() => !!window.LUMEN_BOOK && !document.querySelector('#audio-flag-dialog').open));
  await denied.context.close();
  const stale = await fixture(); stale.state.status = 409;
  await stale.page.locator('#audio-flag-open').click(); await stale.page.locator('#audio-flag-save').click();
  await stale.page.waitForFunction(() => document.querySelector('#audio-flag-status').textContent.includes('recording has changed'));
  check('stale recording asks for a fresh flag and never reports saved',await stale.page.locator('#audio-flag-save').isDisabled());
  await stale.context.close();
  const changing = await fixture(); changing.state.holdSave = true;
  await changing.page.locator('#audio-flag-open').click(); await changing.page.locator('#audio-flag-save').click();
  while (!changing.state.releaseSave) await changing.page.waitForTimeout(10);
  await changing.page.locator('#audio-flag-close').click(); await changing.page.locator('#track-next').click();
  await changing.page.waitForFunction(() => window.LUMEN_CHAPTER.chapterId === 'chapter-002');
  changing.state.releaseSave();
  await changing.page.locator('#audio-flag-open').click();
  check('a late save for the old track cannot alter a new draft',await changing.page.locator('#audio-flag-context').textContent() === 'Chapter 2 · Female narrator' &&
    !(await changing.page.locator('#audio-flag-save').isDisabled()) && !(await changing.page.locator('#audio-flag-status').textContent()).startsWith('Saved'));
  await changing.page.locator('#audio-flag-close').click(); await changing.context.close();
  const signingOut = await fixture(); signingOut.state.holdSave = true;
  await signingOut.page.locator('#audio-flag-open').click(); await signingOut.page.locator('#audio-flag-save').click();
  while (!signingOut.state.releaseSave) await signingOut.page.waitForTimeout(10);
  await signingOut.page.locator('#audio-flag-close').click(); await signingOut.page.locator('#settings-open').click(); await signingOut.page.locator('#sign-out-settings').click();
  await signingOut.page.waitForFunction(() => !document.body.classList.contains('authenticated'));
  signingOut.state.releaseSave();
  check('a late save after logout cannot restore owner data or controls',await signingOut.page.evaluate(() => document.querySelector('#audio-flag-open').hidden &&
    !document.querySelector('#audio-flag-status').textContent && !document.querySelector('#audio-flag-dialog').open));
  await signingOut.context.close();
  const expired = await fixture(); expired.state.status = 401;
  await expired.page.locator('#audio-flag-open').click(); await expired.page.locator('#audio-flag-save').click();
  await expired.page.waitForFunction(() => !document.body.classList.contains('authenticated'));
  check('expired flag session closes private reader and owner dialogs',await expired.page.evaluate(() => !window.LUMEN_BOOK && document.querySelector('#audio-flag-open').hidden && !document.querySelector('#audio-flag-dialog').open));
  await expired.context.close();
  check('no browser errors',errors.length === 0);
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  if (server.listening) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});
