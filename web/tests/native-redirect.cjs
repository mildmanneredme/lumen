'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const {createNetworkLedger,observeNativeNetwork,mediaNetworkVerdict}=require('./native-network-observer.cjs');
const APP='https://candidate.vercel.app',WORKER='https://media.workers.dev',URL_A=APP+'/api/assets/release/audio/voice/chapter.hash.mp3',URL_M=WORKER+'/media/release/audio/voice/chapter.hash.mp3?grant=PRIVATE_GRANT';
const headers={'Content-Type':'audio/mpeg','Content-Length':'100','Content-Range':'bytes 0-99/100','Cache-Control':'private, no-store','Access-Control-Allow-Origin':APP};
function goodLedger(options={}) {
 const ledger=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER,...options}),h=ledger.handlers;
 h.requestWillBeSent({requestId:'media',request:{url:URL_A,method:'GET'},type:'Media'});
 h.requestWillBeSent({requestId:'media',request:{url:URL_M,method:'GET'},type:'Media',redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store','Set-Cookie':'PRIVATE_COOKIE'}}});
 h.responseReceived({requestId:'media',response:{url:URL_M,status:206,headers}});h.dataReceived({requestId:'media',dataLength:100});return ledger;
}
const verdict=ledger=>mediaNetworkVerdict(ledger.snapshot(),{appOrigin:APP,workerOrigin:WORKER});
test('raw redirect and final response URLs prove distinct delivery hops; snapshots exclude secrets',()=>{
 const l=goodLedger(),s=l.snapshot();assert.equal(verdict(l).apiRedirectsOnly,true);assert.equal(verdict(l).workerAudioValid,true);
 assert.deepEqual(s.responses.map(r=>[r.origin,r.status,r.bodyBytes]),[[APP,307,0],[WORKER,206,100]]);
 const serialized=JSON.stringify(s);for(const secret of ['PRIVATE_GRANT','PRIVATE_COOKIE','grant=','Set-Cookie'])assert.equal(serialized.includes(secret),false);
 s.responses[0].status=206;assert.equal(verdict(l).apiRedirectsOnly,true,'Snapshots must not mutate the held ledger');
});
test('the old Playwright native-redirect attribution fails while raw protocol evidence stays strict',()=>{
 const observed=[{origin:APP,path:new URL(URL_A).pathname,status:206,contentType:''}];
 assert.throws(()=>assert.ok(observed.every(r=>r.status===307)),'The exact observed old-gate failure must reproduce');
 assert.equal(verdict(goodLedger()).apiRedirectsOnly,true);
});
test('actual app-origin 206 media and app body bytes are rejected',()=>{
 for(const status of [200,206]) {const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_A,method:'GET'},type:'Media'});l.handlers.responseReceived({requestId:'bad',response:{url:URL_A,status,headers}});l.handlers.dataReceived({requestId:'bad',dataLength:1});assert.equal(verdict(l).apiRedirectsOnly,false);}
 const l=goodLedger();l.handlers.requestWillBeSent({requestId:'unanswered',request:{url:URL_A,method:'GET'},type:'Media'});l.handlers.dataReceived({requestId:'unanswered',dataLength:1});assert.equal(verdict(l).apiRedirectsOnly,false);
});
test('redirect nonzero length, audio type, cached response and public caching cannot pass',()=>{
 for(const changed of [{'Content-Length':'1'},{'Content-Type':'audio/mpeg'},{'Cache-Control':'public, no-store'},{'Cache-Control':'no-store'}]) {
  const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_A,method:'GET'},type:'Media'});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_M,method:'GET'},type:'Media',redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store',...changed}}});assert.equal(verdict(l).apiRedirectsOnly,false);
 }
 for(const cached of [{fromDiskCache:true},{fromServiceWorker:true}]){const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_A,method:'GET'},type:'Media'});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_M,method:'GET'},type:'Media',redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store'},...cached}});assert.equal(verdict(l).apiRedirectsOnly,false);}
});
test('wrong response URL, mismatched CORS and absent audio bytes fail closed',()=>{
 for(const changed of [{url:URL_M+'&different=1'},{headers:{...headers,'Access-Control-Allow-Origin':'https://foreign.example'}},{headers:{...headers,'Content-Type':'text/plain'}}]) {
  const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_M,method:'GET'},type:'Media'});l.handlers.responseReceived({requestId:'bad',response:{url:URL_M,status:206,headers,...changed}});l.handlers.dataReceived({requestId:'bad',dataLength:100});assert.equal(verdict(l).workerAudioValid,false);
 }
 const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_M,method:'GET'},type:'Media'});l.handlers.responseReceived({requestId:'bad',response:{url:URL_M,status:206,headers}});assert.equal(verdict(l).workerAudioValid,false);
});
test('null CORS, incremental byte callbacks and sanitized cancellation/offline classifications',()=>{
 const events=[],l=goodLedger({phase:()=> 'native-test',onData:r=>events.push(r),onFailure:r=>events.push(r)});l.handlers.dataReceived({requestId:'media',dataLength:2});l.handlers.loadingFailed({requestId:'media',errorText:'net::ERR_INTERNET_DISCONNECTED',canceled:false});
 assert.deepEqual(events.filter(e=>e.bytes).map(e=>e.bytes),[100,2]);assert.equal(events.at(-1).errorKind,'offline');assert.equal(events.at(-1).path,new URL(URL_M).pathname);assert.equal(JSON.stringify(events).includes('PRIVATE_GRANT'),false);
 l.handlers.requestWillBeSent({requestId:'uncancelled',request:{url:URL_M,method:'GET'},type:'Media'});
 l.handlers.loadingFailed({requestId:'uncancelled',errorText:'net::ERR_INTERNET_DISCONNECTED',canceled:false});
 const unfinished=l.snapshot().unresponded[0];assert.equal(unfinished.cancelled,false,'An uncancelled transport failure is not a cancellation');assert.equal(unfinished.failed,true);
 l.handlers.requestWillBeSent({requestId:'cancelled',request:{url:URL_M,method:'GET'},type:'Media'});
 l.handlers.loadingFailed({requestId:'cancelled',errorText:'net::ERR_ABORTED',canceled:true});
 assert.deepEqual(l.snapshot().unresponded.map(r=>[r.failed,r.cancelled]),[[true,false],[true,true]]);

 const nullLedger=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});nullLedger.handlers.requestWillBeSent({requestId:'n',request:{url:URL_M,method:'GET'},type:'Media'});nullLedger.handlers.responseReceived({requestId:'n',response:{url:URL_M,status:206,headers:{...headers,'Access-Control-Allow-Origin':'null'}}});nullLedger.handlers.dataReceived({requestId:'n',dataLength:1});assert.equal(verdict(nullLedger).workerAudioValid,true);
});
test('total hop bound includes redirects and invalid counts cannot authorize a proof',()=>{
 const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER,maxRequests:1});l.handlers.requestWillBeSent({requestId:'one',request:{url:URL_A,method:'GET'},type:'Media'});l.handlers.requestWillBeSent({requestId:'one',request:{url:URL_M,method:'GET'},type:'Media',redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store'}}});assert.deepEqual(l.snapshot().issues,['request-limit']);assert.equal(verdict(l).apiRedirectsOnly,false);
 const bad=goodLedger();bad.handlers.dataReceived({requestId:'media',dataLength:-1});assert.equal(verdict(bad).workerAudioValid,false);
});
// Generated 440Hz fixture MPEG frames: 2 seconds, mono, 22.05kHz, 16kbps,
// no ID3/Xing tags or reservoir. Repetition forms a harmless native-decodable
// stream without ffmpeg, external assets, or any production audiobook reads.
const FRAME_FIXTURE=Buffer.from('//MgxAAJwEK8f0MYAknJRtsAA7u7v6O4GBgYG58EAQBA5Lg+B3xAc4P//fynn+U8/ynv6P/zIsQACZh+nAGaKAB9mvfJfqlkhFFpAMQbMBLQlKXcTWpAHDqUBQBAX8SFhLrBVnLHgCmA//MgxAAHuEZ8GdwAAgKB//+pUksqsr4QggwyEDiZVMNA9TimjVsFv////9iYgpqKZlxiYP/zIMQACUA+Sl4XsCSAAAC8BAa8Aa4IEA3zNNLl0myUPkRNhVk19mv/+7//+1lf/7eUTEH/8yDEAAkoPlR+DrwiAkkEUcAHzC8CtjSuUaGIuqeU7fd96uRQn+392y1P+oJ47/7famII//MixAAJsEogAOf0QP1jqq9RlDtposOQBGANADZrgxKQC4I0KZrCqK2Lf/d/s/9b////qTD/8yDEAAkwSiQABz4E3cgkybmFbmGpEmAKgDBqApHKCgKJCFtpDPYCv//+R/9f+r//qTEE//MgxAAJIEokAAc+BMtwUZUyqtrDkVjAEgB008wmeBQFChE2sinbYt///p/9bv///rTEEP/zIMQACCBKJAAHPgTeUEmXccpkw1FUwBMAgNORKUQMBPIGttFZ68K///6f/uTEFNRQAAD/8yLEAAnAPkpeFrwgwAAgA2meHAFFwRis8ppo0nKa9Rqg8ATFrJpf/1/2L/6KP+2r1//sTP/zIMQACXBGJADn9ED/1utAJm3BKBMNQCmAJgExpgpdGc0Aga06Kzwxv//9bv/If///sTD/8yDEAAhgRigIPz4gFqihYzBS1hWdJDGAGAEZoNRWAAgHlJJ1YtZH///93/1+tMQU1FAA//MgxAAIIEYkAAf0CN6gEwHsF2UCXagFMARALjSYzNs5IJClp0Vnhn///d/5FMQU1FAAAP/zIsQACaBGIAAH9AjWMBGBLA0qKqpi2RgCwB6akYeoHUEhwJksPTo9n6//pVs/0////sTA//MgxAAIsEokAAf0CN5QCYEOCzKDLtLumAHgGho1Jv6cUMhS40VtcEP//+R/+/+pMQU1FP/zIMQACShGJAAH9AjKrEjAjAV1QVdxf4wA4A3NFCOgjhB0Bziw9ZHs///3/+3///9qYgj/8yDEAAhYRiQAB/QI3ciJgSYKooMu0uqYAaAdGhrngpwRCGrjRW0MO/+n/v/9CYgpqKAA//MixAAJqEYwVgb8BADCM0AfuaNe12WuUpkAAITsQp2AwILBodprP/6P//30f7en/7Ok6mD/8yDEAAf4RiQAB7II5lETCfFATCWKXVMAsEA/7/+T7KQ1a9GbQxrv//1u/9CYgpqKAAAA//MgxAAJIEYkAOfyQP/n4xI1JN0wVjF1jACgEMz3JBoPg1H5rsasnmf//43/2f///oTEEP/zIMQACQg6Xl4OOiKAAAWiRsCUAbzMeLWNKzI5TqNtrJ72rR+y+7RR/6v//+5vK4umIID/8yLEAAlQRiQAB/II7jEjArAR9K1YxdYwAgBHM5oRODyNRScmNUt//r/1f9NOj//9exMQQP/zIMQACGhKJAAH8gjeURMCxBEkrliluTABwEgzdtGZPE5IlyozTY///8j/9/9SYgpqKAD/8yDEAAjARixQL7AkPABvKAxhCBhrVa8j0AAMzo03QPhVjP7TYsfVf//7//J/9OtMQU1F//MgxAAIoEItiBewJIAA+hvMAmESFwtZrqVIFA0Ob/hM8mXa/1KFf2ipru/VT/60xBTUUP/zIsQACJhCLBAXsCSEvKBGEUFetVryVQEA1OULk47HXdDMqOp//9Xb/63f2f/9SYgpqKAA//MgxAAI6EooSAfwCCCDzKUmBSgXysTEUMQCAhGT/oR57YqV2pTW2jb/Y//Jf/d/WmIKaP/zIMQACDBGJADf8ED/53kaPCz0kV1FzjAAAFEyrhMiP00vnZlVUge///xn/kExBTUUAAD/8yDEAAkgSiAA3/JA/n87GT595EZTUKAAZgAYDEZbasKnGwia4UM1tmv//1Qx/6v+pMQQ//MixAAI0EokoN/wQIP/feS48ivS5XUXOAgCmZKQnMHaaczsyqroz///s/+////6kxBTUUD/8yDEAAnASiAAL/Ak8wFMDrAuEDlNQoABgEBkMm5Wjz3pHlwojNbNGv//4j/+/+pX//dp//MgxAAJWEYliN/wQIAAJJ/efjLjzI1LldReYEAKpj3SfsbqqVPrEqpB/92v/v9/yf1pgP/zIMQACUBGJAjf8ECP/3nZaee9JnLpLyg0BWMbnUHTXRSl0pTcD5n//88t//t/7//qTEH/8yLEAAlgSi1wFvwggADAV9hRzSWv1xk0hAB6aT3ygAAJZFEZq7oz/9vr7f/X/3fp/qTEEP/zIMQACZBKIADf8ED/7zKdPzZELlchYADCoDMYumuYmXierpR2grmn///xJZ/fd///+4D/8yDEAAlIQjh2DrwgAkAByAD7PATb2HmlDgEJkLVOIgwFM3Bb9tH/6P/u/0f/e2tu5aYA//MgxAAJUEYsVBb8IDAxAwPcY50WW84qeoyB4ZKP4ojAIZ7HqG4H3X//qs//yH////rTAP/zIsQACLBKJAgG/gQPeUZ6ganCwYvsMALJg5ykMKACq2npj13Az///s/9X/f//qTEFNRQA//MgxAAJOE4gAAfwCOdoiYHqRKWqIgAMZAaDAK12ch4na2KS3K+Zn//+p//kVer//9CYgv/zIMQACRBKKAoW/iCENrCztgRWlpyWxAAlmC0pARCAHqdQbQXcDP//Vqp/+////trTEED/8yDEAAgQRjRwp55AEAAh+54D5iRhDhZiVQKDGqwjA2Hu1S4D+///6//p/0ITEFNRQAAA//MixAAHsDpSeA48IMAAEB5AAb5bB+KK1NIETDdTOeO4LXfZQPBj///7v7kxBTUUzLjEwMD/8yDEAAmgTiAAB/AI5nLDA8wGFGNaooAAkADMYtYuZmDSi70R+pXzM/7f/WN/7SX///6A//MgxAAJqEogAAfwCO4S8wPIB4RgZMMgAAyAzmNerixn2oY8ccm6gZd+zu/VR/93+n//WP/zIMQACPBGJADf8ED/7zOWHnvKebTUZRQBUMbIUIzNRYOAJuoD7v//6nf6KP//q60xBTT/8yLEAAmYSiQoB/AIMHvJeYGYBFJ4NsjsIwFUx59P8Nc1VIDiFeoGU6vo/9n/qd/R//1JgP/zIMQACTBGLFAXsCQ+sC84AYS4ObF4FWSKAdGzt6CZENLiE3UB9y03+yr0U7//Z/+pMQT/8yDEAAiQPkB2DnwgPAAkOgD4z+0hE9BQ6AeZ0L6iK8QrgmQ0HP//v/9P///7etMQU1FA//MgxAAJKDpRtg48IsAFI2HbwArgPN5+zMpOGSQkezinCMPUhdRXv2f//sv/3f/t/MJiCP/zIMQACTA6Wl4OOiKEABCSBxwUAZYzY6x24ts3f3J36gWmfcjpcv/b/41/+vdt//hBMQT/8yLEAAlASiQIB/IIjrOMGBggbak3lTtBACcZaYl0nAgpm7ErpKcPf/+7kmf9f/T2bExBTf/zIMQACQhCMEwXsCQDBGB5AMwdglGaSJowhArOA/JQ3haxG6QHAz/f/+j/7v////YmIID/8yDEAAjoSiAA5/JA/89ZwwbZj6XbeoFmAAALBnLCnGeCiK7kQ3KKcHH/7P/If9lyYgpo//MgxAAJMEokAOfyQP/ncI2a2gifD3KJAEBJM0xR/jrPTEduMU9ICf//+z/1OZ///6kxBP/zIsQACFA6UHYOfCJUCcAKAPkatdvl1MgzNkI1qU4JsPzFn97R+7p//////8UTEFNRQAAA//MgxAAJCD5OXg58IMAABwB2NpwAXjNric7QI7memlAmvLMwT9//0/f/6dH7NP/29aYggP/zIMQACaA2hl4KtgbsAMf//e/gAS6AH/1DjKOxP9TWrfIXKVn6tb/8iQI/7G/zU5VbXuD/8yDEAAlIPkpeDrwgwAAQ42FFnAGWMz0qWdmEGzSzUIRHllsE0en6Ft+z/6P/s//2dSYA//MixAAI6E4gAAf0CNV4AMC5Bb0r4QsGYAUAkGkLJIpxBxfNrkPxinzDP/q/1u/8mmIKaij/8yDEAAkwOl5cDjoigAAFslbkwCNZmDmHcG5nRs2NvYP9aFgR1/byVhWj/19/rT//rTEE//MgxAAIeEokAOfyQP/es4gaVqam8UZGYAQAbGhkHi5/DI5uJG6SnBz///If+tyYgpqKAP/zIMQACShCOHQW/CAASBnAHoHM8qH8txAKgGnHa8cGAVuJL7AOHP9SL///1Xf6f/9SYgj/8yLEAAlYSiQAB/QI1XgAwIkFlWPLGVmAFAGBozRwecIIjm4kXpKcHP//9P/qd////QmIIP/zIMQACXA6UlwOvCKAABSBxNvALLMxECkzqLTNDkyRM+cDzzmuat9/qop/01d3q/7PrTD/8yDEAAlgRjJQLvwgoAAEYQRU1CBpfm5dI/4AASPGCLkIBMXvFJfYB+nYr0/9f/XZ/sTA//MgxAAIUEY0UAb8BAMEP9TZnGs7FeHAKAWdh7pQYB+zSL2LYIf9v/r/+n/9GtMQU1FAAP/zIsQACRhOIADn9ED/z1deA0DvVM4YYmYAoAWGrQGr52AQsDZJA8sp8wz+r/77v/JpiCmg//MgxAAI2DpWfg56IOAAEeHgTYUADLMAdVN4NbP9Y8VHOGpzUpr97Nv9H/ff///9aYgpoP/zIMQAB5BGNFAG/AQAwM/cwZbwu3Ui4EAEO5BiUSBEZvFLdgH//T///0KTEFNRTMuMTAD/8yDEAAmISiAAB/QI3UeMwGQHQUogtmhgC4A+a0OWKAd+NBGbwJL6QE///8j/93+j//UA//MixAAJQDpaXg56IqAAFZIFGJABI8grnX112D5hkVuT5mflE9jOz/T/RZd//9XVL+xMQU3/8yDEAAjoRjGQPzwggAAIEqpQLmJIc6FDCDADABPaFYoWBbW5C5y2CH/1/9//kP9iYgpo//MgxAAISEokAAc+BNV4IMd6FecFNTMASACjUWh3UMApES28kc5Tg4r//+T/9SYgpqKAAP/zIMQACXhKJAAH9AjdSCzAMgaReEEtRMATABzUgRqITTkQBv5BP0gR///yTP+p////1Jj/8yLEAAm4PkpeF6IkoAAA9lWeGAFFwQNBvm7lGwQ2UhtwfB7PGv61dH9t3R1af+2tH//WmP/zIMQACWhKIAAH9Ajdx6zAJAehZDwrNAQBua9CLFC8smENThE/SBFn//9H/bf/X/6fWmD/8yDEAAmIRnh5XAACAAwA2///cbQ7pblvzAQUMQBA50TTEYTWdDj+XqP+v///q/zpOg2A//MgxAAJKIagAZooAPqNceuakT2jsou2RE2kvqJCKcIWr3jFvhENAkrcWFD30H3+cGpiCP/zIsQACfhl6AHPAAGEnjSNI6mZtJSQljG6LihKmJ0XJ2wq58+jZevTQUF4FPCjugvQX+P4','base64');
const AUDIO=Buffer.concat(Array.from({length:24},()=>FRAME_FIXTURE));
function playwrightModule(){try{return require('playwright');}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}}
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve('http://127.0.0.1:'+server.address().port)));
test('actual installed Chrome native MP3 redirects preserve raw wire-origin evidence', {timeout:60000},async()=>{
 let appOrigin,mediaOrigin,browser,observer;const wire=[],playwrightRows=[],pending=new Set();
 const media=http.createServer((req,res)=>{
  const m=/^bytes=(\d+)-(\d*)$/.exec(req.headers.range||''),start=m?Number(m[1]):0,end=m&&m[2]?Math.min(Number(m[2]),AUDIO.length-1):AUDIO.length-1;
  assert.ok(start<=end,'Fixture native range is valid');const status=m?206:200;
  const row={origin:mediaOrigin,path:req.url,method:req.method,status,bodyBytes:0};wire.push(row);
  const h={'Content-Type':'audio/mpeg','Content-Length':String(end-start+1),'Cache-Control':'private, no-store','Access-Control-Allow-Origin':req.headers.origin||appOrigin,'Cross-Origin-Resource-Policy':'cross-origin','Accept-Ranges':'bytes'};
  if(m)h['Content-Range']=`bytes ${start}-${end}/${AUDIO.length}`;res.writeHead(status,h);
  if(req.method==='HEAD'){res.end();return;}
  let offset=start;const timer=setInterval(()=>{if(offset>end){clearInterval(timer);res.end();return;}const next=Math.min(offset+1024,end+1);row.bodyBytes+=next-offset;res.write(AUDIO.subarray(offset,next));offset=next;},2);res.on('close',()=>clearInterval(timer));
 });
 const app=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/assets/')){wire.push({origin:appOrigin,path:req.url,method:req.method,status:307,bodyBytes:0});res.writeHead(307,{'Location':mediaOrigin+'/media/'+req.url.split('/').at(-1),'Content-Length':'0','Cache-Control':'private, no-store'});res.end();return;}
  res.writeHead(200,{'Content-Type':'text/html'});res.end('<!doctype html><audio id="a" preload="metadata" crossorigin="anonymous"></audio>');
 });
 try {
  appOrigin=await listen(app);mediaOrigin=await listen(media);
  const env=Object.fromEntries(['PATH','HOME','TMPDIR','LANG'].filter(k=>process.env[k]).map(k=>[k,process.env[k]]));
  browser=await playwrightModule().chromium.launch({headless:true,channel:'chrome',env});
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await context.route('**/*',route=>{assert.ok([appOrigin,mediaOrigin].includes(new URL(route.request().url()).origin),'No external fixture traffic');return route.continue();});
  context.on('response',response=>{const task=(async()=>{const u=new URL(response.url());playwrightRows.push({origin:u.origin,path:u.pathname,status:response.status(),contentType:await response.headerValue('content-type')||''});})();pending.add(task);task.finally(()=>pending.delete(task));});
  const page=await context.newPage();observer=await observeNativeNetwork(page,{appOrigin,workerOrigin:mediaOrigin,phase:()=> 'native-loopback'});
  await page.goto(appOrigin,{waitUntil:'domcontentloaded'});
  for(let i=0;i<24;i++)await page.evaluate(async({url,at})=>{const a=document.querySelector('#a');a.pause();a.src=url;a.load();await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('Fixture metadata timeout')),5000);a.addEventListener('loadedmetadata',()=>{clearTimeout(t);resolve();},{once:true});a.addEventListener('error',()=>{clearTimeout(t);reject(Error('Fixture decode failure'));},{once:true});});a.currentTime=at;await a.play();await new Promise(r=>setTimeout(r,35));a.pause();},{url:appOrigin+'/api/assets/'+(i%2?'charon':'autonoe')+'.mp3',at:i%3===0?12:0});
  await page.evaluate(()=>{const a=document.querySelector('#a');a.pause();a.removeAttribute('src');a.load();});await new Promise(r=>setTimeout(r,100));await context.close();await Promise.allSettled([...pending]);
  const snapshot=observer.snapshot(),result=mediaNetworkVerdict(snapshot,{appOrigin,workerOrigin:mediaOrigin});
  assert.equal(result.apiRedirectsOnly,true);assert.equal(result.workerAudioValid,true);assert.equal(result.apiBodyBytes,0);
  const appWire=wire.filter(r=>r.origin===appOrigin);assert.equal(appWire.length,24);assert.ok(appWire.every(r=>r.status===307&&r.bodyBytes===0));
  assert.equal(result.apiResponses,appWire.length);assert.ok(wire.some(r=>r.origin===mediaOrigin&&r.status===206&&r.bodyBytes>0));
  const anomalies=playwrightRows.filter(r=>r.origin===appOrigin&&r.path.endsWith('.mp3')&&r.status!==307);
  const output=fs.mkdtempSync(path.join(os.tmpdir(),'lumen-native-redirect-'));fs.chmodSync(output,0o700);
  const proof={scope:'tiny-dual-loopback-installed-Chrome-native-MP3-redirect',externalRequests:false,credentialRead:false,productionAudioRead:false,
   chromeVersion:browser.version(),fixtureAudioBytes:AUDIO.length,fixtureAudioSha256:crypto.createHash('sha256').update(AUDIO).digest('hex'),
   apiWireResponses:appWire.length,apiWireBodyBytes:0,playwrightMisattributions:anomalies,verdict:result,network:snapshot};
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(proof,null,2)+'\n',{mode:0o600,flag:'wx'});
  console.log(JSON.stringify({scope:proof.scope,apiWireResponses:appWire.length,playwrightMisattributions:anomalies.length,verdict:result,output}));
 }finally{if(observer)await observer.close();if(browser)await browser.close();await Promise.all([new Promise(r=>app.close(r)),new Promise(r=>media.close(r))]);}
});
