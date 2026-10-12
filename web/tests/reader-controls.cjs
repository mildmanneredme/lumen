/* Icon and layout regressions, using historical pilot prose only on loopback. */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const http = require('node:http');
const crypto = require('node:crypto');
let playwrightModule = process.env.PLAYWRIGHT_MODULE;
if (!playwrightModule) {
  try { playwrightModule = require.resolve('playwright'); }
  catch (_) { playwrightModule = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
}
const {chromium} = require(playwrightModule);
const dist = path.resolve(__dirname, '../dist');
const output = process.env.LUMEN_QA_OUT || '/private/tmp/lumen-controls-qa';
fs.mkdirSync(output, {recursive:true});
const report = {checks:[], errors:[], screenshots:[]};
function check(name, pass, detail) {
  report.checks.push({name, pass:!!pass, ...(detail ? {detail} : {})});
  console.log((pass ? 'PASS ' : 'FAIL ') + name);
}
async function section(name, run) {
  try { await run(); }
  catch (error) { check(name + ' completes', false, String(error.message)); }
}
function wav(seconds) {
  const rate = 8000, samples = Math.round(seconds * rate), bytes = Buffer.alloc(44 + samples * 2);
  bytes.write('RIFF'); bytes.writeUInt32LE(bytes.length - 8,4); bytes.write('WAVEfmt ',8);
  bytes.writeUInt32LE(16,16); bytes.writeUInt16LE(1,20); bytes.writeUInt16LE(1,22);
  bytes.writeUInt32LE(rate,24); bytes.writeUInt32LE(rate * 2,28); bytes.writeUInt16LE(2,32); bytes.writeUInt16LE(16,34);
  bytes.write('data',36); bytes.writeUInt32LE(samples * 2,40);
  return bytes;
}
const source = {window:{}};
vm.runInNewContext(fs.readFileSync(path.join(dist,'data/chapter-001.js'),'utf8'),source);
const historical = source.window.LUMEN_CHAPTER;
const audio = wav(historical.duration), hash = crypto.createHash('sha256').update(audio).digest('hex');
const manifest = {schemaVersion:1,bookId:'lumen',manuscriptVersion:'v6',releaseId:'controls-fixture',defaultTrackId:'chapter-001',
  defaultNarratorId:'charon',narrators:[{id:'charon',label:'Male narrator'}],tracks:[],legacyAliases:{}};
const payloads = new Map();
for (let index=1;index<=2;index++) {
  const id = 'chapter-' + String(index).padStart(3,'0'), prefix = 'v6:' + id + ':';
  const payload = JSON.parse(JSON.stringify(historical));
  Object.assign(payload,{id:id+'-controls-fixture',chapterId:id,title:'Chapter '+index,part:1,partTitle:'Part One · The Surface',kind:'story',readingExtent:'full'});
  payload.audio = {...payload.audio,src:'/fixture/audio.wav',sha256:hash,narratorId:'charon'};
  payload.audioSrc = payload.audio.src;
  for (const paragraph of payload.paragraphs) {
    paragraph.id = prefix + paragraph.id;
    for (const sentence of paragraph.sentences) sentence.id = prefix + sentence.id;
  }
  for (const scene of payload.scenes) {
    if (scene.paragraphId) scene.paragraphId = prefix + scene.paragraphId;
    if (scene.sentenceId) scene.sentenceId = prefix + scene.sentenceId;
  }
  if (payload.blocks) for (const block of payload.blocks) if (block.paragraphId) block.paragraphId = prefix + block.paragraphId;
  const url = '/fixture/'+id+'.json';
  payloads.set(url,payload);
  manifest.tracks.push({id,title:payload.title,part:1,partTitle:payload.partTitle,kind:'story',recordings:{charon:{status:'ready',url,audioSha256:hash}}});
}
const mime = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.webp':'image/webp','.png':'image/png','.webmanifest':'application/manifest+json'};
const server = http.createServer((req,res) => {
  const pathname = new URL(req.url,'http://localhost').pathname;
  const file = path.resolve(dist,'.'+(pathname==='/'?'/index.html':pathname));
  if (!file.startsWith(dist+path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {res.writeHead(404);res.end();return;}
  res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});
  fs.createReadStream(file).pipe(res);
});
let browser, origin;
async function reader(view, options={}) {
  const context = await browser.newContext({viewport:{width:view[0],height:view[1]},serviceWorkers:'block',
    ...(view[0]<721 || view[1]<521 ? {hasTouch:true,isMobile:true} : {}),
    ...(options.reducedMotion ? {reducedMotion:'reduce'} : {})});
  if (options.seed) await context.addInitScript(value => localStorage.setItem('lumen-book-v2',JSON.stringify(value)),options.seed);
  const page = await context.newPage();
  page.on('pageerror',error => report.errors.push(String(error.message)));
  const state = {authenticated:options.authenticated!==false,failChapter:false};
  await page.route('**/api/session',route => route.fulfill({json:{authenticated:state.authenticated}}));
  await page.route('**/api/book',route => route.fulfill({json:manifest}));
  await page.route('**/fixture/**',async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (payloads.has(pathname)) {
      if (state.failChapter && pathname.includes('chapter-002')) return route.fulfill({status:503,body:'fixture unavailable'});
      return route.fulfill({json:payloads.get(pathname)});
    }
    if (pathname==='/fixture/audio.wav') {
      const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range||'');
      if (!range) return route.fulfill({contentType:'audio/wav',headers:{'Accept-Ranges':'bytes','Content-Length':String(audio.length)},body:audio});
      const start = Number(range[1]), end = Math.min(audio.length-1,range[2]?Number(range[2]):audio.length-1);
      return route.fulfill({status:206,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes','Content-Range':`bytes ${start}-${end}/${audio.length}`,'Content-Length':String(end-start+1)},body:audio.subarray(start,end+1)});
    }
    return route.fulfill({status:404});
  });
  await page.goto(origin,{waitUntil:'networkidle'});
  if (state.authenticated && !options.seed) await ready(page);
  return {context,page,state};
}
async function ready(page) {
  await page.waitForFunction(() => window.LUMEN_CHAPTER?.chapterId && document.querySelector('#prose .sentence') && document.querySelector('#narration').readyState>=1);
  await page.waitForFunction(() => document.querySelector('#scene-image').complete && document.querySelector('#scene-image').naturalWidth>0);
}
async function shot(page,name) {
  const file = path.join(output,name+'.png');
  await page.screenshot({path:file}); report.screenshots.push(file);
}
async function icon(page,selector,name) {
  return (await page.locator(selector+' svg[data-icon="'+name+'"]').count())===1;
}
async function seek(page,time) {
  await page.locator('#seek').evaluate((element,value) => {element.value=value;element.dispatchEvent(new Event('input',{bubbles:true}));},String(time));
  await page.waitForFunction(value => Math.abs(document.querySelector('#narration').currentTime-value)<.04,time);
}
async function layout(page) {
  return page.evaluate(() => {
    const rect = element => {const r=element.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
    const ids=['chapters-open','follow','settings-open','sign-out','back','play','forward','seek','speed','art-toggle'];
    return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,pane:rect(document.querySelector('#reading-pane')),
      controls:ids.map(id => ({id,hidden:getComputedStyle(document.getElementById(id)).display==='none',...rect(document.getElementById(id))}))};
  });
}
async function dialogFit(page,id,closeId) {
  return page.evaluate(({id,closeId}) => {
    const dialog=document.getElementById(id),close=document.getElementById(closeId),r=dialog.getBoundingClientRect(),c=close.getBoundingClientRect();
    return dialog.open && r.x>=0 && r.right<=innerWidth+.1 && r.y>=0 && r.bottom<=innerHeight+.1 && dialog.scrollWidth<=dialog.clientWidth &&
      c.width>=44 && c.height>=44 && c.x>=r.x && c.right<=r.right && c.y>=r.y && c.bottom<=r.bottom;
  },{id,closeId});
}
(async () => {
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  origin = 'http://127.0.0.1:'+server.address().port;
  const launch = {headless:true}, chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (fs.existsSync(chrome)) launch.executablePath=chrome;
  browser = await chromium.launch(launch);
  for (const view of [[1440,1000],[390,844]]) await section('welcome '+view[0],async () => {
    const fixture=await reader(view,{authenticated:false}),page=fixture.page;
    await page.locator('#invite-submit').waitFor({state:'visible'});
    check('welcome '+view[0]+' decorative icons and single accessible action',await icon(page,'#invite-submit','book-open') && await page.getByRole('button',{name:'Open the book',exact:true}).count()===1);
    check('welcome '+view[0]+' form fits',await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth && document.querySelector('#invite-code').getBoundingClientRect().height>=44));
    await shot(page,'welcome-'+view[0]);await fixture.context.close();
  });
  for (const view of [[320,720],[360,780],[390,844],[430,932],[820,1180],[1440,1000],[844,390]]) await section('reader '+view.join('x'),async () => {
    const fixture=await reader(view),page=fixture.page,result=await layout(page),visible=result.controls.filter(control=>!control.hidden);
    check(view.join('x')+' controls remain visible and at least 44px',result.scrollWidth<=result.width && result.pane.height>=100 && visible.every(c=>c.width>=44 && c.height>=44 && c.x>=-.1 && c.y>=-.1 && c.right<=result.width+.1 && c.bottom<=result.height+.1),result);
    check(view.join('x')+' skip glyphs stay centered in touch targets',await page.evaluate(()=>['back','forward'].every(id=>{const button=document.getElementById(id).getBoundingClientRect(),icon=document.querySelector('#'+id+' svg').getBoundingClientRect();return Math.abs((button.x+button.width/2)-(icon.x+icon.width/2))<.5 && Math.abs((button.y+button.height/2)-(icon.y+icon.height/2))<.5;})));
    const bounds=id=>result.controls.find(control=>control.id===id);
    check(view.join('x')+' switch and transport targets do not overlap',bounds('art-toggle').right<=bounds('back').x && bounds('back').right<=bounds('play').x && bounds('play').right<=bounds('forward').x && bounds('forward').right<=bounds('speed').x);
    check(view.join('x')+' artwork exposes a named checked switch',await page.getByRole('switch',{name:'Artwork',exact:true}).count()===1 && await page.locator('#art-toggle').getAttribute('aria-checked')==='true');
    if (view[0]<=720) check(view.join('x')+' portrait timeline spans reader',result.controls.find(c=>c.id==='seek').width>=result.width-48);
    if (view[0]<=720 || view[0]<=960 && view[1]<=520) check(view.join('x')+' native phone selects retain 16px text',await page.evaluate(()=>
      ['speed','narrator'].every(id=>parseFloat(getComputedStyle(document.getElementById(id)).fontSize)>=16)));
    const visibleButtons=page.locator('button:visible'),names=[];
    for (let index=0;index<await visibleButtons.count();index++) names.push((await visibleButtons.nth(index).ariaSnapshot()).split('\n')[0]);
    check(view.join('x')+' every visible button or switch has an accessible name',names.every(name=>/^- (?:button|switch) \".+\"/.test(name)),names);
    if (view[0]===820) check('tablet sign out stays named and visible',await page.getByRole('button',{name:'Sign out',exact:true}).count()===1 && await page.locator('#sign-out').isVisible());
    check(view.join('x')+' icons are decorative without duplicate accessible text',await page.evaluate(() => [...document.querySelectorAll('svg.lumen-icon')].every(svg=>svg.getAttribute('aria-hidden')==='true' && svg.getAttribute('focusable')==='false' && !svg.querySelector('title,text'))));
    if ([390,1440].includes(view[0])) await shot(page,'reader-'+view[0]);
    await page.getByRole('button',{name:'Choose a chapter',exact:true}).click();
    check(view.join('x')+' chapters dialog and close fit',await dialogFit(page,'chapters','chapters-close'));
    if ([390,1440].includes(view[0])) await shot(page,'chapters-'+view[0]);
    await page.getByRole('button',{name:'Close chapters',exact:true}).click();
    check(view.join('x')+' chapter close returns to reader',!await page.locator('#chapters').evaluate(element=>element.open));
    await page.getByRole('button',{name:'Reading settings',exact:true}).click();
    check(view.join('x')+' settings dialog and close fit',await dialogFit(page,'settings','settings-close'));
    if ([390,1440].includes(view[0])) await shot(page,'settings-'+view[0]);
    await page.getByRole('button',{name:'Close reading settings',exact:true}).click();
    await fixture.context.close();
  });
  await section('dynamic controls',async () => {
    const fixture=await reader([390,844]),page=fixture.page;
    await page.getByRole('button',{name:'Following text',exact:true}).click();
    check('follow toggle preserves icon and accessible name',await icon(page,'#follow','follow') && await page.getByRole('button',{name:'Follow text',exact:true}).count()===1 && await page.locator('#follow').getAttribute('aria-pressed')==='false');
    await page.getByRole('button',{name:'Follow text',exact:true}).click();
    await page.getByRole('button',{name:'Play audiobook',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#narration').paused);
    check('playing exposes a single pause icon and name',await icon(page,'#play','pause') && await page.getByRole('button',{name:'Pause audiobook',exact:true}).count()===1);
    await page.getByRole('button',{name:'Pause audiobook',exact:true}).click();
    await page.locator('#play svg[data-icon="play"]').waitFor({state:'attached'});
    check('paused restores play icon',await icon(page,'#play','play'));
    await seek(page,10);await page.reload({waitUntil:'networkidle'});await ready(page);
    check('saved position preserves resume icon and accessible label',await page.locator('#resume-panel').isVisible() && await icon(page,'#resume-continue','play') && await page.getByRole('button',{name:'Continue from 0:10',exact:true}).count()===1);
    await page.getByRole('button',{name:'Reading settings',exact:true}).click();
    await page.evaluate(() => {const event=new Event('beforeinstallprompt',{cancelable:true});event.prompt=async()=>{};event.userChoice=Promise.resolve({outcome:'dismissed'});window.dispatchEvent(event);});
    check('install prompt retains icon and single accessible label',await icon(page,'#install-app','download') && await page.getByRole('button',{name:'Install Lumen',exact:true}).count()===1);
    await page.getByRole('button',{name:'Install Lumen',exact:true}).click();
    check('dismissed install returns helper with its icon',await icon(page,'#install-app','download') && await page.getByRole('button',{name:'How to install',exact:true}).count()===1);
    await page.getByRole('button',{name:'Close reading settings',exact:true}).click();
    fixture.state.failChapter=true;await page.locator('#track-next').click();
    await page.locator('#transition-retry').waitFor({state:'visible'});
    check('failed navigation presents retry icon and label',await icon(page,'#transition-retry','retry') && await page.getByRole('button',{name:'Retry',exact:true}).count()===1);
    fixture.state.failChapter=false;await page.getByRole('button',{name:'Retry',exact:true}).click();
    await page.waitForFunction(()=>window.LUMEN_CHAPTER?.chapterId==='chapter-002');await ready(page);
    await page.locator('#track-prev').click();await page.waitForFunction(()=>window.LUMEN_CHAPTER?.chapterId==='chapter-001');await ready(page);
    check('current chapter marker preserves accessible text',await icon(page,'[data-track-id="chapter-001"] .chapter-marker','bookmark') && await page.locator('[data-track-id="chapter-001"]').getAttribute('aria-current')==='page' && await page.locator('[data-track-id="chapter-001"] .chapter-state').textContent()==='Current');
    await seek(page,historical.duration-1);await page.locator('#play').click();
    try {await page.waitForFunction(()=>document.querySelector('#narration').ended, null, {timeout:5000});}
    catch (error) {check('ending diagnostic',false,await page.evaluate(()=>{const audio=document.querySelector('#narration');return {time:audio.currentTime,duration:audio.duration,paused:audio.paused,ended:audio.ended,ready:audio.readyState,label:document.querySelector('#play').getAttribute('aria-label')};}));throw error;}
    check('completed playback exposes replay icon and accessible name',await icon(page,'#play','replay') && await page.getByRole('button',{name:'Replay audiobook',exact:true}).count()===1);
    check('completed chapter marker changes to check',await icon(page,'[data-track-id="chapter-001"] .chapter-marker','check') && await page.locator('[data-track-id="chapter-001"] .chapter-state').textContent()==='Finished');
    await page.reload({waitUntil:'networkidle'});await ready(page);
    check('completed bookmark retains resume replay icon',await icon(page,'#resume-continue','replay') && await page.getByRole('button',{name:'Listen again',exact:true}).count()===2);
    await fixture.context.close();
  });
  await section('artwork switch and transport',async () => {
    const fixture=await reader([320,720]),page=fixture.page;
    const artwork=page.getByRole('switch',{name:'Artwork',exact:true});
    await artwork.click();
    check('artwork switch turns off with a stable accessible name',await artwork.getAttribute('aria-checked')==='false' && await page.locator('#scene-stage').isHidden() && await artwork.getAttribute('aria-pressed')===null);
    await page.waitForFunction(()=>new DOMMatrixReadOnly(getComputedStyle(document.querySelector('.art-switch-thumb')).transform).m41===0);
    check('off state places the switch thumb at the start',await page.locator('.art-switch-thumb').evaluate(element=>getComputedStyle(element).transform==='none'));
    await page.reload({waitUntil:'networkidle'});
    await page.waitForFunction(()=>window.LUMEN_CHAPTER?.chapterId && document.querySelector('#narration').readyState>=1);
    check('artwork switch restores its off preference without starting audio',await artwork.getAttribute('aria-checked')==='false' && await page.locator('#scene-stage').isHidden() && await page.locator('#narration').evaluate(element=>element.paused));
    await artwork.focus();await page.keyboard.press('Space');await ready(page);
    await page.waitForFunction(()=>new DOMMatrixReadOnly(getComputedStyle(document.querySelector('.art-switch-thumb')).transform).m41===14);
    check('Space turns artwork on and moves the thumb',await artwork.getAttribute('aria-checked')==='true' && await page.locator('#scene-stage').isVisible() && await page.locator('.art-switch-thumb').evaluate(element=>new DOMMatrixReadOnly(getComputedStyle(element).transform).m41===14));
    await page.keyboard.press('Enter');
    check('Enter turns the same switch off',await artwork.getAttribute('aria-checked')==='false' && await page.locator('#scene-stage').isHidden());
    await artwork.click();await ready(page);
    check('transport uses clean named rewind and forward icons',await icon(page,'#back','rewind') && await icon(page,'#forward','forward') && await page.getByRole('button',{name:'Rewind 15 seconds',exact:true}).count()===1 && await page.getByRole('button',{name:'Forward 15 seconds',exact:true}).count()===1);
    await seek(page,40);await page.getByRole('button',{name:'Forward 15 seconds',exact:true}).click();
    await page.waitForFunction(()=>Math.abs(document.querySelector('#narration').currentTime-55)<.1);
    await page.getByRole('button',{name:'Rewind 15 seconds',exact:true}).click();
    await page.waitForFunction(()=>Math.abs(document.querySelector('#narration').currentTime-40)<.1);
    check('icon transport retains fifteen-second skips and paused listening',await page.locator('#narration').evaluate(element=>element.paused));
    await fixture.context.close();
  });
  await section('bookmark recovery',async () => {
    const bookmark={schemaVersion:1,bookId:'lumen',manuscriptVersion:'v6',chapterId:'chapter-001',sentenceId:'v6:chapter-001:p999-s01',audioSha256:'f'.repeat(64),audioTime:12,sentenceFraction:.5,narratorId:'charon',completed:false,readingExtentId:'chapter-001-controls-fixture'};
    const seed={schemaVersion:2,bookId:'lumen',manuscriptVersion:'v6',releaseId:'older-fixture',lastPosition:bookmark,history:{}};
    const fixture=await reader([320,720],{seed}),page=fixture.page;
    await page.locator('#transition-start').waitFor({state:'visible'});
    check('unmappable bookmark keeps retry and recovery icons',await icon(page,'#transition-start','restart') && await icon(page,'#transition-retry','retry') && await page.getByRole('button',{name:'Start this chapter from the beginning',exact:true}).count()===1);
    await page.getByRole('button',{name:'Start this chapter from the beginning',exact:true}).click();await ready(page);
    check('recovery action opens the reader without losing play icon',await icon(page,'#play','play') && await page.locator('#transition-panel').isHidden());
    await fixture.context.close();
  });
  await section('keyboard and motion preferences',async () => {
    const fixture=await reader([1440,1000],{reducedMotion:true}),page=fixture.page;
    await page.locator('#chapters-open').focus();await page.keyboard.press('Tab');
    check('keyboard focus has visible outline',await page.locator('#follow').evaluate(element=>document.activeElement===element && element.matches(':focus-visible') && getComputedStyle(element).outlineStyle==='solid' && parseFloat(getComputedStyle(element).outlineWidth)>=2));
    await page.keyboard.press('Enter');
    check('keyboard follow action retains icon',await icon(page,'#follow','follow') && await page.locator('#follow').getAttribute('aria-pressed')==='false');
    await page.locator('#settings-open').focus();await page.keyboard.press('Enter');
    check('keyboard opens settings and Escape dismisses',await page.locator('#settings').evaluate(element=>element.open));
    await page.keyboard.press('Escape');
    check('Escape restores focus to settings opener',await page.locator('#settings-open').evaluate(element=>document.activeElement===element) && !await page.locator('#settings').evaluate(element=>element.open));
    check('reduced motion suppresses control, switch and painting transitions',await page.evaluate(()=>['#follow','#play','.chapter-choice','#scene-incoming','.art-switch','.art-switch-thumb'].every(selector=>getComputedStyle(document.querySelector(selector)).transitionDuration.split(',').every(value=>parseFloat(value)===0))));
    await fixture.context.close();
  });
  check('no browser runtime errors',report.errors.length===0,report.errors);
})().catch(error=>{report.errors.push(String(error.stack));process.exitCode=1;}).finally(async () => {
  if (browser) await browser.close();
  await new Promise(resolve=>server.close(resolve));
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  if (report.errors.length || report.checks.some(item=>!item.pass)) process.exitCode=1;
  console.log('Report: '+path.join(output,'report.json'));
});
