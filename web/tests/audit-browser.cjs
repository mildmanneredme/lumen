/* Behavioral checks against a same-origin fixture. No production feedback is changed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const os = require('node:os');
let modulePath = process.env.PLAYWRIGHT_MODULE;
if (!modulePath) {
  try { modulePath = require.resolve('playwright'); }
  catch (_) { modulePath = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
}
const {chromium} = require(modulePath);
const auditRoot = path.join(__dirname, '..', 'audit');
const audioPath = path.join(__dirname, '..', 'dist', 'assets', 'chapter-001-pilot.mp3');
const out = process.env.LUMEN_AUDIT_QA_OUT || '/private/tmp/lumen-audit-qa';
fs.mkdirSync(out, {recursive:true});
const report = {checks:[], screenshots:[], errors:[]};
const candidates = [
  {candidateId:'v7-001-a', edition:'v7', narrator:'Autonoe', chapter:1, title:'Chapter 1', chunkId:'001-01', audioHash:'a'.repeat(64), expected:'The city kept its secrets.', heard:'The city kept secrets.', expectedContext:'<script>unsafe()</script> The city kept its secrets. Rain moved across the glass.', category:'possible_omission', severity:'high', start:12, end:18, precision:'ASR candidate, approximate', scope:'Estimated window in the mastered chapter.', source:'OpenAI / mastered chapter', audioUrl:'/audio.mp3', decision:{status:'pending',notes:'',revision:0}},
  {candidateId:'v8-004-b', edition:'v8', narrator:'Charon', chapter:4, title:'Chapter 4', chunkId:'004-02', audioHash:'b'.repeat(64), expected:'Everything had changed.', heard:'Everything had changed.', expectedContext:'Everything had changed.', category:'long_pause', severity:'medium', start:22, end:28, precision:'technical measurement', scope:'chapter', source:'OpenAI / mastered chapter', audioUrl:'/audio.mp3', decision:{status:'pending',notes:'',revision:0}},
  {candidateId:'v7-014-c', edition:'v7', narrator:'Autonoe', chapter:14, title:'Chapter 14', chunkId:'014-01', audioHash:'c'.repeat(64), expected:'His name was Adrian.', heard:'', expectedContext:'His name was Adrian.', category:'omission', severity:'low', start:30, end:35, precision:'technical measurement', scope:'chapter', source:'OpenAI / mastered chapter', audioUrl:'/audio.mp3', decision:{status:'accepted',notes:'Sounds good.',revision:1}},
  {candidateId:'local:v8-007-d', edition:'v8', narrator:'Charon', chapter:7, title:'Chapter 7', chunkId:'007-01', audioHash:'d'.repeat(64), expected:'', heard:'Unwritten words.', expectedContext:'The intended manuscript passage.', category:'addition', severity:'high', start:40, end:45, precision:'local_whisper_approximate', scope:'Local Whisper estimate in the original source take, before mastering. unresolved_second_pass_mismatch', source:'Local ASR / source take', audioUrl:'/audio.mp3', decision:{status:'accepted',notes:'Earlier review.',revision:1}}
];
candidates.forEach((candidate,index)=>candidate.reviewBinding=String(index+1).repeat(64));
const stressCandidates=Array.from({length:240},(_,i)=>({...candidates[0],candidateId:`stress-${i}`,chapter:i+1,title:`Chapter ${i+1} — ${'UnbrokenReportTitle'.repeat(12)}`,chunkId:`clip-${'f'.repeat(180)}`,category:`possible_omission_${'LongClassification'.repeat(12)}`,decision:{status:'pending',notes:'',revision:0}}));
let failSave = null, failLoad = false, empty = false, stress = false, requests = [];
function json(response, code, body) { response.writeHead(code, {'Content-Type':'application/json'}); response.end(JSON.stringify(body)); }
const server = http.createServer((request,response) => {
  const url = new URL(request.url, 'http://localhost');
  if (url.pathname === '/api/audit') {
    if (failLoad) return json(response,503,{error:'Audit data unavailable'});
    return json(response,200,{schemaVersion:1,csrfToken:'fixture-token',coverage:{complete:false,selected:496,checked:180},candidates:empty?[]:stress?stressCandidates:candidates});
  }
  if (url.pathname === '/api/decisions' && request.method === 'POST') {
    let body=''; request.on('data', chunk => body+=chunk); request.on('end', () => {
      const input=JSON.parse(body); requests.push({input,token:request.headers['x-audit-token']});
      if (failSave) return json(response,failSave,{error:failSave===409?'This passage was updated elsewhere. Reload to review the latest decision.':'Could not save feedback.'});
      const item=candidates.find(c=>c.candidateId===input.candidateId);
      if(input.reviewBinding!==item.reviewBinding)return json(response,409,{error:'The review flag changed. Reload and listen before deciding.'});
      if (input.revision!==item.decision.revision) return json(response,409,{error:'Feedback revision changed.'});
      item.decision={status:input.status,notes:input.notes,revision:input.revision+1}; return json(response,200,{decision:item.decision});
    }); return;
  }
  if (url.pathname === '/api/queue') {
    response.setHeader('Content-Disposition','attachment; filename="regeneration-queue.json"');
    return json(response,200,{schemaVersion:1,candidates:candidates.filter(c=>c.decision.status==='regenerate')});
  }
  if (url.pathname === '/audio.mp3') {
    const size=fs.statSync(audioPath).size, match=/bytes=(\d+)-(\d*)/.exec(request.headers.range||'');
    const start=match?Number(match[1]):0, end=match&&match[2]?Math.min(Number(match[2]),size-1):size-1;
    response.writeHead(match?206:200, {'Content-Type':'audio/mpeg','Accept-Ranges':'bytes','Content-Length':end-start+1,...(match?{'Content-Range':`bytes ${start}-${end}/${size}`}:{})});
    return fs.createReadStream(audioPath,{start,end}).pipe(response);
  }
  const names={'/audit':'index.html','/audit/':'index.html','/audit/index.html':'index.html','/audit/audit.js':'audit.js','/audit/audit.css':'audit.css'};
  const name=names[url.pathname];
  if (!name || !fs.existsSync(path.join(auditRoot,name))) {response.writeHead(404);return response.end('Not found');}
  response.writeHead(200,{'Content-Type':name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html'});fs.createReadStream(path.join(auditRoot,name)).pipe(response);
});
function check(name, fn) { fn(); report.checks.push({name,pass:true}); console.log('PASS '+name); }
async function checkAsync(name, fn) { await fn(); report.checks.push({name,pass:true}); console.log('PASS '+name); }
(async()=>{
  let browser;
  try {
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const url=`http://127.0.0.1:${server.address().port}/audit/`;
    const options={headless:true};
    if(process.env.CHROME_PATH)options.executablePath=process.env.CHROME_PATH;
    else if(process.platform==='darwin')options.executablePath='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
    browser=await chromium.launch(options);
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,acceptDownloads:true});
    const page=await context.newPage();page.on('pageerror',e=>report.errors.push(String(e)));
    await page.goto(url);
    await page.locator('#candidate-title').waitFor({state:'visible',timeout:5000});
    await checkAsync('priority queue opens highest-priority pending passage',async()=>assert.equal(await page.locator('.queue-item[aria-current=true]').getAttribute('data-id'),'v7-001-a'));
    await checkAsync('expected context is text, not executable report markup',async()=>{
      assert.match(await page.locator('#expected-context').textContent(),/<script>unsafe\(\)<\/script>/);
      assert.equal(await page.evaluate(()=>typeof window.unsafe),'undefined');
      assert.match(await page.locator('#precision').textContent(),/ASR candidate/);
      assert.match(await page.locator('#coverage').textContent(),/180.*496/);
    });
    await checkAsync('source filtering keeps recording clock scope and coverage attribution clear',async()=>{
      assert.match(await page.locator('#coverage').textContent(),/OpenAI checks in progress/);
      assert.match(await page.locator('#source-coverage').textContent(),/1.*local ASR.*additional/i);
      await page.locator('#source-filter').selectOption('Local ASR / source take');
      assert.match(await page.locator('#candidate-title').textContent(),/Chapter 7/);
      assert.equal(await page.locator('#expected').textContent(),'No added passage expected.');
      assert.match(await page.locator('#candidate-source').textContent(),/Local ASR.*source take/);
      assert.match(await page.locator('#precision').textContent(),/original source take, before mastering/);
      await page.locator('#source-filter').selectOption('OpenAI / mastered chapter');
      await page.locator('#search').fill('Chapter 14');
      assert.equal(await page.locator('#heard').textContent(),'Not recognised.');
      await page.locator('#search').fill('');
      await page.locator('#source-filter').selectOption('all');
      await page.locator('.queue-item[data-id="v7-001-a"]').tap();
    });
    await page.waitForFunction(()=>document.querySelector('#audit-audio').readyState>=1);
    await checkAsync('audition starts at flagged window and real media seeks forward/back',async()=>{
      await page.locator('#play-window').tap();
      await page.waitForFunction(()=>document.querySelector('#audit-audio').currentTime>=12);
      await page.locator('#audit-audio').evaluate(a=>a.pause());
      const before=await page.locator('#audit-audio').evaluate(a=>a.currentTime);
      await page.locator('#skip-forward').tap();
      assert.ok(Math.abs(await page.locator('#audit-audio').evaluate(a=>a.currentTime)-before-5)<.3);
      await page.locator('#skip-back').tap();
      assert.ok(Math.abs(await page.locator('#audit-audio').evaluate(a=>a.currentTime)-before)<.3);
      await page.locator('#playback-rate').selectOption('1.25');
      assert.equal(await page.locator('#audit-audio').evaluate(a=>a.playbackRate),1.25);
      await page.locator('#play-window').tap();
      await page.locator('#audit-audio').evaluate(a=>{a.currentTime=17.7;});
      await page.waitForFunction(()=>document.querySelector('#audit-audio').paused&&document.querySelector('#audit-audio').currentTime>=18);
      assert.ok(await page.locator('#audit-audio').evaluate(a=>a.currentTime)<19);
    });
    await checkAsync('dirty candidate navigation can be cancelled without losing notes',async()=>{
      await page.locator('#feedback-notes').fill('The missing “its” is audible.');
      await page.locator('#next-candidate').tap();
      await page.locator('#unsaved-dialog').waitFor({state:'visible'});
      await page.locator('#keep-editing').tap();
      assert.equal(await page.locator('#feedback-notes').inputValue(),'The missing “its” is audible.');
      assert.match(await page.locator('#candidate-title').textContent(),/Chapter 1\b/);
    });
    await checkAsync('save and next persists exact recording identity and revision',async()=>{
      await page.locator('#decision-status').selectOption('regenerate');
      await page.locator('#save-next').tap();
      await page.waitForFunction(()=>document.querySelector('#candidate-title').textContent.includes('Chapter 4'));
      assert.deepEqual(requests.at(-1),{input:{candidateId:'v7-001-a',audioHash:'a'.repeat(64),reviewBinding:'1'.repeat(64),status:'regenerate',notes:'The missing “its” is audible.',revision:0},token:'fixture-token'});
      await page.reload();await page.locator('#candidate-title').waitFor();
      await page.locator('#status-filter').selectOption('regenerate');
      assert.equal(await page.locator('#feedback-notes').inputValue(),'The missing “its” is audible.');
      assert.equal(await page.locator('#decision-status').inputValue(),'regenerate');
      assert.match(await page.locator('#review-counts').textContent(),/1.*regenerat/i);
    });
    await checkAsync('filters, search, and empty matching queue are usable',async()=>{
      await page.locator('#status-filter').selectOption('all');
      await page.locator('#edition-filter').selectOption('v8');
      assert.match(await page.locator('#candidate-title').textContent(),/Chapter 4/);
      await page.locator('#search').fill('no such passage');
      await page.locator('#empty-state').waitFor({state:'visible'});
      assert.equal(await page.locator('#save-decision').isVisible(),false);
      await page.locator('#search').fill('');
      await page.locator('#severity-filter').selectOption('low');
      await page.locator('#empty-state').waitFor({state:'visible'});
      await page.locator('#severity-filter').selectOption('all');
    });
    await checkAsync('stale/save failures retain feedback for retry and never advance',async()=>{
      await page.locator('#feedback-notes').fill('Check this pause.');
      await page.locator('#decision-status').selectOption('unsure');
      failSave=409;await page.locator('#save-next').tap();
      await page.waitForFunction(()=>document.querySelector('#save-status').textContent.includes('updated elsewhere'));
      assert.equal(await page.locator('#feedback-notes').inputValue(),'Check this pause.');
      assert.match(await page.locator('#candidate-title').textContent(),/Chapter 4/);
      candidates[1].decision={status:'accepted',notes:'Reviewed elsewhere.',revision:1};
      await page.locator('#review-latest').tap();
      await page.waitForFunction(()=>document.querySelector('#conflict-detail').textContent.includes('Reviewed elsewhere.'));
      assert.equal(await page.locator('#feedback-notes').inputValue(),'Check this pause.');
      assert.equal(await page.locator('#decision-status').inputValue(),'unsure');
      failSave=503;await page.locator('#save-decision').tap();
      await page.waitForFunction(()=>document.querySelector('#save-status').textContent.includes('Could not save'));
      assert.equal(await page.locator('#feedback-notes').inputValue(),'Check this pause.');
      failSave=null;await page.locator('#save-decision').tap();
      await page.waitForFunction(()=>document.querySelector('#save-status').textContent.includes('Saved'));
      assert.equal(candidates[1].decision.revision,2);
      assert.equal(requests.at(-1).input.revision,1);
    });
    await checkAsync('dirty filter navigation can be cancelled or explicitly discarded',async()=>{
      await page.locator('#feedback-notes').fill('Draft that must not vanish.');
      await page.locator('#edition-filter').selectOption('v7');
      await page.locator('#keep-editing').tap();
      assert.equal(await page.locator('#edition-filter').inputValue(),'v8');
      assert.equal(await page.locator('#feedback-notes').inputValue(),'Draft that must not vanish.');
      await page.locator('#edition-filter').selectOption('v7');
      await page.locator('#guard-discard').tap();
      assert.match(await page.locator('#candidate-title').textContent(),/Chapter 1\b/);
      assert.equal(await page.locator('#feedback-notes').inputValue(),'The missing “its” is audible.');
    });
    await checkAsync('save-and-continue guard keeps failed drafts and moves only after confirmation',async()=>{
      await page.locator('#feedback-notes').fill('Check the pronunciation on the last word.');
      await page.locator('#next-candidate').tap();
      failSave=503;await page.locator('#guard-save').tap();
      await page.waitForFunction(()=>document.querySelector('#guard-error').textContent.includes('Could not save'));
      assert.equal(await page.locator('#unsaved-dialog').isVisible(),true);
      assert.equal(await page.locator('#feedback-notes').inputValue(),'Check the pronunciation on the last word.');
      assert.match(await page.locator('#candidate-title').textContent(),/Chapter 1\b/);
      failSave=null;await page.locator('#guard-save').tap();
      await page.waitForFunction(()=>document.querySelector('#candidate-title').textContent.includes('Chapter 14'));
      assert.equal(await page.locator('#unsaved-dialog').isVisible(),false);
      assert.equal(candidates[0].decision.notes,'Check the pronunciation on the last word.');
    });
    await checkAsync('queue export downloads only human-selected regeneration items',async()=>{
      const pending=page.waitForEvent('download');await page.locator('#export-queue').tap();
      const download=await pending;const file=await download.path();const data=JSON.parse(fs.readFileSync(file,'utf8'));
      assert.deepEqual(data.candidates.map(c=>c.candidateId),['v7-001-a']);
    });
    await checkAsync('changed review binding on the same audio requires reloading and listening again',async()=>{
      await page.locator('.queue-item[data-id="v7-001-a"]').tap();
      await page.locator('#feedback-notes').fill('Preserve this draft while the report changes.');
      const binding=candidates[0].reviewBinding;candidates[0].reviewBinding='9'.repeat(64);
      await page.locator('#save-decision').tap();
      await page.waitForFunction(()=>document.querySelector('#save-status').textContent.includes('review flag changed'));
      assert.equal(requests.at(-1).input.reviewBinding,binding);
      await page.locator('#review-latest').tap();
      await page.waitForFunction(()=>document.querySelector('#conflict-detail').textContent.includes('reload the audit'));
      assert.equal(await page.locator('#feedback-notes').inputValue(),'Preserve this draft while the report changes.');
      await page.locator('#save-decision').tap();
      await page.waitForFunction(()=>document.querySelector('#save-status').textContent.includes('review flag changed'));
      assert.equal(requests.at(-1).input.reviewBinding,binding);
      candidates[0].reviewBinding=binding;await page.locator('#save-decision').tap();
      await page.waitForFunction(()=>document.querySelector('#save-status').textContent.includes('Saved'));
    });
    await checkAsync('phone has no horizontal overflow and touch controls are at least44px',async()=>{
      for(const width of [320,360,390,430]){
        await page.setViewportSize({width,height:844});
        const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,small:[...document.querySelectorAll('button,select,input:not([type=hidden]),a.action')].filter(el=>el.getClientRects().length).filter(el=>{const r=el.getBoundingClientRect();return r.width<44||r.height<44}).map(el=>el.id)}));
        assert.ok(layout.scroll<=layout.width,`overflow at ${width}`);assert.deepEqual(layout.small,[],`small controls at ${width}`);
      }
      await page.setViewportSize({width:390,height:844});
      const file=path.join(out,'audit-phone.png');await page.screenshot({path:file,fullPage:true});report.screenshots.push(file);
    });
    const desktop=await browser.newContext({viewport:{width:1280,height:900}}), keyboard=await desktop.newPage();await keyboard.goto(url);await keyboard.locator('#candidate-title').waitFor();
    await checkAsync('keyboard can choose a verdict and save without a pointer',async()=>{
      await keyboard.locator('#decision-status').focus();await keyboard.keyboard.press('Home');await keyboard.keyboard.press('ArrowDown');await keyboard.keyboard.press('Tab');
      assert.equal(await keyboard.locator('#feedback-notes').evaluate(el=>document.activeElement===el),true);
      await keyboard.keyboard.type('Keyboard reviewed.');await keyboard.keyboard.press('Tab');assert.equal(await keyboard.locator('#save-decision').evaluate(el=>document.activeElement===el),true);
      await keyboard.keyboard.press('Enter');await keyboard.waitForFunction(()=>document.querySelector('#save-status').textContent.includes('Saved'));
      const file=path.join(out,'audit-desktop.png');await keyboard.screenshot({path:file,fullPage:true});report.screenshots.push(file);
    });
    failLoad=true;await page.reload();
    await checkAsync('missing local server/data reports a recoverable unavailable state',async()=>{
      await page.locator('#load-error').waitFor({state:'visible'});assert.match(await page.locator('#load-error').textContent(),/unavailable/i);
      failLoad=false;await page.locator('#reload-audit').tap();await page.locator('#candidate-title').waitFor();
    });
    empty=true;await page.reload();
    await checkAsync('no flagged pieces shows honest incomplete-coverage state',async()=>{
      await page.locator('#empty-state').waitFor({state:'visible'});
      assert.match(await page.locator('#empty-state').textContent(),/no flagged/i);
      assert.match(await page.locator('#coverage').textContent(),/in progress/i);
    });
    await checkAsync('large queues preserve row text and separation on desktop and phones',async()=>{
      empty=false;stress=true;
      for(const view of [{page:keyboard,width:1280,height:900},{page,width:320,height:844},{page,width:390,height:844}]){
        await view.page.setViewportSize({width:view.width,height:view.height});await view.page.reload();
        await view.page.waitForFunction(()=>document.querySelectorAll('.queue-item').length===240);
        const layout=await view.page.evaluate(()=>{
          const rows=[...document.querySelectorAll('.queue-item')];
          return {width:innerWidth,scroll:document.documentElement.scrollWidth,failures:rows.flatMap((row,index)=>{
            const rect=row.getBoundingClientRect(),next=rows[index+1]?.getBoundingClientRect(),faults=[];
            for(const child of row.children){
              const range=document.createRange();range.selectNodeContents(child);
              for(const r of [child.getBoundingClientRect(),...range.getClientRects()])if(r.left<rect.left-1||r.right>rect.right+1||r.top<rect.top-1||r.bottom>rect.bottom+1)faults.push({index,kind:'text exceeds button',buttonHeight:rect.height,childHeight:r.height,childBottom:r.bottom,buttonBottom:rect.bottom});
            }
            if(next&&rect.bottom>next.top+1)faults.push({index,kind:'rows overlap'});
            return faults;
          })};
        });
        assert.ok(layout.scroll<=layout.width,`large queue overflows at ${view.width}`);
        assert.deepEqual(layout.failures,[],`large queue row content at ${view.width}`);
        const file=path.join(out,`audit-large-queue-${view.width}.png`);await view.page.screenshot({path:file,fullPage:true});report.screenshots.push(file);
      }
    });
    check('no page JavaScript errors',()=>assert.deepEqual(report.errors,[]));
    await context.close();await desktop.close();
  } catch(error) {
    report.checks.push({name:'audit browser suite',pass:false,detail:String(error.stack)});console.error(error.stack);process.exitCode=1;
  } finally {
    if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2)+'\n');
  }
})();
