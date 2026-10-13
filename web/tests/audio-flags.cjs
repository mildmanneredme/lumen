'use strict';
const {test,before}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {createPrivateReader}=require('../server/private-access.cjs');
const {createR2FlagStore}=require('../server/audio-flags.cjs');
const {createCloudflareBridge}=require('../server/cloudflare-bridge.cjs');
const {createNodeHandler}=require('../server/node-handler.cjs');
let createWorker;before(async()=>({createWorker}=await import('../cloudflare/worker.mjs')));
const origin='https://lumen.example',workerOrigin='https://media.example',now=1_800_000_000_000;
const ownerCode=Buffer.alloc(32,7).toString('base64url'),readerCode='acorn-blend-choir-drift-eager-flock';
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const id='12345678-1234-4234-8234-123456789abc';
function fixture() {
  const calls=[],objects=new Map(),audio=Buffer.from('fixture audio'),audioHash=digest(audio);
  const audioPath='audio/charon/chapter-001.'+audioHash+'.mp3';
  const chapter={schemaVersion:2,bookId:'lumen',manuscriptVersion:'v6',chapterId:'chapter-001',duration:180,
    audio:{src:origin+'/api/assets/'+audioPath,sha256:audioHash,narratorId:'charon'},paragraphs:[
      {text:'First sentence.  Second sentence.',sentences:[
        {id:'v6:chapter-001:p001-s01',text:'First sentence.',textStart:0,textEnd:15,start:0,end:4},
        {id:'v6:chapter-001:p001-s02',text:'Second sentence.',textStart:17,textEnd:33,start:4,end:8}]}]};
  const chapterBytes=Buffer.from(JSON.stringify(chapter)),chapterPath='tracks/chapter-001/charon.'+digest(chapterBytes)+'.json';
  const manifest={schemaVersion:1,releaseId:'release-one',bookId:'lumen',manuscriptVersion:'v6',
    tracks:[{id:'chapter-001',recordings:{charon:{status:'ready',audioSha256:audioHash,url:origin+'/api/assets/'+chapterPath}}}]};
  const manifestBytes=Buffer.from(JSON.stringify(manifest)),manifestPath='tracks/chapter-000/book-manifest.'+digest(manifestBytes)+'.json';
  const rows=[[audioPath,audio,'audio/mpeg'],[chapterPath,chapterBytes,'application/json'],[manifestPath,manifestBytes,'application/json']];
  const index={schemaVersion:1,releaseId:'release-one',appOrigin:origin,manifestPath,assets:rows.map(([logical,bytes,contentType])=>
    ({path:logical,blobPath:'lumen/releases/release-one/assets/'+logical,bytes:bytes.length,sha256:digest(bytes),contentType}))};
  for(const [logical,bytes,contentType]of rows){const key=index.assets.find(row=>row.path===logical).blobPath;
    objects.set(key,{key,bytes,size:bytes.length,httpMetadata:{contentType},customMetadata:{sha256:digest(bytes)}});}
  const indexBytes=Buffer.from(JSON.stringify(index)),indexHash=digest(indexBytes),indexPath='lumen/releases/release-one/server-index.'+indexHash+'.json';
  objects.set(indexPath,{key:indexPath,bytes:indexBytes,size:indexBytes.length,httpMetadata:{contentType:'application/json'},customMetadata:{sha256:indexHash}});
  const bucket={async get(key){calls.push({method:'get',key});const row=objects.get(key);return row?{...row,body:new Response(row.bytes).body}:null;},
    async put(key,bytes,options){calls.push({method:'put',key,options});assert.equal(options.onlyIf.get('If-None-Match'),'*');
      if(objects.has(key))return null;assert.equal(digest(bytes),options.sha256);
      const row={key,bytes:Buffer.from(bytes),size:bytes.length,httpMetadata:options.httpMetadata,customMetadata:options.customMetadata};objects.set(key,row);return row;},
    async list(options){calls.push({method:'list',options});return {objects:[...objects.values()].filter(row=>row.key.startsWith(options.prefix)).sort((a,b)=>a.key.localeCompare(b.key)),truncated:false};}};
  const env={LUMEN_APP_ORIGIN:origin,LUMEN_WORKER_ORIGIN:workerOrigin,LUMEN_SESSION_SECRET:Buffer.alloc(32,9).toString('base64url'),
    LUMEN_BRIDGE_KEY:Buffer.alloc(32,10).toString('base64url'),LUMEN_MEDIA_SECRET:Buffer.alloc(32,11).toString('base64url'),
    LUMEN_INVITES_JSON:JSON.stringify([{id:'readers',tokenHash:digest(readerCode),version:1,expiresAt:2_100_000_000,revoked:false},
      {id:'owner',tokenHash:digest(ownerCode),version:1,expiresAt:2_100_000_000,revoked:false,role:'owner'}]),
    LUMEN_RELEASE_INDEX_PATH:indexPath,LUMEN_RELEASE_INDEX_SHA256:indexHash,LUMEN_R2:bucket};
  const input={id,releaseId:'release-one',chapterId:'chapter-001',narratorId:'charon',audioSha256:audioHash,start:1,end:7,note:'Wrong pronunciation'};
  const worker=createWorker({now:()=>now});
  const bridge=(pathname,method='GET',headers={},body)=>worker.fetch(new Request(workerOrigin+'/bridge',{method:'POST',
    headers:{'X-Lumen-Bridge-Key':env.LUMEN_BRIDGE_KEY,'Content-Type':'application/json'},
    body:JSON.stringify({url:origin+pathname,method,headers,...(body===undefined?{}:{body:JSON.stringify(body)})})}),env);
  const getBlob=async key=>{const row=await bucket.get(key);return {statusCode:200,stream:row.body,headers:new Headers({'Content-Length':String(row.size)})};};
  const reader=(store=createR2FlagStore(bucket))=>createPrivateReader({env,now:()=>now,loadIndex:async()=>{calls.push({method:'index'});return index;},getBlob,flagStore:store});
  return {objects,calls,bucket,env,input,index,indexPath,indexBytes,rows,chapterBytes,manifestBytes,bridge,reader};
}
async function login(f,code=ownerCode) {
  const response=await f.bridge('/api/session','POST',{origin,'content-type':'application/json'},{invite:code});assert.equal(response.status,200);
  return {cookie:response.headers.get('set-cookie').split(';')[0],state:await response.json()};
}
const post=(f,cookie,input=f.input,extra={})=>f.bridge('/api/audio-flags','POST',{origin,cookie,'content-type':'application/json',...extra},input);
test('only the separate live owner grant receives the capability; shared codes remain readers',async()=>{
  const f=fixture(),owner=await login(f),reader=await login(f,readerCode);
  assert.deepEqual(owner.state,{authenticated:true,canFlagAudio:true});assert.deepEqual(reader.state,{authenticated:true});
  assert.deepEqual(await(await f.bridge('/api/session','GET',{cookie:owner.cookie})).json(),owner.state);
  const invites=JSON.parse(f.env.LUMEN_INVITES_JSON);delete invites[1].role;f.env.LUMEN_INVITES_JSON=JSON.stringify(invites);
  assert.deepEqual(await(await f.bridge('/api/session','GET',{cookie:owner.cookie})).json(),{authenticated:true});
  assert.equal((await post(f,owner.cookie)).status,403);assert.equal(f.calls.length,0);
});
test('guests shared sessions forged role claims and origin failures are rejected before all storage',async()=>{
  const f=fixture(),owner=await login(f),reader=await login(f,readerCode);
  const [payload]=reader.cookie.split('=')[1].split('.'),claims={...JSON.parse(Buffer.from(payload,'base64url').toString()),role:'owner',canFlagAudio:true};
  const changed=Buffer.from(JSON.stringify(claims)).toString('base64url'),signed=crypto.createHmac('sha256',Buffer.from(f.env.LUMEN_SESSION_SECRET,'base64url')).update(changed).digest('base64url');
  const forged='__Host-lumen_session='+changed+'.'+signed;
  for(const cookie of [undefined,reader.cookie,forged,'__Host-lumen_session='+changed+'.'+'x'.repeat(43)]) {
    for(const method of ['GET','POST'])assert.ok([401,403].includes((await f.bridge('/api/audio-flags',method,{origin,'content-type':'application/json',...(cookie?{cookie}:{})},method==='POST'?f.input:undefined)).status));
  }
  for(const extra of [{origin:'https://evil.example'},{origin:'null'},{origin:''},{host:'evil.example'},{'sec-fetch-site':'cross-site'}])assert.equal((await post(f,owner.cookie,f.input,extra)).status,403);
  assert.equal(f.calls.length,0);
});
test('removal revocation version expiry and role downgrade invalidate owner flags before reads or writes',async()=>{
  for(const change of [invite=>invite.revoked=true,invite=>invite.version++,invite=>invite.expiresAt=1_700_000_000,invite=>invite.role='reader',()=>null]) {
    const f=fixture(),owner=await login(f),invites=JSON.parse(f.env.LUMEN_INVITES_JSON),value=change(invites[1]);
    f.env.LUMEN_INVITES_JSON=JSON.stringify(value===null?[invites[0]]:invites);
    for(const method of ['GET','POST'])assert.ok([401,403].includes((await f.bridge('/api/audio-flags',method,{origin,cookie:owner.cookie,'content-type':'application/json'},method==='POST'?f.input:undefined)).status));
    assert.equal(f.calls.length,0);
  }
});
test('valid flags persist exact recording clock and server-derived canonical prose; retries keep one object',async()=>{
  const f=fixture(),owner=await login(f),saved=await post(f,owner.cookie);assert.equal(saved.status,201);
  assert.equal(saved.headers.get('cache-control'),'private, no-store');const {flag}=await saved.json();
  assert.equal(flag.text,'First sentence.  Second sentence.');assert.deepEqual(flag.sentenceIds,['v6:chapter-001:p001-s01','v6:chapter-001:p001-s02']);
  assert.equal(flag.manuscriptVersion,'v6');assert.equal(flag.audioSha256,f.input.audioSha256);assert.equal(flag.start,1);assert.equal(flag.end,7);
  assert.ok(f.calls.filter(row=>row.method==='put').every(row=>row.key.startsWith('lumen/owner-audio-flags/v1/')));
  assert.ok(!f.calls.filter(row=>row.method==='get').some(row=>row.key.endsWith('.mp3')));
  const repeated=await post(f,owner.cookie);assert.equal(repeated.status,201);assert.deepEqual((await repeated.json()).flag,flag);
  assert.equal([...f.objects.keys()].filter(key=>key.startsWith('lumen/owner-audio-flags/')).length,1);
  assert.equal((await post(f,owner.cookie,{...f.input,note:'different'})).status,409);
  const listed=await f.bridge('/api/audio-flags','GET',{cookie:owner.cookie});assert.equal(listed.status,200);assert.deepEqual(await listed.json(),{flags:[flag]});
});
test('concurrent separate flags survive and concurrent retries cannot overwrite an original record',async()=>{
  const f=fixture(),owner=await login(f),second={...f.input,id:'22345678-1234-4234-8234-123456789abc'};
  const responses=await Promise.all([post(f,owner.cookie),post(f,owner.cookie,second),post(f,owner.cookie)]);
  assert.deepEqual(responses.map(value=>value.status),[201,201,201]);
  assert.equal((await(await f.bridge('/api/audio-flags','GET',{cookie:owner.cookie})).json()).flags.length,2);
});
test('stale release and audio hashes reject with conflicts and malformed snippet bodies reject',async()=>{
  const f=fixture(),owner=await login(f);
  for(const input of [{...f.input,releaseId:'old-release'},{...f.input,audioSha256:'0'.repeat(64)}])assert.equal((await post(f,owner.cookie,input)).status,409);
  for(const change of [{start:-1},{end:1},{end:181},{start:0,end:121},{start:'1'},{note:'x'.repeat(1001)},
    {id:'../arbitrary'},{audioSrc:'https://evil.example/x'},{chapterId:'chapter-999'},{narratorId:'unavailable'},{canFlagAudio:true}])assert.equal((await post(f,owner.cookie,{...f.input,...change})).status,400);
  assert.equal(f.calls.filter(row=>row.method==='put').length,0);
});
test('non-string UUIDs and recording hashes cannot create durable flags or poison listing',async()=>{
  const f=fixture(),owner=await login(f);
  for(const value of [[id],[[id]],{toString:id},null,42,true]) {
    assert.equal((await post(f,owner.cookie,{...f.input,id:value})).status,400);
  }
  for(const value of [[f.input.audioSha256],{toString:f.input.audioSha256},null,42,true]) {
    assert.equal((await post(f,owner.cookie,{...f.input,audioSha256:value})).status,400);
  }
  assert.equal(f.calls.filter(row=>row.method==='put').length,0);
  const listed=await f.bridge('/api/audio-flags','GET',{cookie:owner.cookie});
  assert.equal(listed.status,200);assert.deepEqual(await listed.json(),{flags:[]});
});
test('stored-record validation rejects array UUIDs before issuing R2 writes',async()=>{
  const f=fixture(),owner=await login(f),first=await post(f,owner.cookie),record=(await first.json()).flag;
  const malformed={...record,id:[record.id]};
  const {createdAt,bindingSha256,...identity}=malformed;malformed.bindingSha256=digest(JSON.stringify(identity));
  const before=f.calls.filter(row=>row.method==='put').length;
  await assert.rejects(()=>createR2FlagStore(f.bucket).save(malformed),error=>error.status===503);
  assert.equal(f.calls.filter(row=>row.method==='put').length,before);
  assert.deepEqual(await createR2FlagStore(f.bucket).list(),[record]);
});
test('all manifest and chapter bytes remain physically hash-bound before flags are stored',async()=>{
  for(const select of [f=>f.index.assets.find(row=>row.path===f.index.manifestPath),f=>f.index.assets.find(row=>row.path.startsWith('tracks/chapter-001/'))]) {
    const f=fixture(),owner=await login(f),asset=select(f),row=f.objects.get(asset.blobPath);row.bytes=Buffer.alloc(row.size);
    assert.equal((await post(f,owner.cookie)).status,503);assert.equal(f.calls.filter(row=>row.method==='put').length,0);
  }
});
test('unmeasured timing gaps retain the exact clip without inventing manuscript positions',async()=>{
  const f=fixture(),owner=await login(f),response=await post(f,owner.cookie,{...f.input,start:50,end:55});
  assert.equal(response.status,201);const {flag}=await response.json();assert.deepEqual(flag.sentenceIds,[]);assert.equal(flag.text,'');assert.equal(flag.textStatus,'unavailable');
});
test('missing fallback storage and failed or corrupted durable writes fail closed',async()=>{
  const f=fixture(),owner=await login(f),request=()=>new Request(origin+'/api/audio-flags',{method:'POST',headers:{Origin:origin,Cookie:owner.cookie,'Content-Type':'application/json'},body:JSON.stringify(f.input)});
  assert.equal((await f.reader(null)(request())).status,503);assert.equal(f.calls.length,0);
  for(const put of [async()=>{throw Error('private failure');},async()=>null,async(key,bytes,options)=>{
    const row={key,bytes:Buffer.alloc(bytes.length),size:bytes.length,httpMetadata:options.httpMetadata,customMetadata:options.customMetadata};f.objects.set(key,row);return row;}]) {
    f.bucket.put=put;assert.equal((await f.reader()(request())).status,503);
  }
});
test('list pagination uses timestamps rather than UUID ordering and returns the latest 100',async()=>{
  const f=fixture(),owner=await login(f),response=await post(f,owner.cookie),original=(await response.json()).flag;
  const base='lumen/owner-audio-flags/v1/',rows=[];
  for(let i=0;i<105;i++) {
    const record={...original,id:i.toString(16).padStart(8,'0')+'-1234-4234-8234-123456789abc',createdAt:new Date(now+i*1000).toISOString()};
    const {createdAt,bindingSha256,...identity}=record;record.bindingSha256=digest(JSON.stringify(identity));const bytes=Buffer.from(JSON.stringify(record));
    const row={key:base+record.id+'.json',bytes,size:bytes.length,httpMetadata:{contentType:'application/json'},customMetadata:{sha256:digest(bytes),createdAt:record.createdAt}};f.objects.set(row.key,row);rows.push(row);
  }
  f.bucket.list=async options=>options.cursor?{objects:rows.slice(60),truncated:false}:{objects:rows.slice(0,60),truncated:true,cursor:'second'};
  const flags=await createR2FlagStore(f.bucket).list();assert.equal(flags.length,100);assert.equal(flags[0].createdAt,new Date(now+104000).toISOString());assert.equal(flags.at(-1).createdAt,new Date(now+5000).toISOString());
  const listed=await f.bridge('/api/audio-flags','GET',{cookie:owner.cookie});assert.equal(listed.status,200);assert.deepEqual(await listed.json(),{flags});
});
test('large valid latest flags stay within the Cloudflare bridge JSON budget without dropping newer records',async()=>{
  const f=fixture(),owner=await login(f),saved=await post(f,owner.cookie),original=(await saved.json()).flag;
  const base='lumen/owner-audio-flags/v1/',records=[];
  f.objects.delete(base+original.id+'.json');
  for(let i=0;i<100;i++) {
    const record={...original,id:i.toString(16).padStart(8,'0')+'-1234-4234-8234-123456789abc',
      createdAt:new Date(now+i*1000).toISOString(),text:'文'.repeat(20000)+'\n"\\'.repeat(10000)};
    const {createdAt,bindingSha256,...identity}=record;record.bindingSha256=digest(JSON.stringify(identity));
    const bytes=Buffer.from(JSON.stringify(record));assert.ok(bytes.length<=128*1024);
    const row={key:base+record.id+'.json',bytes,size:bytes.length,httpMetadata:{contentType:'application/json'},
      customMetadata:{sha256:digest(bytes),createdAt:record.createdAt}};
    f.objects.set(row.key,row);records.unshift(record);
  }
  const worker=createWorker({now:()=>now});
  const bridge=createCloudflareBridge({env:f.env,fetch:(url,init)=>worker.fetch(new Request(url,init),f.env)});
  const response=await bridge(new Request(origin+'/api/audio-flags',{headers:{Cookie:owner.cookie}}));
  assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'private, no-store');
  const bytes=Buffer.from(await response.arrayBuffer());assert.ok(bytes.length<=2*1024*1024);
  assert.equal(response.headers.get('content-length'),String(bytes.length));
  const payload=JSON.parse(bytes);assert.equal(payload.truncated,true);assert.ok(payload.flags.length>0&&payload.flags.length<100);
  assert.deepEqual(payload.flags,records.slice(0,payload.flags.length));
  assert.ok(Buffer.byteLength(JSON.stringify({flags:[...payload.flags,records[payload.flags.length]],truncated:true}))>2*1024*1024);
});
test('Node handler and Cloudflare bridge preserve bounded owner POST bodies and reject CSRF before forwarding',async()=>{
  const f=fixture(),owner=await login(f);let captured;
  const bridge=createCloudflareBridge({env:f.env,fetch:async(url,init)=>{captured=JSON.parse(init.body);return new Response('{"flag":{"id":"fixture"}}',{headers:{'Content-Type':'application/json'}});}});
  const req=()=>new Request(origin+'/api/audio-flags',{method:'POST',headers:{Origin:origin,Cookie:owner.cookie,'Content-Type':'application/json'},body:JSON.stringify(f.input)});
  assert.equal((await bridge(req())).status,200);assert.deepEqual(JSON.parse(captured.body),f.input);assert.equal(captured.method,'POST');
  captured=undefined;assert.equal((await bridge(new Request(origin+'/api/audio-flags',{method:'POST',headers:{Cookie:owner.cookie,'Content-Type':'application/json'},body:'{}'}))).status,403);assert.equal(captured,undefined);
  let delivered;const server=http.createServer(createNodeHandler({env:f.env,loadReader:async()=>async request=>{
    delivered=await request.json();return new Response('{"ok":true}',{headers:{'Content-Type':'application/json'}});}}));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {
    const url='http://127.0.0.1:'+server.address().port+'/api/audio-flags';
    assert.equal((await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(f.input)})).status,200);assert.deepEqual(delivered,f.input);
    delivered=undefined;assert.equal((await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:'x'.repeat(4097)})).status,413);assert.equal(delivered,undefined);
  }finally{await new Promise(resolve=>server.close(resolve));}
});
test('actual workerd R2 conditional owner flags remain private immutable and retry-safe',{timeout:30000},async()=>{
  const {Miniflare,Log,LogLevel,convertV4MiniflareOptions}=require('../cloudflare/node_modules/miniflare');
  const bundle=path.resolve(__dirname,'../cloudflare/.cloudflare/output/v0/workers/default/bundle/worker.js');assert.ok(fs.existsSync(bundle));
  const f=fixture(),{LUMEN_R2,...env}=f.env,options={modules:true,scriptPath:bundle,compatibilityDate:'2026-10-10',compatibilityFlags:['nodejs_compat'],
    host:'127.0.0.1',port:0,https:true,bindings:env,r2Buckets:{LUMEN_R2:'flags-fixture-only'},log:new Log(LogLevel.NONE)};
  const mf=new Miniflare(convertV4MiniflareOptions(options));
  try {
    const ready=await mf.ready;env.LUMEN_WORKER_ORIGIN=ready.origin;options.port=Number(ready.port);options.bindings={...env};
    await mf.setOptions(convertV4MiniflareOptions(options));await mf.ready;const bucket=await mf.getR2Bucket('LUMEN_R2');
    for(const row of f.objects.values())await bucket.put(row.key,row.bytes,{sha256:Buffer.from(row.customMetadata.sha256,'hex'),httpMetadata:row.httpMetadata,customMetadata:row.customMetadata});
    const request=(pathname,method='GET',headers={},body)=>mf.dispatchFetch(env.LUMEN_WORKER_ORIGIN+'/bridge',{method:'POST',
      headers:{'X-Lumen-Bridge-Key':env.LUMEN_BRIDGE_KEY,'Content-Type':'application/json'},
      body:JSON.stringify({url:origin+pathname,method,headers,...(body===undefined?{}:{body:JSON.stringify(body)})})});
    const exchange=await request('/api/session','POST',{origin,'content-type':'application/json'},{invite:ownerCode});assert.equal(exchange.status,200);
    assert.equal((await exchange.json()).canFlagAudio,true);const cookie=exchange.headers.get('set-cookie').split(';')[0];
    const save=()=>request('/api/audio-flags','POST',{origin,cookie,'content-type':'application/json'},f.input);
    const first=await save();assert.equal(first.status,201);const {flag}=await first.json();
    const key='lumen/owner-audio-flags/v1/'+id+'.json',version=(await bucket.head(key)).version;
    const repeated=await save();assert.equal(repeated.status,201);assert.deepEqual((await repeated.json()).flag,flag);assert.equal((await bucket.head(key)).version,version);
    assert.equal((await request('/api/audio-flags','POST',{origin,cookie,'content-type':'application/json'},{...f.input,note:'changed'})).status,409);
    const listed=await request('/api/audio-flags','GET',{cookie});assert.equal(listed.headers.get('cache-control'),'private, no-store');assert.deepEqual((await listed.json()).flags,[flag]);
    assert.equal((await request('/api/audio-flags')).status,401);
    assert.equal((await request('/api/assets/owner-audio-flags/v1/'+id+'.json','GET',{cookie})).status,404);
  }finally{await mf.dispose();}
});
