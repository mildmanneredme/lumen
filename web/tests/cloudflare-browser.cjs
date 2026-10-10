#!/usr/bin/env node
'use strict';
// Hosted acceptance only. Codes/bypass credentials stay in memory; no traces,
// HAR, storageState, response bodies, cookies or grant queries are recorded.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');

function originOf(value,vercel=false) {
  assert.equal(typeof value,'string','An explicit HTTPS origin is required');
  const url=new URL(value);
  assert.ok(url.protocol==='https:'&&url.origin===value&&!url.username&&!url.password,'Use a canonical HTTPS origin');
  if(vercel)assert.ok(url.hostname.endsWith('.vercel.app'),'Select the Vercel candidate origin');
  return url.origin;
}
function readConfig(env) {
  const origin=originOf(env.LUMEN_QA_ORIGIN,true),workerOrigin=originOf(env.LUMEN_QA_WORKER_ORIGIN);
  assert.notEqual(origin,workerOrigin,'Media must use the separate Worker origin');
  const code=env.LUMEN_QA_ACCESS_CODE;
  assert.ok(typeof code==='string'&&code.length>0&&code.length<=256,'Supply the access code privately through the environment');
  const bypass=env.LUMEN_QA_VERCEL_BYPASS;
  assert.ok(bypass===undefined||typeof bypass==='string'&&bypass.length>0&&bypass.length<=4096,'Invalid private preview bypass');
  const output=path.resolve(env.LUMEN_QA_OUT||'');
  assert.ok(typeof env.LUMEN_QA_OUT==='string'&&output.startsWith('/private/tmp/'),'Evidence must remain under /private/tmp');
  return {origin,workerOrigin,code,bypass,output};
}
function safeURL(value) {
  try {const url=new URL(value);return {origin:url.origin,path:url.pathname};}
  catch(_){return {origin:'invalid',path:''};}
}
function prepareOutput(output) {
  let current='/private/tmp';
  for(const part of path.relative(current,output).split(path.sep)) {
    assert.ok(part&&part!=='.'&&part!=='..');current=path.join(current,part);
    try{fs.mkdirSync(current,{mode:0o700});}catch(error){if(error.code!=='EEXIST')throw error;}
    const info=fs.lstatSync(current);
    assert.ok(info.isDirectory()&&!info.isSymbolicLink()&&info.uid===process.getuid()&&(info.mode&0o077)===0,'Use an owned private evidence directory');
  }
}
async function previewCookie(context,config) {
  if(!config.bypass)return;
  // APIRequestContext shares cookies with this browser context. Only this
  // original-host request receives the bypass; redirects cannot forward it.
  const response=await context.request.get(config.origin+'/',{maxRedirects:0,headers:{
    'x-vercel-protection-bypass':config.bypass,'x-vercel-set-bypass-cookie':'true'}});
  try {assert.ok([200,302,303,307,308].includes(response.status()),'Preview cookie exchange failed');}
  finally{await response.dispose();}
}
async function run(config) {
  let playwright;
  try{playwright=require(process.env.PLAYWRIGHT_MODULE||'playwright');}
  catch(_){playwright=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}
  const evidence={schemaVersion:1,verificationScope:'hosted-native-chrome-representative-playback',origin:config.origin,
    workerOrigin:config.workerOrigin,physicalDeviceTested:false,humanFullBookListeningApproval:'not-claimed',checks:[],errors:[],requests:[],responses:[],playback:[]};
  const check=(name,condition,detail)=>{
    evidence.checks.push({name,pass:!!condition,...(detail===undefined?{}:{detail})});
    assert.ok(condition,name);console.log('PASS '+name);
  };
  let phase='launch',browser;const pending=new Set();
  const contexts=[];
  const childEnv=Object.fromEntries(['PATH','HOME','TMPDIR','TEMP','TMP','LANG'].filter(key=>process.env[key]!==undefined).map(key=>[key,process.env[key]]));
  function observe(context) {
    context.on('request',request=>evidence.requests.push({...safeURL(request.url()),method:request.method(),kind:request.resourceType()}));
    context.on('response',response=>{
      const task=(async()=>{
        const entry={...safeURL(response.url()),status:response.status(),method:response.request().method(),
          contentType:(await response.headerValue('content-type')||'').split(';')[0]};
        if(entry.origin===config.workerOrigin&&entry.path.startsWith('/media/')) {
          entry.corsOrigin=await response.headerValue('access-control-allow-origin');
          entry.contentRange=await response.headerValue('content-range');
        }
        evidence.responses.push(entry);
      })().catch(()=>evidence.errors.push({kind:'response-observer',phase}));
      pending.add(task);task.finally(()=>pending.delete(task));
    });
    context.on('page',page=>{
      page.on('pageerror',()=>evidence.errors.push({kind:'javascript',phase}));
      page.on('console',message=>{if(message.type()==='error')evidence.errors.push({kind:'console-error',phase});});
    });
    context.on('requestfailed',request=>{
      if(!(request.failure()?.errorText||'').includes('ERR_ABORTED'))evidence.errors.push({kind:'network-failure',...safeURL(request.url()),phase});
    });
  }
  async function device(viewport,mobile) {
    const context=await browser.newContext({viewport,isMobile:mobile,hasTouch:mobile,serviceWorkers:'allow'});contexts.push(context);observe(context);
    await context.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.origin===config.origin||url.origin===config.workerOrigin&&url.pathname.startsWith('/media/'))return route.continue();
      evidence.errors.push({kind:'unexpected-origin',...safeURL(url.href),phase});return route.abort();
    });
    await previewCookie(context,config);return {context,page:await context.newPage()};
  }
  async function waitTrack(page,id,voice,at) {
    await page.waitForFunction(({id,voice,at})=>{
      const audio=document.querySelector('#narration');
      return window.LUMEN_CHAPTER?.chapterId===id&&window.LUMEN_CHAPTER.audio.narratorId===voice&&
        audio.readyState>=2&&!audio.seeking&&document.querySelector('#seek-feedback').hidden&&
        (at==null||Math.abs(audio.currentTime-at)<.4);
    },{id,voice,at},{timeout:60000});
  }
  const state=page=>page.evaluate(()=>{
    const audio=document.querySelector('#narration'),chapter=window.LUMEN_CHAPTER;
    return {track:chapter?.chapterId,voice:chapter?.audio.narratorId,time:audio.currentTime,paused:audio.paused,
      duration:audio.duration,declaredDuration:chapter?.duration,bytes:chapter?.audio.bytes,sha256:chapter?.audio.sha256,
      format:chapter?.audio.format,error:!!audio.error};
  });
  async function login(page) {
    await page.goto(config.origin+'/',{waitUntil:'domcontentloaded'});
    await page.locator('#invite-code').waitFor({state:'visible'});await page.locator('#invite-code').fill(config.code);
    await page.locator('#invite-submit').click();
    await page.waitForFunction(()=>document.body.classList.contains('authenticated')&&window.LUMEN_CHAPTER,{}, {timeout:60000});
    assert.equal(await page.locator('#invite-code').inputValue(),'','The successful code field must be cleared');
  }
  async function selectTrack(page,id,voice) {
    await page.locator('#chapters-open').click();await page.locator(`[data-track-id="${id}"]`).click();await waitTrack(page,id,voice);
  }
  async function selectVoice(page,voice) {
    await page.locator('#settings-open').click();await page.locator('#narrator').selectOption(voice);await page.locator('#settings-close').click();
  }
  async function screenshot(page,name) {
    assert.equal(await page.locator('#invite-code').inputValue(),'','Screenshots require a blank code field');
    assert.ok(await page.evaluate(()=>document.body.classList.contains('authenticated')),'Screenshots require the authenticated reader');
    const filename=path.join(config.output,name+'.png');assert.ok(!fs.existsSync(filename),'Preserve existing evidence');
    await page.screenshot({path:filename});fs.chmodSync(filename,0o600);
  }
  async function cacheProof(page,name) {
    await page.waitForFunction(async()=>!!(await navigator.serviceWorker.getRegistration())?.active,{timeout:30000});
    const cache=await page.evaluate(async()=>{
      const names=await caches.keys(),entries=[];
      for(const name of names)for(const request of await(await caches.open(name)).keys()) {
        const url=new URL(request.url);entries.push({origin:url.origin,path:url.pathname});
      }
      return {active:!!(await navigator.serviceWorker.getRegistration())?.active,entries};
    });
    check(name+' has an active HTTPS shell worker without cached private/media URLs',cache.active&&cache.entries.length>0&&
      cache.entries.every(row=>row.origin===config.origin&&!row.path.startsWith('/api/')&&!/\.(mp3|wav|m4a|m4b)$/i.test(row.path)),{cachedEntries:cache.entries.length});
  }
  try {
    browser=await playwright.chromium.launch({headless:true,env:childEnv,
      ...(fs.existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')?{executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}:{})});
    phase='anonymous';const guest=await device({width:390,height:844},true);
    await guest.page.goto(config.origin+'/',{waitUntil:'domcontentloaded'});
    await guest.page.waitForFunction(()=>document.querySelector('#access-status').textContent.includes('Use your access code'));
    check('anonymous shell contains no private prose or audio source',await guest.page.evaluate(()=>
      !window.LUMEN_BOOK&&!document.querySelector('#prose').textContent&&!document.querySelector('#narration').getAttribute('src')));
    const denied=await guest.context.request.get(config.origin+'/api/book',{maxRedirects:0});
    check('anonymous book API requires access',denied.status()===401);await denied.dispose();
    phase='phone-login';const phone=await device({width:390,height:844},true),page=phone.page;await login(page);
    const manifest=await page.evaluate(()=>{const m=window.LUMEN_BOOK.getManifest();return {releaseId:m.releaseId,defaultTrackId:m.defaultTrackId,
      defaultNarratorId:m.defaultNarratorId,tracks:m.tracks.map(t=>({id:t.id,kind:t.kind,recordings:t.recordings}))};});
    evidence.releaseId=manifest.releaseId;
    await waitTrack(page,manifest.defaultTrackId,'autonoe',0);
    check('a fresh reader defaults to paused female narration',manifest.defaultNarratorId==='autonoe'&&(await state(page)).paused);
    check('all 91 tracks and both approved voices are available',manifest.tracks.length===91&&manifest.tracks.filter(t=>t.kind==='story').length===89&&
      manifest.tracks.every(t=>t.recordings.autonoe?.status==='ready'&&t.recordings.charon?.status==='ready')&&await page.locator('#chapter-list [data-track-id]').count()===91);
    const first=manifest.tracks.find(t=>t.kind==='story');await selectTrack(page,first.id,'autonoe');
    const audioPath=await page.evaluate(()=>new URL(window.LUMEN_CHAPTER.audio.src,location.href).pathname);
    assert.ok(audioPath.startsWith('/api/assets/'),'Media retains the stable private API path');
    // Retain the separate guest jar: it has only a possible preview bypass,
    // never a reader session, so deployment protection cannot mask this check.
    const guestAudio=await guest.context.request.get(config.origin+audioPath,{maxRedirects:0});
    check('anonymous audio API rejects media before redirection',guestAudio.status()===401);await guestAudio.dispose();
    const noGrant=await guest.context.request.get(config.workerOrigin+'/media/'+audioPath.slice('/api/assets/'.length),{maxRedirects:0});
    check('direct Cloudflare media requires a grant',[400,401,403].includes(noGrant.status()));await noGrant.dispose();
    await guest.context.close();
    phase='native-playback';
    for(const voice of ['autonoe','charon']) {
      const before=await state(page);
      if(before.voice!==voice){await page.locator('#seek').press('Home');await waitTrack(page,first.id,before.voice,0);await selectVoice(page,voice);await waitTrack(page,first.id,voice,0);}
      const start=await state(page);await page.locator('#play').click();
      await page.waitForFunction(at=>{const a=document.querySelector('#narration');return !a.paused&&a.currentTime>at+.8;},start.time,{timeout:30000});
      await page.locator('#play').click();const end=await state(page);
      const meanBitRate=end.bytes*8/end.duration;
      check('native '+voice+' MP3 plays and matches the declared 192 kbps clock',!end.error&&end.paused&&end.time>start.time&&end.format==='audio/mpeg'&&
        Math.abs(end.duration-end.declaredDuration)<.5&&Math.abs(meanBitRate-192000)<4000);
      evidence.playback.push({trackId:end.track,narratorId:voice,sha256:end.sha256,bytes:end.bytes,nativeDuration:end.duration,
        meanBitRate:Math.round(meanBitRate),playedSeconds:Number((end.time-start.time).toFixed(3))});
    }
    phase='mapped-voice';
    const common=await page.evaluate(async()=>{
      const chapter=window.LUMEN_CHAPTER,m=window.LUMEN_BOOK.getManifest(),record=m.tracks.find(t=>t.id===chapter.chapterId).recordings.autonoe;
      const targetURL=new URL(record.url,location.href),response=await fetch(new URL(targetURL.pathname,location.origin),{cache:'no-store'});
      if(!response.ok)throw Error('Target chapter unavailable');const target=await response.json();
      const cues=new Map(target.paragraphs.flatMap(p=>p.sentences).map(s=>[s.id,s]));
      const source=chapter.paragraphs.flatMap(p=>p.sentences).find(s=>s.syncStatus==='measured'&&s.end-s.start>3&&cues.get(s.id)?.syncStatus==='measured'&&cues.get(s.id).end-cues.get(s.id).start>3);
      return source?{id:source.id,sourceStart:source.start,targetStart:cues.get(source.id).start}:null;
    });
    check('both voices share a measured canonical passage',!!common);
    await page.locator(`[id="${common.id}"]`).click();await waitTrack(page,first.id,'charon',common.sourceStart);
    await selectVoice(page,'autonoe');await waitTrack(page,first.id,'autonoe',common.targetStart);
    check('narrator change preserves the measured passage and stays paused',(await state(page)).paused);
    phase='seeking';const timeline=await page.locator('#seek').boundingBox();assert.ok(timeline);
    await page.touchscreen.tap(timeline.x+timeline.width*.55,timeline.y+timeline.height/2);
    await page.waitForFunction(()=>{const a=document.querySelector('#narration');return !a.seeking&&document.querySelector('#seek-feedback').hidden&&Math.abs(a.currentTime/a.duration-.55)<.04;},{},{timeout:60000});
    const at=(await state(page)).time;
    check('phone touch timeline seeks native Cloudflare MP3 while paused',(await state(page)).paused);
    await page.locator('#forward').tap();await waitTrack(page,first.id,'autonoe',at+15);check('forward fifteen seconds seeks native MP3',(await state(page)).paused);
    await page.locator('#back').tap();await waitTrack(page,first.id,'autonoe',at);check('rewind fifteen seconds seeks native MP3',(await state(page)).paused);
    const saved=(await state(page)).time;await page.reload({waitUntil:'domcontentloaded'});await waitTrack(page,first.id,'autonoe',saved);
    check('reload restores exact same-recording seconds and remains paused',(await state(page)).paused);
    await page.locator('#track-next').click();const second=manifest.tracks[manifest.tracks.findIndex(t=>t.id===first.id)+1];await waitTrack(page,second.id,'autonoe');
    check('next chapter changes the actual audio source',(await state(page)).track===second.id);
    await page.locator('#track-prev').click();await waitTrack(page,first.id,'autonoe',saved);
    check('returning to the previous chapter restores its saved position',(await state(page)).paused);
    phase='layout';
    async function layout(p,name) {
      const dimensions=await p.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
        controls:['play','back','forward','chapters-open','settings-open'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {id,width:r.width,height:r.height};}),
        timeline:document.querySelector('#seek').getBoundingClientRect().width}));
      check(name+' fits its viewport with 44px controls and a usable timeline',dimensions.scrollWidth<=dimensions.width&&dimensions.controls.every(c=>c.width>=44&&c.height>=44)&&dimensions.timeline>100,dimensions);
      await cacheProof(p,name);await screenshot(p,name+'-reader');
    }
    await layout(page,'phone');
    for(const [name,width,height,mobile] of [['ipad',820,1180,true],['desktop',1440,960,false]]) {
      const d=await device({width,height},mobile);await login(d.page);await waitTrack(d.page,manifest.defaultTrackId,'autonoe',0);
      await layout(d.page,name);await d.context.close();
    }
    phase='sign-out';const beforeLogout=await state(page);
    await page.locator('#settings-open').click();await page.locator('#sign-out-settings').click();
    await page.waitForFunction(()=>!document.body.classList.contains('authenticated')&&!window.LUMEN_BOOK);
    check('sign out removes private prose, audio and media metadata',await page.evaluate(()=>!document.querySelector('#prose').textContent&&
      !document.querySelector('#narration').getAttribute('src')&&(!navigator.mediaSession||!navigator.mediaSession.metadata)));
    const bookmark=await page.evaluate(()=>{const b=JSON.parse(localStorage.getItem('lumen-book-v2'));return b?.lastPosition&&{track:b.lastPosition.chapterId,voice:b.lastPosition.narratorId,time:b.lastPosition.audioTime};});
    check('sign out retains the listening bookmark',bookmark?.track===beforeLogout.track&&bookmark.voice===beforeLogout.voice&&Math.abs(bookmark.time-beforeLogout.time)<.4);
    const locked=await phone.context.request.get(config.origin+'/api/book',{maxRedirects:0});check('sign out restores anonymous API protection',locked.status()===401);await locked.dispose();
    await cacheProof(page,'signed-out browser');
    await login(page);await waitTrack(page,beforeLogout.track,beforeLogout.voice,beforeLogout.time);check('returning with the code resumes the retained place paused',(await state(page)).paused);
    phase='network-evidence';await Promise.all([...pending]);
    const media=evidence.responses.filter(r=>r.origin===config.workerOrigin&&r.path.startsWith('/media/')&&r.contentType==='audio/mpeg');
    const proxied=evidence.responses.filter(r=>r.origin===config.origin&&r.path.startsWith('/api/assets/')&&r.path.endsWith('.mp3'));
    check('native media receives direct Cloudflare 206 responses with working CORS',media.some(r=>r.status===206)&&media.every(r=>[200,206,304].includes(r.status)&&[config.origin,'null'].includes(r.corsOrigin)));
    check('Vercel media requests only redirect and never return audio bodies',proxied.length>0&&proxied.every(r=>r.status===307));
    check('Chrome reports no JavaScript, CORS or unexpected-origin errors',evidence.errors.length===0);
    evidence.status='verified';return evidence;
  }catch(_){evidence.status='failed';evidence.errors.push({kind:'check-failure',phase});process.exitCode=1;return evidence;}
  finally{
    for(const context of contexts)await context.close().catch(()=>{});if(browser)await browser.close().catch(()=>{});await Promise.all([...pending]);
    if(evidence.errors.length){evidence.status='failed';process.exitCode=1;}
  }
}
async function selfTest() {
  const good={LUMEN_QA_ORIGIN:'https://candidate.vercel.app',LUMEN_QA_WORKER_ORIGIN:'https://media.workers.dev',LUMEN_QA_ACCESS_CODE:'fixture-code',LUMEN_QA_OUT:'/private/tmp/lumen-hosted-selftest'};
  assert.equal(readConfig(good).origin,good.LUMEN_QA_ORIGIN);
  for(const change of [{LUMEN_QA_ORIGIN:'http://candidate.vercel.app'},{LUMEN_QA_ORIGIN:'https://candidate.vercel.app/'},
    {LUMEN_QA_ORIGIN:'https://foreign.example'},{LUMEN_QA_WORKER_ORIGIN:good.LUMEN_QA_ORIGIN},{LUMEN_QA_OUT:'/tmp/../public'},
    {LUMEN_QA_ACCESS_CODE:''},{LUMEN_QA_VERCEL_BYPASS:''}])assert.throws(()=>readConfig({...good,...change}));
  assert.deepEqual(safeURL('https://user:password@media.workers.dev/media/audio.mp3?grant=SECRET#CODE'),{origin:'https://media.workers.dev',path:'/media/audio.mp3'});
  let captured,disposed=false;
  const context={request:{get:async(url,options)=>{captured={url,options};return {status:()=>302,dispose:async()=>{disposed=true;}};}}};
  await previewCookie(context,readConfig({...good,LUMEN_QA_VERCEL_BYPASS:'private-fixture-bypass'}));
  assert.equal(captured.url,good.LUMEN_QA_ORIGIN+'/');assert.equal(captured.options.maxRedirects,0);assert.equal(disposed,true);
  assert.deepEqual(Object.keys(captured.options.headers).sort(),['x-vercel-protection-bypass','x-vercel-set-bypass-cookie']);
  console.log('PASS configuration and query/credential-free URL evidence self-test');
}
async function main() {
  if(process.argv.includes('--self-test')){await selfTest();return;}
  const config=readConfig(process.env);prepareOutput(config.output);
  const filename=path.join(config.output,'report.json');assert.ok(!fs.existsSync(filename),'Preserve existing evidence');
  const result=await run(config),bytes=JSON.stringify(result,null,2)+'\n';
  assert.ok(!bytes.includes(config.code)&&(!config.bypass||!bytes.includes(config.bypass)),'Private credentials must never enter evidence');
  fs.writeFileSync(filename,bytes,{mode:0o600,flag:'wx'});
  console.log(result.status==='verified'?'Hosted Chrome proof verified.':'Hosted Chrome proof failed; inspect the private summary.');
}
module.exports={readConfig,safeURL,previewCookie};
if(require.main===module)main().catch(()=>{console.error('Hosted Chrome proof could not run. Check private inputs and evidence permissions.');process.exitCode=1;});
