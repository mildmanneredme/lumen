'use strict';
const {test,before}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
let createWorker;
before(async()=>({createWorker}=await import('../cloudflare/worker.mjs')));
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const origin='https://lumen.example',workerOrigin='https://media.example';
const code='acorn-blend-choir-drift-eager-flock';
const audio=Buffer.from('0123456789abcdefghijklmnopqrstuvwxyz');
const painting=Buffer.from('tiny private webp');
const manuscript=Buffer.from('{"bookId":"lumen","privateText":"fixture"}\n');
const manifestPath=`tracks/chapter-000/book-manifest.${digest(manuscript)}.json`;
const audioPath=`audio/autonoe/chapter-001.${digest(audio)}.mp3`;
const artPath=`art/scene-one.${digest(painting)}.webp`;
function fixture() {
  let clock=1_800_000_000_000;
  const rows=[[manifestPath,manuscript,'application/json'],[audioPath,audio,'audio/mpeg'],[artPath,painting,'image/webp']];
  const index={schemaVersion:1,releaseId:'release-one',appOrigin:origin,manifestPath,
    assets:rows.map(([path,bytes,contentType])=>({path,blobPath:`lumen/release-one/${path}`,sha256:digest(bytes),bytes:bytes.length,contentType}))};
  const indexBytes=Buffer.from(JSON.stringify(index)),indexHash=digest(indexBytes);
  const indexPath=`lumen/releases/release-one/server-index.${indexHash}.json`;
  const objects=new Map(rows.map(([path,bytes,contentType])=>[index.assets.find(row=>row.path===path).blobPath,{bytes,size:bytes.length,httpMetadata:{contentType},customMetadata:{sha256:digest(bytes)}}]));
  objects.set(indexPath,{bytes:indexBytes,size:indexBytes.length,httpMetadata:{contentType:'application/json'},customMetadata:{sha256:indexHash}});
  const calls=[];
  const bucket={async get(key,options={}) {
    calls.push({key,options}); const object=objects.get(key);if(!object)return null;
    const range=options.range;
    const body=range?object.bytes.subarray(range.offset,range.offset+range.length):object.bytes;
    return {...object,body:new Response(body).body,...(range?{range:{...range}}:{})};
  }};
  const env={LUMEN_APP_ORIGIN:origin,LUMEN_WORKER_ORIGIN:workerOrigin,
    LUMEN_SESSION_SECRET:Buffer.alloc(32,9).toString('base64url'),
    LUMEN_BRIDGE_KEY:Buffer.alloc(32,10).toString('base64url'),
    LUMEN_MEDIA_SECRET:Buffer.alloc(32,11).toString('base64url'),
    LUMEN_INVITES_JSON:JSON.stringify([{id:'shared-readers',tokenHash:digest(code),version:1,expiresAt:1_900_000_000,revoked:false}]),
    LUMEN_RELEASE_INDEX_PATH:indexPath,LUMEN_RELEASE_INDEX_SHA256:indexHash,LUMEN_R2:bucket};
  const worker=createWorker({now:()=>clock});
  const raw=(url,method='GET',headers={},body)=>worker.fetch(new Request(url,{method,headers,...(body!==undefined?{body}:{})}),env);
  const bridge=(path,method='GET',headers={},body)=>{
    const envelope=JSON.stringify({url:origin+path,method,headers,...(body!==undefined?{body}:{})});
    return raw(workerOrigin+'/bridge',method==='HEAD'?'HEAD':'POST',
      {'X-Lumen-Bridge-Key':env.LUMEN_BRIDGE_KEY,'Content-Type':'application/json',...(method==='HEAD'?{'X-Lumen-Request':Buffer.from(envelope).toString('base64url')}:{})},method==='HEAD'?undefined:envelope);
  };
  const media=(url,method='GET',headers={})=>raw(url,method,headers);
  return {env,index,indexPath,objects,calls,bridge,media,raw,worker,setNow:value=>clock=value};
}
async function login(f) {
  const response=await f.bridge('/api/session','POST',{origin,'content-type':'application/json'},JSON.stringify({invite:code}));
  assert.equal(response.status,200);return response.headers.get('set-cookie').split(';')[0];
}
async function grant(f,path=audioPath,cookie) {
  cookie??=await login(f);const response=await f.bridge('/api/assets/'+path,'GET',{cookie});
  assert.equal(response.status,307);assert.equal(await response.text(),'');
  return {url:response.headers.get('location'),cookie,response};
}
function signedGrant(url,env,mutate) {
  const result=new URL(url),[payload]=result.searchParams.get('grant').split('.');
  const claims=JSON.parse(Buffer.from(payload,'base64url').toString());mutate(claims);
  const next=Buffer.from(JSON.stringify(claims)).toString('base64url');
  result.searchParams.set('grant',next+'.'+crypto.createHmac('sha256',Buffer.from(env.LUMEN_MEDIA_SECRET,'base64url')).update(next).digest('base64url'));
  return result.href;
}
test('private bridge rejects absent/wrong service keys before configuration or R2 reads',async()=>{
  const f=fixture();f.env.LUMEN_INVITES_JSON='bad';
  for(const key of [undefined,'wrong',Buffer.alloc(32,8).toString('base64url')]) {
    const response=await f.raw(workerOrigin+'/bridge','POST',{'content-type':'application/json',...(key?{'X-Lumen-Bridge-Key':key}:{})},'{}');
    assert.equal(response.status,401);assert.equal(f.calls.length,0);
  }
});
test('bridge login preserves secure same-origin cookie and JSON is hash-checked and private',async()=>{
  const f=fixture(),cookie=await login(f);
  for(const path of ['/api/book','/api/assets/'+manifestPath]) {
    const response=await f.bridge(path,'GET',{cookie});assert.equal(response.status,200);
    assert.equal(await response.text(),manuscript.toString());assert.equal(response.headers.get('cache-control'),'private, no-store');
    assert.equal(response.headers.get('cross-origin-resource-policy'),'same-origin');assert.equal(response.headers.get('location'),null);
  }
  assert.ok(cookie.startsWith('__Host-lumen_session='));
});
test('guests and forged same-origin headers never read index or media',async()=>{
  const f=fixture();for(const path of ['/api/book','/api/assets/'+audioPath]) {
    assert.equal((await f.bridge(path)).status,401);assert.equal(f.calls.length,0);
  }
  const cookie=await login(f);
  for(const headers of [{host:'evil.example'},{origin:'https://evil.example'},{'sec-fetch-site':'cross-site'}]) {
    assert.equal((await f.bridge('/api/book','GET',{cookie,...headers})).status,403);assert.equal(f.calls.length,0);
  }
});
test('media redirect contains only a separate scoped grant and does not read or buffer MP3 bytes',async()=>{
  const f=fixture(),{url,cookie,response}=await grant(f);
  assert.equal(new URL(url).origin,workerOrigin);assert.equal(new URL(url).pathname,'/media/'+audioPath);
  assert.equal(response.headers.get('cache-control'),'private, no-store');assert.ok(!url.includes(cookie));assert.ok(!url.includes(code));
  assert.deepEqual(f.calls.map(row=>row.key),[f.indexPath]);
  const token=new URL(url).searchParams.get('grant'),payload=JSON.parse(Buffer.from(token.split('.')[0],'base64url').toString());
  assert.equal(payload.path,audioPath);assert.equal(payload.origin,origin);assert.equal(payload.releaseId,'release-one');
  assert.equal(payload.sha256,digest(audio));assert.equal(payload.aud,workerOrigin);assert.equal(payload.i,'shared-readers');assert.equal(payload.v,1);
  const cookieClaims=JSON.parse(Buffer.from(cookie.split('=')[1].split('.')[0],'base64url').toString());assert.equal(payload.exp,cookieClaims.exp);
  for(const value of Object.values(payload))assert.ok(value!==cookie&&value!==code);
});
test('closed open suffix and oversized-end media ranges stream exact bytes directly from R2',async()=>{
  const f=fixture(),{url}=await grant(f);
  for(const [range,start,end] of [['bytes=2-6',2,6],['bytes=20-',20,35],['bytes=-5',31,35],['bytes=30-999',30,35]]) {
    const response=await f.media(url,'GET',{Range:range,Origin:origin});assert.equal(response.status,206);
    assert.equal(response.headers.get('content-range'),`bytes ${start}-${end}/36`);assert.equal(response.headers.get('content-length'),String(end-start+1));
    assert.equal(await response.text(),audio.subarray(start,end+1).toString());assert.deepEqual(f.calls.at(-1).options.range,{offset:start,length:end-start+1});
    assert.equal(response.headers.get('access-control-allow-origin'),origin);assert.equal(response.headers.get('cross-origin-resource-policy'),'cross-origin');
    assert.equal(response.headers.get('cache-control'),'private, no-store');assert.equal(response.headers.get('set-cookie'),null);
  }
});
test('media permits legitimate absent/null redirect origins but rejects foreign origins and worker audiences',async()=>{
  const f=fixture(),{url}=await grant(f);
  for(const headers of [{},{Origin:'null'},{Origin:origin}]) {
    const response=await f.media(url,'HEAD',headers);assert.equal(response.status,200);
    assert.equal(response.headers.get('access-control-allow-origin'),headers.Origin==='null'?'null':origin);
  }
  for(const headers of [{Origin:'https://evil.example'},{Host:'evil.example'}])assert.equal((await f.media(url,'HEAD',headers)).status,403);
  assert.equal((await f.media(url.replace(workerOrigin,'https://evil.example'),'HEAD')).status,403);
  assert.equal((await f.media(signedGrant(url,f.env,c=>c.aud='https://evil.example'),'HEAD')).status,401);
});
test('every existing media grant rechecks sole current invite revocation version expiry and removal',async()=>{
  for(const change of [invite=>invite.revoked=true,invite=>invite.version++,invite=>invite.expiresAt=1_700_000_000,()=>null]) {
    const f=fixture(),{url}=await grant(f);assert.equal((await f.media(url,'HEAD')).status,200);
    const invite=JSON.parse(f.env.LUMEN_INVITES_JSON)[0],changed=change(invite);f.env.LUMEN_INVITES_JSON=JSON.stringify(changed===null?[]:[invite]);
    const count=f.calls.length;
    for(const headers of [{},{Range:'bytes=1-2'},{'If-None-Match':'*'}])assert.equal((await f.media(url,'GET',headers)).status,401);
    assert.equal((await f.media(url,'HEAD')).status,401);assert.equal(f.calls.length,count);
  }
});
test('tampered malformed expired and cross-path grants fail before private R2 reads',async()=>{
  const f=fixture(),{url}=await grant(f);const count=f.calls.length;
  const missing=new URL(url);missing.search='';const bad=new URL(url);bad.searchParams.set('grant','x.y');
  const altered=new URL(url);altered.searchParams.set('grant',altered.searchParams.get('grant').slice(0,-1)+'x');
  const duplicated=new URL(url);duplicated.searchParams.append('grant',duplicated.searchParams.get('grant'));
  for(const invalid of [missing.href,bad.href,altered.href,duplicated.href,url.replace('/media/'+audioPath,'/media/'+artPath),signedGrant(url,f.env,c=>c.exp=1_700_000_000)]) {
    assert.ok([400,401].includes((await f.media(invalid)).status));assert.equal(f.calls.length,count);
  }
  f.setNow(1_800_000_000_000+31*86400*1000);assert.equal((await f.media(url)).status,401);assert.equal(f.calls.length,count);
});
test('media HEAD and conditional replies authenticate without downloading and invalid ranges remain 416',async()=>{
  const f=fixture(),{url}=await grant(f),count=f.calls.length;
  const head=await f.media(url,'HEAD',{Range:'bytes=1-2'});assert.equal(head.status,200);assert.equal(head.headers.get('content-length'),'36');assert.equal(await head.text(),'');
  const etag=head.headers.get('etag');assert.equal((await f.media(url,'GET',{'If-None-Match':etag})).status,304);assert.equal(f.calls.length,count);
  for(const range of ['bytes=99-','bytes=5-2','bytes=-0','bytes=1-2,4-5','items=0-1','bytes=9007199254740993-','bytes=-']) {
    const response=await f.media(url,'GET',{Range:range});assert.equal(response.status,416);assert.equal(response.headers.get('content-range'),'bytes */36');
  }
  assert.equal(f.calls.length,count);
  const full=await f.media(url,'GET',{Range:'bytes=1-2','If-Range':'"old"'});assert.equal(full.status,200);assert.equal(await full.text(),audio.toString());
});
test('WebP uses the same private grant checks and JSON cannot bypass bridge through media',async()=>{
  const f=fixture(),{url}=await grant(f,artPath),response=await f.media(url);assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'image/webp');assert.equal(await response.text(),painting.toString());
  const wrong=signedGrant(url,f.env,c=>{c.path=manifestPath;c.sha256=digest(manuscript);});const switched=new URL(wrong);switched.pathname='/media/'+manifestPath;
  const count=f.calls.length;assert.equal((await f.media(switched.href)).status,403);assert.equal(f.calls.length,count);
});
test('bridge envelopes enforce bounded exact fields header whitelist origin and original session body',async()=>{
  const f=fixture(),base={url:origin+'/api/session',method:'POST',headers:{origin,'content-type':'application/json'},body:JSON.stringify({invite:code})};
  const send=value=>f.raw(workerOrigin+'/bridge','POST',{'X-Lumen-Bridge-Key':f.env.LUMEN_BRIDGE_KEY,'content-type':'application/json'},JSON.stringify(value));
  for(const alter of [v=>v.headers.authorization='Bearer x',v=>v.extra=true,v=>v.body={invite:code},v=>v.method='PUT',v=>v.url+='?invite=x',v=>v.url+='\n',v=>v.url='https://evil.example/api/session',v=>v.headers.origin='https://evil.example',v=>v.headers.host='evil.example',v=>v.headers['content-length']='9999',v=>v.body='x'.repeat(4097)]) {
    const value=structuredClone(base);alter(value);assert.ok([400,403,405,413].includes((await send(value)).status));
  }
  const response=await f.raw(workerOrigin+'/bridge','POST',{'X-Lumen-Bridge-Key':f.env.LUMEN_BRIDGE_KEY,'content-type':'application/json'},'x'.repeat(25000));assert.equal(response.status,413);assert.equal(f.calls.length,0);
});
test('trusted previews retain canonical book binding and grant exact granted CORS origin',async()=>{
  const f=fixture(),preview='https://lumen-review.vercel.app';f.env.LUMEN_ALLOWED_ORIGINS_JSON=JSON.stringify([preview]);
  const bridge=(path,method,headers,body)=>f.raw(workerOrigin+'/bridge','POST',{'X-Lumen-Bridge-Key':f.env.LUMEN_BRIDGE_KEY,'content-type':'application/json'},JSON.stringify({url:preview+path,method,headers,...(body?{body}:{})}));
  const exchange=await bridge('/api/session','POST',{origin:preview,'content-type':'application/json'},JSON.stringify({invite:code}));assert.equal(exchange.status,200);
  const cookie=exchange.headers.get('set-cookie').split(';')[0];const book=await bridge('/api/book','GET',{cookie});assert.equal(await book.text(),manuscript.toString());
  const redirect=await bridge('/api/assets/'+audioPath,'GET',{cookie});assert.equal(redirect.status,307);
  const response=await f.media(redirect.headers.get('location'),'HEAD',{Origin:preview});assert.equal(response.headers.get('access-control-allow-origin'),preview);assert.equal(f.index.appOrigin,origin);
  assert.equal((await f.media(redirect.headers.get('location'),'HEAD',{Origin:origin})).status,403);
});
test('index configuration hash size type and malformed content fail closed without exposing private bytes',async()=>{
  for(const alter of [f=>f.env.LUMEN_RELEASE_INDEX_SHA256='0'.repeat(64),f=>f.objects.get(f.indexPath).size++,f=>f.objects.get(f.indexPath).httpMetadata.contentType='text/html',f=>f.objects.get(f.indexPath).customMetadata.sha256='0'.repeat(64),f=>f.objects.get(f.indexPath).bytes=Buffer.alloc(f.objects.get(f.indexPath).size)]) {
    const f=fixture(),cookie=await login(f);alter(f);const response=await f.bridge('/api/book','GET',{cookie});assert.equal(response.status,503);assert.ok(!(await response.text()).includes('fixture'));
  }
});
test('R2 metadata and full JSON byte mismatches fail closed and media truncation fails its stream',async()=>{
  for(const alter of [o=>o.size++,o=>o.httpMetadata.contentType='text/html',o=>o.customMetadata.sha256='0'.repeat(64),o=>o.httpMetadata.contentEncoding='gzip']) {
    const f=fixture(),{url}=await grant(f);alter(f.objects.get(f.index.assets[1].blobPath));assert.equal((await f.media(url,'GET',{Range:'bytes=2-6'})).status,503);
  }
  const f=fixture(),{url}=await grant(f);f.objects.get(f.index.assets[1].blobPath).bytes=audio.subarray(0,5);
  const response=await f.media(url);assert.equal(response.status,200);await assert.rejects(()=>response.arrayBuffer());
  const g=fixture(),cookie=await login(g);g.objects.get(g.index.assets[0].blobPath).bytes=Buffer.alloc(manuscript.length);assert.equal((await g.bridge('/api/book','GET',{cookie})).status,503);
});
test('configuration errors routes queries methods and distinct-key enforcement never reveal secrets',async()=>{
  for(const alter of [f=>f.env.LUMEN_MEDIA_SECRET=f.env.LUMEN_SESSION_SECRET,f=>f.env.LUMEN_MEDIA_SECRET='bad',f=>f.env.LUMEN_WORKER_ORIGIN='https://evil.example/path',f=>f.env.LUMEN_ALLOWED_ORIGINS_JSON='["https://evil.example"]']) {
    const f=fixture();alter(f);const response=await f.bridge('/api/session');assert.equal(response.status,503);assert.ok(!(await response.text()).includes(f.env.LUMEN_SESSION_SECRET));assert.equal(f.calls.length,0);
  }
  const f=fixture();for(const path of ['/','/api/book','/media/'+audioPath+'?extra=1'])assert.ok([400,401,404].includes((await f.raw(workerOrigin+path)).status));
  assert.equal((await f.raw(workerOrigin+'/bridge')).status,405);
});
test('R2 adapter propagates cancellation before a read and honors actual range metadata',async()=>{
  const f=fixture(),{url}=await grant(f),count=f.calls.length,controller=new AbortController();controller.abort();
  const response=await f.env.LUMEN_R2.get(f.indexPath);await response.body.cancel();
  f.calls.splice(count);
  const aborted=await f.worker.fetch(new Request(url,{signal:controller.signal}),f.env);assert.equal(aborted.status,503);
  assert.equal(f.calls.length,count);
  const original=f.env.LUMEN_R2.get;f.env.LUMEN_R2.get=async(...args)=>{const result=await original(...args);if(args[1]?.range)result.range={offset:0,length:5};return result;};
  assert.equal((await f.media(url,'GET',{Range:'bytes=2-6'})).status,503);
});
test('original HEAD uses bodyless authenticated HEAD transport and rejects POST HEAD envelopes',async()=>{
  const f=fixture(),cookie=await login(f);
  const response=await f.bridge('/api/book','HEAD',{cookie});assert.equal(response.status,200);
  assert.equal(response.headers.get('content-length'),String(manuscript.length));assert.equal(await response.text(),'');
  const bad=await f.raw(workerOrigin+'/bridge','POST',{'X-Lumen-Bridge-Key':f.env.LUMEN_BRIDGE_KEY,'content-type':'application/json'},JSON.stringify({url:origin+'/api/book',method:'HEAD',headers:{cookie}}));assert.equal(bad.status,400);
  for(const header of ['not!base64',Buffer.from('{"method":"GET"}').toString('base64url'),'x'.repeat(17000)]) {
    const result=await f.raw(workerOrigin+'/bridge','HEAD',{'X-Lumen-Bridge-Key':f.env.LUMEN_BRIDGE_KEY,'X-Lumen-Request':header});assert.ok([400,413].includes(result.status));
  }
});
test('R2 reads cancelled during index arrival or active streaming cancel the upstream body',async()=>{
  const f=fixture(),cookie=await login(f),controller=new AbortController();let cancelled=0;
  const get=f.env.LUMEN_R2.get;
  f.env.LUMEN_R2.get=async(...args)=>{
    const result=await get(...args);result.body=new ReadableStream({cancel(){cancelled++;}});controller.abort();return result;
  };
  const envelope=JSON.stringify({url:origin+'/api/book',method:'GET',headers:{cookie}});
  const response=await f.worker.fetch(new Request(workerOrigin+'/bridge',{method:'POST',signal:controller.signal,
    headers:{'X-Lumen-Bridge-Key':f.env.LUMEN_BRIDGE_KEY,'content-type':'application/json'},body:envelope}),f.env);
  assert.equal(response.status,503);assert.equal(cancelled,1);

  const g=fixture(),{url}=await grant(g),streamController=new AbortController();let mediaCancelled=0;
  const mediaGet=g.env.LUMEN_R2.get;
  g.env.LUMEN_R2.get=async(...args)=>{
    const result=await mediaGet(...args);if(args[0]===g.index.assets[1].blobPath)result.body=new ReadableStream({start(output){output.enqueue(audio.subarray(0,2));},cancel(){mediaCancelled++;}});return result;
  };
  const streamed=await g.worker.fetch(new Request(url,{signal:streamController.signal}),g.env),reader=streamed.body.getReader();
  assert.deepEqual(Buffer.from((await reader.read()).value),audio.subarray(0,2));streamController.abort();
  await new Promise(resolve=>setImmediate(resolve));assert.equal(mediaCancelled,1);
  await assert.rejects(()=>reader.read());reader.releaseLock();
});
test('signed claims still require exact finite policy and current immutable release bindings',async()=>{
  const f=fixture(),{url}=await grant(f),count=f.calls.length;
  for(const mutate of [c=>c.v=2,c=>c.i='other-reader',c=>c.exp=Number.MAX_SAFE_INTEGER,c=>c.exp='1800000001',c=>c.extra=true]) {
    assert.equal((await f.media(signedGrant(url,f.env,mutate))).status,401);assert.equal(f.calls.length,count);
  }
  for(const mutate of [c=>c.releaseId='release-other',c=>c.sha256='0'.repeat(64),c=>c.path='audio/missing.'+digest(audio)+'.mp3']) {
    const modified=new URL(signedGrant(url,f.env,mutate)),claims=JSON.parse(Buffer.from(modified.searchParams.get('grant').split('.')[0],'base64url').toString());modified.pathname='/media/'+claims.path;
    assert.equal((await f.media(modified.href)).status,401);assert.equal(f.calls.length,count);
  }
});
test('immutable index caches are isolated by bucket and binding while policy always reloads',async()=>{
  const f=fixture(),g=fixture(),cookie=await login(f);g.objects.get(g.indexPath).bytes=Buffer.alloc(g.objects.get(g.indexPath).size);
  assert.equal((await f.bridge('/api/book','HEAD',{cookie})).status,200);
  const envelope=JSON.stringify({url:origin+'/api/book',method:'GET',headers:{cookie}});
  const response=await f.worker.fetch(new Request(workerOrigin+'/bridge',{method:'POST',headers:{'X-Lumen-Bridge-Key':g.env.LUMEN_BRIDGE_KEY,'content-type':'application/json'},body:envelope}),g.env);assert.equal(response.status,503);
  assert.equal(g.calls.length,1);assert.equal(f.calls.length,1);
  f.env.LUMEN_INVITES_JSON='bad';assert.equal((await f.bridge('/api/book','HEAD',{cookie})).status,503);assert.equal(f.calls.length,1);
});
test('native R2 full reads may report the exact full interval but never a partial interval',async()=>{
  const f=fixture(),cookie=await login(f),get=f.env.LUMEN_R2.get;
  f.env.LUMEN_R2.get=async(...args)=>{const value=await get(...args);if(!args[1]?.range)value.range={offset:0,length:value.size};return value;};
  const book=await f.bridge('/api/book','GET',{cookie});assert.equal(book.status,200);assert.equal(await book.text(),manuscript.toString());
  const {url}=await grant(f,audioPath,cookie),response=await f.media(url);assert.equal(response.status,200);assert.equal(response.headers.get('content-range'),null);assert.equal(await response.text(),audio.toString());
  f.env.LUMEN_R2.get=async(...args)=>{const value=await get(...args);value.range={offset:1,length:value.size-1};return value;};
  assert.equal((await f.media(url)).status,503);
});
test('CORS preflight uses the valid current grant and exact allowed origin without reading private objects',async()=>{
  const f=fixture(),{url}=await grant(f),count=f.calls.length;
  for(const Origin of [origin,'null']) {
    const response=await f.media(url,'OPTIONS',{Origin,'Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'Range, If-Range, If-None-Match'});
    assert.equal(response.status,204);assert.equal(await response.text(),'');assert.equal(response.headers.get('access-control-allow-origin'),Origin);
    assert.equal(response.headers.get('access-control-allow-methods'),'GET, HEAD');assert.equal(response.headers.get('access-control-allow-credentials'),null);assert.equal(f.calls.length,count);
  }
  for(const headers of [{},{Origin:origin,'Access-Control-Request-Method':'POST'},{Origin:origin,'Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'Authorization'},{Origin:'https://evil.example','Access-Control-Request-Method':'GET'}])assert.ok([400,403,405].includes((await f.media(url,'OPTIONS',headers)).status));
  const invite=JSON.parse(f.env.LUMEN_INVITES_JSON)[0];invite.revoked=true;f.env.LUMEN_INVITES_JSON=JSON.stringify([invite]);
  assert.equal((await f.media(url,'OPTIONS',{Origin:origin,'Access-Control-Request-Method':'GET'})).status,401);assert.equal(f.calls.length,count);
});
