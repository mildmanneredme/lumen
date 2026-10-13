/* Approved PR #21 skip icon restoration, native 15-second seeking and viewport geometry. */
'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const http = require('node:http'), crypto = require('node:crypto'), os = require('node:os');
let playwright;
try { playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright'); }
catch (_) { playwright = require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const dist = path.resolve(__dirname,'../dist'), output = process.env.LUMEN_QA_OUT || '/private/tmp/lumen-transport-qa';
fs.mkdirSync(output,{recursive:true});
const report = {checks:[],errors:[],layouts:[]};
const check = (name,pass) => { report.checks.push({name,pass:!!pass}); assert.ok(pass,name); console.log('PASS '+name); };
const duration = 40, samples = duration*8000, audio = Buffer.alloc(44+samples*2);
audio.write('RIFF'); audio.writeUInt32LE(audio.length-8,4); audio.write('WAVEfmt ',8);
audio.writeUInt32LE(16,16); audio.writeUInt16LE(1,20); audio.writeUInt16LE(1,22);
audio.writeUInt32LE(8000,24); audio.writeUInt32LE(16000,28); audio.writeUInt16LE(2,32); audio.writeUInt16LE(16,34);
audio.write('data',36); audio.writeUInt32LE(samples*2,40);
const hash = crypto.createHash('sha256').update(audio).digest('hex'), paragraphId = 'v6:chapter-001:p001';
const text = 'The first sentence. The second sentence.';
const chapter = {schemaVersion:1,bookId:'lumen',manuscriptVersion:'v6',chapterId:'chapter-001',id:'chapter-001-full',title:'Chapter 1',
  readingExtent:'full',part:1,kind:'story',author:'Rob Xie',duration,audio:{src:'/fixture/audio.wav',sha256:hash,narratorId:'autonoe'},scenes:[],
  paragraphs:[{id:paragraphId,text,emphasis:[],start:0,end:40,sentences:[
    {id:paragraphId+'-s01',text:'The first sentence.',textStart:0,textEnd:19,start:0,end:20},
    {id:paragraphId+'-s02',text:'The second sentence.',textStart:20,textEnd:text.length,start:20,end:40}
  ]}]};
const manifest = {schemaVersion:1,bookId:'lumen',manuscriptVersion:'v6',releaseId:'transport-fixture',defaultTrackId:'chapter-001',
  defaultNarratorId:'autonoe',narrators:[{id:'autonoe',label:'Female narrator'}],legacyAliases:{},
  tracks:[{id:'chapter-001',title:'Chapter 1',part:1,kind:'story',recordings:{autonoe:{status:'ready',url:'/fixture/chapter.json',audioSha256:hash}}}]};
const mime = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.webmanifest':'application/manifest+json'};
const server = http.createServer((request,response) => {
  const pathname = new URL(request.url,'http://localhost').pathname;
  const file = path.resolve(dist,'.'+(pathname==='/'?'/index.html':pathname));
  if (!file.startsWith(dist+path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {response.writeHead(404);response.end();return;}
  response.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});fs.createReadStream(file).pipe(response);
});
let browser;
(async () => {
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  const origin = 'http://127.0.0.1:'+server.address().port;
  const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  browser = await playwright.chromium.launch({headless:true,...(fs.existsSync(chrome)?{executablePath:chrome}:{})});
  for (const [name,width,height] of [['phone',390,844],['small-phone',320,740],['small-desktop',1024,768],['landscape-phone',844,390]]) {
    const mobile = name.includes('phone');
    const context = await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,serviceWorkers:'block'});
    const page = await context.newPage();page.on('pageerror',error => report.errors.push(String(error)));
    await page.route('**/api/session',route => route.fulfill({json:{authenticated:true,canFlagAudio:true}}));
    await page.route('**/api/book',route => route.fulfill({json:manifest}));
    await page.route('**/fixture/**',route => {
      if (new URL(route.request().url()).pathname.endsWith('.json')) return route.fulfill({json:chapter});
      const match = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range||'');
      if (!match) return route.fulfill({contentType:'audio/wav',headers:{'Accept-Ranges':'bytes','Content-Length':String(audio.length)},body:audio});
      const start = Number(match[1]),end = Math.min(audio.length-1,match[2]?Number(match[2]):audio.length-1);
      return route.fulfill({status:206,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes','Content-Length':String(end-start+1),'Content-Range':`bytes ${start}-${end}/${audio.length}`},body:audio.subarray(start,end+1)});
    });
    await page.goto(origin,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => window.LUMEN_BOOK && document.querySelector('#narration').readyState >= 2);
    check(name+' restores the approved SVG directions and accessible 15-second actions',await page.evaluate(() => {
      const back = document.querySelector('#back'),forward = document.querySelector('#forward');
      return back.querySelector('svg path')?.getAttribute('d') === 'm11 6-7 6 7 6m9-12-7 6 7 6' && forward.querySelector('svg path')?.getAttribute('d') === 'm4 6 7 6-7 6m9-12 7 6-7 6' &&
        !/[↶↷]/.test(back.textContent+forward.textContent) && back.getAttribute('aria-label') === 'Rewind 15 seconds' && forward.getAttribute('aria-label') === 'Forward 15 seconds' &&
        back.querySelector('svg').getAttribute('aria-hidden') === 'true' && forward.querySelector('svg').getAttribute('focusable') === 'false';
    }));
    const layout = await page.evaluate(() => ({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
      back:document.querySelector('#back').getBoundingClientRect().toJSON(),forward:document.querySelector('#forward').getBoundingClientRect().toJSON(),
      seek:document.querySelector('#seek').getBoundingClientRect().toJSON(),player:document.querySelector('.player').getBoundingClientRect().toJSON(),
      flag:document.querySelector('#audio-flag-open').getBoundingClientRect().toJSON()}));
    report.layouts.push({name,...layout});
    check(name+' keeps skip targets and owner flag inside the viewport',layout.scrollWidth <= layout.width && layout.back.width >= 44 && layout.back.height >= 44 &&
      layout.forward.width >= 44 && layout.forward.height >= 44 && layout.player.bottom <= layout.height && layout.flag.height >= 44 && layout.flag.bottom <= layout.height);
    if (width <= 720) check(name+' preserves the full-width portrait timeline',layout.seek.width >= width-40);
    await page.locator('#seek').evaluate(element => {element.value='10';element.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.waitForFunction(() => Math.abs(document.querySelector('#narration').currentTime-10) < .2 && !document.querySelector('#narration').seeking);
    await page.locator('#forward').click();
    await page.waitForFunction(() => Math.abs(document.querySelector('#narration').currentTime-25) < .2 && !document.querySelector('#narration').seeking);
    await page.locator('#back').click();
    await page.waitForFunction(() => Math.abs(document.querySelector('#narration').currentTime-10) < .2 && !document.querySelector('#narration').seeking);
    check(name+' SVG buttons still seek native audio forward and back by 15 seconds',await page.evaluate(() => document.querySelector('#narration').paused && !document.querySelector('#narration').error));
    if (name === 'phone' || name === 'small-desktop') {
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.screenshot({path:path.join(output,name+'-reader.png')});
      await page.locator('.player').screenshot({path:path.join(output,name+'-transport.png')});
    }
    await context.close();
  }
  check('restored controls cause no browser JavaScript errors',report.errors.length === 0);
})().catch(error => {console.error(error.stack||String(error));process.exitCode=1;}).finally(async () => {
  if (browser) await browser.close();
  if (server.listening) {server.closeAllConnections();await new Promise(resolve => server.close(resolve));}
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
});
