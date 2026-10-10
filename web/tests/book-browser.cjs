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
const regression = (name, condition) => { checks.push({name, pass:!!condition}); console.log((condition?'PASS ':'FAIL ') + name); };
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
let cookieFixture=null;
const server=http.createServer((req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  if(cookieFixture && pathname==='/api/session') {
    const fixture=cookieFixture,send=(authenticated,cookie)=>{
      if(res.destroyed) return;
      res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store',...(cookie?{'Set-Cookie':cookie}: {})});
      res.end(JSON.stringify({authenticated}));
    };
    if(req.method==='POST') {
      let body='';req.on('data',block=>body+=block);req.on('end',()=>{
        const token=JSON.parse(body).invite,session=fixture.tokens.get(token);
        fixture.posts.push(token);
        const finish=()=>send(!!session,'lumen_fixture_session='+session+'; Path=/; HttpOnly; SameSite=Strict');
        if(fixture.held.has(token)) fixture.responses.set(token,finish);else finish();
      });
    } else if(req.method==='DELETE') {
      const finish=()=>send(false,'lumen_fixture_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict');
      if(fixture.heldLogout) fixture.responses.set('logout',finish);else finish();
    }
    else send(!fixture.expired && /(?:^|; )lumen_fixture_session=[ABC](?:;|$)/.test(req.headers.cookie||''));
    return;
  }
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
  const storedPosition=(id,voice,time)=>{
    const payload=payloads.get('/fixture/'+id+'-'+voice+'.json');
    const sentence=payload.paragraphs[0].sentences[time>=payload.duration/2?1:0];
    return {schemaVersion:1,bookId:'lumen',manuscriptVersion:'v6',chapterId:id,readingExtentId:payload.id,
      sentenceId:sentence.id,sentenceFraction:(time-sentence.start)/(sentence.end-sentence.start),
      narratorId:voice,audioSha256:payload.audio.sha256,audioTime:time,completed:false,updatedAt:new Date().toISOString()};
  };
  const fixtureContext=async({legacy=null,saved=null,index=manifest,failures=[],overrides=new Map()}={})=>{
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,serviceWorkers:'block'});
    const page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));
    await page.route('**/api/session',r=>r.fulfill({json:{authenticated:true}}));
    await page.route('**/api/book',r=>r.fulfill({json:index}));
    await page.route('**/fixture/**',r=>{
      const pathname=new URL(r.request().url()).pathname;
      if(failures.includes(pathname)) return r.fulfill({status:503});
      return overrides.has(pathname)?r.fulfill({json:overrides.get(pathname)}):fixtureRoute(r);
    });
    await context.addInitScript(({legacy,saved})=>{
      if(legacy) localStorage.setItem('lumen-reader-v1',JSON.stringify(legacy));
      if(saved) localStorage.setItem('lumen-book-v2',JSON.stringify(saved));
    },{legacy,saved});
    return {context,page};
  };
  const ready=page=>page.waitForFunction(()=>window.LUMEN_CHAPTER && document.querySelector('#narration').readyState>=1);
  const choose=async(page,id)=>{await page.locator('#chapters-open').click();await page.locator('#chapter-list [data-track-id="'+id+'"]').click();};
  const reached=(page,id)=>page.waitForFunction(id=>window.LUMEN_CHAPTER?.chapterId===id && document.querySelector('#narration').readyState>=1,id);
  const finished=storedPosition('chapter-001','autonoe',12);finished.completed=true;
  const trailingSilence=JSON.parse(JSON.stringify(payloads.get('/fixture/chapter-001-charon.json')));
  trailingSilence.paragraphs[0].end=7;trailingSilence.paragraphs[0].sentences[1].end=7;
  const finishedReader=await fixtureContext({legacy:finished,overrides:new Map([['/fixture/chapter-001-charon.json',trailingSilence]])});
  await finishedReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(finishedReader.page);
  await finishedReader.page.locator('#settings-open').click();await finishedReader.page.locator('#narrator').selectOption('charon');
  await finishedReader.page.locator('#settings-close').click();
  await finishedReader.page.waitForFunction(()=>window.LUMEN_CHAPTER.audio.narratorId==='charon' && document.querySelector('#narration').readyState>=1);
  await finishedReader.page.waitForTimeout(100);
  regression('a completed full chapter stays completed at the new narrator endpoint',await finishedReader.page.evaluate(()=>{
    const audio=document.querySelector('#narration'),saved=JSON.parse(localStorage.getItem('lumen-book-v2')).lastPosition;
    return audio.paused && Math.abs(audio.currentTime-8)<.04 && saved.completed && Math.abs(saved.audioTime-8)<.04;
  }));
  await finishedReader.context.close();
  const rapidReader=await fixtureContext();
  let heldChapter=false,heldRequested=false,releaseHeld;
  await rapidReader.page.route('**/fixture/chapter-002-autonoe.json',async route=>{
    if(heldChapter) {heldRequested=true;await new Promise(resolve=>releaseHeld=resolve);}
    await route.fulfill({json:payloads.get('/fixture/chapter-002-autonoe.json')});
  });
  const holdNext=()=>{heldChapter=true;heldRequested=false;releaseHeld=undefined;};
  const held=async()=>{for(let n=0;n<100&&!heldRequested;n++) await rapidReader.page.waitForTimeout(10);assert.ok(heldRequested,'deferred chapter request started');};
  const releaseNext=async()=>{heldChapter=false;releaseHeld();await rapidReader.page.waitForTimeout(80);};
  await rapidReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(rapidReader.page);
  await rapidReader.page.locator('#play').click();await rapidReader.page.waitForFunction(()=>!document.querySelector('#narration').paused);
  holdNext();await choose(rapidReader.page,'chapter-002');await held();
  await choose(rapidReader.page,'chapter-003');await reached(rapidReader.page,'chapter-003');await rapidReader.page.waitForTimeout(100);
  regression('rapid chapter navigation inherits the pending listening intent',await rapidReader.page.evaluate(()=>!document.querySelector('#narration').paused));
  await releaseNext();
  regression('a superseded chapter response cannot stop the current listening choice',await rapidReader.page.evaluate(()=>
    window.LUMEN_CHAPTER.chapterId==='chapter-003' && !document.querySelector('#narration').paused));
  if(await rapidReader.page.locator('#play').getAttribute('aria-label')!=='Pause audiobook') await rapidReader.page.locator('#play').click();
  await rapidReader.page.waitForFunction(()=>!document.querySelector('#narration').paused);
  await choose(rapidReader.page,'chapter-001');await reached(rapidReader.page,'chapter-001');
  holdNext();await choose(rapidReader.page,'chapter-002');await held();
  await rapidReader.page.locator('#settings-open').click();await rapidReader.page.locator('#narrator').selectOption('charon');await rapidReader.page.locator('#settings-close').click();
  await reached(rapidReader.page,'chapter-002');await rapidReader.page.waitForTimeout(100);
  regression('a narrator choice for a loading chapter inherits the pending listening intent',await rapidReader.page.evaluate(()=>
    window.LUMEN_CHAPTER.audio.narratorId==='charon' && !document.querySelector('#narration').paused));
  await releaseNext();
  await rapidReader.page.locator('#settings-open').click();await rapidReader.page.locator('#narrator').selectOption('autonoe');await rapidReader.page.locator('#settings-close').click();
  await rapidReader.page.waitForFunction(()=>window.LUMEN_CHAPTER.audio.narratorId==='autonoe');
  if(await rapidReader.page.locator('#play').getAttribute('aria-label')!=='Pause audiobook') await rapidReader.page.locator('#play').click();
  await rapidReader.page.waitForFunction(()=>!document.querySelector('#narration').paused);
  await choose(rapidReader.page,'chapter-001');await reached(rapidReader.page,'chapter-001');
  holdNext();await choose(rapidReader.page,'chapter-002');await held();
  await rapidReader.page.locator('#play').click();
  regression('an explicit pause during loading leaves the old source paused',await rapidReader.page.evaluate(()=>document.querySelector('#narration').paused));
  await choose(rapidReader.page,'chapter-003');await reached(rapidReader.page,'chapter-003');await rapidReader.page.waitForTimeout(100);
  regression('an explicit pause overrides listening intent for a later chapter choice',await rapidReader.page.evaluate(()=>document.querySelector('#narration').paused));
  await releaseNext();
  if(await rapidReader.page.locator('#play').getAttribute('aria-label')==='Pause audiobook') await rapidReader.page.locator('#play').click();
  await rapidReader.page.waitForFunction(()=>document.querySelector('#narration').paused);
  await choose(rapidReader.page,'chapter-001');await reached(rapidReader.page,'chapter-001');
  holdNext();await choose(rapidReader.page,'chapter-002');await held();await choose(rapidReader.page,'chapter-003');
  await reached(rapidReader.page,'chapter-003');await rapidReader.page.waitForTimeout(100);
  regression('rapid navigation that starts paused stays paused',await rapidReader.page.evaluate(()=>document.querySelector('#narration').paused));
  await releaseNext();await rapidReader.context.close();
  const inviteContext=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});
  const inviteReader=await inviteContext.newPage();inviteReader.on('pageerror',error=>errors.push(String(error)));
  const firstInvite='r'.repeat(43),replacementInvite='s'.repeat(43),invitePosts=[];
  let inviteAuthenticated=false,inviteUnavailable=true;
  await inviteReader.route('**/api/session',async route=>{
    if(route.request().method()==='POST') {
      invitePosts.push(JSON.parse(route.request().postData()).invite);
      if(inviteUnavailable) return route.fulfill({status:503});
      inviteAuthenticated=true;
    }
    if(route.request().method()==='DELETE') inviteAuthenticated=false;
    await route.fulfill({json:{authenticated:inviteAuthenticated}});
  });
  await inviteReader.route('**/api/book',route=>route.fulfill({json:manifest}));await inviteReader.route('**/fixture/**',fixtureRoute);
  await inviteReader.goto(origin+'/#invite='+firstInvite,{waitUntil:'domcontentloaded'});
  await inviteReader.waitForFunction(()=>document.querySelector('#access-status').textContent.includes('could not be verified'));
  regression('a failed invitation is immediately removed from the address bar',new URL(inviteReader.url()).hash==='');
  inviteUnavailable=false;await inviteReader.locator('#invite-submit').click();await inviteReader.waitForTimeout(200);
  regression('an empty invitation retry reuses the original fragment token',invitePosts.length===2 && invitePosts[1]===firstInvite);
  const inviteRetried=await inviteReader.evaluate(()=>!!window.LUMEN_BOOK);
  regression('a successful fragment retry opens the book without pasting the invitation again',inviteRetried);
  if(!inviteRetried) {await inviteReader.locator('#invite-code').fill(firstInvite);await inviteReader.locator('#invite-submit').click();}
  await ready(inviteReader);
  regression('retained invitation tokens never enter browser storage',await inviteReader.evaluate(tokens=>
    !Object.values(localStorage).some(value=>tokens.some(token=>value.includes(token))) &&
    !Object.values(sessionStorage).some(value=>tokens.some(token=>value.includes(token))),[firstInvite,replacementInvite]));
  await inviteReader.locator('#settings-open').click();await inviteReader.locator('#sign-out-settings').click();
  await inviteReader.waitForFunction(()=>!document.body.classList.contains('authenticated'));
  const postCount=invitePosts.length;await inviteReader.locator('#invite-submit').click();await inviteReader.waitForTimeout(100);
  regression('sign out clears the retained invitation retry token',invitePosts.length===postCount);
  inviteUnavailable=true;await inviteReader.evaluate(token=>{location.hash='invite='+token;},firstInvite);
  await inviteReader.waitForFunction(()=>document.querySelector('#access-status').textContent.includes('could not be verified'));
  await inviteReader.evaluate(token=>{location.hash='invite='+token;},replacementInvite);
  for(let n=0;n<100&&invitePosts.at(-1)!==replacementInvite;n++) await inviteReader.waitForTimeout(10);
  await inviteReader.waitForFunction(()=>!document.querySelector('#invite-submit').disabled);
  inviteUnavailable=false;await inviteReader.locator('#invite-submit').click();await inviteReader.waitForTimeout(200);
  regression('a new invitation replaces the prior token for an empty retry',invitePosts.at(-1)===replacementInvite && await inviteReader.evaluate(()=>!!window.LUMEN_BOOK));
  await inviteContext.close();
  const cookieContext=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});
  const cookieReader=await cookieContext.newPage();cookieReader.on('pageerror',error=>errors.push(String(error)));
  const cookieA='u'.repeat(43),cookieB='v'.repeat(43),cookieC='w'.repeat(43);
  cookieFixture={tokens:new Map([[cookieA,'A'],[cookieB,'B'],[cookieC,'C']]),held:new Set([cookieA,cookieC]),responses:new Map(),posts:[]};
  const cookieRequested=async token=>{for(let n=0;n<200&&!cookieFixture.responses.has(token);n++) await cookieReader.waitForTimeout(10);assert.ok(cookieFixture.responses.has(token),'held cookie exchange reached server');};
  await cookieReader.route('**/api/book',route=>route.fulfill({json:manifest}));await cookieReader.route('**/fixture/**',fixtureRoute);
  await cookieReader.goto(origin+'/#invite='+cookieA,{waitUntil:'domcontentloaded'});await cookieRequested(cookieA);
  await cookieReader.evaluate(token=>{location.hash='invite='+token;},cookieB);await ready(cookieReader);
  regression('replacement invitation installs the actual new browser session cookie',(await cookieContext.cookies(origin)).some(cookie=>cookie.name==='lumen_fixture_session' && cookie.value==='B'));
  await cookieReader.locator('#settings-open').click();await cookieReader.locator('#sign-out-settings').click();
  await cookieReader.waitForFunction(()=>!document.body.classList.contains('authenticated'));
  cookieFixture.responses.get(cookieA)();await cookieReader.waitForTimeout(100);
  regression('a late obsolete invitation response cannot restore a signed-out browser cookie',(await cookieContext.cookies(origin)).every(cookie=>cookie.name!=='lumen_fixture_session'));
  await cookieReader.reload({waitUntil:'domcontentloaded'});await cookieReader.waitForTimeout(200);
  regression('reload stays locked after an obsolete invitation responds late',await cookieReader.evaluate(()=>!window.LUMEN_BOOK && !document.body.classList.contains('authenticated')));
  await cookieReader.evaluate(token=>{location.hash='invite='+token;},cookieB);await ready(cookieReader);
  const cookieD='x'.repeat(43);cookieFixture.tokens.set(cookieD,'C');cookieFixture.held.add(cookieD);cookieFixture.expired=true;
  await cookieReader.evaluate(token=>{window.fixtureBookBeforeInvite=window.LUMEN_BOOK;location.hash='invite='+token;},cookieD);
  await cookieRequested(cookieD);await cookieReader.evaluate(()=>document.querySelector('#narration').dispatchEvent(new Event('error')));
  await cookieReader.waitForTimeout(150);cookieFixture.expired=false;cookieFixture.responses.get(cookieD)();await cookieReader.waitForTimeout(250);
  regression('an expired old-source error cannot cancel an active valid replacement invitation',await cookieReader.evaluate(()=>
    !!window.LUMEN_BOOK && window.LUMEN_BOOK!==window.fixtureBookBeforeInvite && document.body.classList.contains('authenticated')));
  regression('replacement invitation still installs its browser cookie after an old-source error',(await cookieContext.cookies(origin)).some(cookie=>
    cookie.name==='lumen_fixture_session' && cookie.value==='C'));
  await cookieReader.evaluate(token=>{location.hash='invite='+token;},cookieB);await ready(cookieReader);
  await cookieReader.evaluate(token=>{location.hash='invite='+token;},cookieC);await cookieRequested(cookieC);
  await cookieReader.locator('#settings-open').click();await cookieReader.locator('#sign-out-settings').click();
  await cookieReader.waitForFunction(()=>!document.body.classList.contains('authenticated'));
  cookieFixture.responses.get(cookieC)();await cookieReader.waitForTimeout(100);
  regression('sign out cancels an active invitation exchange before clearing its cookie',(await cookieContext.cookies(origin)).every(cookie=>cookie.name!=='lumen_fixture_session'));
  await cookieReader.reload({waitUntil:'domcontentloaded'});await cookieReader.waitForTimeout(200);
  regression('reload stays locked after sign out during an invitation exchange',await cookieReader.evaluate(()=>!window.LUMEN_BOOK && !document.body.classList.contains('authenticated')));
  await cookieReader.evaluate(token=>{location.hash='invite='+token;},cookieB);await ready(cookieReader);
  cookieFixture.heldLogout=true;
  await cookieReader.locator('#settings-open').click();await cookieReader.locator('#sign-out-settings').click();
  await cookieRequested('logout');await cookieReader.evaluate(()=>{location.hash='invite=invalid';});
  await cookieReader.waitForTimeout(50);cookieFixture.responses.get('logout')();await cookieReader.waitForTimeout(150);
  regression('an invalid invitation cannot cancel an explicit sign-out exchange',(await cookieContext.cookies(origin)).every(cookie=>cookie.name!=='lumen_fixture_session'));
  regression('an invalid invitation during logout cannot keep the old reader unlocked',await cookieReader.evaluate(()=>!window.LUMEN_BOOK && !document.body.classList.contains('authenticated')));
  await cookieContext.close();cookieFixture=null;
  const voiceRetryFailures=['/fixture/chapter-001-charon.json'],voiceRetryReader=await fixtureContext({failures:voiceRetryFailures});
  await voiceRetryReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(voiceRetryReader.page);
  await voiceRetryReader.page.locator('#seek').evaluate(el=>{el.value='3';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await voiceRetryReader.page.locator('#play').click();await voiceRetryReader.page.waitForFunction(()=>!document.querySelector('#narration').paused);
  await voiceRetryReader.page.locator('#settings-open').click();await voiceRetryReader.page.locator('#narrator').selectOption('charon');await voiceRetryReader.page.locator('#settings-close').click();
  await voiceRetryReader.page.locator('#transition-retry').waitFor({state:'visible'});
  await voiceRetryReader.page.waitForFunction(()=>!document.querySelector('#narration').paused);
  await voiceRetryReader.page.locator('#play').click();
  await voiceRetryReader.page.locator('#seek').evaluate(el=>{el.value='9';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await voiceRetryReader.page.waitForFunction(()=>Math.abs(document.querySelector('#narration').currentTime-9)<.04);
  voiceRetryFailures.length=0;await voiceRetryReader.page.locator('#transition-retry').click();
  await voiceRetryReader.page.waitForFunction(()=>window.LUMEN_CHAPTER.audio.narratorId==='charon' && document.querySelector('#narration').readyState>=1);
  await voiceRetryReader.page.waitForTimeout(100);
  regression('a failed narrator retry maps the current passage after old-source playback and seeking',await voiceRetryReader.page.evaluate(()=>
    document.querySelector('#narration').paused && Math.abs(document.querySelector('#narration').currentTime-6)<.04));
  await voiceRetryReader.context.close();
  const invalidBlocks=JSON.parse(JSON.stringify(payloads.get('/fixture/chapter-002-autonoe.json')));invalidBlocks.blocks=[];
  const blockReader=await fixtureContext({overrides:new Map([['/fixture/chapter-002-autonoe.json',invalidBlocks]])});
  await blockReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(blockReader.page);
  const oldBlockState=await blockReader.page.evaluate(()=>({src:document.querySelector('#narration').getAttribute('src'),prose:document.querySelector('#prose').textContent}));
  await choose(blockReader.page,'chapter-002');await blockReader.page.waitForTimeout(150);
  regression('invalid chapter blocks are rejected before any reader commit',await blockReader.page.evaluate(old=>
    window.LUMEN_CHAPTER.chapterId==='chapter-001' && document.querySelector('#narration').getAttribute('src')===old.src &&
    document.querySelector('#prose').textContent===old.prose && !document.querySelector('#transition-retry').hidden,oldBlockState));
  await blockReader.context.close();
  const validEmphasis=JSON.parse(JSON.stringify(payloads.get('/fixture/chapter-002-autonoe.json'))),emphasisParagraph=validEmphasis.paragraphs[0];
  emphasisParagraph.emphasis=[{start:0,end:4},{start:4,end:22,kind:'bold'},{start:22,end:emphasisParagraph.text.length,kind:'italic'}];
  const invalidEmphasis=JSON.parse(JSON.stringify(payloads.get('/fixture/chapter-003-autonoe.json')));
  const emphasisReader=await fixtureContext({overrides:new Map([['/fixture/chapter-002-autonoe.json',validEmphasis],['/fixture/chapter-003-autonoe.json',invalidEmphasis]])});
  await emphasisReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(emphasisReader.page);
  await choose(emphasisReader.page,'chapter-002');await reached(emphasisReader.page,'chapter-002');
  regression('adjacent emphasis across sentence boundaries displays the manuscript exactly once',await emphasisReader.page.evaluate(text=>
    document.querySelector('#prose').textContent===text && document.querySelectorAll('.sentence strong').length===2 &&
    document.querySelectorAll('.sentence em').length===2,emphasisParagraph.text));
  const oldEmphasisState=await emphasisReader.page.evaluate(()=>({src:document.querySelector('#narration').getAttribute('src'),prose:document.querySelector('#prose').textContent}));
  for(const [name,spans] of [
    ['overlapping',[{start:0,end:12,kind:'italic'},{start:8,end:20,kind:'bold'}]],
    ['out-of-order',[{start:20,end:25,kind:'bold'},{start:0,end:5,kind:'italic'}]]
  ]) {
    invalidEmphasis.paragraphs[0].emphasis=spans;
    await choose(emphasisReader.page,'chapter-003');await emphasisReader.page.waitForTimeout(150);
    regression(name+' emphasis is rejected before changing prose or its audio source',await emphasisReader.page.evaluate(old=>
      window.LUMEN_CHAPTER.chapterId==='chapter-002' && document.querySelector('#narration').getAttribute('src')===old.src &&
      document.querySelector('#prose').textContent===old.prose && !document.querySelector('#transition-retry').hidden,oldEmphasisState));
  }
  await emphasisReader.context.close();
  const failedNext=['/fixture/chapter-002-autonoe.json'];
  const endedReader=await fixtureContext({failures:failedNext});
  await endedReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(endedReader.page);
  await endedReader.page.locator('#play').click();await endedReader.page.waitForTimeout(150);
  await choose(endedReader.page,'chapter-002');await endedReader.page.locator('#transition-retry').waitFor({state:'visible'});
  regression('failed transition resumes an old source that was actively playing',await endedReader.page.evaluate(()=>
    window.LUMEN_CHAPTER.chapterId==='chapter-001' && !document.querySelector('#narration').paused));
  await endedReader.page.locator('#play').click();
  await endedReader.page.locator('#settings-open').click();await endedReader.page.locator('#auto-continue').check();await endedReader.page.locator('#settings-close').click();
  await endedReader.page.locator('#seek').evaluate(el=>{el.value='11.9';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await endedReader.page.waitForFunction(()=>Math.abs(document.querySelector('#narration').currentTime-11.9)<.04);
  await endedReader.page.evaluate(()=>{
    window.fixtureEndedCount=0;document.querySelector('#narration').addEventListener('ended',()=>window.fixtureEndedCount++);
  });
  await endedReader.page.locator('#play').click();
  await endedReader.page.waitForFunction(()=>window.fixtureEndedCount===1 && !document.querySelector('#transition-retry').hidden &&
    document.querySelector('#transition-status').textContent.includes('could not be loaded'));
  await endedReader.page.waitForTimeout(150);
  regression('failed automatic continuation leaves completed narration stopped at its end',await endedReader.page.evaluate(()=>{
    const audio=document.querySelector('#narration');return window.LUMEN_CHAPTER.chapterId==='chapter-001' && audio.paused && audio.ended && Math.abs(audio.currentTime-12)<.04;
  }));
  regression('failed automatic continuation preserves the completed end bookmark',await endedReader.page.evaluate(()=>{
    const saved=JSON.parse(localStorage.getItem('lumen-book-v2')).history['chapter-001'].bookmark;
    return saved.completed===true && Math.abs(saved.audioTime-12)<.04;
  }));
  failedNext.length=0;await endedReader.page.locator('#transition-retry').click();await reached(endedReader.page,'chapter-002');
  await endedReader.page.waitForFunction(()=>!document.querySelector('#narration').paused);
  regression('successful continuation retry starts the requested next chapter',await endedReader.page.evaluate(()=>
    window.LUMEN_CHAPTER.chapterId==='chapter-002' && document.querySelector('#narration').currentTime<1));
  await endedReader.context.close();
  const introPayloads=new Map();
  for(const [voice,start] of [['autonoe',4],['charon',2]]) {
    const key='/fixture/chapter-001-'+voice+'.json',payload=JSON.parse(JSON.stringify(payloads.get(key)));
    payload.paragraphs[0].start=payload.paragraphs[0].sentences[0].start=start;introPayloads.set(key,payload);
  }
  const introReader=await fixtureContext({overrides:introPayloads});
  await introReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(introReader.page);
  await introReader.page.locator('#seek').evaluate(el=>{el.value='1';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await introReader.page.waitForFunction(()=>Math.abs(document.querySelector('#narration').currentTime-1)<.04);
  await introReader.page.locator('#settings-open').click();await introReader.page.locator('#narrator').selectOption('charon');await introReader.page.locator('#settings-close').click();
  await introReader.page.waitForFunction(()=>window.LUMEN_CHAPTER.audio.narratorId==='charon' ||
    !document.querySelector('#transition-panel').hidden && !document.querySelector('#transition-retry').hidden);
  await introReader.page.waitForTimeout(100);
  regression('an unmeasured introduction voice change keeps the current source and exact position',await introReader.page.evaluate(()=>
    window.LUMEN_CHAPTER.audio.narratorId==='autonoe' && Math.abs(document.querySelector('#narration').currentTime-1)<.04 && document.querySelector('#narration').paused));
  regression('an introduction without an anchor reports unavailable narrator mapping',await introReader.page.evaluate(()=>
    !document.querySelector('#transition-panel').hidden && document.querySelector('#transition-status').textContent.includes('could not be mapped')));
  regression('the saved introductory position explicitly has no measured anchor',await introReader.page.evaluate(()=>{
    const saved=JSON.parse(localStorage.getItem('lumen-book-v2')).lastPosition;
    return saved.anchorMeasured===false && saved.audioTime===1 && saved.narratorId==='autonoe';
  }));
  await introReader.page.locator('#seek').evaluate(el=>{el.value='0';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await introReader.page.locator('#settings-open').click();await introReader.page.locator('#narrator').selectOption('charon');await introReader.page.locator('#settings-close').click();
  await introReader.page.waitForFunction(()=>window.LUMEN_CHAPTER.audio.narratorId==='charon' && document.querySelector('#narration').readyState>=1);
  regression('exact zero still switches voices at the start of the introduction',await introReader.page.evaluate(()=>
    document.querySelector('#narration').currentTime===0 && document.querySelector('#narration').paused));
  await introReader.context.close();
  const legacyReader=await fixtureContext({legacy:storedPosition('chapter-002','charon',3.2)});
  await legacyReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(legacyReader.page);
  await legacyReader.page.waitForFunction(()=>Math.abs(document.querySelector('#narration').currentTime-3.2)<.25);
  regression('initial legacy resume is saved in v2 before the reader plays or navigates',await legacyReader.page.evaluate(()=>
    Math.abs(JSON.parse(localStorage.getItem('lumen-book-v2')||'null')?.history['chapter-002']?.bookmark.audioTime-3.2)<.25));
  await choose(legacyReader.page,'chapter-003');await reached(legacyReader.page,'chapter-003');
  await choose(legacyReader.page,'chapter-002');await reached(legacyReader.page,'chapter-002');
  await legacyReader.page.waitForTimeout(100);
  regression('navigating before playback preserves the migrated chapter history',await legacyReader.page.evaluate(()=>
    Math.abs(document.querySelector('#narration').currentTime-3.2)<.25));
  await legacyReader.context.close();
  const retryState={schemaVersion:2,bookId:'lumen',manuscriptVersion:'v6',releaseId:manifest.releaseId,
    lastPosition:storedPosition('chapter-001','charon',1.6),history:{'chapter-003':{bookmark:storedPosition('chapter-003','charon',6)}}};
  const retryReader=await fixtureContext({saved:retryState,failures:['/fixture/chapter-003-charon.json']});
  await retryReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(retryReader.page);
  await choose(retryReader.page,'chapter-003');await retryReader.page.locator('#transition-retry').waitFor({state:'visible'});
  await retryReader.page.locator('#settings-open').click();await retryReader.page.locator('#narrator').selectOption('autonoe');
  await retryReader.page.locator('#settings-close').click();await reached(retryReader.page,'chapter-003');
  await retryReader.page.waitForTimeout(100);
  regression('alternate target narrator restores the target history rather than resetting it',await retryReader.page.evaluate(()=>
    window.LUMEN_CHAPTER.audio.narratorId==='autonoe' && Math.abs(document.querySelector('#narration').currentTime-9)<.25));
  await retryReader.context.close();
  const disjoint=JSON.parse(JSON.stringify(manifest));disjoint.defaultNarratorId='charon';
  disjoint.tracks[0].recordings.autonoe={status:'unavailable'};disjoint.tracks[2].recordings.charon={status:'unavailable'};
  const disjointReader=await fixtureContext({index:disjoint});
  await disjointReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(disjointReader.page);
  await choose(disjointReader.page,'chapter-003');await disjointReader.page.locator('#transition-retry').waitFor({state:'visible'});
  await disjointReader.page.locator('#settings-open').click();
  const targetAvailable=await disjointReader.page.locator('#narrator option[value="autonoe"]').evaluate(el=>!el.disabled);
  regression('failed target keeps its available narrator choices reachable',targetAvailable);
  regression('disjoint target failure keeps the current chapter playable',await disjointReader.page.evaluate(()=>
    window.LUMEN_CHAPTER.chapterId==='chapter-001' && !document.querySelector('#play').disabled && !!document.querySelector('#narration').getAttribute('src')));
  if(targetAvailable) {
    await disjointReader.page.locator('#narrator').selectOption('autonoe');await disjointReader.page.locator('#settings-close').click();
    await reached(disjointReader.page,'chapter-003');
    regression('an available alternative can open a disjoint target',await disjointReader.page.evaluate(()=>window.LUMEN_CHAPTER.audio.narratorId==='autonoe'));
  }
  await disjointReader.context.close();
  const painted=JSON.parse(JSON.stringify(payloads.get('/fixture/chapter-001-autonoe.json')));
  const assetPath=id=>'/api/assets/'+id+'.'+id.repeat(64).slice(0,64)+'.png';
  painted.scenes=[{id:'a',start:4,src:'https://lumen-phi-five.vercel.app'+assetPath('a'),title:'Late reveal'},
    {id:'b',start:8,src:'https://lumen-phi-five.vercel.app'+assetPath('b'),title:'Second reveal'}];
  const artReader=await fixtureContext({overrides:new Map([['/fixture/chapter-001-autonoe.json',painted]])});
  const artRequests=[],pendingPaint=[];
  await artReader.page.route('**/api/assets/**',async route=>{
    artRequests.push(route.request().url());
    if(new URL(route.request().url()).pathname===assetPath('b')) await new Promise(resolve=>pendingPaint.push(resolve));
    await route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=','base64')});
  });
  await artReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(artReader.page);await artReader.page.waitForTimeout(100);
  regression('before the first illustration trigger the reader shows only a generic placeholder',await artReader.page.evaluate(()=>
    document.querySelector('#scene-stage').classList.contains('placeholder-art') && !document.querySelector('#scene-title').textContent && !document.querySelector('#scene-image').getAttribute('src')));
  await artReader.page.locator('#seek').evaluate(el=>{el.value='4.5';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await artReader.page.waitForFunction(()=>!!document.querySelector('#scene-image').getAttribute('src'));
  regression('private painting URLs use the authenticated preview origin',artRequests.length>0 && artRequests.every(url=>new URL(url).origin===origin));
  await artReader.page.locator('#seek').evaluate(el=>{el.value='8.5';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await artReader.page.waitForFunction(()=>document.querySelector('#scene-title').textContent==='Second reveal');
  await artReader.page.locator('#seek').evaluate(el=>{el.value='0';el.dispatchEvent(new Event('input',{bubbles:true}));});
  regression('rewinding before the trigger clears the painting and caption',await artReader.page.evaluate(()=>
    document.querySelector('#scene-stage').classList.contains('placeholder-art') && !document.querySelector('#scene-image').getAttribute('src') && !document.querySelector('#scene-incoming').getAttribute('src') && !document.querySelector('#scene-title').textContent));
  for(const releasePaint of pendingPaint) releasePaint();await artReader.page.waitForTimeout(150);
  regression('a delayed painting cannot reveal itself after rewinding',await artReader.page.evaluate(()=>
    document.querySelector('#scene-stage').classList.contains('placeholder-art') && !document.querySelector('#scene-image').getAttribute('src') && !document.querySelector('#scene-title').textContent));
  painted.scenes[0].start=0;
  await artReader.page.reload({waitUntil:'domcontentloaded'});await ready(artReader.page);
  await artReader.page.waitForFunction(()=>!!document.querySelector('#scene-image').getAttribute('src'));
  await artReader.page.locator('#settings-open').click();await artReader.page.locator('#sign-out-settings').click();
  await artReader.page.waitForFunction(()=>!document.body.classList.contains('authenticated'));
  await artReader.page.evaluate(()=>document.querySelector('#narration').dispatchEvent(new Event('timeupdate')));
  await artReader.page.waitForTimeout(100);
  regression('late audio events after sign out cannot restore a private painting',await artReader.page.evaluate(()=>
    !document.querySelector('#scene-image').getAttribute('src') && !document.querySelector('#scene-incoming').getAttribute('src') && !document.querySelector('#scene-title').textContent));
  await artReader.context.close();
  const staleSessionReader=await fixtureContext();
  await staleSessionReader.page.goto(origin,{waitUntil:'domcontentloaded'});await ready(staleSessionReader.page);
  let deferOldStatus=true,oldStatusRequested=false,releaseOldStatus;
  await staleSessionReader.page.route('**/api/session',async route=>{
    if(route.request().method()==='GET' && deferOldStatus){
      deferOldStatus=false;oldStatusRequested=true;await new Promise(resolve=>releaseOldStatus=resolve);
      return route.fulfill({json:{authenticated:false}});
    }
    return route.fulfill({json:{authenticated:true}});
  });
  await staleSessionReader.page.evaluate(()=>{
    window.fixtureOldBook=window.LUMEN_BOOK;document.querySelector('#narration').dispatchEvent(new Event('error'));
  });
  while(!oldStatusRequested)await staleSessionReader.page.waitForTimeout(10);
  await staleSessionReader.page.evaluate(()=>location.hash='invite='+('a'.repeat(43)));
  await staleSessionReader.page.waitForFunction(()=>window.LUMEN_BOOK && window.LUMEN_BOOK!==window.fixtureOldBook &&
    window.LUMEN_BOOK.getActive() && document.body.classList.contains('authenticated'));
  releaseOldStatus();await staleSessionReader.page.waitForTimeout(250);
  regression('an old audio-error session result cannot close a newly authenticated reader',await staleSessionReader.page.evaluate(()=>
    document.body.classList.contains('authenticated') && !!window.LUMEN_BOOK?.getActive()));
  await staleSessionReader.context.close();
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
  await guestContext.addInitScript(()=>{
    if(navigator.mediaSession?.setPositionState) {
      const native=navigator.mediaSession.setPositionState.bind(navigator.mediaSession);
      window.fixtureMediaPositions=[];
      navigator.mediaSession.setPositionState=function(value){window.fixtureMediaPositions.push(value??null);return native(value);};
    }
  });
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
  await guest.locator('#settings-open').click();
  const autoLabel=await guest.locator('.auto-continue-label').boundingBox();
  regression('phone auto-continue label provides a 44px touch target',autoLabel.height>=44);
  const autoBefore=await guest.locator('#auto-continue').isChecked();
  await guest.touchscreen.tap(autoLabel.x+autoLabel.width-12,autoLabel.y+autoLabel.height/2);
  check('phone label touch toggles native auto-continue checkbox',await guest.locator('#auto-continue').isChecked()!==autoBefore);
  await guest.locator('#sign-out-settings').click();
  await guest.waitForFunction(()=>!document.body.classList.contains('authenticated'));
  check('phone sign out clears displayed prose and audio',await guest.evaluate(()=>!document.querySelector('#prose').textContent && !document.querySelector('#narration').getAttribute('src')));
  await guest.waitForTimeout(100);
  regression('phone sign out clears private lock-screen media metadata and playback state',await guest.evaluate(()=>
    navigator.mediaSession.metadata===null && navigator.mediaSession.playbackState==='none'));
  regression('phone sign out resets the lock-screen audio position',await guest.evaluate(()=>window.fixtureMediaPositions.at(-1)===null));
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
  regression('the authenticated shell stays generic while its book index loads',await race.evaluate(()=>
    document.querySelector('#scene-stage').classList.contains('placeholder-art') && !document.querySelector('#scene-title').textContent && !document.querySelector('#scene-image').getAttribute('src')));
  await race.locator('#settings-open').click();await race.locator('#sign-out-settings').click();
  await race.waitForFunction(()=>!document.body.classList.contains('authenticated'));
  releaseIndex();await race.waitForTimeout(250);
  check('delayed bootstrap cannot restore private content after sign out',await race.evaluate(()=>
    !document.querySelector('#prose').textContent && !document.querySelector('#narration').getAttribute('src') && !window.LUMEN_BOOK));
  await raceContext.close();
  check('no browser JavaScript errors',errors.length===0);
  assert.ok(checks.every(check=>check.pass),'All browser regression checks must pass.');
  await context.close();
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({checks,errors},null,2)+'\n');
  if(browser) await browser.close();await new Promise(resolve=>server.close(resolve));
});
