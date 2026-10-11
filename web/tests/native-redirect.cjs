'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const {createNetworkLedger,observeNativeNetwork,mediaNetworkVerdict,safeFailureDetails}=require('./native-network-observer.cjs');
const {observeRequestScope,finalizeHostedEvidence,safeConsoleError}=require('./cloudflare-browser.cjs');
const APP='https://candidate.vercel.app',WORKER='https://media.workers.dev',URL_A=APP+'/api/assets/release/audio/voice/chapter.hash.mp3',URL_M=WORKER+'/media/release/audio/voice/chapter.hash.mp3?grant=PRIVATE_GRANT';
const headers={'Content-Type':'audio/mpeg','Content-Length':'100','Content-Range':'bytes 0-99/100','Cache-Control':'private, no-store','Access-Control-Allow-Origin':APP};
function goodLedger(options={}) {
 const {responseFlags={},...ledgerOptions}=options;
 const ledger=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER,...ledgerOptions}),h=ledger.handlers;
 h.requestWillBeSent({requestId:'media',request:{url:URL_A,method:'GET'},type:'Media'});
 h.requestWillBeSent({requestId:'media',request:{url:URL_M,method:'GET'},type:'Media',redirectHasExtraInfo:false,redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store','Set-Cookie':'PRIVATE_COOKIE'}}});
 h.responseReceived({requestId:'media',hasExtraInfo:false,response:{url:URL_M,status:206,headers,...responseFlags}});h.dataReceived({requestId:'media',dataLength:100});return ledger;
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
 for(const status of [200,206]) {const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_A,method:'GET'},type:'Media'});l.handlers.responseReceived({requestId:'bad',hasExtraInfo:false,response:{url:URL_A,status,headers}});l.handlers.dataReceived({requestId:'bad',dataLength:1});assert.equal(verdict(l).apiRedirectsOnly,false);}
 const l=goodLedger();l.handlers.requestWillBeSent({requestId:'unanswered',request:{url:URL_A,method:'GET'},type:'Media'});l.handlers.dataReceived({requestId:'unanswered',dataLength:1});assert.equal(verdict(l).apiRedirectsOnly,false);
});
test('redirect nonzero length, audio type, cached response and public caching cannot pass',()=>{
 for(const changed of [{'Content-Length':'1'},{'Content-Type':'audio/mpeg'},{'Cache-Control':'public, no-store'},{'Cache-Control':'no-store'}]) {
  const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_A,method:'GET'},type:'Media'});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_M,method:'GET'},type:'Media',redirectHasExtraInfo:false,redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store',...changed}}});assert.equal(verdict(l).apiRedirectsOnly,false);
 }
 for(const cached of [{fromDiskCache:true},{fromServiceWorker:true}]){const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_A,method:'GET'},type:'Media'});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_M,method:'GET'},type:'Media',redirectHasExtraInfo:false,redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store'},...cached}});assert.equal(verdict(l).apiRedirectsOnly,false);}
});
test('wrong response URL, mismatched CORS and absent audio bytes fail closed',()=>{
 for(const changed of [{url:URL_M+'&different=1'},{headers:{...headers,'Access-Control-Allow-Origin':'https://foreign.example'}},{headers:{...headers,'Content-Type':'text/plain'}}]) {
  const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_M,method:'GET'},type:'Media'});l.handlers.responseReceived({requestId:'bad',hasExtraInfo:false,response:{url:URL_M,status:206,headers,...changed}});l.handlers.dataReceived({requestId:'bad',dataLength:100});assert.equal(verdict(l).workerAudioValid,false);
 }
 const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});l.handlers.requestWillBeSent({requestId:'bad',request:{url:URL_M,method:'GET'},type:'Media'});l.handlers.responseReceived({requestId:'bad',hasExtraInfo:false,response:{url:URL_M,status:206,headers}});assert.equal(verdict(l).workerAudioValid,false);
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

 const nullLedger=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER});nullLedger.handlers.requestWillBeSent({requestId:'n',request:{url:URL_M,method:'GET'},type:'Media'});nullLedger.handlers.responseReceived({requestId:'n',hasExtraInfo:false,response:{url:URL_M,status:206,headers:{...headers,'Access-Control-Allow-Origin':'null'}}});nullLedger.handlers.dataReceived({requestId:'n',dataLength:1});assert.equal(verdict(nullLedger).workerAudioValid,true);
});
test('a memory-cache signal rejects an otherwise valid native media response',()=>{
 const l=goodLedger();l.handlers.requestServedFromCache?.({requestId:'media'});
 assert.equal(verdict(l).workerAudioValid,false,'Memory-delivered bytes cannot prove fresh network delivery');
 assert.equal(l.snapshot().responses[1].servedFromCache,true);
});
test('prefetch and Early Hints cache responses cannot prove direct network media',()=>{
 const accepted=[];for(const flag of ['fromPrefetchCache','fromEarlyHints']) {
  const l=goodLedger({responseFlags:{[flag]:true}});if(verdict(l).workerAudioValid)accepted.push(flag);
 }assert.deepEqual(accepted,[],'Both prefetch-cache variants must reject otherwise valid media proof');
});
test('memory-cache attribution remains on the correct redirect hop',()=>{
 const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER}),h=l.handlers;
 h.requestWillBeSent({requestId:'r',request:{url:URL_A,method:'GET'},type:'Media'});
 h.requestServedFromCache?.({requestId:'r'});
 h.requestWillBeSent({requestId:'r',request:{url:URL_M,method:'GET'},type:'Media',redirectHasExtraInfo:false,redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store'}}});
 h.responseReceived({requestId:'r',hasExtraInfo:false,response:{url:URL_M,status:206,headers}});h.dataReceived({requestId:'r',dataLength:100});
 assert.equal(verdict(l).apiRedirectsOnly,false,'A cached API redirect is not current network proof');
 assert.equal(verdict(l).workerAudioValid,true,'The following uncached response is a distinct hop');
 assert.deepEqual(l.snapshot().responses.map(r=>r.servedFromCache),[true,false]);
});
test('partial media followed by an uncancelled failure cannot become successful transport proof',()=>{
 const outcomes=[];for(const cancelled of [false,true]) {
  const l=goodLedger({phase:()=> 'native-playback'});l.handlers.loadingFailed({requestId:'media',errorText:cancelled?'net::ERR_ABORTED':'net::ERR_CONNECTION_CLOSED',canceled:cancelled});
  outcomes.push(verdict(l).workerAudioValid);
  const row=l.snapshot().responses[1];if(cancelled)assert.equal(row.bodyBytes,100);
 }assert.deepEqual(outcomes,[false,true],'Only the actual cancellation may retain otherwise valid delivered audio evidence');
 const l=goodLedger({phase:()=> 'native-playback'});l.handlers.loadingFailed({requestId:'media',errorText:'net::ERR_CONNECTION_CLOSED',canceled:false});
 assert.deepEqual(Object.fromEntries(['failed','cancelled','failureKind','failurePhase'].map(k=>[k,l.snapshot().responses[1][k]])),
  {failed:true,cancelled:false,failureKind:'network',failurePhase:'native-playback'});
 assert.equal(verdict(l).apiRedirectsOnly,true,'A failed Worker hop does not invent an API failure');
});
test('uncancelled media failures before any response reject the correct origin even after other success',()=>{
 const failures=[];for(const [url,key,other]of [[URL_A,'apiRedirectsOnly','workerAudioValid'],[URL_M,'workerAudioValid','apiRedirectsOnly']]) {
  const l=goodLedger();l.handlers.requestWillBeSent({requestId:'failed',request:{url,method:'GET'},type:'Media'});
  l.handlers.loadingFailed({requestId:'failed',errorText:'net::ERR_CONNECTION_CLOSED',canceled:false});
  if(verdict(l)[key])failures.push(key);assert.equal(verdict(l)[other],true,'Failure attribution must stay with its actual hop origin');
 }assert.deepEqual(failures,[],'An earlier good response cannot conceal a later pre-response failure');
 const cancelled=goodLedger();cancelled.handlers.requestWillBeSent({requestId:'cancelled-before-response',request:{url:URL_M,method:'GET'},type:'Media'});
 cancelled.handlers.loadingFailed({requestId:'cancelled-before-response',errorText:'net::ERR_ABORTED',canceled:true});
 assert.equal(verdict(cancelled).workerAudioValid,true);assert.equal(verdict(cancelled).apiRedirectsOnly,true);
});
test('intentional offline phases do not silently waive shared failed-transport requirements',()=>{
 const l=goodLedger({phase:()=> 'autonoe-offline'});l.handlers.loadingFailed({requestId:'media',errorText:'net::ERR_INTERNET_DISCONNECTED',canceled:false});
 assert.equal(verdict(l).workerAudioValid,false,'Expected offline simulation needs an explicit caller-side acceptance scope');
 const row=l.snapshot().responses[1];assert.equal(row.failureKind,'offline');assert.equal(row.failurePhase,'autonoe-offline');
 assert.equal(l.snapshot().failures[0].cancelled,false);assert.equal(row.cancelled,false);
});
test('wire 304 cannot hide behind effective 200 and cached body bytes with false cache flags',()=>{
 const l=goodLedger(),h=l.handlers;
 h.requestWillBeSent({requestId:'revalidated',request:{url:URL_M,method:'GET'},type:'Media'});
 h.responseReceived({requestId:'revalidated',hasExtraInfo:true,response:{url:URL_M,status:200,headers}});
 h.dataReceived({requestId:'revalidated',dataLength:100});
 h.responseReceivedExtraInfo?.({requestId:'revalidated',statusCode:304,headers:{'Set-Cookie':'PRIVATE_EXTRA_COOKIE'}});
 assert.equal(verdict(l).workerAudioValid,false,'Revalidation is not fresh native media delivery');
 const r=l.snapshot().responses.at(-1);assert.equal(r.status,200);assert.equal(r.wireStatus,304);assert.equal(r.wireStatusMatched,false);
 assert.equal(JSON.stringify(l.snapshot()).includes('PRIVATE_EXTRA_COOKIE'),false);
});
test('numeric ExtraInfo pairs with the correct redirect and final hops in either event ordering',()=>{
 for(const order of ['early','late','mixed']) {
  const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER}),h=l.handlers;
  const extra=status=>h.responseReceivedExtraInfo?.({requestId:'order',statusCode:status,headers:{Authorization:'PRIVATE_EXTRA_HEADER'}});
  if(order==='early'){extra(307);extra(206);}
  h.requestWillBeSent({requestId:'order',request:{url:URL_A,method:'GET'},type:'Media'});
  if(order==='mixed')extra(307);
  h.requestWillBeSent({requestId:'order',request:{url:URL_M,method:'GET'},type:'Media',redirectHasExtraInfo:true,redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store'}}});
  h.responseReceived({requestId:'order',hasExtraInfo:true,response:{url:URL_M,status:206,headers}});h.dataReceived({requestId:'order',dataLength:100});
  if(order==='late'){extra(307);extra(206);}if(order==='mixed')extra(206);
  assert.deepEqual(l.snapshot().responses.map(r=>[r.wireStatus,r.wireStatusMatched,r.wireStatusSource]),[[307,true,'extra-info'],[206,true,'extra-info']],order);
  assert.equal(verdict(l).apiRedirectsOnly,true);assert.equal(verdict(l).workerAudioValid,true);
  assert.equal(JSON.stringify(l.snapshot()).includes('PRIVATE_EXTRA_HEADER'),false);
 }
 const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER}),h=l.handlers;
 h.requestWillBeSent({requestId:'mixed-flags',request:{url:URL_A,method:'GET'},type:'Media'});
 h.responseReceivedExtraInfo?.({requestId:'mixed-flags',statusCode:206});
 h.requestWillBeSent({requestId:'mixed-flags',request:{url:URL_M,method:'GET'},type:'Media',redirectHasExtraInfo:false,redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store'}}});
 h.responseReceived({requestId:'mixed-flags',hasExtraInfo:true,response:{url:URL_M,status:206,headers}});h.dataReceived({requestId:'mixed-flags',dataLength:100});
 assert.deepEqual(l.snapshot().responses.map(r=>[r.wireStatus,r.wireStatusSource]),[[307,'response-no-extra'],[206,'extra-info']]);
 assert.equal(verdict(l).workerAudioValid,true);
});
test('missing expected, invalid or over-budget ExtraInfo cannot certify fresh transport',()=>{
 const accepted=[];
 for(const mode of ['missing','mismatch','invalid','overflow','undeclared']) {
  const l=goodLedger({maxPendingExtraInfo:1}),h=l.handlers;
  if(mode==='overflow'){h.responseReceivedExtraInfo?.({requestId:'unused-one',statusCode:200});h.responseReceivedExtraInfo?.({requestId:'unused-two',statusCode:200});}
  else {
   h.requestWillBeSent({requestId:'bad-wire',request:{url:URL_M,method:'GET'},type:'Media'});
   h.responseReceived({requestId:'bad-wire',...(mode==='undeclared'?{}:{hasExtraInfo:true}),response:{url:URL_M,status:206,headers}});h.dataReceived({requestId:'bad-wire',dataLength:100});
   if(mode==='mismatch')h.responseReceivedExtraInfo?.({requestId:'bad-wire',statusCode:200});
   if(mode==='invalid')h.responseReceivedExtraInfo?.({requestId:'bad-wire',statusCode:'PRIVATE_INVALID_STATUS'});
  }
  if(verdict(l).workerAudioValid)accepted.push(mode);assert.equal(JSON.stringify(l.snapshot()).includes('PRIVATE_INVALID_STATUS'),false);
 }assert.deepEqual(accepted,[]);
});
test('unsupported or mismatched wire status cannot validate an application redirect',()=>{
 const accepted=[];for(const status of [200,304,308,503]) {
  const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER}),h=l.handlers;
  h.requestWillBeSent({requestId:'redirect-wire',request:{url:URL_A,method:'GET'},type:'Media'});
  h.requestWillBeSent({requestId:'redirect-wire',request:{url:URL_M,method:'GET'},type:'Media',redirectHasExtraInfo:true,redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store'}}});
  h.responseReceivedExtraInfo?.({requestId:'redirect-wire',statusCode:status});
  if(verdict(l).apiRedirectsOnly)accepted.push(status);
 }assert.deepEqual(accepted,[]);
});
test('unexpected ExtraInfo cannot be ignored after a completed no-extra response',()=>{
 for(const status of [206,304]) {
  const l=goodLedger();l.handlers.responseReceivedExtraInfo({requestId:'media',statusCode:status});
  assert.equal(verdict(l).workerAudioValid,false,'Unpaired protocol status is not complete wire evidence');
  assert.ok(l.snapshot().issues.includes('unmatched-extra-info'));
 }
});
test('observed request scope leaves allowed native requests unmodified and closes on a sanitized violation',async()=>{
 const handlers={},violations=[];let closed=0,routes=0;
 const context={on:(name,fn)=>{handlers[name]=fn;},route:()=>{routes++;throw Error('Routing must not be enabled');},close:async()=>{closed++;}};
 const settle=observeRequestScope(context,u=>u.origin===APP||u.origin===WORKER&&u.pathname.startsWith('/media/'),row=>violations.push(row));
 handlers.request({url:()=>URL_A});handlers.request({url:()=>URL_M});await settle();
 assert.equal(routes,0);assert.equal(closed,0);assert.deepEqual(violations,[]);
 handlers.request({url:()=>WORKER+'/bridge?grant=PRIVATE_GRANT'});await settle();
 assert.equal(closed,1);assert.deepEqual(violations,[{origin:WORKER,path:'/bridge'}]);
 assert.equal(JSON.stringify(violations).includes('PRIVATE_GRANT'),false);
});
test('failure diagnostics retain only known protocol enums and never raw messages or failed parameters',()=>{
 assert.deepEqual(safeFailureDetails({errorText:'net::ERR_CONTENT_LENGTH_MISMATCH PRIVATE_FAILURE',corsErrorStatus:{corsError:'AllowOriginMismatch',failedParameter:'PRIVATE_ORIGIN'},blockedReason:'corp-not-same-origin'}),
  {netErrorCode:'ERR_CONTENT_LENGTH_MISMATCH',corsError:'AllowOriginMismatch',blockedReason:'corp-not-same-origin'});
 const bad=safeFailureDetails({errorText:'PRIVATE_FAILURE',corsErrorStatus:{corsError:'PRIVATE_CORS',failedParameter:'PRIVATE_PARAMETER'},blockedReason:'PRIVATE_BLOCK'});
 assert.deepEqual(bad,{netErrorCode:'unknown',corsError:'unknown',blockedReason:'unknown'});
 assert.deepEqual(safeConsoleError('Access to https://host/audio?grant=PRIVATE_GRANT blocked by CORS policy; net::ERR_FAILED'),{consoleKind:'cors',netErrorCode:'ERR_FAILED'});
 assert.equal(JSON.stringify([bad,safeConsoleError('PRIVATE_CONSOLE')]).includes('PRIVATE'),false);
});
test('official QUIC and HTTP2 error families remain identifiable without echoing unknown secrets',()=>{
 const codes=['ERR_QUIC_PROTOCOL_ERROR','ERR_QUIC_HANDSHAKE_FAILED','ERR_QUIC_CERT_ROOT_NOT_KNOWN','ERR_QUIC_GOAWAY_REQUEST_CAN_BE_RETRIED',
  'ERR_INCOMPLETE_HTTP2_HEADERS','ERR_HTTP2_SERVER_REFUSED_STREAM','ERR_HTTP2_PING_FAILED','ERR_HTTP2_INADEQUATE_TRANSPORT_SECURITY','ERR_HTTP2_FLOW_CONTROL_ERROR',
  'ERR_HTTP2_FRAME_SIZE_ERROR','ERR_HTTP2_COMPRESSION_ERROR','ERR_HTTP2_RST_STREAM_NO_ERROR_RECEIVED','ERR_HTTP2_STREAM_CLOSED','ERR_CONTENT_DECODING_FAILED'];
 const rejected=codes.filter(code=>safeFailureDetails({errorText:'net::'+code+' PRIVATE_DIAGNOSTIC'}).netErrorCode!==code);
 assert.deepEqual(rejected,[],'Legitimate transport error names must not be lost as unknown');
 for(const value of ['net::ERR_PRIVATE_SECRET','net::ERR_QUIC_PROTOCOL_ERROR_PRIVATE_SECRET','PRIVATE_DIAGNOSTIC']) {
  const result=safeFailureDetails({errorText:value});assert.equal(result.netErrorCode,'unknown');assert.equal(JSON.stringify(result).includes('PRIVATE'),false);
 }
 assert.equal(safeConsoleError('net::ERR_QUIC_PROTOCOL_ERROR https://media/audio?grant=PRIVATE_DIAGNOSTIC').netErrorCode,'ERR_QUIC_PROTOCOL_ERROR');
 const l=goodLedger();l.handlers.loadingFailed({requestId:'media',canceled:false,errorText:'net::ERR_QUIC_PROTOCOL_ERROR'});assert.equal(verdict(l).workerAudioValid,false);
});
test('response protocol diagnostics expose a fixed transport enum without changing acceptance',()=>{
 for(const protocol of ['http/1.0','http/1.1','h2','h3','quic','PRIVATE_PROTOCOL',undefined]) {
  const l=goodLedger({responseFlags:{protocol}}),row=l.snapshot().responses[1];
  assert.equal(row.protocol,protocol==='PRIVATE_PROTOCOL'?'other':protocol===undefined?'unknown':protocol);assert.equal(verdict(l).workerAudioValid,true);
  assert.equal(JSON.stringify(row).includes('PRIVATE'),false);
 }
});
test('failed pre-response media retains unpaired numeric statuses and safe CORS diagnostics in either order',()=>{
 for(const late of [false,true]) {
  const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER}),h=l.handlers;
  h.requestWillBeSent({requestId:'cors',request:{url:URL_M,method:'GET'},type:'Media'});
  if(!late)h.responseReceivedExtraInfo({requestId:'cors',statusCode:403,headers:{'Set-Cookie':'PRIVATE_COOKIE'}});
  h.loadingFailed({requestId:'cors',canceled:false,errorText:'net::ERR_FAILED PRIVATE_RAW',corsErrorStatus:{corsError:'MissingAllowOriginHeader',failedParameter:'PRIVATE_HEADER'},blockedReason:'other'});
  if(late)h.responseReceivedExtraInfo({requestId:'cors',statusCode:403});
  const snapshot=l.snapshot();assert.deepEqual(snapshot.unresponded[0].unpairedWireStatuses,[403]);
  assert.equal(snapshot.failures[0].netErrorCode,'ERR_FAILED');assert.equal(snapshot.failures[0].corsError,'MissingAllowOriginHeader');assert.equal(snapshot.failures[0].blockedReason,'other');
  assert.equal(snapshot.unresponded[0].corsError,'MissingAllowOriginHeader');assert.equal(JSON.stringify(snapshot).includes('PRIVATE'),false);
  assert.equal(verdict(l).workerAudioValid,false);
 }
});
test('failed hosted runs always retain final sanitized protocol snapshots before detaching',async()=>{
 const l=goodLedger(),evidence={status:'failed',errors:[{kind:'check-failure',phase:'seeking'}]};let detached=false;
 const observer={snapshot:()=>{assert.equal(detached,false);return l.snapshot();},close:async()=>{detached=true;}};
 const context={close:async()=>l.handlers.loadingFailed({requestId:'media',canceled:false,errorText:'net::ERR_CONNECTION_RESET PRIVATE_RAW'})};
 await finalizeHostedEvidence(evidence,{contexts:[context],scopeGuards:[],networkObservers:[observer],pending:new Set(),browser:null,appOrigin:APP,workerOrigin:WORKER});
 assert.equal(evidence.status,'failed');assert.deepEqual(evidence.errors[0],{kind:'check-failure',phase:'seeking'});assert.equal(detached,true);
 assert.equal(evidence.nativeNetwork.responses[1].failed,true);assert.equal(evidence.nativeNetwork.failures[0].netErrorCode,'ERR_CONNECTION_RESET');assert.equal(JSON.stringify(evidence).includes('PRIVATE'),false);
});
test('late uncancelled transport failure makes final hosted status fail even without a Playwright error',async()=>{
 const l=goodLedger(),evidence={status:'verified',errors:[]};
 await finalizeHostedEvidence(evidence,{contexts:[{close:async()=>l.handlers.loadingFailed({requestId:'media',canceled:false,errorText:'net::ERR_FAILED'})}],scopeGuards:[],networkObservers:[{snapshot:l.snapshot,close:async()=>{}}],pending:new Set(),browser:null,appOrigin:APP,workerOrigin:WORKER});
 assert.equal(evidence.status,'failed');assert.ok(evidence.errors.some(e=>e.kind==='native-network-finalization'));assert.equal(evidence.nativeNetwork.responses[1].cancelled,false);
});
test('total hop bound includes redirects and invalid counts cannot authorize a proof',()=>{
 const l=createNetworkLedger({appOrigin:APP,workerOrigin:WORKER,maxRequests:1});l.handlers.requestWillBeSent({requestId:'one',request:{url:URL_A,method:'GET'},type:'Media'});l.handlers.requestWillBeSent({requestId:'one',request:{url:URL_M,method:'GET'},type:'Media',redirectHasExtraInfo:false,redirectResponse:{url:URL_A,status:307,headers:{'Content-Length':'0','Cache-Control':'private, no-store'}}});assert.deepEqual(l.snapshot().issues,['request-limit']);assert.equal(verdict(l).apiRedirectsOnly,false);
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
  let routeRegistrations=0;context.route=async()=>{routeRegistrations++;throw Error('Native media fixture must not enable request interception');};
  let externalRequests=0;
  const settleScope=observeRequestScope(context,u=>[appOrigin,mediaOrigin].includes(u.origin),()=>{externalRequests++;});
  context.on('response',response=>{const task=(async()=>{const u=new URL(response.url());playwrightRows.push({origin:u.origin,path:u.pathname,status:response.status(),contentType:await response.headerValue('content-type')||''});})();pending.add(task);task.finally(()=>pending.delete(task));});
  const page=await context.newPage();page.route=async()=>{routeRegistrations++;throw Error('Native media fixture must not intercept page requests');};observer=await observeNativeNetwork(page,{appOrigin,workerOrigin:mediaOrigin,phase:()=> 'native-loopback'});
  await page.goto(appOrigin,{waitUntil:'domcontentloaded'});
  for(let i=0;i<24;i++)await page.evaluate(async({url,at})=>{const a=document.querySelector('#a');a.pause();a.src=url;a.load();await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('Fixture metadata timeout')),5000);a.addEventListener('loadedmetadata',()=>{clearTimeout(t);resolve();},{once:true});a.addEventListener('error',()=>{clearTimeout(t);reject(Error('Fixture decode failure'));},{once:true});});a.currentTime=at;await a.play();await new Promise(r=>setTimeout(r,35));a.pause();},{url:appOrigin+'/api/assets/'+(i%2?'charon':'autonoe')+'.mp3',at:i%3===0?12:0});
  await page.evaluate(()=>{const a=document.querySelector('#a');a.pause();a.removeAttribute('src');a.load();});await new Promise(r=>setTimeout(r,100));await context.close();await settleScope();await Promise.allSettled([...pending]);
  assert.equal(routeRegistrations,0);assert.equal(externalRequests,0);
  const snapshot=observer.snapshot(),result=mediaNetworkVerdict(snapshot,{appOrigin,workerOrigin:mediaOrigin});
  assert.equal(result.apiRedirectsOnly,true);assert.equal(result.workerAudioValid,true);assert.equal(result.apiBodyBytes,0);
  const appWire=wire.filter(r=>r.origin===appOrigin);assert.equal(appWire.length,24);assert.ok(appWire.every(r=>r.status===307&&r.bodyBytes===0));
  assert.equal(result.apiResponses,appWire.length);assert.ok(wire.some(r=>r.origin===mediaOrigin&&r.status===206&&r.bodyBytes>0));
  const anomalies=playwrightRows.filter(r=>r.origin===appOrigin&&r.path.endsWith('.mp3')&&r.status!==307);
  const output=fs.mkdtempSync(path.join(os.tmpdir(),'lumen-native-redirect-'));fs.chmodSync(output,0o700);
  const proof={scope:'tiny-dual-loopback-installed-Chrome-native-MP3-redirect',externalRequests:false,requestInterceptionUsed:false,routeRegistrations,requestScopeGuard:'observe-and-close-context',credentialRead:false,productionAudioRead:false,
   chromeVersion:browser.version(),fixtureAudioBytes:AUDIO.length,fixtureAudioSha256:crypto.createHash('sha256').update(AUDIO).digest('hex'),
   apiWireResponses:appWire.length,apiWireBodyBytes:0,playwrightMisattributions:anomalies,verdict:result,network:snapshot};
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(proof,null,2)+'\n',{mode:0o600,flag:'wx'});
  console.log(JSON.stringify({scope:proof.scope,apiWireResponses:appWire.length,playwrightMisattributions:anomalies.length,verdict:result,output}));
 }finally{if(observer)await observer.close();if(browser)await browser.close();await Promise.all([new Promise(r=>app.close(r)),new Promise(r=>media.close(r))]);}
});
