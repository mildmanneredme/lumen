'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {createPrivateReader, buildServerIndex, parseRange} = require('../server/private-access.cjs');

const origin = 'https://lumen.example';
const token = Buffer.alloc(32, 7).toString('base64url');
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const audio = Buffer.from('0123456789abcdefghijklmnopqrstuvwxyz');
const manifest = Buffer.from('{"bookId":"lumen","privateText":"fixture"}\n');
const manifestHash = digest(manifest), audioHash = digest(audio);
const manifestPath = `tracks/chapter-000/book-manifest.${manifestHash}.json`;
const audioPath = `audio/autonoe/chapter-001.${audioHash}.mp3`;
const asset = (path, bytes, type) => ({path, blobPath:`lumen/release-one/${path}`,sha256:digest(bytes),bytes:bytes.length,contentType:type});
const index = {schemaVersion:1,releaseId:'release-one',appOrigin:origin,manifestPath,assets:[asset(manifestPath,manifest,'application/json'),asset(audioPath,audio,'audio/mpeg')]};
function fixture(options={}) {
  let now=1_800_000_000_000;
  const env={LUMEN_APP_ORIGIN:origin,LUMEN_SESSION_SECRET:Buffer.alloc(32,9).toString('base64url'),LUMEN_INVITES_JSON:JSON.stringify([{id:'reader-one',tokenHash:digest(token),version:1,expiresAt:1_900_000_000,revoked:false}])};
  const calls=[];
  const get = async (path, opts) => {
    calls.push({path,opts});
    const data=path===index.assets[0].blobPath?manifest:audio;
    const range=opts.headers?.Range;
    const parsed=range?parseRange(range,data.length):null;
    const body=parsed?data.subarray(parsed.start,parsed.end+1):data;
    const headers=new Headers({'content-length':String(body.length),'content-type':path.endsWith('.json')?'application/json':'audio/mpeg'});
    if(parsed) headers.set('content-range',`bytes ${parsed.start}-${parsed.end}/${data.length}`);
    return {statusCode:200,stream:new Response(body).body,headers,blob:{size:body.length}};
  };
  const reader=createPrivateReader({env,now:()=>now,loadIndex:async()=>index,getBlob:get,...options});
  const request=(path,method='GET',headers={},body) => new Request(origin+path,{method,headers,...(body!==undefined?{body:JSON.stringify(body)}:{})});
  return {env,calls,reader,request,setNow:value=>now=value};
}
async function login(f) {
  const response=await f.reader(f.request('/api/session','POST',{'Origin':origin,'Content-Type':'application/json'},{invite:token}));
  assert.equal(response.status,200);
  return response.headers.get('set-cookie').split(';')[0];
}
test('private prose and audio reject guests before index or Blob reads',async()=>{
  let reads=0; const f=fixture({loadIndex:async()=>{reads++;return index;}});
  for(const path of ['/api/book','/api/assets/'+manifestPath,'/api/assets/'+audioPath]) {
    const response=await f.reader(f.request(path)); assert.equal(response.status,401);
    assert.equal(response.headers.get('cache-control'),'private, no-store');
    assert.equal(response.headers.get('access-control-allow-origin'),null);
  }
  assert.equal(reads,0);assert.equal(f.calls.length,0);
});
test('invite exchange sets a secure opaque cookie and grants access',async()=>{
  const f=fixture(); const response=await f.reader(f.request('/api/session','POST',{'Origin':origin,'Content-Type':'application/json'},{invite:token}));
  assert.equal(response.status,200); const cookie=response.headers.get('set-cookie');
  for(const attribute of ['HttpOnly','Secure','SameSite=Lax','Path=/']) assert.ok(cookie.includes(attribute));
  assert.ok(!cookie.includes(token));assert.ok(!JSON.stringify(await response.json()).includes(token));
  const read=await f.reader(f.request('/api/book','GET',{Cookie:cookie.split(';')[0]}));
  assert.equal(read.status,200);assert.equal(await read.text(),manifest.toString());
});
test('bad, expired, revoked and version-changed invites cannot authorize',async()=>{
  const f=fixture(); const cookie=await login(f);
  const invalid=await f.reader(f.request('/api/session','POST',{'Origin':origin,'Content-Type':'application/json'},{invite:Buffer.alloc(32,1).toString('base64url')}));assert.equal(invalid.status,401);
  for(const change of [{revoked:true},{expiresAt:1_700_000_000},{version:2}]) {
    f.env.LUMEN_INVITES_JSON=JSON.stringify([{id:'reader-one',tokenHash:digest(token),version:1,expiresAt:1_900_000_000,revoked:false,...change}]);
    assert.equal((await f.reader(f.request('/api/book','GET',{Cookie:cookie}))).status,401);
  }
});
test('session expiry and tampered or duplicate cookies fail closed',async()=>{
  const f=fixture();const cookie=await login(f);
  for(const invalid of [cookie.slice(0,-1)+'x',cookie+'; '+cookie]) assert.equal((await f.reader(f.request('/api/book','GET',{Cookie:invalid}))).status,401);
  f.setNow(1_800_000_000_000+31*86400*1000);
  assert.equal((await f.reader(f.request('/api/book','GET',{Cookie:cookie}))).status,401);
});
test('same-origin writes reject CSRF and cross-site asset requests reject cookie access',async()=>{
  const f=fixture();const cookie=await login(f);
  for(const headers of [{},{Origin:'https://evil.example'},{Origin:origin,'Sec-Fetch-Site':'cross-site'}]) {
    assert.equal((await f.reader(f.request('/api/session','POST',{'Content-Type':'application/json',...headers},{invite:token}))).status,403);
    assert.equal((await f.reader(f.request('/api/session','DELETE',{Cookie:cookie,...headers}))).status,403);
  }
  const response=await f.reader(f.request('/api/assets/'+audioPath,'GET',{Cookie:cookie,Origin:'https://evil.example'}));
  assert.equal(response.status,403);assert.equal(response.headers.get('access-control-allow-origin'),null);
});
const previewHost='lumen-fixture-robert-xies-projects.vercel.app';
const previewOrigin='https://'+previewHost;
function previewRequest(path,method='GET',headers={},body) {
  return new Request(previewOrigin+path,{method,headers:{Host:previewHost,...headers},
    ...(body!==undefined?{body:JSON.stringify(body)}:{})});
}
test('trusted Vercel preview origin accepts invite exchange and canonical production book index',async()=>{
  const f=fixture();Object.assign(f.env,{VERCEL_ENV:'preview',VERCEL_URL:previewHost});
  const response=await f.reader(previewRequest('/api/session','POST',{Origin:previewOrigin,'Content-Type':'application/json','Sec-Fetch-Site':'same-origin'},{invite:token}));
  assert.equal(response.status,200);const cookie=response.headers.get('set-cookie').split(';')[0];
  const read=await f.reader(previewRequest('/api/book','GET',{Cookie:cookie,Origin:previewOrigin}));
  assert.equal(read.status,200);assert.equal(await read.text(),manifest.toString());assert.equal(index.appOrigin,origin);
  const range=await f.reader(previewRequest('/api/assets/'+audioPath,'GET',{Cookie:cookie,Range:'bytes=2-6'}));
  assert.equal(range.status,206);assert.equal(await range.text(),'23456');
});
test('preview requests reject forged Host, mismatched Origin and foreign request URLs',async()=>{
  const f=fixture();Object.assign(f.env,{VERCEL_ENV:'preview',VERCEL_URL:previewHost});
  for(const headers of [{Origin:origin},{Origin:'https://evil.example'},
    {Origin:previewOrigin,Host:'evil.example'},{Origin:previewOrigin,Host:new URL(origin).host},
    {Origin:previewOrigin,Host:previewHost+', evil.example'},{Origin:previewOrigin,'Sec-Fetch-Site':'cross-site'}]) {
    const response=await f.reader(previewRequest('/api/session','POST',{'Content-Type':'application/json',...headers},{invite:token}));
    assert.equal(response.status,403);
  }
  const forged=new Request('https://evil.example/api/session',{method:'POST',headers:{Host:previewHost,Origin:previewOrigin,'Content-Type':'application/json'},body:JSON.stringify({invite:token})});
  assert.equal((await f.reader(forged)).status,403);assert.equal(f.calls.length,0);
});
test('preview origin fails closed for missing or malformed platform URL and never trusts forwarded hosts',async()=>{
  for(const value of [undefined,'https://'+previewHost,previewHost+'/path',previewHost+'?query=1',
    previewHost+':443','user@'+previewHost,'evil.example','vercel.app','bad_.vercel.app',previewHost+' ']) {
    const f=fixture();Object.assign(f.env,{VERCEL_ENV:'preview',VERCEL_URL:value});
    assert.equal((await f.reader(previewRequest('/api/session','POST',{Origin:previewOrigin,'Content-Type':'application/json'},{invite:token}))).status,503);
  }
  const f=fixture();Object.assign(f.env,{VERCEL_ENV:'preview',VERCEL_URL:previewHost});
  const response=await f.reader(previewRequest('/api/session','POST',{Origin:previewOrigin,'Content-Type':'application/json','X-Forwarded-Host':'evil.example'},{invite:token}));
  assert.equal(response.status,200);
});
test('production ignores deployment preview hostname and preview cannot relabel canonical index',async()=>{
  const production=fixture();Object.assign(production.env,{VERCEL_ENV:'production',VERCEL_URL:previewHost});
  const cookie=await login(production);
  assert.equal((await production.reader(production.request('/api/book','GET',{Cookie:cookie,Host:new URL(origin).host}))).status,200);
  assert.equal((await production.reader(previewRequest('/api/book','GET',{Cookie:cookie}))).status,403);
  assert.equal((await production.reader(production.request('/api/book','GET',{Cookie:cookie,Host:previewHost}))).status,403);
  const altered={...index,appOrigin:previewOrigin};
  const preview=fixture({loadIndex:async()=>altered});Object.assign(preview.env,{VERCEL_ENV:'preview',VERCEL_URL:previewHost});
  const exchange=await preview.reader(previewRequest('/api/session','POST',{Origin:previewOrigin,'Content-Type':'application/json'},{invite:token}));
  assert.equal(exchange.status,200);
  assert.equal((await preview.reader(previewRequest('/api/book','GET',{Cookie:exchange.headers.get('set-cookie').split(';')[0]}))).status,503);
});
test('logout clears the cookie and access response reveals no invite identifiers',async()=>{
  const f=fixture(); const cookie=await login(f);
  const status=await f.reader(f.request('/api/session','GET',{Cookie:cookie}));assert.deepEqual(await status.json(),{authenticated:true});
  const logout=await f.reader(f.request('/api/session','DELETE',{Origin:origin,Cookie:cookie}));
  assert.equal(logout.status,200);assert.ok(logout.headers.get('set-cookie').includes('Max-Age=0'));
});
test('invalid configuration and index failures hide details and fail closed',async()=>{
  const f=fixture(); f.env.LUMEN_SESSION_SECRET='short';assert.equal((await f.reader(f.request('/api/book'))).status,503);
  const broken=fixture({loadIndex:async()=>{throw new Error('secret fake token');}});const cookie=await login(broken);
  const response=await broken.reader(broken.request('/api/book','GET',{Cookie:cookie}));assert.equal(response.status,503);assert.ok(!(await response.text()).includes('secret'));
});
test('invite body is bounded and rejects malformed types, query tokens and methods',async()=>{
  const f=fixture();
  for(const value of [{invite:'x'.repeat(5000)},{invite:42},{token},{}]) {
    const response=await f.reader(f.request('/api/session','POST',{Origin:origin,'Content-Type':'application/json'},value));assert.ok([400,413].includes(response.status));
  }
  assert.equal((await f.reader(f.request('/api/session?invite='+token))).status,400);
  assert.equal((await f.reader(f.request('/api/book','POST'))).status,405);
});
test('native audio closed, open and suffix ranges stream exact bytes with 206',async()=>{
  const f=fixture();const cookie=await login(f);
  for(const [range,start,end] of [['bytes=2-6',2,6],['bytes=20-',20,35],['bytes=-5',31,35],['bytes=30-999',30,35]]) {
    const response=await f.reader(f.request('/api/assets/'+audioPath,'GET',{Cookie:cookie,Range:range}));
    assert.equal(response.status,206);assert.equal(response.headers.get('content-range'),`bytes ${start}-${end}/36`);assert.equal(response.headers.get('content-length'),String(end-start+1));
    assert.equal(await response.text(),audio.subarray(start,end+1).toString());assert.equal(f.calls.at(-1).opts.headers.Range,`bytes=${start}-${end}`);assert.equal(f.calls.at(-1).opts.headers['Accept-Encoding'],'identity');assert.equal(f.calls.at(-1).opts.access,'private');
  }
});
test('invalid or unsatisfiable ranges return 416 without Blob fetch',async()=>{
  const f=fixture();const cookie=await login(f);
  for(const range of ['bytes=99-','bytes=5-2','bytes=-0','bytes=1-2,4-5','items=0-1','bytes=9007199254740993-','bytes=-']) {
    const response=await f.reader(f.request('/api/assets/'+audioPath,'GET',{Cookie:cookie,Range:range}));
    assert.equal(response.status,416);assert.equal(response.headers.get('content-range'),'bytes */36');
  }
  assert.equal(f.calls.length,0);
});
test('HEAD provides metadata without downloading; conditional access still authenticates',async()=>{
  const f=fixture();const cookie=await login(f);
  const head=await f.reader(f.request('/api/assets/'+audioPath,'HEAD',{Cookie:cookie,Range:'bytes=1-2'}));assert.equal(head.status,200);assert.equal(head.headers.get('content-length'),'36');assert.equal(await head.text(),'');assert.equal(f.calls.length,0);
  const etag=head.headers.get('etag');assert.equal((await f.reader(f.request('/api/assets/'+audioPath,'GET',{'If-None-Match':etag}))).status,401);
  assert.equal((await f.reader(f.request('/api/assets/'+audioPath,'GET',{Cookie:cookie,'If-None-Match':etag}))).status,304);
  const full=await f.reader(f.request('/api/assets/'+audioPath,'GET',{Cookie:cookie,Range:'bytes=1-2','If-Range':'"old"'}));assert.equal(full.status,200);assert.equal(await full.text(),audio.toString());
});
test('SDK hardcodes 200 for upstream 206; raw range headers govern response validation',async()=>{
  const f=fixture({getBlob:async()=>({statusCode:200,headers:new Headers({'content-length':'36'}),stream:new Response(audio).body})});
  const cookie=await login(f); const response=await f.reader(f.request('/api/assets/'+audioPath,'GET',{Cookie:cookie,Range:'bytes=2-6'}));assert.equal(response.status,503);
});
test('wrong full JSON bytes or mismatched upstream range metadata fail closed',async()=>{
  for(const [path,headers,data] of [[manifestPath,{'content-length':String(manifest.length)},Buffer.alloc(manifest.length)],[audioPath,{'content-length':'5','content-range':'bytes 3-7/36'},audio.subarray(3,8)]]) {
    const f=fixture({getBlob:async()=>({statusCode:200,headers:new Headers(headers),stream:new Response(data).body})});const cookie=await login(f);
    const response=await f.reader(f.request('/api/assets/'+path,'GET',{Cookie:cookie,...(path===audioPath?{Range:'bytes=2-6'}:{})}));assert.equal(response.status,503);
  }
});
test('exact allowlist rejects unknown paths, arbitrary URLs, traversal and unexpected query values',async()=>{
  const f=fixture();const cookie=await login(f);
  for(const path of ['/api/assets/other.mp3','/api/assets/https%3A%2F%2Fevil.example','/api/assets/%2e%2e/secret','/api/assets/'+audioPath+'?download=1','/api/assets/'+audioPath+'%2fsecret']) assert.ok([400,404].includes((await f.reader(f.request(path,'GET',{Cookie:cookie}))).status));
  assert.equal(f.calls.length,0);
});
test('index validation rejects unsafe paths, foreign origin, public Blob references and malformed bytes',async()=>{
  for(const change of [value=>value.appOrigin='https://evil.example',value=>value.assets[0].blobPath='https://public.blob.vercel-storage.com/a',value=>value.assets[0].path='../a',value=>value.assets[0].bytes=-1,value=>value.assets.push(value.assets[0]),value=>value.manifestPath=audioPath]) {
    const altered=JSON.parse(JSON.stringify(index));change(altered);const f=fixture({loadIndex:async()=>altered});const cookie=await login(f);assert.equal((await f.reader(f.request('/api/book','GET',{Cookie:cookie}))).status,503);
  }
});
test('operator server index reconciles every private receipt with release upload inventory',()=>{
  const inventory={schemaVersion:1,releaseId:'release-one',accessModel:'private',appOrigin:origin,mediaOrigins:[origin],assets:index.assets.map(row=>({url:origin+'/api/assets/'+row.path,sha256:row.sha256,bytes:row.bytes,contentType:row.contentType,immutable:true,sourcePath:'/private/sensitive/'+row.path}))};
  const receipts=index.assets.map(row=>({url:origin+'/api/assets/'+row.path,blobPath:row.blobPath,sha256:row.sha256,bytes:row.bytes,contentType:row.contentType,access:'private'}));
  const result=buildServerIndex(inventory,receipts,origin+'/api/assets/'+manifestPath);assert.deepEqual(result,index);assert.ok(!JSON.stringify(result).includes('sourcePath'));
  for(const alter of [i=>i.accessModel='public',i=>i.assets[0].sha256='0'.repeat(64),i=>i.assets[0].url='https://evil.example/api/assets/a',i=>i.assets.push(i.assets[0])]) {
    const changed=JSON.parse(JSON.stringify(inventory));alter(changed);assert.throws(()=>buildServerIndex(changed,receipts,origin+'/api/assets/'+manifestPath));
  }
  for(const alter of [r=>r.pop(),r=>r[0].access='public',r=>r[0].bytes++,r=>r[0].blobPath='../outside']) {
    const changed=JSON.parse(JSON.stringify(receipts));alter(changed);assert.throws(()=>buildServerIndex(inventory,changed,origin+'/api/assets/'+manifestPath));
  }
});
test('runtime loads only a hash-bound private index and rejects tampered index bytes',async()=>{
  const {createIndexLoader}=require('../server/runtime.cjs');
  const bytes=Buffer.from(JSON.stringify(index)), hash=digest(bytes);
  const env={LUMEN_APP_ORIGIN:origin,LUMEN_RELEASE_INDEX_PATH:`lumen/releases/release-one/server-index.${hash}.json`,LUMEN_RELEASE_INDEX_SHA256:hash};
  const calls=[];
  const loader=createIndexLoader({env,getBlob:async(path,options)=>{calls.push({path,options});return {statusCode:200,headers:new Headers(),stream:new Response(bytes).body};}});
  assert.deepEqual(await loader(),index);assert.equal(calls[0].options.access,'private');
  for(const [path,hashValue] of [['https://evil.example/index.json',hash],['lumen/releases/release-one/server-index.'+'0'.repeat(64)+'.json',hash]]) {
    const bad=createIndexLoader({env:{...env,LUMEN_RELEASE_INDEX_PATH:path,LUMEN_RELEASE_INDEX_SHA256:hashValue},getBlob:async()=>{throw Error('should not fetch');}});await assert.rejects(()=>bad());
  }
  const another=Buffer.from(JSON.stringify({...index,releaseId:'release-two'})), anotherHash=digest(another);
  const broken=createIndexLoader({env:{...env,LUMEN_RELEASE_INDEX_PATH:`lumen/releases/release-two/server-index.${anotherHash}.json`,LUMEN_RELEASE_INDEX_SHA256:anotherHash},getBlob:async()=>({statusCode:200,headers:new Headers(),stream:new Response(bytes).body})});await assert.rejects(()=>broken());
});
test('Vercel Node adapter streams exact authenticated ranges and conceals secrets on failures',async()=>{
  const {createNodeHandler}=require('../server/node-handler.cjs');
  const {Readable,Writable}=require('node:stream');
  function incoming(path,method,headers={},body='') {
    const request=Readable.from(body?[Buffer.from(body)]:[]);Object.assign(request,{url:path,method,headers});return request;
  }
  function outgoing() {
    const parts=[];const response=new Writable({write(chunk,encoding,done){parts.push(Buffer.from(chunk));done();}});
    response.writeHead=(status,headers)=>{response.statusCode=status;response.outputHeaders=headers;response.headersSent=true;};
    response.bytes=()=>Buffer.concat(parts);
    return response;
  }
  const f=fixture(),cookie=await login(f),handler=createNodeHandler({env:f.env,loadReader:async()=>f.reader});
  const response=outgoing();
  await handler(incoming('/api/assets/'+audioPath,'GET',{cookie,range:'bytes=2-6'}),response);
  assert.equal(response.statusCode,206);assert.equal(response.bytes().toString(),'23456');assert.equal(response.outputHeaders['content-range'],'bytes 2-6/36');
  const loginResponse=outgoing();
  await handler(incoming('/api/session','POST',{origin,'content-type':'application/json'},JSON.stringify({invite:token})),loginResponse);
  assert.equal(loginResponse.statusCode,200);assert.ok(loginResponse.outputHeaders['set-cookie'].includes('HttpOnly'));
  const broken=createNodeHandler({env:f.env,loadReader:async()=>{throw Error('secret fake token');}}),failed=outgoing();
  await broken(incoming('/api/book','GET'),failed);assert.equal(failed.statusCode,503);assert.ok(!failed.bytes().toString().includes('secret'));
});
test('Vercel Node adapter uses trusted preview origin for login and book reads, rejecting client host/origin overrides',async()=>{
  const {createNodeHandler}=require('../server/node-handler.cjs');
  const {Readable,Writable}=require('node:stream');
  function incoming(path,method,headers={},body='') {
    const request=Readable.from(body?[Buffer.from(body)]:[]);Object.assign(request,{url:path,method,headers:{host:previewHost,...headers}});return request;
  }
  function outgoing() {
    const parts=[];const response=new Writable({write(chunk,encoding,done){parts.push(Buffer.from(chunk));done();}});
    response.writeHead=(status,headers)=>{response.statusCode=status;response.outputHeaders=headers;response.headersSent=true;};
    response.bytes=()=>Buffer.concat(parts);return response;
  }
  const f=fixture();Object.assign(f.env,{VERCEL_ENV:'preview',VERCEL_URL:previewHost});
  const handler=createNodeHandler({env:f.env,loadReader:async()=>f.reader}),exchange=outgoing();
  await handler(incoming('/api/session','POST',{origin:previewOrigin,'content-type':'application/json'},JSON.stringify({invite:token})),exchange);
  assert.equal(exchange.statusCode,200);const cookie=exchange.outputHeaders['set-cookie'].split(';')[0];
  const read=outgoing();await handler(incoming('/api/book','GET',{cookie,origin:previewOrigin}),read);
  assert.equal(read.statusCode,200);assert.equal(read.bytes().toString(),manifest.toString());
  for(const headers of [{host:'evil.example',origin:previewOrigin},{host:previewHost,origin:origin},
    {host:previewHost,origin:'https://evil.example'}]) {
    const rejected=outgoing();
    await handler(incoming('/api/session','POST',{'content-type':'application/json',...headers},JSON.stringify({invite:token})),rejected);
    assert.equal(rejected.statusCode,403);
    const denied=outgoing();await handler(incoming('/api/book','GET',{cookie,...headers}),denied);assert.equal(denied.statusCode,403);
  }
  const absolute=outgoing();await handler(incoming('https://evil.example/api/book','GET',{cookie}),absolute);assert.equal(absolute.statusCode,403);
});
