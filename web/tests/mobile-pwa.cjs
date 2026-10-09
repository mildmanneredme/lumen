/* Real Chrome/touch checks. Emulation verifies web behavior, not physical installation. */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
let playwrightModule = process.env.PLAYWRIGHT_MODULE;
if (!playwrightModule) {
  try { playwrightModule = require.resolve('playwright'); }
  catch (_) { playwrightModule = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
}
const { chromium } = require(playwrightModule);
const url = process.env.LUMEN_QA_URL || 'http://127.0.0.1:8765/';
const out = process.env.LUMEN_QA_OUT || '/private/tmp/lumen-mobile-pwa-qa';
fs.mkdirSync(out, {recursive:true});
const report = {url, generatedAt:new Date().toISOString(), physicalPhoneInstallVerified:false, checks:[], errors:[], screenshots:[]};
const androidUA = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36';
let browser;
function check(name, pass, detail) {
  report.checks.push({name, pass:!!pass, detail});
  console.log(`${pass ? 'PASS' : 'FAIL'} ${name} ${JSON.stringify(detail || '')}`);
}
async function section(name, run) {
  if (process.env.LUMEN_QA_ONLY && !new RegExp(process.env.LUMEN_QA_ONLY).test(name)) return;
  try { await run(); }
  catch (error) { check(name + ' completes', false, String(error.stack)); }
}
async function shot(page, name) {
  const file = path.join(out, name + '.png');
  await page.screenshot({path:file}); report.screenshots.push(file);
}
async function phone(width=390, height=844, init) {
  const context = await browser.newContext({viewport:{width,height}, isMobile:true, hasTouch:true, deviceScaleFactor:1, userAgent:androidUA});
  if (init) await context.addInitScript(init);
  const page = await context.newPage();
  page.audioNetwork = [];
  if(process.env.LUMEN_QA_AUDIO_DIAGNOSTICS){report.audioRequests ||= [];report.audioRequests.push({width,height,records:page.audioNetwork});}
  page.on('response', async response => { if(response.url().includes('chapter-001-pilot.mp3')){const entry={type:'response',url:response.url(),status:response.status(),fromServiceWorker:response.fromServiceWorker(),requestHeaders:response.request().headers(),headers:response.headers()};page.audioNetwork.push(entry);if(process.env.LUMEN_QA_AUDIO_DIAGNOSTICS){try{const body=await response.body();entry.bodyBytes=body.length;entry.bodySha256=crypto.createHash('sha256').update(body).digest('hex');entry.prefix=body.subarray(0,32).toString('hex');}catch(error){entry.bodyError=String(error);}}} });
  page.on('requestfailed', request => { if(request.url().includes('chapter-001-pilot.mp3'))page.audioNetwork.push({type:'failed',url:request.url(),headers:request.headers(),failure:request.failure()}); });
  page.on('pageerror', error => report.errors.push(String(error)));
  await page.goto(url, {waitUntil:'networkidle'});
  try{await page.waitForFunction(() => window.LUMEN_CHAPTER && document.querySelector('#prose .sentence'));}
  catch(error){check('fresh phone load diagnostics',false,{state:await page.evaluate(()=>({chapter:!!window.LUMEN_CHAPTER,progress:!!window.LumenProgress,sentences:document.querySelectorAll('#prose .sentence').length,status:document.querySelector('#play-status')?.textContent,scripts:[...document.scripts].map(s=>s.src),body:document.body.innerText.slice(0,500)})),network:page.audioNetwork});throw error;}
  return {context,page};
}
async function state(page) {
  return page.evaluate(() => {
    const a = document.querySelector('#narration'), chapter = window.LUMEN_CHAPTER;
    return {time:a.currentTime, paused:a.paused, ready:a.readyState,
      scene:document.querySelector('#scene-stage').dataset.scene,
      expectedScene:chapter.scenes.filter(s => s.start <= a.currentTime).at(-1)?.id,
      sentence:document.querySelector('.sentence.active')?.id,
      expectedSentence:chapter.paragraphs.flatMap(p => p.sentences).filter(s => s.start <= a.currentTime).at(-1)?.id};
  });
}
async function tapTrack(page, fraction) {
  const box = await page.locator('#seek').boundingBox();
  // Phone thumbs are 18px; choose the actual center of the available track.
  await page.touchscreen.tap(box.x + 9 + (box.width - 18) * fraction, box.y + box.height / 2);
  await page.waitForTimeout(350);
  return state(page);
}
async function layout(page) {
  return page.evaluate(() => {
    const rect = el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}; };
    const ids = ['back','play','forward','seek','speed','art-toggle','follow','settings-open'];
    return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
      pane:rect(document.querySelector('#reading-pane')),scene:rect(document.querySelector('#scene-stage')),
      paneOverflow:document.querySelector('#reading-pane').scrollWidth > document.querySelector('#reading-pane').clientWidth,
      controls:ids.map(id => ({id,...rect(document.getElementById(id))})),
      viewport:document.querySelector('meta[name=viewport]').content};
  });
}
function validLayout(result) {
  return result.scrollWidth <= result.width && !result.paneOverflow && result.pane.height >= 100 &&
    result.controls.every(c => c.width >= 44 && c.height >= 44 && c.x >= -.1 && c.y >= -.1 && c.right <= result.width+.1 && c.bottom <= result.height+.1);
}
async function initWorker(page) {
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  await page.reload({waitUntil:'networkidle'});
  await page.waitForFunction(() => !!navigator.serviceWorker.controller);
}
async function rangeFetch(page, audioURL, range) {
  return page.evaluate(async ({audioURL,range}) => {
    const response = await fetch(audioURL, {headers:{Range:range},cache:'no-store'});
    return {status:response.status,range:response.headers.get('Content-Range'),length:response.headers.get('Content-Length'),bytes:Array.from(new Uint8Array(await response.arrayBuffer()))};
  }, {audioURL,range});
}

(async () => {
  const options = {headless:true};
  if (process.env.CHROME_PATH) options.executablePath = process.env.CHROME_PATH;
  else if (process.platform === 'darwin' && fs.existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')) options.executablePath = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  browser = await chromium.launch(options);

  const views=[[320,720],[360,780],[390,844],[430,932],[844,390]];
  const repetitions=Math.max(1,Math.min(20,Number(process.env.LUMEN_QA_REPEATS)||1));
  for (const [width,height] of Array.from({length:repetitions},()=>views).flat()) {
    await section(`mobile ${width}×${height}`, async () => {
      const {context,page} = await phone(width,height);
      await page.waitForFunction(() => document.querySelector('#narration').readyState >= 1);
      let result = await layout(page);
      const landscape = width > height;
      check(`${width}×${height} no overflow and all controls visible ≥44px`,validLayout(result),result);
      const track = result.controls.find(c => c.id === 'seek');
      check(`${width}×${height} reachable timeline`,track.width >= (landscape ? 300 : width-48) && track.height >= 44,track);
      check(`${width}×${height} safe-area viewport and visible art`,result.viewport.includes('viewport-fit=cover') && result.scene.width > 100 && result.scene.height > 100,result.scene);
      await shot(page,`phone-${width}x${height}-opening`);
      const at = await tapTrack(page,.55);
      const duration = await page.evaluate(() => window.LUMEN_CHAPTER.duration);
      check(`${width}×${height} actual touch timeline seeks with prose and painting`,at.paused && Math.abs(at.time-duration*.55)<1.3 && at.scene===at.expectedScene && at.sentence===at.expectedSentence,at);
      await page.locator('#forward').tap(); await page.waitForTimeout(200);
      const forward = await state(page);
      check(`${width}×${height} actual forward touch`,forward.paused && Math.abs(forward.time-at.time-15)<.2,forward);
      await page.locator('#back').tap(); await page.waitForTimeout(200);
      const back = await state(page);
      check(`${width}×${height} actual rewind touch`,back.paused && Math.abs(back.time-at.time)<.2,back);
      await page.locator('#settings-open').tap();
      await page.locator('#text-size').evaluate(el => {el.value='28';el.dispatchEvent(new Event('input',{bubbles:true}));});
      await page.locator('#settings-close').tap();
      check(`${width}×${height} maximum type retains controls and reading area`,validLayout(await layout(page)) && await page.locator('#prose').evaluate(el => parseFloat(getComputedStyle(el).fontSize)>=27));
      await page.reload({waitUntil:'networkidle'});
      try { await page.waitForFunction(() => !document.querySelector('#resume-panel').hidden && document.querySelector('#narration').readyState>=1); }
      catch(error) { await shot(page,`phone-${width}x${height}-resume-failure`);check(`${width}×${height} resume diagnostics`,false,{state:await page.evaluate(()=>({ready:document.querySelector('#narration').readyState,error:document.querySelector('#narration').error?.message,time:document.querySelector('#narration').currentTime,hidden:document.querySelector('#resume-panel').hidden,bookmark:localStorage.getItem('lumen-reader-v1'),status:document.querySelector('#play-status').textContent,pending:document.querySelector('#seek-feedback').textContent})),network:page.audioNetwork});throw error; }
      const restored = await state(page); result = await layout(page);
      check(`${width}×${height} returning bookmark paused with visible controls`,Math.abs(restored.time-at.time)<.2 && restored.paused && validLayout(result),{state:restored,layout:result});
      await shot(page,`phone-${width}x${height}-returning-large-type`);
      const before = result.pane.height;
      await page.locator('#art-toggle').tap();
      result = await layout(page);
      check(`${width}×${height} art toggle gives prose more room`,result.scene.height===0 && result.pane.height >= before && await page.locator('#art-toggle').getAttribute('aria-pressed')==='false',result);
      await page.reload({waitUntil:'networkidle'});
      check(`${width}×${height} artwork preference survives reload`,await page.locator('#scene-stage').evaluate(el => getComputedStyle(el).display==='none') && await page.locator('#art-toggle').getAttribute('aria-pressed')==='false');
      await context.close();
    });
  }

  await section('phone painting downloads',async()=>{
    for(const saveData of [false,true]){
      const init=saveData?()=>Object.defineProperty(navigator,'connection',{configurable:true,value:{saveData:true}}):undefined;
      const {context,page}=await phone(390,844,init);
      const images=await page.evaluate(()=>performance.getEntriesByType('resource').filter(e=>/\/assets\/.*\.(webp|png)(\?|$)/.test(e.name)).map(e=>({name:e.name,bytes:e.encodedBodySize})));
      const names=[...new Set(images.map(i=>i.name))];
      check(`fresh phone ${saveData?'Save-Data':'normal'} loads ${saveData?'current':'current and next'} painting only`,names.length===(saveData?1:2)&&names.every(n=>n.includes('.webp')),images);
      check(`fresh phone ${saveData?'Save-Data':'normal'} artwork under1MB`,images.reduce((total,i)=>total+i.bytes,0)<1000000,images);
      check('phone audio initially requests metadata',await page.locator('#narration').getAttribute('preload')==='metadata');
      await context.close();
    }
  });

  await section('phone safe areas',async()=>{
    for(const config of [{width:390,height:844,insets:{top:47,bottom:34,left:0,right:0}},{width:844,height:390,insets:{top:0,bottom:21,left:47,right:47}}]){
      const {context,page}=await phone(config.width,config.height);const cdp=await context.newCDPSession(page);
      await cdp.send('Emulation.setSafeAreaInsetsOverride',{insets:config.insets});
      const result=await layout(page);const safe=config.insets;
      check(`${config.width}×${config.height} controls clear notch and Home indicator`,validLayout(result)&&result.controls.every(c=>c.x>=safe.left&&c.right<=result.width-safe.right&&c.y>=safe.top&&c.bottom<=result.height-safe.bottom),result);
      await page.locator('#settings-open').tap();const settings=await page.locator('#settings').boundingBox();
      check(`${config.width}×${config.height} settings inside safe area`,settings.y>=safe.top&&settings.y+settings.height<=config.height-safe.bottom,settings);
      await page.locator('#settings-close').tap();await shot(page,`phone-${config.width}x${config.height}-safe-area`);await cdp.detach();await context.close();
    }
  });

  await section('manifest and installability',async () => {
    const {context,page} = await phone();
    const manifest = await page.evaluate(async () => {const link=document.querySelector('link[rel=manifest]');return {url:link?.href,data:link?await (await fetch(link.href)).json():null};});
    const m=manifest.data;
    check('manifest defines stable standalone app identity',m && m.name==='Lumen' && m.display==='standalone' && new URL(m.start_url,manifest.url).origin===new URL(url).origin && m.id==='/' && m.scope==='/',m);
    check('manifest has192/512 PNG and separate maskable icon',m?.icons?.some(i=>i.sizes==='192x192'&&i.type==='image/png')&&m.icons.some(i=>i.sizes==='512x512'&&i.purpose?.includes('maskable')),m?.icons);
    for (const icon of m.icons) {
      const decoded=await page.evaluate(async icon=>{
        const image=new Image();image.src=icon.src;await image.decode();const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;const c=canvas.getContext('2d');c.drawImage(image,0,0);const pixels=c.getImageData(0,0,canvas.width,canvas.height).data;
        let transparent=0,maxRadius=0;const background=Array.from(pixels.slice(0,3));
        for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){const p=(y*canvas.width+x)*4;if(pixels[p+3]!==255)transparent++;if(Math.max(...background.map((v,k)=>Math.abs(v-pixels[p+k])))>45)maxRadius=Math.max(maxRadius,Math.hypot(x+.5-canvas.width/2,y+.5-canvas.height/2));}
        return {width:canvas.width,height:canvas.height,transparent,foregroundRadiusFraction:maxRadius/canvas.width};
      },icon);
      const [w,h]=icon.sizes.split('x').map(Number);
      check(`icon ${icon.src} decodes at declared dimensions`,decoded.width===w&&decoded.height===h,decoded);
      if(icon.purpose?.includes('maskable'))check('maskable icon background opaque and mark inside40% safe radius',decoded.transparent===0&&decoded.foregroundRadiusFraction<=.4,decoded);
    }
    const apple=await page.evaluate(async()=>{const i=new Image();i.src=document.querySelector('link[rel=apple-touch-icon]')?.href;await i.decode();return {width:i.naturalWidth,height:i.naturalHeight};});
    check('Apple Home screen icon decodes at180px',apple.width===180&&apple.height===180,apple);
    await initWorker(page);
    check('service worker controls after reload',await page.evaluate(()=>!!navigator.serviceWorker.controller));
    await context.close();
    // Ordinary isolated Playwright contexts are incognito, where Chrome blocks
    // installation regardless of site quality. Use an empty disposable profile.
    const profile=fs.mkdtempSync(path.join(os.tmpdir(),'lumen-installability-'));
    const installedContext=await chromium.launchPersistentContext(profile,{...options,viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:androidUA});
    try {const installedPage=installedContext.pages()[0]||await installedContext.newPage();await installedPage.goto(url,{waitUntil:'networkidle'});await initWorker(installedPage);const cdp=await installedContext.newCDPSession(installedPage);const installability=await cdp.send('Page.getInstallabilityErrors');check('Chrome reports no installability errors in normal profile',installability.installabilityErrors.length===0,installability);await cdp.detach();}
    finally {await installedContext.close();fs.rmSync(profile,{recursive:true,force:true});}
  });

  await section('installer choices',async()=>{
    for(const outcome of ['dismissed','accepted']){
      const {context,page}=await phone();await page.locator('#settings-open').tap();
      const fixture=await page.evaluate(outcome=>{window.__promptCount=0;const e=new Event('beforeinstallprompt',{cancelable:true});e.prompt=async()=>{window.__promptCount++;};e.userChoice=Promise.resolve({outcome,platform:'web'});window.dispatchEvent(e);return {prevented:e.defaultPrevented,count:window.__promptCount,label:document.querySelector('#install-app').textContent};},outcome);
      check(`${outcome} installation prompt waits for explicit tap`,fixture.prevented&&fixture.count===0&&fixture.label==='Install Lumen',fixture);
      await page.locator('#install-app').tap();await page.waitForTimeout(100);
      const choice=await page.evaluate(()=>({hidden:document.querySelector('#install-section').hidden,count:window.__promptCount,label:document.querySelector('#install-app').textContent,status:document.querySelector('#install-status').textContent}));
      check(`${outcome} installation choice handled`,choice.count===1 && (outcome==='accepted'?choice.hidden:!choice.hidden&&choice.label==='How to install'&&choice.status.includes('later')),choice);
      if(outcome==='dismissed'){await page.locator('#install-app').tap();check('dismissed event consumed; helper offers Chrome menu',await page.evaluate(()=>window.__promptCount===1&&document.querySelector('#install-status').textContent.includes('Chrome')));await shot(page,'phone-install-helper');}
      await context.close();
    }
    const {context,page}=await phone(390,844,()=>{const native=window.matchMedia.bind(window);window.matchMedia=q=>q==='(display-mode: standalone)'?{matches:true,media:q,addEventListener(){},removeEventListener(){},addListener(){},removeListener(){}}:native(q);});
    await page.locator('#settings-open').tap();check('installed standalone app hides installation section',await page.locator('#install-section').evaluate(el=>el.hidden));await context.close();
    const iosContext=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/140.0.0.0 Mobile/15E148 Safari/604.1'});
    const ios=await iosContext.newPage();await ios.goto(url,{waitUntil:'networkidle'});await ios.locator('#settings-open').tap();await ios.locator('#install-app').tap();const iosHelp=await ios.locator('#install-status').textContent();check('iPhone Chrome helper explains Share to Home screen',iosHelp.includes('Chrome')&&iosHelp.includes('Share')&&iosHelp.includes('Add to Home Screen'),iosHelp);await iosContext.close();
  });

  await section('service worker shell offline and native audio',async()=>{
    const {context,page}=await phone();await initWorker(page);
    const probeURL=new URL('assets/chapter-001-pilot.mp3?qa-range-isolation=1',url).href;
    const online=await rangeFetch(page,probeURL,'bytes=100000-100099');
    const direct=await page.request.get(probeURL,{headers:{Range:'bytes=100000-100099'}});
    const directBody=await direct.body();
    check('controlled online audio206 has exact native bytes',online.status===206&&online.range?.startsWith('bytes 100000-100099/')&&Buffer.from(online.bytes).equals(directBody),online);
    const nativeResponse=page.audioNetwork.find(r=>r.type==='response'&&r.url===probeURL);
    check('controlled audio requests bypass service worker',nativeResponse?.fromServiceWorker===false,nativeResponse);
    const cacheEntries=await page.evaluate(async()=>{const entries=[];for(const name of await caches.keys()){const c=await caches.open(name);for(const r of await c.keys())entries.push({cache:name,url:r.url});}return entries;});
    check('installation caches shell and artwork without audio',cacheEntries.some(e=>e.cache.startsWith('lumen-shell-')&&new URL(e.url).pathname==='/index.html')&&!cacheEntries.some(e=>/\.(mp3|wav|m4a)(\?|$)/.test(e.url)),cacheEntries);
    const before=await tapTrack(page,.55);
    await context.setOffline(true);
    const absent=await page.evaluate(async probeURL=>{try{const r=await fetch(probeURL,{headers:{Range:'bytes=100000-100099'},cache:'no-store'});return {status:r.status};}catch(error){return {networkError:String(error)};}},probeURL);
    check('uncached audio requires a connection',!!absent.networkError,absent);
    await page.goto(url,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>document.querySelector('#prose .sentence'));
    const offline=await page.evaluate(()=>{const chapter=window.LUMEN_CHAPTER,time=Number(document.querySelector('#seek').value);return {time,paused:document.querySelector('#narration').paused,error:document.querySelector('#narration').error?.message,scene:document.querySelector('#scene-stage').dataset.scene,expectedScene:chapter.scenes.filter(s=>s.start<=time).at(-1)?.id,sentence:document.querySelector('.sentence.active')?.id,expectedSentence:chapter.paragraphs.flatMap(p=>p.sentences).filter(s=>s.start<=time).at(-1)?.id,status:document.querySelector('#play-status').textContent,feedback:document.querySelector('#seek-feedback').textContent};});
    check('cached app opens offline with saved prose and painting',Math.abs(offline.time-before.time)<.2&&offline.paused&&offline.scene===offline.expectedScene&&offline.sentence===offline.expectedSentence&&await page.locator('#scene-image').evaluate(el=>el.complete&&el.naturalWidth>0),offline);
    if(offline.error)check('offline media error clearly explains reconnection',/offline|reconnect/i.test(offline.status+' '+offline.feedback),offline);
    await shot(page,'phone-offline-returning');await context.setOffline(false);await page.locator('#play').tap();
    await page.waitForFunction(()=>!document.querySelector('#narration').paused&&document.querySelector('#narration').currentTime>175);
    check('Play retries narration after reconnect at saved position',!(await state(page)).paused&&Math.abs((await state(page)).time-before.time)<3,await state(page));await page.locator('#play').tap();await context.close();
  });

  await section('media error recovery',async()=>{
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:androidUA});
    await context.route('**/assets/chapter-001-pilot.mp3*',route=>route.fulfill({status:200,contentType:'audio/mpeg',body:'This is deliberately invalid audio for error recovery QA.'}));
    const page=await context.newPage();page.on('pageerror',error=>report.errors.push(String(error)));await page.goto(url,{waitUntil:'networkidle'});await page.waitForFunction(()=>!!document.querySelector('#narration').error);
    await page.locator('#forward').tap();await page.waitForTimeout(200);
    const errorUI=await page.evaluate(()=>({error:document.querySelector('#narration').error?.message,status:document.querySelector('#play-status').textContent,feedback:document.querySelector('#seek-feedback').textContent,displayedTime:Number(document.querySelector('#seek').value)}));
    check('format error remains visible after a seek attempt',/could not|reload|retry/i.test(errorUI.status+' '+errorUI.feedback)&&errorUI.displayedTime===15,errorUI);
    await context.unroute('**/assets/chapter-001-pilot.mp3*');await page.locator('#play').tap();await page.waitForFunction(()=>!document.querySelector('#narration').paused&&document.querySelector('#narration').currentTime>=15);
    check('Play reloads narration and keeps requested place',!(await state(page)).paused&&(await state(page)).time<18,await state(page));await page.locator('#play').tap();await context.close();
  });

  await section('phone media controls',async()=>{
    const {context,page}=await phone(390,844,()=>{window.__mediaHandlers={};window.__mediaPositions=[];Object.defineProperty(navigator,'mediaSession',{configurable:true,value:{metadata:null,playbackState:'none',setActionHandler(name,fn){window.__mediaHandlers[name]=fn;},setPositionState(value){window.__mediaPositions.push(value);}}});});
    await page.waitForFunction(()=>document.querySelector('#narration').readyState>=1);
    const metadata=await page.evaluate(()=>({title:navigator.mediaSession.metadata?.title,artist:navigator.mediaSession.metadata?.artist,actions:Object.keys(window.__mediaHandlers),positions:window.__mediaPositions}));
    check('media session advertises book and narration controls',metadata.title?.includes('Lumen')&&metadata.artist==='Rob Xie'&&['play','pause','seekbackward','seekforward','seekto'].every(a=>metadata.actions.includes(a)),metadata);
    await tapTrack(page,.3);const start=await state(page);
    await page.evaluate(()=>window.__mediaHandlers.seekforward({seekOffset:15}));await page.waitForTimeout(200);const forward=await state(page);check('media forward action seeks15 seconds',Math.abs(forward.time-start.time-15)<.2,forward);
    await page.evaluate(()=>window.__mediaHandlers.seekbackward({seekOffset:15}));await page.waitForTimeout(200);const back=await state(page);check('media rewind action seeks15 seconds',Math.abs(back.time-start.time)<.2,back);
    await page.evaluate(()=>window.__mediaHandlers.seekto({seekTime:210,fastSeek:false}));await page.waitForTimeout(200);const absolute=await state(page);check('media absolute seek updates prose and painting',Math.abs(absolute.time-210)<.2&&absolute.scene===absolute.expectedScene&&absolute.sentence===absolute.expectedSentence,absolute);
    await page.locator('#play').tap();await page.waitForTimeout(200);await page.evaluate(()=>window.__mediaHandlers.pause());const paused=await state(page);check('media pause freezes narration',paused.paused&&await page.evaluate(()=>navigator.mediaSession.playbackState==='paused'));
    await page.evaluate(()=>window.__mediaHandlers.play());await page.waitForTimeout(500);check('media play resumes narration',!(await state(page)).paused);await page.evaluate(()=>window.__mediaHandlers.pause());
    const positions=await page.evaluate(()=>window.__mediaPositions);check('media position state has valid duration and current position',positions.length>0&&positions.every(p=>Number.isFinite(p.duration)&&p.duration>0&&p.position>=0&&p.position<=p.duration&&p.playbackRate>0),positions.at(-1));
    await context.close();
  });
  check('no browser JavaScript errors',report.errors.length===0,report.errors);
  report.passed=report.checks.filter(c=>c.pass).length;report.failed=report.checks.filter(c=>!c.pass).length;
  fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));
  await browser.close();
  console.log(JSON.stringify({passed:report.passed,failed:report.failed,report:path.join(out,'report.json'),screenshots:report.screenshots}));process.exitCode=report.failed?1:0;
})().catch(async error=>{report.errors.push(String(error.stack));fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));if(browser)await browser.close();console.error(error);process.exit(1);});
