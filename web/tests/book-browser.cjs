/* Three-track/two-voice behavioral slice; fixture media is generated in memory. */
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
const { chromium } = require(playwrightModule);
const dist = path.resolve(__dirname, '../dist');
const output = process.env.LUMEN_QA_OUT || '/private/tmp/lumen-book-qa';
fs.mkdirSync(output, {recursive:true});
const checks = [], errors = [];
const check = (name, condition) => { checks.push({name, pass:!!condition}); assert.ok(condition, name); console.log('PASS ' + name); };
function wav(seconds, frequency) {
  const rate = 8000, samples = seconds * rate, b = Buffer.alloc(44 + samples * 2);
  b.write('RIFF'); b.writeUInt32LE(b.length - 8, 4); b.write('WAVEfmt ', 8);
  b.writeUInt32LE(16,16); b.writeUInt16LE(1,20); b.writeUInt16LE(1,22);
  b.writeUInt32LE(rate,24); b.writeUInt32LE(rate*2,28); b.writeUInt16LE(2,32); b.writeUInt16LE(16,34);
  b.write('data',36); b.writeUInt32LE(samples*2,40);
  for (let i=0;i<samples;i++) b.writeInt16LE(Math.round(60*Math.sin(i*frequency*2*Math.PI/rate)),44+i*2);
  return b;
}
const media = new Map(), payloads = new Map();
const manifest = {schemaVersion:1,bookId:'lumen',manuscriptVersion:'v6',releaseId:'fixture-release',
  defaultTrackId:'chapter-001',defaultNarratorId:'autonoe',narrators:[{id:'charon',label:'Male narrator'},{id:'autonoe',label:'Female narrator'}],tracks:[],legacyAliases:{}};
for (let index=1;index<=3;index++) {
  const id='chapter-'+String(index).padStart(3,'0');
  const track={id,title:'Chapter '+index,part:1,kind:'story',recordings:{}};
  for (const [voice,duration] of [['charon',8],['autonoe',12]]) {
    const audio=wav(duration,100+index*30+(voice==='autonoe'?10:0));
    const hash=crypto.createHash('sha256').update(audio).digest('hex');
    const src='/fixture/'+id+'-'+voice+'.wav', url='/fixture/'+id+'-'+voice+'.json';
    media.set(src,audio);
    const paragraphId='v6:'+id+':p001', text='The first sentence. The second sentence.';
    const paragraphs=[{id:paragraphId,text,emphasis:[],start:0,end:duration,sentences:[
      {id:paragraphId+'-s01',text:'The first sentence.',textStart:0,textEnd:19,start:0,end:duration/2},
      {id:paragraphId+'-s02',text:'The second sentence.',textStart:20,textEnd:text.length,start:duration/2,end:duration}
    ]}];
    const payload={schemaVersion:1,bookId:'lumen',manuscriptVersion:'v6',chapterId:id,id:id+'-full',readingExtent:'full',
      title:track.title,part:1,partTitle:'The surface',kind:'story',author:'Rob Xie',duration,
      audio:{src,sha256:hash,narratorId:voice},paragraphs,scenes:[]};
    payloads.set(url,payload); track.recordings[voice]={status:'ready',url,audioSha256:hash};
  }
  manifest.tracks.push(track);
}
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.png':'image/png','.webp':'image/webp','.mp3':'audio/mpeg','.webmanifest':'application/manifest+json'};
const server=http.createServer((req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  const file=path.resolve(dist,'.'+(pathname==='/'?'/index.html':pathname));
  if (!file.startsWith(dist+path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {res.writeHead(404);res.end();return;}
  const size=fs.statSync(file).size, match=/^bytes=(\d+)-(\d*)$/.exec(req.headers.range||'');
  const headers={'Content-Type':mime[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store','Accept-Ranges':'bytes'};
  if (match) {
    const start=Number(match[1]), end=Math.min(size-1,match[2]?Number(match[2]):size-1);
    if (start>=size || end<start) {res.writeHead(416,{'Content-Range':'bytes */'+size});res.end();return;}
    res.writeHead(206,{...headers,'Content-Length':end-start+1,'Content-Range':`bytes ${start}-${end}/${size}`});
    fs.createReadStream(file,{start,end}).pipe(res);
  } else {res.writeHead(200,{...headers,'Content-Length':size});fs.createReadStream(file).pipe(res);}
});
let browser;
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  const launch={headless:true};
  const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (fs.existsSync(chrome)) launch.executablePath=chrome;
  browser=await chromium.launch(launch);
  const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});
  const page=await context.newPage(); page.on('pageerror',e=>errors.push(String(e)));
  let failing='', delayed='', release;
  await page.route('**/api/session',r=>r.fulfill({json:{authenticated:true}}));
  await page.route('**/api/book',r=>r.fulfill({json:manifest}));
  const fixtureRoute=async r=>{
    const pathname=new URL(r.request().url()).pathname;
    if (pathname===delayed) await new Promise(resolve=>{release=resolve;});
    if (pathname===failing) return r.fulfill({status:503,body:'temporarily unavailable'});
    if (payloads.has(pathname)) return r.fulfill({json:payloads.get(pathname)});
    if (media.has(pathname)) {
      const bytes=media.get(pathname), range=/^bytes=(\d+)-(\d*)$/.exec(r.request().headers().range||'');
      const headers={'Accept-Ranges':'bytes'};
      if (!range) return r.fulfill({contentType:'audio/wav',headers:{...headers,'Content-Length':String(bytes.length)},body:bytes});
      const start=Number(range[1]),end=Math.min(bytes.length-1,range[2]?Number(range[2]):bytes.length-1);
      if (start>=bytes.length || end<start) return r.fulfill({status:416,headers:{'Content-Range':'bytes */'+bytes.length}});
      return r.fulfill({status:206,contentType:'audio/wav',headers:{...headers,'Content-Length':String(end-start+1),
        'Content-Range':`bytes ${start}-${end}/${bytes.length}`},body:bytes.subarray(start,end+1)});
    }
    return r.fulfill({status:404});
  };
  await page.route('**/fixture/**',fixtureRoute);
  const second=payloads.get('/fixture/chapter-002-charon.json');
  const bookmark={schemaVersion:1,bookId:'lumen',manuscriptVersion:'v6',chapterId:'chapter-002',readingExtentId:second.id,
    sentenceId:second.paragraphs[0].sentences[0].id,sentenceFraction:.8,narratorId:'charon',audioSha256:second.audio.sha256,
    audioTime:3.2,completed:false,updatedAt:new Date().toISOString()};
  await context.addInitScript(value=>{
    if (!sessionStorage.getItem('fixture-seeded')) {
      localStorage.setItem('lumen-book-v2',JSON.stringify(value)); sessionStorage.setItem('fixture-seeded','yes');
    }
  },{schemaVersion:2,bookId:'lumen',manuscriptVersion:'v6',releaseId:manifest.releaseId,lastPosition:bookmark,history:{}});
  const state=()=>page.evaluate(()=>{const a=document.querySelector('#narration');return{chapter:window.LUMEN_CHAPTER.chapterId,
    voice:window.LUMEN_CHAPTER.audio.narratorId,time:a.currentTime,paused:a.paused,src:a.getAttribute('src'),
    title:document.querySelector('#chapter-title').textContent,bookmark:JSON.parse(localStorage.getItem('lumen-book-v2')||'null'),
    active:document.querySelector('.sentence.active')?.id,tabStops:document.querySelectorAll('.sentence[tabindex="0"]').length};});
  const waitTrack=async(id,voice,time)=>{
    try { await page.waitForFunction(({id,voice,time})=>{const a=document.querySelector('#narration');return window.LUMEN_CHAPTER?.chapterId===id &&
      window.LUMEN_CHAPTER.audio.narratorId===voice && a.readyState>=1 && (time==null||Math.abs(a.currentTime-time)<.25);}, {id,voice,time},{timeout:10000});
    } catch (error) {
      console.error('Track state:',await page.evaluate(()=>{const a=document.querySelector('#narration');return {
        chapter:window.LUMEN_CHAPTER?.chapterId, voice:window.LUMEN_CHAPTER?.audio.narratorId,time:a.currentTime,
        duration:a.duration,readyState:a.readyState,networkState:a.networkState,src:a.currentSrc,error:a.error?.message,
        seekable:Array.from({length:a.seekable.length},(_,i)=>[a.seekable.start(i),a.seekable.end(i)]),
        status:document.querySelector('#seek-feedback').textContent};}));
      throw error;
    }
  };
  const selectTrack=async id=>{await page.locator('#chapters-open').click();await page.locator('#chapter-list [data-track-id="'+id+'"]').click();};
  const selectVoice=async voice=>{await page.locator('#settings-open').click();await page.locator('#narrator').selectOption(voice);await page.locator('#settings-close').click();};
  await page.goto(origin,{waitUntil:'domcontentloaded'});
  await page.waitForTimeout(300);
  check('saved chapter is chosen before the default chapter', (await state()).title==='Chapter 2');
  await waitTrack('chapter-002','charon',3.2);
  check('same recording resumes exact seconds and stays paused',(await state()).paused);
  await selectVoice('autonoe');
  await waitTrack('chapter-002','autonoe',4.8);
  check('voice switch maps canonical sentence fraction and keeps paused state',(await state()).paused);
  await page.locator('#play').click();await page.waitForTimeout(180);
  await selectVoice('charon');
  await waitTrack('chapter-002','charon');await page.waitForTimeout(180);
  check('playing voice switch keeps listening intent',!(await state()).paused);
  await page.locator('#play').click();
  const before=await state(); failing='/fixture/chapter-003-charon.json';
  await selectTrack('chapter-003');await page.waitForTimeout(200);
  check('failed transition preserves old chapter and source',(await state()).chapter===before.chapter && (await state()).src===before.src);
  check('failed transition offers a usable retry',await page.locator('#transition-retry').isVisible());
  failing='';await page.locator('#transition-retry').click();await waitTrack('chapter-003','charon',0);
  check('retry enters requested chapter at its own start',(await state()).chapter==='chapter-003');
  await selectTrack('chapter-001');await waitTrack('chapter-001','charon');
  delayed='/fixture/chapter-002-charon.json';await selectTrack('chapter-002');
  await page.waitForFunction(()=>document.querySelector('#transition-status').textContent.includes('Loading'));
  await selectTrack('chapter-003');await waitTrack('chapter-003','charon');
  if (release) release(); delayed='';await page.waitForTimeout(150);
  check('late earlier chapter response cannot replace the current chapter',(await state()).chapter==='chapter-003');
  await page.goBack();await waitTrack('chapter-001','charon');
  check('browser Back restores the prior successful chapter',(await state()).chapter==='chapter-001');
  check('chapter prose has a bounded keyboard tab chain',(await state()).tabStops<=1);
  await page.locator('#settings-open').click();await page.locator('#auto-continue').check();await page.locator('#settings-close').click();
  await page.locator('#seek').evaluate(el=>{el.value='7.9';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.locator('#play').click();await waitTrack('chapter-002','charon');
  check('opted-in chapter continuation advances exactly once',(await state()).chapter==='chapter-002');
  const completedHistory=(await state()).bookmark.history['chapter-001'];
  check('continuation saves chapter completion before advancing',completedHistory.completed===true);
  await page.locator('#play').click();await page.reload({waitUntil:'domcontentloaded'});await waitTrack('chapter-002','charon');
  check('reload uses last book position and leaves playback paused',(await state()).paused);
  await page.screenshot({path:path.join(output,'phone-book-reader.png')});
  for(const [name,width,height] of [['small-phone',360,780],['ipad',820,1180],['desktop',1440,960]]) {
    await page.setViewportSize({width,height});
    const layout=await page.evaluate(()=>({width:innerWidth,body:document.documentElement.scrollWidth,
      play:document.querySelector('#play').getBoundingClientRect().toJSON(),
      seek:document.querySelector('#seek').getBoundingClientRect().toJSON()}));
    check(name+' reader has no horizontal overflow',layout.body<=layout.width);
    check(name+' play and timeline stay usable',layout.play.width>=44 && layout.play.height>=44 && layout.seek.width>100);
    await page.screenshot({path:path.join(output,name+'-reader.png')});
  }
  const guestContext=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,serviceWorkers:'block'});
  const guest=await guestContext.newPage();guest.on('pageerror',e=>errors.push(String(e)));
  let authenticated=false,privateRequests=0;
  const invite='a'.repeat(43);
  await guest.route('**/api/session',async route=>{
    const method=route.request().method();
    if(method==='POST') {
      const good=JSON.parse(route.request().postData()).invite===invite;
      if(!good) return route.fulfill({status:403,json:{error:'Request rejected.'}});
      authenticated=true;
    }
    if(method==='DELETE') authenticated=false;
    await route.fulfill({json:{authenticated}});
  });
  await guest.route('**/api/book',route=>{privateRequests++;return route.fulfill({json:manifest});});
  await guest.route('**/fixture/**',fixtureRoute);
  await guest.goto(origin,{waitUntil:'domcontentloaded'});
  await guest.waitForFunction(()=>document.querySelector('#access-status').textContent.includes('Use your invitation'));
  check('guest shell never requests the private book',privateRequests===0);
  check('guest has no book prose or audio source',await guest.evaluate(()=>!document.querySelector('#prose').textContent && !document.querySelector('#narration').getAttribute('src')));
  await guest.locator('#invite-code').fill('b'.repeat(43));await guest.locator('#invite-submit').click();
  await guest.waitForFunction(()=>document.querySelector('#access-status').textContent.includes('could not be verified'));
  check('rejected invite leaves the reading room locked',privateRequests===0 && !authenticated);
  await guest.goto(origin+'/#invite='+invite,{waitUntil:'domcontentloaded'});
  await guest.waitForFunction(()=>window.LUMEN_CHAPTER?.chapterId==='chapter-001' && document.querySelector('#narration').readyState>=1);
  check('invitation is removed from the URL before opening the book',new URL(guest.url()).hash==='');
  check('new reader starts with the female narrator',await guest.evaluate(()=>window.LUMEN_CHAPTER.audio.narratorId==='autonoe'));
  const timeline=await guest.locator('#seek').boundingBox();
  await guest.touchscreen.tap(timeline.x+timeline.width*.5,timeline.y+timeline.height*.5);
  await guest.waitForFunction(()=>Math.abs(document.querySelector('#narration').currentTime-6)<.6);
  check('native phone timeline touch seeks the actual audio',await guest.evaluate(()=>document.querySelector('#narration').paused));
  await guest.locator('#forward').tap();
  await guest.waitForFunction(()=>Math.abs(document.querySelector('#narration').currentTime-12)<.25);
  check('forward fifteen seconds clamps to the chapter end',true);
  await guest.locator('#back').tap();
  await guest.waitForFunction(()=>document.querySelector('#narration').currentTime<.25);
  check('rewind fifteen seconds restores the start and stays paused',await guest.evaluate(()=>document.querySelector('#narration').paused));
  await guest.locator('#seek').evaluate(el=>{el.value='2.5';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await guest.waitForFunction(()=>Math.abs(document.querySelector('#narration').currentTime-2.5)<.25);
  await guest.locator('#settings-open').click();await guest.locator('#sign-out-settings').click();
  await guest.waitForFunction(()=>!document.body.classList.contains('authenticated'));
  check('phone sign out clears displayed prose and audio',await guest.evaluate(()=>!document.querySelector('#prose').textContent && !document.querySelector('#narration').getAttribute('src')));
  check('sign out preserves the saved listening place',await guest.evaluate(()=>JSON.parse(localStorage.getItem('lumen-book-v2')).lastPosition.audioTime>2));
  await guest.reload({waitUntil:'domcontentloaded'});
  await guest.waitForFunction(()=>document.querySelector('#access-status').textContent.includes('Use your invitation'));
  check('returning signed-out reader remains locked',!authenticated);
  const gapPayload=payloads.get('/fixture/chapter-001-autonoe.json'),paragraph=gapPayload.paragraphs[0];
  gapPayload.schemaVersion=2;Object.assign(paragraph.sentences[1],{start:null,end:null,syncStatus:'unavailable'});paragraph.end=paragraph.sentences[0].end;
  await guest.locator('#invite-code').fill(invite);await guest.locator('#invite-submit').click();
  await guest.waitForFunction(()=>window.LUMEN_CHAPTER?.schemaVersion===2);
  check('uncertain cues remain faithful plain prose',await guest.locator('.sentence.unsynchronized').textContent()==='The second sentence.');
  check('uncertain cues have no sentence-seek control',await guest.locator('.sentence.unsynchronized').getAttribute('role')===null);
  await guest.locator('#seek').evaluate(el=>{el.value='8';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await guest.waitForFunction(()=>Math.abs(document.querySelector('#narration').currentTime-8)<.25);
  check('uncertain audio intervals never highlight an invented sentence',await guest.locator('.sentence.active').count()===0);
  await guest.screenshot({path:path.join(output,'phone-invitation.png')});
  await guestContext.close();
  const raceContext=await browser.newContext({viewport:{width:820,height:1180},serviceWorkers:'block'});
  const race=await raceContext.newPage();race.on('pageerror',e=>errors.push(String(e)));
  let raceAuthenticated=true,indexRequested=false,releaseIndex;
  await race.route('**/api/session',route=>{
    if(route.request().method()==='DELETE') raceAuthenticated=false;
    return route.fulfill({json:{authenticated:raceAuthenticated}});
  });
  await race.route('**/api/book',async route=>{
    indexRequested=true;await new Promise(resolve=>releaseIndex=resolve);await route.fulfill({json:manifest});
  });
  await race.route('**/fixture/**',fixtureRoute);
  await race.goto(origin,{waitUntil:'domcontentloaded'});
  while(!indexRequested) await race.waitForTimeout(10);
  await race.locator('#settings-open').click();await race.locator('#sign-out-settings').click();
  await race.waitForFunction(()=>!document.body.classList.contains('authenticated'));
  releaseIndex();await race.waitForTimeout(250);
  check('delayed bootstrap cannot restore private content after sign out',await race.evaluate(()=>
    !document.querySelector('#prose').textContent && !document.querySelector('#narration').getAttribute('src') && !window.LUMEN_BOOK));
  await raceContext.close();
  check('no browser JavaScript errors',errors.length===0);
  await context.close();
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({checks,errors},null,2)+'\n');
  if(browser) await browser.close();await new Promise(resolve=>server.close(resolve));
});
